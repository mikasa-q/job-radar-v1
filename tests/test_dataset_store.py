"""Independent-dataset writes are atomic, idempotent, and full-JD only."""
import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from job_radar import dataset_store as store
from job_radar.import_models import ImportedJob


URL = "https://www.zhipin.com/job_detail/abc123.html"


def record(**changes):
    job = ImportedJob(
        title="Python 实习生", url=URL, description="岗位职责\n开发 Python API。",
        company="公司", city="深圳", salary="200元/天", education="本科",
        experience="", attendance="4天/周 4个月", posted_at="",
        source_tags=["Python", "Redis"], skills=["Python"], warnings=[],
    ).model_dump()
    job.update(changes)
    return {"job": job, "provenance": [{
        "source_file": "/evidence/job.html", "source_file_sha256": "a" * 64,
        "input_format": "html", "parser_version": "boss-detail-v1",
    }]}


def run(number=1):
    return {"run_id": f"run-{number}", "input_dir": "/evidence", "report_path": "/reports/plan.json",
            "plan_sha256": "b" * 64, "failed_count": 0}


def apply(path, records=None, number=1):
    return store.apply_records(
        path, [record()] if records is None else records, run(number),
        store.dataset_snapshot(path)["state_sha256"],
    )


def rows(path, table):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]


def test_missing_snapshot_does_not_create_file_or_parent(tmp_path):
    path = tmp_path / "not-created" / "evidence.sqlite3"
    snapshot = store.dataset_snapshot(path)
    assert snapshot["exists"] is False and snapshot["jobs"] == []
    assert len(snapshot["state_sha256"]) == 64
    assert not path.parent.exists()


