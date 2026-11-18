"""Behavior tests against a trimmed, real-layout detail fixture."""
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from pydantic import ValidationError

from job_radar.import_models import ImportedJob
from job_radar.importer import ImportParseError, parse_boss_detail_html


FIXTURE = Path(__file__).parent / "fixtures" / "boss-detail.html"
URL = "https://www.zhipin.com/job_detail/89cdce766fdcb2020nF82N64F1JR.html"


@pytest.fixture
def html():
    return FIXTURE.read_text(encoding="utf-8")


def test_real_layout_fields_and_jd_boundary(html):
    job = parse_boss_detail_html(html)
    assert (job.title, job.company, job.city) == ("AI Agent 开发实习生", "ONES", "深圳")
    assert (job.salary, job.education) == ("180-200元/天", "本科")
    assert job.url == URL and job.description_kind == "full_jd"
    assert job.attendance == "4天/周 4个月" and job.experience == ""
    assert job.posted_at == "" and job.warnings == []
    assert len(job.description) == 790 and len(job.description.splitlines()) == 20
    for heading in ["岗位介绍", "岗位职责", "任职要求", "你将获得"]:
        assert heading in job.description.splitlines()
    for decoy in ["成立于 2015", "后端开发-SZ", "招聘者信息", "不要解析脚本"]:
        assert decoy not in job.description
    assert job.description.endswith("4. 表现优秀者有转正机会。")
    assert "落地⽀持" in job.description  # Keep original Unicode evidence.


def test_page_tags_are_not_promoted_to_jd_skill_mentions(html):
    job = parse_boss_detail_html(html)
    assert "Redis" in job.source_tags and "Redis" not in job.skills
    assert job.skills == ["Python", "Git", "SQL"]
    assert "Java、C++、TypeScript、JavaScript 或 Python" in job.description


@pytest.mark.parametrize("selector,warning", [
    (".sider-company", "missing_company"),
    (".text-city", "missing_city"),
    (".salary", "missing_salary"),
    (".text-degree", "missing_education"),
    (".text-experiece", "missing_experience_or_attendance"),
])
def test_optional_missing_fields_return_warnings(html, selector, warning):
    soup = BeautifulSoup(html, "html.parser")
    soup.select_one(selector).decompose()
    job = parse_boss_detail_html(str(soup))
    assert warning in job.warnings
    assert job.title and job.description


def test_experience_years_are_not_attendance(html):
    job = parse_boss_detail_html(html.replace("4天/周 4个月", "1-3年"))
    assert job.experience == "1-3年" and job.attendance == ""


@pytest.mark.parametrize("selector", [
    ".job-banner .info-primary .name h1",
    ".job-detail > .job-detail-section > .job-sec-text",
])
@pytest.mark.parametrize("action", ["remove", "empty", "duplicate"])
def test_missing_empty_or_ambiguous_required_fields_fail(html, selector, action):
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one(selector)
    if action == "remove":
        node.decompose()
    elif action == "empty":
        node.clear()
        node.append("  \n ")
    else:
        node.insert_after(BeautifulSoup(str(node), "html.parser").find())
    with pytest.raises(ImportParseError):
        parse_boss_detail_html(str(soup))


def test_conflicting_companies_fail_instead_of_guessing(html):
    soup = BeautifulSoup(html, "html.parser")
    extra = soup.new_tag("a")
    extra.string = "另一家公司"
    soup.select_one(".sider-company .company-info").append(extra)
    with pytest.raises(ImportParseError, match="company"):
        parse_boss_detail_html(str(soup))


@pytest.mark.parametrize("bad_url", [
    "file:///tmp/job.html",
    "javascript:alert(1)",
    "https://www.zhipin.com.evil.test/job_detail/abc.html",
    "https://www.zhipin.com/gongsi/abc.html",
    "https://user:pass@www.zhipin.com/job_detail/abc.html",
    "https://www.zhipin.com:8443/job_detail/abc.html",
    "https://www.zhipin.com/job_detail/a b.html",
])
def test_invalid_identity_rejected(html, bad_url):
    soup = BeautifulSoup(html, "html.parser")
    soup.select_one('link[rel="canonical"]')["href"] = bad_url
    with pytest.raises(ImportParseError):
        parse_boss_detail_html(str(soup))


def test_missing_canonical_needs_explicit_url(html):
    soup = BeautifulSoup(html, "html.parser")
    soup.select_one('link[rel="canonical"]').decompose()
    with pytest.raises(ImportParseError, match="source_url"):
        parse_boss_detail_html(str(soup))
    assert parse_boss_detail_html(str(soup), source_url=URL).url == URL


def test_tracking_and_host_spelling_normalize_before_comparison(html):
    job = parse_boss_detail_html(
        html, source_url=URL.replace("https://www.", "http://") + "?from=saved#top",
    )
    assert job.url == URL


def test_conflicting_or_duplicate_canonical_fails(html):
    with pytest.raises(ImportParseError, match="不同岗位"):
        parse_boss_detail_html(html, source_url="https://www.zhipin.com/job_detail/other.html")
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one('link[rel="canonical"]')
    node.insert_after(BeautifulSoup(str(node), "html.parser").find())
    with pytest.raises(ImportParseError, match="多个候选"):
        parse_boss_detail_html(str(soup))


@pytest.mark.parametrize("bad_html", [
    '<title>安全验证 - BOSS直聘</title>',
    '<article class="job-card"><h2>列表页</h2></article>',
    '<html><div id="app"></div></html>',
    "",
])
def test_non_detail_pages_fail(bad_html):
    with pytest.raises(ImportParseError):
        parse_boss_detail_html(bad_html)


def test_jd_without_standard_heading_still_parses(html):
    job = parse_boss_detail_html(html.replace("岗位职责", "工作内容"))
    assert "工作内容" in job.description


def test_parser_has_no_io_and_is_deterministic(html, monkeypatch):
    import builtins
    import sqlite3
    import requests

    def unexpected(*args, **kwargs):
        raise AssertionError("纯解析不能执行 I/O")

    monkeypatch.setattr(builtins, "open", unexpected)
    monkeypatch.setattr(sqlite3, "connect", unexpected)
    monkeypatch.setattr(requests, "get", unexpected)
    assert parse_boss_detail_html(html) == parse_boss_detail_html(html)


@pytest.mark.parametrize("changes", [
    {"title": "   "}, {"description": "\n "},
    {"url": "file:///tmp/job.html"}, {"source": " "},
    {"description_kind": "listing_summary"},
    {"required_skills": ["Python"]}, {"skills": ["Python", 123]},
])
def test_contract_rejects_invalid_or_invented_fields(changes):
    values = {"title": "岗位", "url": URL, "description": "职位正文", **changes}
    with pytest.raises(ValidationError):
        ImportedJob(**values)


def test_contract_list_defaults_are_independent():
    first = ImportedJob(title="岗位", url=URL, description="正文")
    second = ImportedJob(title="岗位", url=URL, description="正文")
    first.warnings.append("example")
    assert second.warnings == []

