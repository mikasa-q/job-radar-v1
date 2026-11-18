import json

from job_radar.storage import count_jobs, list_jobs, upsert_jobs


def jobs():
    return [
        {
            "source": "demo", "title": "Python 后端", "company": "甲公司",
            "city": "深圳", "url": "https://example.com/1", "description": "FastAPI",
            "skills": ["Python", "FastAPI"], "crawled_at": "2026-10-28T00:00:00Z",
        },
        {
            "source": "demo", "title": "前端开发", "company": "乙公司",
            "city": "北京", "url": "https://example.com/2", "description": "React",
            "skills": [], "crawled_at": "2026-10-28T00:00:00Z",
        },
    ]


def test_upsert_and_query_jobs(tmp_path):
    path = tmp_path / "jobs.db"
    assert upsert_jobs(path, jobs()) == 2
    assert count_jobs(path) == 2
    assert len(list_jobs(path, city="深圳")) == 1
    assert list_jobs(path, keyword="FastAPI")[0]["skills"] == ["Python", "FastAPI"]


def test_upsert_same_url_updates_without_duplicate(tmp_path):
    path = tmp_path / "jobs.db"
    first = jobs()[0]
    second = {**first, "title": "更新后的 Python 后端"}
    assert upsert_jobs(path, [first, second]) == 2
    result = list_jobs(path)
    assert count_jobs(path) == 1
    assert result[0]["title"] == "更新后的 Python 后端"
