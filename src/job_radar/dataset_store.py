"""Independent full-JD SQLite store; never uses the legacy crawler database.

All writes require the logical snapshot approved in a batch plan. Source files
are evidence references only: this module neither reads them nor calls parsers.
"""
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .import_models import ImportedJob
from .importer import ImportParseError, _boss_url


APPLICATION_ID = 0x4A525631  # JRV1, not a generic/legacy SQLite database.
SCHEMA_VERSION = 1
LEGACY_DATABASE = Path(__file__).resolve().parents[2] / "data" / "jobs.db"


class DatasetError(ValueError):
    """Dataset identity, approval snapshot or record content is invalid."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validated_job(value: dict | ImportedJob) -> ImportedJob:
    # Dump and validate again: model_construct() must not bypass the write gate.
    if isinstance(value, ImportedJob):
        value = value.model_dump()
    try:
        job = ImportedJob.model_validate(value)
        if job.source not in {"boss", "manual"}:
            raise DatasetError("独立数据集来源只接受 boss 或 manual。")
        if job.source == "boss":
            job = ImportedJob.model_validate({**job.model_dump(), "url": _boss_url(job.url)})
        return job
    except (ValidationError, ImportParseError, TypeError) as exc:
        raise DatasetError(f"导入岗位契约无效：{exc}") from exc


def job_fingerprints(job: dict | ImportedJob) -> dict[str, str]:
    record = _validated_job(job)
    return {
        "jd_sha256": _sha(record.description),
        "fact_sha256": _sha(canonical_json(record.model_dump())),
    }


_SCHEMA = (
    """CREATE TABLE import_runs (
        run_id TEXT PRIMARY KEY,
        input_dir TEXT NOT NULL,
        database_path TEXT NOT NULL,
        report_path TEXT NOT NULL,
        plan_sha256 TEXT NOT NULL,
        mode TEXT NOT NULL CHECK(mode='apply'),
        started_at TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status='applied'),
        candidate_count INTEGER NOT NULL CHECK(candidate_count>=0),
        failed_count INTEGER NOT NULL CHECK(failed_count>=0),
        inserted_count INTEGER NOT NULL CHECK(inserted_count>=0),
        existing_exact_count INTEGER NOT NULL CHECK(existing_exact_count>=0),
        provenance_added_count INTEGER NOT NULL CHECK(provenance_added_count>=0)
    )""",
    """CREATE TABLE imported_jobs (
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL CHECK(source IN ('boss','manual')),
        url TEXT NOT NULL CHECK(length(trim(url))>0),
        title TEXT NOT NULL CHECK(length(trim(title))>0),
        company TEXT NOT NULL DEFAULT '',
        city TEXT NOT NULL DEFAULT '',
        salary TEXT NOT NULL DEFAULT '',
        education TEXT NOT NULL DEFAULT '',
        experience TEXT NOT NULL DEFAULT '',
        attendance TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL CHECK(length(trim(description))>0),
        description_kind TEXT NOT NULL CHECK(description_kind='full_jd'),
        posted_at TEXT NOT NULL DEFAULT '',
        source_tags_json TEXT NOT NULL CHECK(json_valid(source_tags_json) AND json_type(source_tags_json)='array'),
        skills_json TEXT NOT NULL CHECK(json_valid(skills_json) AND json_type(skills_json)='array'),
        warnings_json TEXT NOT NULL CHECK(json_valid(warnings_json) AND json_type(warnings_json)='array'),
        jd_sha256 TEXT NOT NULL,
        fact_sha256 TEXT NOT NULL,
        review_status TEXT NOT NULL DEFAULT 'unconfirmed' CHECK(review_status IN ('unconfirmed','confirmed','rejected')),
        first_imported_at TEXT NOT NULL,
        last_imported_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(source,url)
    )""",
    """CREATE TABLE job_provenance (
        id INTEGER PRIMARY KEY,
        job_id INTEGER NOT NULL REFERENCES imported_jobs(id),
        source_file TEXT NOT NULL,
        source_file_sha256 TEXT NOT NULL CHECK(length(source_file_sha256)=64 AND source_file_sha256 NOT GLOB '*[^0-9a-f]*'),
        input_format TEXT NOT NULL CHECK(input_format IN ('html','txt')),
        parser_version TEXT NOT NULL CHECK(length(trim(parser_version))>0),
        imported_at TEXT NOT NULL,
        import_run_id TEXT NOT NULL REFERENCES import_runs(run_id),
        UNIQUE(job_id,source_file_sha256,parser_version)
    )""",
)
_TABLES = ("import_runs", "imported_jobs", "job_provenance")
_JOB_SCALARS = (
    "source", "url", "title", "company", "city", "salary", "education",
    "experience", "attendance", "description", "description_kind", "posted_at",
)
_JOB_LISTS = ("source_tags", "skills", "warnings")
_MISSING_HASH = _sha(canonical_json({"exists": False}))


def _target(path: str | Path) -> Path:
    target = Path(path).expanduser().resolve()
    legacy = LEGACY_DATABASE.resolve()
    if target == legacy or (target.exists() and legacy.exists() and target.samefile(legacy)):
        raise DatasetError("目标是原岗位数据库或其别名；请选择独立 V1 数据集。")
    if target.exists() and not target.is_file():
        raise DatasetError("数据集路径必须是文件。")
    return target


def _normalized_sql(sql: str) -> str:
    return " ".join(sql.split()).rstrip(";")


def _schema(connection: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    )]


def _validate_schema(connection: sqlite3.Connection) -> None:
    if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION):
        raise DatasetError("目标不是本版本的独立 V1 数据集。")
    actual = _schema(connection)
    expected = {name: _normalized_sql(sql) for name, sql in zip(_TABLES, _SCHEMA)}
    if len(actual) != len(expected) or any(
        row["type"] != "table" or row["name"] not in expected
        or _normalized_sql(row["sql"] or "") != expected[row["name"]]
        for row in actual
    ):
        raise DatasetError("独立数据集表结构与版本不一致。")


def _snapshot(connection: sqlite3.Connection) -> dict:
    _validate_schema(connection)
    tables = {
        name: [dict(row) for row in connection.execute(
            f"SELECT * FROM {name} ORDER BY " + ("run_id" if name == "import_runs" else "id")
        )]
        for name in _TABLES
    }
    for row in tables["imported_jobs"]:
        try:
            value = {key: row[key] for key in _JOB_SCALARS}
            value.update({key: json.loads(row[key + "_json"]) for key in _JOB_LISTS})
            record = _validated_job(value)
            hashes = job_fingerprints(record)
            if (record.url != row["url"] or any(row[key] != value for key, value in hashes.items())):
                raise DatasetError("数据集事实或指纹不一致，请先核对已存记录。")
        except (TypeError, json.JSONDecodeError) as exc:
            raise DatasetError("数据集事实 JSON 无效。") from exc
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise DatasetError("数据集来源记录存在无效外键。")
    # Cover all committed logical data, not only facts/file bytes: review edits,
    # provenance, runs and WAL-backed content must invalidate an old plan too.
    state = {
        "application_id": APPLICATION_ID, "schema_version": SCHEMA_VERSION,
        "schema": _schema(connection), "tables": tables,
    }
    jobs = [{key: row[key] for key in (
        "id", "source", "url", "fact_sha256", "jd_sha256", "review_status",
    )} for row in tables["imported_jobs"]]
    return {"exists": True, "state_sha256": _sha(canonical_json(state)), "jobs": jobs}


def dataset_snapshot(path: str | Path) -> dict:
    """Read a consistent logical snapshot without creating files/directories."""
    target = _target(path)
    if not target.exists():
        return {"exists": False, "state_sha256": _MISSING_HASH, "jobs": []}
    try:
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            return _snapshot(connection)
    except sqlite3.Error as exc:
        raise DatasetError(f"读取独立数据集失败：{exc}") from exc


def _prepare(candidates: list[dict]) -> list[dict]:
    prepared = []
    identities: dict[tuple[str, str], str] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) != {"job", "provenance"}:
            raise DatasetError("候选必须只包含 job 与 provenance。")
        job = _validated_job(candidate["job"])
        hashes = job_fingerprints(job)
        identity = (job.source, job.url)
        if identity in identities and identities[identity] != hashes["fact_sha256"]:
            raise DatasetError("批次内同 URL 存在不同事实，未写入。")
        identities[identity] = hashes["fact_sha256"]
        provenance = candidate["provenance"]
        if not isinstance(provenance, list) or not provenance:
            raise DatasetError("每条岗位都必须有 provenance。")
        for item in provenance:
            if not isinstance(item, dict) or set(item) != {
                "source_file", "source_file_sha256", "input_format", "parser_version",
            }:
                raise DatasetError("provenance 字段不完整。")
            if (not isinstance(item["source_file"], str)
                    or not Path(item["source_file"]).is_absolute()
                    or not isinstance(item["source_file_sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", item["source_file_sha256"])
                    or item["input_format"] not in {"html", "txt"}
                    or not isinstance(item["parser_version"], str)
                    or not item["parser_version"].strip()):
                raise DatasetError("provenance 路径、SHA-256、格式或版本无效。")
        prepared.append({"job": job, "hashes": hashes, "provenance": provenance})
    if not prepared:
        raise DatasetError("没有可写入候选，未创建数据集。")
    return prepared


def apply_records(
    path: str | Path, candidates: list[dict], run: dict, expected_state: str,
) -> dict:
    """Atomically insert approved full JDs/provenance; conflicts never overwrite.

    Validate the complete input before opening a write connection. Recheck the
    logical snapshot under BEGIN IMMEDIATE to reject changes after dry-run.
    """
    target = _target(path)
    prepared = _prepare(candidates)
    required = {"run_id", "input_dir", "report_path", "plan_sha256", "failed_count"}
    if (not isinstance(run, dict) or not required.issubset(run)
            or any(not isinstance(run[key], str) or not run[key].strip()
                   for key in required - {"failed_count"})
            or not re.fullmatch(r"[0-9a-f]{64}", run["plan_sha256"])
            or type(run["failed_count"]) is not int or run["failed_count"] < 0):
        raise DatasetError("导入运行信息不完整。")
    if not isinstance(expected_state, str):
        raise DatasetError("需要 dry-run 的数据集状态哈希。")
    before = dataset_snapshot(target)
    if before["state_sha256"] != expected_state:
        raise DatasetError("数据集自 dry-run 后已变化，请重新生成报告。")
    target.parent.mkdir(parents=True, exist_ok=True)
    inserted = existing_exact = provenance_added = 0
    try:
        with closing(sqlite3.connect(target.as_uri() + "?mode=rwc", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            try:
                objects = _schema(connection)
                fresh = not objects and connection.execute("PRAGMA application_id").fetchone()[0] == 0
                if fresh and not before["exists"]:
                    state_hash = _MISSING_HASH
                else:
                    state_hash = _snapshot(connection)["state_sha256"]
                if state_hash != expected_state:
                    raise DatasetError("数据集自 dry-run 后已变化，请重新生成报告。")
                if fresh:
                    for sql in _SCHEMA:
                        connection.execute(sql)  # No executescript: retain the transaction.
                    connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                now = datetime.now(timezone.utc).isoformat()
                connection.execute(
                    "INSERT INTO import_runs VALUES (?,?,?,?,?,'apply',?,?,'applied',?,?,?,?,?)",
                    (run["run_id"], run["input_dir"], str(target), run["report_path"],
                     run["plan_sha256"], now, now, len(prepared), run["failed_count"], 0, 0, 0),
                )
                for candidate in prepared:
                    job = candidate["job"]
                    hashes = candidate["hashes"]
                    row = connection.execute(
                        "SELECT id,fact_sha256 FROM imported_jobs WHERE source=? AND url=?",
                        (job.source, job.url),
                    ).fetchone()
                    if row is not None:
                        if row["fact_sha256"] != hashes["fact_sha256"]:
                            raise DatasetError("已有 URL 对应不同事实，未覆盖；整批回滚。")
                        job_id = row["id"]
                        existing_exact += 1
                        # Re-observation time changes; facts and human review do not.
                        connection.execute(
                            "UPDATE imported_jobs SET last_imported_at=? WHERE id=?",
                            (now, job_id),
                        )
                    else:
                        columns = [*_JOB_SCALARS, *(name + "_json" for name in _JOB_LISTS),
                                   "jd_sha256", "fact_sha256", "review_status",
                                   "first_imported_at", "last_imported_at", "created_at", "updated_at"]
                        values = [*(getattr(job, name) for name in _JOB_SCALARS),
                                  *(canonical_json(getattr(job, name)) for name in _JOB_LISTS),
                                  hashes["jd_sha256"], hashes["fact_sha256"], "unconfirmed",
                                  now, now, now, now]
                        cursor = connection.execute(
                            "INSERT INTO imported_jobs (" + ",".join(columns) + ") VALUES ("
                            + ",".join("?" for _ in columns) + ")", values,
                        )
                        job_id = cursor.lastrowid
                        inserted += 1
                    for item in candidate["provenance"]:
                        cursor = connection.execute(
                            "INSERT INTO job_provenance "
                            "(job_id,source_file,source_file_sha256,input_format,parser_version,imported_at,import_run_id) "
                            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(job_id,source_file_sha256,parser_version) DO NOTHING",
                            (job_id, item["source_file"], item["source_file_sha256"], item["input_format"],
                             item["parser_version"], now, run["run_id"]),
                        )
                        provenance_added += cursor.rowcount
                connection.execute(
                    "UPDATE import_runs SET completed_at=?,inserted_count=?,existing_exact_count=?,"
                    "provenance_added_count=? WHERE run_id=?",
                    (datetime.now(timezone.utc).isoformat(), inserted, existing_exact,
                     provenance_added, run["run_id"]),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
    except sqlite3.Error as exc:
        raise DatasetError(f"独立数据集写入失败，事务已回滚：{exc}") from exc
    return {"run_id": run["run_id"], "inserted": inserted,
            "existing_exact": existing_exact, "provenance_added": provenance_added}