def test_full_contract_and_provenance_roundtrip(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    result = apply(path)
    assert result == {"run_id": "run-1", "inserted": 1, "existing_exact": 0, "provenance_added": 1}
    job = rows(path, "imported_jobs")[0]
    original = record()["job"]
    for key in store._JOB_SCALARS:
        assert job[key] == original[key]
    for key in ("source_tags", "skills", "warnings"):
        assert json.loads(job[key + "_json"]) == original[key]
    assert job["review_status"] == "unconfirmed"
    assert job["jd_sha256"] == hashlib.sha256(original["description"].encode()).hexdigest()
    provenance = rows(path, "job_provenance")[0]
    assert provenance["job_id"] == job["id"]
    assert provenance["source_file"] == "/evidence/job.html"
    assert provenance["source_file_sha256"] == "a" * 64
    assert provenance["parser_version"] == "boss-detail-v1"
    assert provenance["import_run_id"] == "run-1"
    assert datetime.fromisoformat(provenance["imported_at"]).utcoffset().total_seconds() == 0
    assert rows(path, "import_runs")[0]["status"] == "applied"


def test_snapshot_hash_stable_and_read_only(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    apply(path)
    before = path.read_bytes()
    first = store.dataset_snapshot(path)
    assert first == store.dataset_snapshot(path)
    assert path.read_bytes() == before
    assert first["jobs"][0]["url"] == URL


def test_exact_repeat_preserves_facts_review_and_provenance(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    apply(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE imported_jobs SET review_status='confirmed'")
    before = rows(path, "imported_jobs")
    result = apply(path, number=2)
    assert result["inserted"] == 0 and result["existing_exact"] == 1 and result["provenance_added"] == 0
    after = rows(path, "imported_jobs")
    assert after[0]["last_imported_at"] >= before[0]["last_imported_at"]
    after[0]["last_imported_at"] = before[0]["last_imported_at"]
    assert after == before
    assert len(rows(path, "job_provenance")) == 1
    assert len(rows(path, "import_runs")) == 2


def test_same_fact_new_file_hash_preserves_multiple_provenances(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    apply(path)
    candidate = record()
    candidate["provenance"][0]["source_file_sha256"] = "c" * 64
    candidate["provenance"][0]["source_file"] = "/evidence/job-copy.html"
    result = apply(path, [candidate], number=2)
    assert result["provenance_added"] == 1 and result["inserted"] == 0
    assert len(rows(path, "job_provenance")) == 2


@pytest.mark.parametrize("changes", [{"description": "不同 JD"}, {"company": "另一家公司"}, {"attendance": "3天/周"}])
def test_same_url_different_fact_never_overwrites(tmp_path, changes):
    path = tmp_path / "evidence.sqlite3"
    apply(path)
    before = store.dataset_snapshot(path)
    with pytest.raises(store.DatasetError, match="不同事实"):
        apply(path, [record(**changes)], number=2)
    assert store.dataset_snapshot(path) == before


def test_different_url_same_title_is_not_duplicate(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    result = apply(path, [record(), record(url=URL.replace("abc123", "other"))])
    assert result["inserted"] == 2 and len(rows(path, "imported_jobs")) == 2


def test_stale_snapshot_rejected_before_writes(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    previous = store.dataset_snapshot(path)["state_sha256"]
    apply(path)
    before = path.read_bytes()
    with pytest.raises(store.DatasetError, match="已变化"):
        store.apply_records(path, [record()], run(2), previous)
    assert path.read_bytes() == before


def test_snapshot_includes_review_provenance_and_run_rows(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    apply(path)
    hashes = [store.dataset_snapshot(path)["state_sha256"]]
    for sql in [
        "UPDATE imported_jobs SET review_status='confirmed'",
        "UPDATE job_provenance SET source_file='/different/location.html'",
        "UPDATE import_runs SET report_path='/different/report.json'",
    ]:
        with sqlite3.connect(path) as connection:
            connection.execute(sql)
        hashes.append(store.dataset_snapshot(path)["state_sha256"])
    assert len(set(hashes)) == 4


@pytest.mark.parametrize("method", ["dict", "model_construct"])
def test_listing_summary_rejected_before_creation(tmp_path, method):
    path = tmp_path / "evidence.sqlite3"
    candidate = record(description_kind="listing_summary")
    if method == "model_construct":
        candidate["job"] = ImportedJob.model_construct(**candidate["job"])
    with pytest.raises(store.DatasetError):
        apply(path, [candidate])
    assert not path.exists()


def test_sql_check_independently_rejects_summary(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    apply(path)
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE imported_jobs SET description_kind='listing_summary'")
    assert rows(path, "imported_jobs")[0]["description_kind"] == "full_jd"


def test_atomic_rollback_on_conflict_after_prior_insert(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    apply(path)
    before = store.dataset_snapshot(path)
    new = record(url=URL.replace("abc123", "new"))
    with pytest.raises(store.DatasetError):
        apply(path, [new, record(company="冲突")], number=2)
    assert store.dataset_snapshot(path) == before
    assert len(rows(path, "import_runs")) == 1


def test_entire_input_validated_before_database_creation(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    with pytest.raises(store.DatasetError):
        apply(path, [record(), record(title="")])
    assert not path.exists()


@pytest.mark.parametrize("kind", ["other", "empty", "legacy", "bad_version"])
def test_unrelated_or_invalid_schema_database_is_rejected_unchanged(tmp_path, kind):
    path = tmp_path / "evidence.sqlite3"
    if kind == "bad_version":
        apply(path)
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA user_version=999")
    else:
        with sqlite3.connect(path) as connection:
            if kind == "other":
                connection.execute("CREATE TABLE other(id INTEGER)")
            elif kind == "legacy":
                connection.execute("CREATE TABLE jobs(id INTEGER)")
    before = path.read_bytes()
    with pytest.raises(store.DatasetError):
        store.dataset_snapshot(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("alias", ["direct", "symlink", "hardlink"])
def test_legacy_path_and_file_aliases_rejected_before_writes(tmp_path, monkeypatch, alias):
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as connection:
        connection.execute("CREATE TABLE jobs(id INTEGER)")
    monkeypatch.setattr(store, "LEGACY_DATABASE", legacy)
    target = legacy
    if alias == "symlink":
        target = tmp_path / "alias.sqlite3"
        target.symlink_to(legacy)
    elif alias == "hardlink":
        target = tmp_path / "alias.sqlite3"
        os.link(legacy, target)
    before = legacy.read_bytes()
    with pytest.raises(store.DatasetError, match="原岗位数据库"):
        apply(target)
    assert legacy.read_bytes() == before


def test_schema_tampering_and_fact_hash_mismatch_rejected(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    apply(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE imported_jobs SET company='改动但未更新指纹'")
    with pytest.raises(store.DatasetError, match="指纹"):
        store.dataset_snapshot(path)


def test_snapshot_detects_extra_schema_object(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    apply(path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE INDEX unexpected ON imported_jobs(title)")
    with pytest.raises(store.DatasetError, match="表结构"):
        store.dataset_snapshot(path)


def test_no_candidates_does_not_create_store(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    with pytest.raises(store.DatasetError):
        apply(path, [])
    assert not path.exists()


@pytest.mark.parametrize("change", [
    {"source_file": "relative.html"}, {"source_file_sha256": "not-a-hash"},
    {"input_format": "pdf"}, {"parser_version": ""},
])
def test_provenance_validation_precedes_database_creation(tmp_path, change):
    path = tmp_path / "evidence.sqlite3"
    candidate = record()
    candidate["provenance"][0].update(change)
    with pytest.raises(store.DatasetError):
        apply(path, [candidate])
    assert not path.exists()


def test_boss_url_aliases_normalize_before_identity_and_fingerprint(tmp_path):
    path = tmp_path / "evidence.sqlite3"
    aliased = record(url="http://zhipin.com/job_detail/abc123.html?from=backup#top")
    assert store.job_fingerprints(aliased["job"]) == store.job_fingerprints(record()["job"])
    apply(path, [aliased])
    result = apply(path, number=2)
    assert result["inserted"] == 0 and rows(path, "imported_jobs")[0]["url"] == URL


def test_second_snapshot_inside_write_lock_detects_racing_review(tmp_path, monkeypatch):
    path = tmp_path / "evidence.sqlite3"
    apply(path)
    original_snapshot = store.dataset_snapshot
    approved = original_snapshot(path)["state_sha256"]

    def racing_snapshot(target):
        result = original_snapshot(target)
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE imported_jobs SET review_status='confirmed'")
        return result

    monkeypatch.setattr(store, "dataset_snapshot", racing_snapshot)
    with pytest.raises(store.DatasetError, match="已变化"):
        store.apply_records(path, [record()], run(2), approved)
    assert len(rows(path, "import_runs")) == 1
    assert rows(path, "imported_jobs")[0]["review_status"] == "confirmed"


def test_sql_failure_rolls_back_new_schema_and_entire_fresh_import(tmp_path, monkeypatch):
    path = tmp_path / "evidence.sqlite3"
    original_connect = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("INSERT INTO job_provenance"):
                raise sqlite3.OperationalError("injected provenance failure")
            return super().execute(sql, parameters)

    def failing_connect(*args, **kwargs):
        return original_connect(*args, **kwargs, factory=FailingConnection)

    monkeypatch.setattr(store.sqlite3, "connect", failing_connect)
    with pytest.raises(store.DatasetError, match="事务已回滚"):
        apply(path)
    with original_connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []
        assert connection.execute("PRAGMA application_id").fetchone()[0] == 0
