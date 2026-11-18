"""Two-phase import: deterministic plan/report first, explicit apply later."""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .dataset_store import (
    DatasetError, apply_records, canonical_json, dataset_snapshot, job_fingerprints,
)
from .importer import ImportParseError, parse_boss_detail_html
from .txt_importer import parse_job_txt


PARSER_VERSION = "jobradar-detail-v1"
DEFAULT_DATABASE = Path.home() / "Desktop/jobradar-v1-data/evidence.sqlite3"
DEFAULT_REPORT = Path.home() / "Desktop/jobradar-v1-data/reports/evidence-dry-run.json"


class BatchImportError(ValueError):
    """Input, report or approval no longer matches the reviewed plan."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _implementation_hash() -> str:
    # No old parser is edited. Changing any participating implementation after
    # approval invalidates the plan even when its current sample output matches.
    names = [
        "batch_import.py", "dataset_store.py", "import_models.py", "importer.py",
        "txt_importer.py", "parser.py",
    ]
    return _sha(canonical_json({
        name: _sha(Path(__file__).with_name(name).read_bytes()) for name in names
    }).encode("utf-8"))


def _plan_hash(report: dict) -> str:
    # Preview timestamps/run UUID are presentation metadata, not data mutations.
    stable = {key: value for key, value in report.items()
              if key not in {"run_id", "generated_at", "plan_sha256"}}
    return _sha(canonical_json(stable).encode("utf-8"))


def _read_candidate(path: Path, fmt: str, inputs: list[dict]) -> dict:
    entry = {"source_file": str(path), "input_format": fmt}
    inputs.append(entry)
    try:
        raw = path.read_bytes()
        entry["source_file_sha256"] = _sha(raw)
        text = raw.decode("utf-8-sig")
        job = parse_boss_detail_html(text) if fmt == "html" else parse_job_txt(text)
    except (OSError, UnicodeError, ImportParseError) as exc:
        entry.update(error_type=type(exc).__name__, message=str(exc))
        raise
    entry["parsed"] = True
    record = job.model_dump()
    return {
        "job": record,
        "jd_chars": len(job.description), "review_status": "unconfirmed",
        "provenance": [{
            "source_file": str(path), "source_file_sha256": entry["source_file_sha256"],
            "input_format": fmt, "parser_version": PARSER_VERSION,
        }],
        **job_fingerprints(record),
    }


def build_plan(input_dir: str | Path, database: str | Path) -> dict:
    """Read source files and an optional dataset; never create/write a database."""
    folder = Path(input_dir).expanduser().resolve()
    db = Path(database).expanduser().resolve()
    if not folder.is_dir():
        raise BatchImportError(f"证据目录不存在：{folder}")
    if db.is_relative_to(folder):
        raise BatchImportError("独立数据库必须位于证据目录之外。")
    snapshot = dataset_snapshot(db)
    files = sorted(
        (path for path in folder.iterdir() if path.suffix.lower() == ".html"),
        key=lambda path: str(path),
    )
    inputs: list[dict] = []
    parsed: list[dict] = []
    failures: list[dict] = []
    diagnostics: list[dict] = []
    html_parsed = txt_fallback_used = 0
    for path in files:
        # Retain the named source path so renames/additions invalidate approval.
        try:
            item = _read_candidate(path, "html", inputs)
            html_parsed += 1
        except (OSError, UnicodeError, ImportParseError) as exc:
            fallback = path.with_suffix(".txt")
            diagnostic = {
                "source_file": str(path), "matching_txt": str(fallback),
                "stage": "html_parse", "error_type": type(exc).__name__,
                "message": str(exc),
            }
            diagnostics.append(diagnostic)
            if fallback.is_file():
                try:
                    item = _read_candidate(fallback, "txt", inputs)
                    txt_fallback_used += 1
                    diagnostic["action"] = "recovered_by_txt"
                except (OSError, UnicodeError, ImportParseError) as txt_exc:
                    failures.append({
                        **diagnostic, "stage": "txt_fallback",
                        "error_type": type(txt_exc).__name__, "message": str(txt_exc),
                        "action": (
                            "fallback_requires_header" if isinstance(txt_exc, ImportParseError)
                            else "repair_fallback_file"
                        ),
                    })
                    continue
            else:
                inputs.append({"source_file": str(fallback), "input_format": "txt", "missing": True})
                failures.append({**diagnostic, "action": "provide_headed_txt"})
                continue
        parsed.append(item)

    # Compare complete facts, not just JD: a changed company/city is a conflict.
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in parsed:
        groups[(item["job"]["source"], item["job"]["url"])].append(item)
    duplicates: list[dict] = []
    conflicts: list[dict] = []
    candidates: list[dict] = []
    stored = {(row["source"], row["url"]): row for row in snapshot["jobs"]}
    batch_conflicts = existing_conflicts = 0
    for key, items in groups.items():
        if len({item["fact_sha256"] for item in items}) != 1:
            batch_conflicts += 1
            conflicts.append({
                "kind": "batch_conflict", "source": key[0], "url": key[1],
                "source_files": [p["source_file"] for i in items for p in i["provenance"]],
                "reason": "同一来源链接有不同岗位字段或 JD；本组全部跳过。",
            })
            continue
        kept = items[0]
        for duplicate in items[1:]:
            duplicates.append({
                "kind": "exact_duplicate", "url": key[1],
                "duplicate_file": duplicate["provenance"][0]["source_file"],
                "kept_file": kept["provenance"][0]["source_file"],
                "reason": "相同来源链接与岗位事实；不重复事实行，保留来源。",
            })
            kept["provenance"].extend(duplicate["provenance"])
        old = stored.get(key)
        if old and old["fact_sha256"] != kept["fact_sha256"]:
            existing_conflicts += 1
            conflicts.append({
                "kind": "existing_conflict", "source": key[0], "url": key[1],
                "source_files": [p["source_file"] for p in kept["provenance"]],
                "reason": "与目标库已有事实不同；不覆盖。",
            })
            continue
        kept["action"] = "existing_exact" if old else "new"
        candidates.append(kept)

    report = {
        "schema_version": 1, "run_id": str(uuid4()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_dir": str(folder), "database_path": str(db),
        "parser_version": PARSER_VERSION, "implementation_sha256": _implementation_hash(),
        "dataset_state": snapshot["state_sha256"],
        "inputs": inputs, "candidates": candidates,
        "duplicates": duplicates, "conflicts": conflicts,
        "failures": failures, "diagnostics": diagnostics,
        "counts": {
            "html_seen": len(files), "html_parsed": html_parsed,
            "txt_fallback_used": txt_fallback_used,
            "new_candidates": sum(i["action"] == "new" for i in candidates),
            "existing_exact": sum(i["action"] == "existing_exact" for i in candidates),
            "batch_exact_duplicates": len(duplicates),
            "batch_conflicts": batch_conflicts, "existing_conflicts": existing_conflicts,
            "failures": len(failures),
            "planned_inserts": sum(i["action"] == "new" for i in candidates),
        },
    }
    report["plan_sha256"] = _plan_hash(report)
    return report


def _markdown(report: dict) -> str:
    def cell(value):
        return str(value or "未知").replace("|", "\\|").replace("\n", " ")
    lines = [
        "# JobRadar V1 批量预览（未入库）", "",
        f"- 输入目录：{report['input_dir']}",
        f"- 目标库：{report['database_path']}",
        f"- 生成时间：{report['generated_at']}",
        f"- 计划 SHA-256：{report['plan_sha256']}",
        "- 状态：全部待逐条人工核对；apply 不等于 confirmed。", "",
        "## 汇总", "", *[f"- {key}: {value}" for key, value in report["counts"].items()], "",
        "## 拟导入清单", "",
        "| # | 动作 | 岗位 | 公司 | 城市 | JD字符数 | URL |",
        "|---:|---|---|---|---|---:|---|",
    ]
    for index, item in enumerate(report["candidates"], 1):
        job = item["job"]
        lines.append(
            f"| {index} | {item['action']} | {cell(job['title'])} | "
            f"{cell(job['company'])} | {cell(job['city'])} | {len(job['description'])} | {cell(job['url'])} |"
        )
    for title, key in [
        ("批内重复", "duplicates"), ("冲突清单（跳过）", "conflicts"),
        ("失败清单", "failures"), ("HTML 回退诊断", "diagnostics"),
    ]:
        lines += ["", f"## {title}", ""]
        lines += [json.dumps(row, ensure_ascii=False) for row in report[key]] or ["无。"]
    lines += ["", "## 逐条字段与完整 JD", ""]
    for index, item in enumerate(report["candidates"], 1):
        job = item["job"]
        lines += [f"### {index}. {cell(job['title'])}", ""]
        lines += [f"- {key}: {cell(value)}"
                  for key, value in job.items() if key != "description"]
        lines += ["- review_status: unconfirmed"]
        lines += [f"- provenance: {json.dumps(p, ensure_ascii=False)}" for p in item["provenance"]]
        lines += ["", job["description"], ""]
    return "\n".join(lines) + "\n"


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve() or (
        left.exists() and right.exists() and os.path.samefile(left, right)
    )


def _report_paths(report: dict, path: str | Path) -> tuple[Path, Path]:
    target = Path(path).expanduser().resolve()
    if target.suffix.lower() != ".json":
        raise BatchImportError("报告路径必须以 .json 结尾，另生成同名 .md。")
    targets = (target, target.with_suffix(".md"))
    folder, db = Path(report["input_dir"]), Path(report["database_path"])
    legacy = Path(__file__).resolve().parents[2] / "data/jobs.db"
    for output in targets:
        if output.is_relative_to(folder):
            raise BatchImportError("报告必须位于证据目录之外，避免覆盖原文件。")
        for protected in [db, legacy, *folder.iterdir()]:
            if _same_path(output, protected):
                raise BatchImportError(f"报告路径与数据库/原始证据冲突：{output}")
    return targets


def write_report(report: dict, path: str | Path) -> None:
    """Persist JSON plus readable full-JD Markdown, never a dataset."""
    if report.get("plan_sha256") != _plan_hash(report):
        raise BatchImportError("报告计划哈希不一致。")
    json_path, md_path = _report_paths(report, path)
    # Atomic replacement prevents a half-written individual report.
    for target, text in [
        (json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n"),
        (md_path, _markdown(report)),
    ]:
        target.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent, delete=False,
            ) as handle:
                name = handle.name
                handle.write(text)
            os.replace(name, target)
        finally:
            if name and Path(name).exists():
                Path(name).unlink()


def apply_plan(
    report_path: str | Path, input_dir: str | Path, database: str | Path,
    *, confirm: bool = False,
) -> dict:
    """Rebuild the reviewed plan; only unchanged eligible candidates may commit."""
    if not confirm:
        raise BatchImportError("写库需要 --apply --confirm，先查看 dry-run 报告。")
    try:
        report = json.loads(Path(report_path).expanduser().read_text(encoding="utf-8"))
        if report["schema_version"] != 1 or report["plan_sha256"] != _plan_hash(report):
            raise BatchImportError("报告版本/计划哈希无效。")
        _report_paths(report, report_path)
        current = build_plan(input_dir, database)
        if current["plan_sha256"] != report["plan_sha256"]:
            raise BatchImportError("源文件、解析实现或目标库已变化；请重新 dry-run 并确认。")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BatchImportError("报告结构无效；请重新 dry-run。") from exc
    # A confirmed partial plan imports only candidates already listed as eligible;
    # failures and conflicting identities are never coerced into successful rows.
    result = apply_records(
        Path(report["database_path"]),
        [{"job": item["job"], "provenance": item["provenance"]}
         for item in report["candidates"]],
        {
            "run_id": report["run_id"], "input_dir": report["input_dir"],
            "report_path": str(Path(report_path).expanduser().resolve()),
            "plan_sha256": report["plan_sha256"],
            "failed_count": len(report["failures"]) + len(report["conflicts"]),
        },
        expected_state=report["dataset_state"],
    )
    return {
        **result, "skipped_failures": len(report["failures"]),
        "skipped_conflicts": len(report["conflicts"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V1 独立数据集：先预览报告，再显式确认入库")
    parser.add_argument("--input-dir", default=str(Path.home() / "Desktop/boss-evidence"))
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.dry_run:
            if args.confirm:
                raise BatchImportError("--confirm 仅与 --apply 一起使用。")
            report = build_plan(args.input_dir, args.database)
            write_report(report, args.report)
            print("DRY-RUN（未入库）: " + json.dumps(report["counts"], ensure_ascii=False))
            print("JSON: " + str(Path(args.report).expanduser().resolve()))
            print("Markdown: " + str(Path(args.report).expanduser().resolve().with_suffix(".md")))
            return int(bool(report["failures"] or report["conflicts"]))
        result = apply_plan(
            args.report, args.input_dir, args.database, confirm=args.confirm,
        )
        print("APPLY（待逐条核对）: " + json.dumps(result, ensure_ascii=False))
        return int(bool(result["skipped_failures"] or result["skipped_conflicts"]))
    except (BatchImportError, DatasetError, OSError, UnicodeError) as exc:
        print(f"批量导入失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

