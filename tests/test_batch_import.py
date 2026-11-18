"""Batch planning/apply tests use temporary evidence and isolated datasets only."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from job_radar.batch_import import apply_plan, build_plan, main, write_report


FIXTURE = Path(__file__).parent / "fixtures" / "boss-detail.html"
ORIGINAL_ID = "89cdce766fdcb2020nF82N64F1JR"


@pytest.fixture
def workspace(tmp_path):
    folder = tmp_path / "evidence"
    folder.mkdir()
    return folder, tmp_path / "dataset.sqlite3", tmp_path / "reports" / "plan.json"


def add_html(folder, name="one.html", identity="job-one", **replacements):
    text = FIXTURE.read_text(encoding="utf-8").replace(ORIGINAL_ID, identity)
    for old, new in replacements.items():
        text = text.replace(old, new)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def headed_txt(identity="fallback-job"):
    return (
        "格式版本: jobradar-txt-v1\n来源: boss\n岗位名称: Python 实习生\n"
        f"岗位链接: https://www.zhipin.com/job_detail/{identity}.html\n"
        "正文:\n岗位职责\n开发 Python API。\n\n任职要求\n熟悉 Git。"
    )


def save_plan(folder, database, report):
    plan = build_plan(folder, database)
    write_report(plan, report)
    return plan


def row_count(database, table="imported_jobs"):
    with sqlite3.connect(database) as connection:
        return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_plan_scans_only_top_level_html_and_preserves_evidence(workspace):
    folder, database, report = workspace
    second = add_html(folder, "z.html", "second")
    first = add_html(folder, "a.html", "first")
    raw_txt = folder / "a.txt"
    raw_txt.write_text("无头部的原文备份", encoding="utf-8")
    (folder / "nested").mkdir()
    add_html(folder / "nested", "nested.html", "nested")
    (folder / ".DS_Store").write_bytes(b"metadata")
    before = {path: path.read_bytes() for path in (first, second, raw_txt)}

    plan = save_plan(folder, database, report)

    assert plan["counts"]["html_seen"] == 2
    assert plan["counts"]["html_parsed"] == 2
    assert plan["counts"]["new_candidates"] == 2
    assert plan["counts"]["planned_inserts"] == 2
    assert plan["counts"]["failures"] == 0
    assert not database.exists()
    assert all(path.read_bytes() == content for path, content in before.items())
    assert str(raw_txt) not in json.dumps(plan["inputs"], ensure_ascii=False)
    paths = [item["provenance"][0]["source_file"] for item in plan["candidates"]]
    assert paths == sorted(paths)


def test_same_title_different_urls_are_not_duplicates(workspace):
    folder, database, _ = workspace
    add_html(folder, "one.html", "one")
    add_html(folder, "two.html", "two")
    plan = build_plan(folder, database)
    assert len(plan["candidates"]) == 2
    assert len({item["job"]["title"] for item in plan["candidates"]}) == 1
    assert len({item["job"]["url"] for item in plan["candidates"]}) == 2
    assert plan["duplicates"] == [] and plan["conflicts"] == []


def test_exact_batch_duplicates_keep_one_job_and_all_source_versions(workspace):
    folder, database, report = workspace
    first = add_html(folder, "a.html", "same")
    second = folder / "b.html"
    second.write_text(first.read_text() + "<!-- different source bytes -->", encoding="utf-8")
    plan = save_plan(folder, database, report)
    assert plan["counts"]["batch_exact_duplicates"] == 1
    assert plan["counts"]["planned_inserts"] == 1
    assert len(plan["candidates"]) == 1 and len(plan["duplicates"]) == 1
    provenance = plan["candidates"][0]["provenance"]
    assert {item["source_file"] for item in provenance} == {str(first), str(second)}
    apply_plan(report, folder, database, confirm=True)
    assert row_count(database) == 1
    assert row_count(database, "job_provenance") == 2


@pytest.mark.parametrize("changed", ["description", "company"])
def test_same_url_different_facts_excludes_entire_conflicting_group(workspace, changed):
    folder, database, _ = workspace
    first = add_html(folder, "a.html", "same")
    second = folder / "b.html"
    text = first.read_text()
    if changed == "description":
        text = text.replace("岗位介绍", "岗位介绍：新的职责")
    else:
        text = text.replace(">ONES<", ">另一家公司<")
    second.write_text(text, encoding="utf-8")
    plan = build_plan(folder, database)
    assert plan["counts"]["batch_conflicts"] >= 1
    assert plan["counts"]["planned_inserts"] == 0
    assert plan["candidates"] == [] and plan["conflicts"]


def test_failed_file_does_not_stop_other_files_and_backup_needs_headers(workspace):
    folder, database, report = workspace
    good = add_html(folder)
    bad = folder / "bad.html"
    bad.write_text("<title>安全验证</title>", encoding="utf-8")
    backup = folder / "bad.txt"
    backup.write_text("岗位职责\n这只是备份正文。", encoding="utf-8")
    originals = {p: p.read_bytes() for p in (good, bad, backup)}
    plan = save_plan(folder, database, report)
    assert plan["counts"]["html_seen"] == 2
    assert plan["counts"]["html_parsed"] == 1
    assert plan["counts"]["planned_inserts"] == 1
    assert plan["counts"]["failures"] == 1
    assert "fallback_requires_header" in json.dumps(plan["failures"], ensure_ascii=False)
    assert str(bad) in json.dumps(plan["failures"], ensure_ascii=False)
    assert str(backup) in json.dumps(plan["failures"], ensure_ascii=False)
    assert all(p.read_bytes() == b for p, b in originals.items())
    apply_plan(report, folder, database, confirm=True)
    assert row_count(database) == 1  # Explicit apply accepts the reviewed good subset.


def test_header_completed_txt_recovers_failed_html_without_second_job(workspace):
    folder, database, report = workspace
    (folder / "fallback.html").write_text("<html>JS shell</html>", encoding="utf-8")
    txt = folder / "fallback.txt"
    txt.write_text(headed_txt(), encoding="utf-8")
    plan = save_plan(folder, database, report)
    assert plan["counts"]["txt_fallback_used"] == 1
    assert plan["counts"]["failures"] == 0
    assert plan["counts"]["planned_inserts"] == 1
    candidate = plan["candidates"][0]
    assert candidate["job"]["title"] == "Python 实习生"
    assert candidate["provenance"][0]["input_format"] == "txt"
    assert candidate["provenance"][0]["source_file_sha256"] == hashlib.sha256(txt.read_bytes()).hexdigest()
    apply_plan(report, folder, database, confirm=True)
    assert row_count(database) == 1


def test_source_hash_is_original_bytes_and_plan_hash_is_repeatable(workspace):
    folder, database, _ = workspace
    path = add_html(folder)
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes().replace(b"\n", b"\r\n"))
    first = build_plan(folder, database)
    second = build_plan(folder, database)
    assert first["plan_sha256"] == second["plan_sha256"]
    assert first["candidates"][0]["provenance"][0]["source_file_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert first["candidates"][0]["jd_sha256"] == hashlib.sha256(
        first["candidates"][0]["job"]["description"].encode("utf-8"),
    ).hexdigest()


def test_reports_have_fields_full_jd_failures_and_no_database(workspace):
    folder, database, report = workspace
    add_html(folder)
    plan = save_plan(folder, database, report)
    saved = json.loads(report.read_text(encoding="utf-8"))
    markdown = report.with_suffix(".md").read_text(encoding="utf-8")
    assert saved == plan
    assert "AI Agent 开发实习生" in markdown and "ONES" in markdown
    assert "岗位职责" in markdown and "任职要求" in markdown
    assert "unconfirmed" in markdown
    assert not database.exists()


def test_apply_requires_explicit_confirmation(workspace):
    folder, database, report = workspace
    add_html(folder)
    save_plan(folder, database, report)
    with pytest.raises(ValueError):
        apply_plan(report, folder, database)
    assert not database.exists()


@pytest.mark.parametrize("mutation", ["change", "add", "remove", "becomes_directory"])
def test_changed_inputs_after_report_refuse_apply_without_database(workspace, mutation):
    folder, database, report = workspace
    source = add_html(folder)
    save_plan(folder, database, report)
    if mutation == "change":
        source.write_text(source.read_text() + "<!-- updated -->", encoding="utf-8")
    elif mutation == "add":
        add_html(folder, "new.html", "new")
    elif mutation == "remove":
        source.unlink()
    else:
        source.unlink()
        source.mkdir()
    with pytest.raises(ValueError):
        apply_plan(report, folder, database, confirm=True)
    assert not database.exists()


def test_changed_fallback_txt_after_report_refuses_apply(workspace):
    folder, database, report = workspace
    (folder / "fallback.html").write_text("not HTML", encoding="utf-8")
    fallback = folder / "fallback.txt"
    fallback.write_text(headed_txt(), encoding="utf-8")
    save_plan(folder, database, report)
    fallback.write_text(headed_txt() + "\n新增要求", encoding="utf-8")
    with pytest.raises(ValueError):
        apply_plan(report, folder, database, confirm=True)
    assert not database.exists()


def test_unused_raw_txt_can_change_without_invalidating_html_plan(workspace):
    folder, database, report = workspace
    add_html(folder)
    raw = folder / "one.txt"
    raw.write_text("备份", encoding="utf-8")
    save_plan(folder, database, report)
    raw.write_text("另一个备份，不用于 HTML 解析", encoding="utf-8")
    apply_plan(report, folder, database, confirm=True)
    assert row_count(database) == 1


def test_tampered_report_or_wrong_paths_refuse_apply(workspace):
    folder, database, report = workspace
    add_html(folder)
    save_plan(folder, database, report)
    tampered = json.loads(report.read_text())
    tampered["candidates"][0]["job"]["title"] = "被篡改的岗位"
    report.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError):
        apply_plan(report, folder, database, confirm=True)
    assert not database.exists()
    save_plan(folder, database, report)
    with pytest.raises(ValueError):
        apply_plan(report, folder, database.with_name("other.sqlite3"), confirm=True)
    other = folder.parent / "other"
    other.mkdir()
    with pytest.raises(ValueError):
        apply_plan(report, other, database, confirm=True)


def test_existing_exact_and_field_only_conflict_are_classified(workspace):
    folder, database, report = workspace
    source = add_html(folder)
    save_plan(folder, database, report)
    apply_plan(report, folder, database, confirm=True)
    plan = build_plan(folder, database)
    assert plan["counts"]["existing_exact"] == 1
    assert plan["counts"]["planned_inserts"] == 0
    source.write_text(source.read_text().replace(">ONES<", ">另一家公司<"), encoding="utf-8")
    conflict = build_plan(folder, database)
    assert conflict["counts"]["existing_conflicts"] == 1
    assert conflict["counts"]["planned_inserts"] == 0
    assert conflict["conflicts"]
    assert row_count(database) == 1


def test_dataset_changed_since_plan_refuses_before_partial_writes(workspace):
    folder, database, report = workspace
    add_html(folder)
    save_plan(folder, database, report)
    other = folder.parent / "other-input"
    other.mkdir()
    add_html(other, identity="other")
    other_report = report.with_name("other.json")
    save_plan(other, database, other_report)
    apply_plan(other_report, other, database, confirm=True)
    before = database.read_bytes()
    with pytest.raises(ValueError):
        apply_plan(report, folder, database, confirm=True)
    assert database.read_bytes() == before and row_count(database) == 1


@pytest.mark.parametrize("collision", ["source_json", "source_md", "database", "database_md"])
def test_report_path_aliases_cannot_overwrite_evidence_or_database(workspace, collision):
    folder, database, _ = workspace
    source = add_html(folder)
    plan = build_plan(folder, database)
    if collision == "source_json":
        report = source
    elif collision == "source_md":
        # The derived Markdown path aliases an input through a symlink.
        report = folder.parent / "report.json"
        report.with_suffix(".md").symlink_to(source)
    elif collision == "database":
        report = database
    else:
        database = folder.parent / "report.md"
        plan = build_plan(folder, database)
        report = database.with_suffix(".json")
    original = source.read_bytes()
    with pytest.raises(ValueError):
        write_report(plan, report)
    assert source.read_bytes() == original
    assert not database.exists()


def test_cli_dry_run_and_apply_confirmation(workspace, capsys):
    folder, database, report = workspace
    add_html(folder)
    args = ["--input-dir", str(folder), "--database", str(database), "--report", str(report)]
    assert main([*args, "--dry-run"]) == 0
    assert report.exists() and not database.exists()
    assert main([*args, "--apply"]) == 2
    assert not database.exists()
    assert main([*args, "--apply", "--confirm"]) == 0
    assert row_count(database) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_empty_plan_and_conflict_only_plan_never_create_dataset(workspace):
    folder, database, report = workspace
    save_plan(folder, database, report)
    with pytest.raises(ValueError, match="没有可写入候选"):
        apply_plan(report, folder, database, confirm=True)
    assert not database.exists()
    first = add_html(folder, "a.html", "same")
    (folder / "b.html").write_text(
        first.read_text().replace("岗位介绍", "岗位介绍不同版本"), encoding="utf-8",
    )
    save_plan(folder, database, report)
    with pytest.raises(ValueError):
        apply_plan(report, folder, database, confirm=True)
    assert not database.exists()


def test_new_fallback_after_preview_invalidates_report(workspace):
    folder, database, report = workspace
    add_html(folder)
    (folder / "bad.html").write_text("broken page", encoding="utf-8")
    save_plan(folder, database, report)
    (folder / "bad.txt").write_text(headed_txt(), encoding="utf-8")
    with pytest.raises(ValueError):
        apply_plan(report, folder, database, confirm=True)
    assert not database.exists()


def test_partial_apply_reports_skipped_items_and_exit_one(workspace, capsys):
    folder, database, report = workspace
    add_html(folder)
    (folder / "bad.html").write_text("broken page", encoding="utf-8")
    save_plan(folder, database, report)
    args = ["--input-dir", str(folder), "--database", str(database),
            "--report", str(report), "--apply", "--confirm"]
    assert main(args) == 1
    assert row_count(database) == 1
    assert '"skipped_failures": 1' in capsys.readouterr().out


def test_changed_implementation_requires_new_report(workspace, monkeypatch):
    import job_radar.batch_import as batch

    folder, database, report = workspace
    add_html(folder)
    save_plan(folder, database, report)
    monkeypatch.setattr(batch, "_implementation_hash", lambda: "c" * 64)
    with pytest.raises(ValueError):
        apply_plan(report, folder, database, confirm=True)
    assert not database.exists()


def test_report_cannot_alias_existing_dataset_via_hardlink(workspace):
    import os

    folder, database, report = workspace
    add_html(folder)
    save_plan(folder, database, report)
    apply_plan(report, folder, database, confirm=True)
    before = database.read_bytes()
    alias = folder.parent / "alias.json"
    os.link(database, alias)
    with pytest.raises(ValueError):
        write_report(build_plan(folder, database), alias)
    assert database.read_bytes() == before


def test_cli_malformed_report_returns_two_without_traceback(workspace, capsys):
    folder, database, report = workspace
    add_html(folder)
    report.parent.mkdir()
    report.write_text("[]", encoding="utf-8")
    args = ["--input-dir", str(folder), "--database", str(database),
            "--report", str(report), "--apply", "--confirm"]
    assert main(args) == 2
    assert "Traceback" not in capsys.readouterr().err
    assert not database.exists()



def test_cli_partial_report_exit_one_and_bad_directory_exit_two(workspace, capsys):
    folder, database, report = workspace
    add_html(folder)
    (folder / "bad.html").write_text("not detail", encoding="utf-8")
    args = ["--input-dir", str(folder), "--database", str(database), "--report", str(report)]
    assert main([*args, "--dry-run"]) == 1
    assert report.exists() and not database.exists()
    assert main([
        "--input-dir", str(folder / "missing"), "--database", str(database),
        "--report", str(report), "--dry-run",
    ]) == 2
    assert "Traceback" not in capsys.readouterr().err
