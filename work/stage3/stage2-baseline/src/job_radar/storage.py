"""SQLite storage for normalized jobs."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    description_kind TEXT NOT NULL DEFAULT '',
    salary TEXT NOT NULL DEFAULT '',
    experience TEXT NOT NULL DEFAULT '',
    education TEXT NOT NULL DEFAULT '',
    skills_json TEXT NOT NULL DEFAULT '[]',
    posted_at TEXT NOT NULL DEFAULT '',
    crawled_at TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL DEFAULT '',
    last_seen_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_city ON jobs(city);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen_at ON jobs(last_seen_at);
"""

JOB_COLUMNS = (
    "source", "title", "company", "city", "url", "description",
    "description_kind", "salary", "experience", "education", "skills_json",
    "posted_at", "crawled_at", "first_seen_at", "last_seen_at",
)


def _connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(path: str | Path) -> None:
    with _connect(path) as connection:
        connection.executescript(SCHEMA)


def upsert_jobs(path: str | Path, jobs: Iterable[dict[str, Any]]) -> int:
    """Insert new URLs and update existing URLs; return processed count."""
    jobs = list(jobs)
    if not jobs:
        init_db(path)
        return 0
    with _connect(path) as connection:
        connection.executescript(SCHEMA)
        for job in jobs:
            crawled_at = str(job.get("crawled_at", ""))
            values = {
                "source": str(job.get("source", "")),
                "title": str(job.get("title", "")),
                "company": str(job.get("company", "")),
                "city": str(job.get("city", "")),
                "url": str(job.get("url", "")),
                "description": str(job.get("description", "")),
                "description_kind": str(job.get("description_kind", "")),
                "salary": str(job.get("salary", "")),
                "experience": str(job.get("experience", "")),
                "education": str(job.get("education", "")),
                "skills_json": json.dumps(job.get("skills", []), ensure_ascii=False),
                "posted_at": str(job.get("posted_at", "")),
                "crawled_at": crawled_at,
                "first_seen_at": crawled_at,
                "last_seen_at": crawled_at,
            }
            columns = ", ".join(JOB_COLUMNS)
            placeholders = ", ".join(f":{column}" for column in JOB_COLUMNS)
            updates = ", ".join(
                f"{column}=excluded.{column}"
                for column in JOB_COLUMNS if column not in {"url", "first_seen_at"}
            )
            connection.execute(
                f"INSERT INTO jobs ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(url) DO UPDATE SET {updates}",
                values,
            )
    return len(jobs)


def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    job = dict(row)
    job.pop("id", None)
    job["skills"] = json.loads(job.pop("skills_json") or "[]")
    return job


def list_jobs(
    path: str | Path, *, city: str | None = None, keyword: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return stored jobs with simple SQL filters."""
    if limit is not None and limit <= 0:
        raise ValueError("limit 必须大于 0。")
    clauses: list[str] = []
    params: list[Any] = []
    if city:
        clauses.append("city LIKE ?")
        params.append(f"%{city}%")
    if keyword:
        clauses.append("(title LIKE ? OR description LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    sql = "SELECT * FROM jobs"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with _connect(path) as connection:
        return [_row_to_job(row) for row in connection.execute(sql, params)]


def count_jobs(path: str | Path) -> int:
    with _connect(path) as connection:
        connection.executescript(SCHEMA)
        return int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
