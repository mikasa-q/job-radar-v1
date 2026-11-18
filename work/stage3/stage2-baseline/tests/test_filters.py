from pathlib import Path

from job_radar.crawler import crawl
from job_radar.filters import filter_jobs


FIXTURE = Path(__file__).parents[1] / "sample_data" / "jobs.html"


def test_custom_filters_combine_categories_with_and():
    jobs = crawl(str(FIXTURE))
    result = filter_jobs(jobs, title=["Python"], city=["北京"], skill=["FastAPI"])
    assert [job["company"] for job in result] == ["星河智能科技"]


def test_repeated_values_are_or_and_exclude_is_applied():
    jobs = crawl(str(FIXTURE))
    result = filter_jobs(jobs, company=["星河", "远帆"], exclude_keyword=["Agent"])
    assert [job["company"] for job in result] == ["星河智能科技"]


def test_remote_only_and_case_insensitive_skill():
    jobs = [
        {"title": "Python API", "city": "Remote", "skills": ["Python"], "description": ""},
        {"title": "Python API", "city": "北京", "skills": ["Python"], "description": ""},
    ]
    assert len(filter_jobs(jobs, skill=["python"], remote_only=True)) == 1
