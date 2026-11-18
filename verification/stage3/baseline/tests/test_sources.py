from pathlib import Path

import pytest

from job_radar.parser import deduplicate_jobs, extract_skills, parse_jobs
from job_radar.sources import SourcePageError, detect_site

FIXTURES = Path(__file__).parents[1] / "sample_data"


def test_real_boss_home_snapshot():
    jobs = deduplicate_jobs(parse_jobs((FIXTURES / "boss-home.html").read_text(encoding="utf-8"),
                                      base_url="https://www.zhipin.com/"))
    assert len(jobs) > 0
    first = jobs[0]
    assert first["title"] == "财富经理"
    assert first["company"] == "新华保险上海分公司"
    assert first["city"] == "上海"
    assert first["url"].startswith("https://www.zhipin.com/job_detail/")
    assert first["experience"] == "1-3年"
    assert first["education"] == "大专"
    assert first["salary"] == ""  # The actual public response has no salary text.
    assert first["source"] == "boss"
    assert first["description_kind"] == "listing_summary"
    assert "crawled_at" in first


def test_real_python_org_snapshot():
    jobs = parse_jobs((FIXTURES / "python-org.html").read_text(encoding="utf-8"),
                      base_url="https://www.python.org/jobs/")
    assert len(jobs) > 0
    assert jobs[0]["title"] == "AI Engineer"
    assert jobs[0]["company"] == "webbyfox consultancy"
    assert jobs[0]["city"] == "London, UK"
    assert jobs[0]["url"] == "https://www.python.org/jobs/8184/"
    assert jobs[0]["posted_at"]
    assert "New" not in jobs[0]["company"]


@pytest.mark.parametrize("filename,site", [
    ("jobs.html", "demo"), ("boss-home.html", "boss"), ("python-org.html", "python-org"),
])
def test_auto_detect_local_snapshots(filename, site):
    html = (FIXTURES / filename).read_text(encoding="utf-8")
    assert detect_site(html, "file:///saved.html") == site
    assert parse_jobs(html, "file:///saved.html")


@pytest.mark.parametrize("html,url", [
    ("<title>安全验证 - BOSS直聘</title>", "https://www.zhipin.com/"),
    ("<title>请验证</title>", "https://www.zhipin.com/web/passport/zp/verify.html"),
])
def test_verification_is_not_empty_success(html, url):
    with pytest.raises(SourcePageError, match="验证页面"):
        parse_jobs(html, url)


def test_search_app_shell_has_actionable_error():
    with pytest.raises(SourcePageError, match="JavaScript"):
        parse_jobs('<title>职位搜索</title><div id="app"></div>',
                   "https://www.zhipin.com/web/geek/job?query=Python")


def test_unknown_markup_is_not_silent_empty_success():
    with pytest.raises(SourcePageError, match="未识别"):
        parse_jobs("<h1>Not a job list</h1>", "https://example.org/")


@pytest.mark.parametrize("site", ["python-org", "boss"])
def test_explicit_site_with_no_cards_errors(site):
    with pytest.raises(SourcePageError):
        parse_jobs("<html></html>", "https://example.org/", site=site)


@pytest.mark.parametrize("href", [
    "javascript:alert(1)", "https://example.net/job_detail/123.html", "/gongsi/123.html",
])
def test_boss_invalid_links_are_rejected(href):
    html = f'''<div class="sub-li"><a class="job-info" href="{href}">
        <p class="name">Python</p></a></div>'''
    with pytest.raises(SourcePageError, match="有效岗位"):
        parse_jobs(html, "https://www.zhipin.com/", site="boss")


def test_boss_tracking_params_removed_and_deduplicated():
    card = '''<div class="sub-li"><a class="job-info" href="/job_detail/abc.html?x={}">
        <p class="name">Python</p><p class="job-text"><span>沈阳</span></p>
        </a><div class="sub-li-bottom"><a class="user-info"><span class="name">示例</span>
        </a></div></div>'''
    jobs = parse_jobs(card.format(1) + card.format(2), "https://www.zhipin.com/")
    assert len(jobs) == 2
    assert len(deduplicate_jobs(jobs)) == 1
    assert jobs[0]["url"] == "https://www.zhipin.com/job_detail/abc.html"
    assert jobs[0]["skills"] == ["Python"]
    assert jobs[0]["education"] == ""


def test_mysql_does_not_imply_sql():
    assert extract_skills("MySQL") == ["MySQL"]
