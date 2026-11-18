from pathlib import Path

from job_radar.parser import deduplicate_jobs, extract_skills, parse_jobs

FIXTURE = Path(__file__).parents[1] / "sample_data" / "jobs.html"


def test_parse_and_extract_skills():
    jobs = parse_jobs(FIXTURE.read_text(encoding="utf-8"))
    assert len(jobs) == 3
    assert jobs[0]["title"] == "Python 后端开发实习生"
    assert jobs[0]["skills"] == ["Python", "FastAPI", "MySQL", "Git", "Docker"]
    assert jobs[1]["company"] == "字节跳动"
    assert "Tool Calling" in jobs[2]["skills"]


def test_deduplicate_by_url():
    jobs = parse_jobs(FIXTURE.read_text(encoding="utf-8"))
    unique = deduplicate_jobs(jobs)
    assert len(unique) == 2
    assert len({job["url"] for job in unique}) == 2


def test_extract_skills_is_case_insensitive():
    assert extract_skills("python FASTAPI and rag") == ["Python", "FastAPI", "RAG"]
