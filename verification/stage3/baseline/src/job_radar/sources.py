"""Adapters for the HTML actually returned by public job-listing pages."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup


class SourcePageError(ValueError):
    """The response is a verification page, an app shell, or unknown markup."""


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def detect_site(html: str, base_url: str) -> str:
    host = (urlsplit(base_url).hostname or "").lower()
    if _host_matches(host, "zhipin.com"):
        return "boss"
    if _host_matches(host, "python.org"):
        return "python-org"
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one("article.job-card"):
        return "demo"
    if soup.select_one(".sub-li a.job-info[href*='/job_detail/']"):
        return "boss"
    if soup.select_one(".list-recent-jobs .listing-company-name"):
        return "python-org"
    raise SourcePageError("未识别岗位页面结构，请使用已支持的页面或明确指定 --site。")


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node is not None else ""


def _job_url(href: str, base_url: str, domain: str, pattern: str) -> str:
    url = urlsplit(urljoin(base_url, href))
    if url.scheme not in {"http", "https"}:
        return ""
    if not _host_matches((url.hostname or "").lower(), domain):
        return ""
    if not re.fullmatch(pattern, url.path):
        return ""
    # Listing tracking parameters are not part of a job's identity.
    return urlunsplit((url.scheme, url.netloc, url.path, "", ""))


def parse_public_jobs(html: str, base_url: str, site: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    if site == "boss":
        title = _text(soup.title)
        if "安全验证" in title or "/passport/" in urlsplit(base_url).path:
            raise SourcePageError("Boss 返回验证页面；本次停止，原 JSON 保持不变。")
        if not soup.select_one(".sub-li a.job-info[href]"):
            raise SourcePageError(
                "Boss 页面未包含支持的首页岗位卡片；搜索页可能只有 JavaScript 外壳。"
                "请使用公开首页或保存后的首页 HTML。"
            )
        domain = "zhipin.com"
        if not _host_matches((urlsplit(base_url).hostname or "").lower(), domain):
            base_url = "https://www.zhipin.com/"
        return _parse_boss(soup, base_url)
    if site == "python-org":
        if not _host_matches((urlsplit(base_url).hostname or "").lower(), "python.org"):
            base_url = "https://www.python.org/jobs/"
        jobs = _parse_python_org(soup, base_url)
        if not jobs:
            raise SourcePageError("Python.org 页面未包含有效岗位，请检查页面地址或结构。")
        return jobs
    raise SourcePageError(f"未知来源类型：{site}")


def _parse_boss(soup: BeautifulSoup, base_url: str) -> list[dict]:
    jobs = []
    for card in soup.select(".sub-li"):
        link = card.select_one("a.job-info[href]")
        company = card.select_one(".sub-li-bottom .user-info .name")
        if link is None:
            continue
        title = _text(link.select_one(".name"))
        url = _job_url(link.get("href", ""), base_url, "zhipin.com", r"/job_detail/[^/]+\.html")
        if not title or not url:
            continue
        tags = [_text(tag) for tag in link.select(".job-text > span")]
        jobs.append({
            "source": "boss", "title": title, "company": _text(company),
            "city": tags[0] if tags else "", "url": url,
            "description": " / ".join(tags), "description_kind": "listing_summary",
            "salary": _text(link.select_one(".salary")),
            "experience": tags[1] if len(tags) > 1 else "",
            "education": tags[2] if len(tags) > 2 else "",
        })
    if not jobs:
        raise SourcePageError("Boss 卡片缺少有效岗位标题或链接；原 JSON 保持不变。")
    return jobs


def _parse_python_org(soup: BeautifulSoup, base_url: str) -> list[dict]:
    jobs = []
    for card in soup.select(".list-recent-jobs > li"):
        name = card.select_one(".listing-company-name")
        link = name.select_one("a[href]") if name is not None else None
        if link is None:
            continue
        url = _job_url(link.get("href", ""), base_url, "python.org", r"/jobs/\d+/")
        title = _text(link)
        if not title or not url:
            continue
        # The company is the direct text after <a> and <br>, not the title or New badge.
        company = " ".join(" ".join(name.find_all(string=True, recursive=False)).split())
        posted = card.select_one(".listing-posted time")
        jobs.append({
            "source": "python.org", "title": title, "company": company,
            "city": _text(card.select_one(".listing-location")), "url": url,
            "description": " / ".join(filter(None, [
                _text(card.select_one(".listing-job-type")),
                _text(card.select_one(".listing-company-category")),
            ])),
            "description_kind": "listing_summary",
            "posted_at": posted.get("datetime", "") if posted else "",
        })
    return jobs
