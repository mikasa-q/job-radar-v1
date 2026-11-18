from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from .filters import filter_jobs
from .parser import deduplicate_jobs, parse_jobs


def fetch_html(source: str, timeout: float = 15) -> tuple[str, str]:
    """Read a local HTML fixture or fetch an HTTP(S) page with requests."""
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError("HTTP mode needs requests; run pip install -r requirements.txt") from exc
        try:
            response = requests.get(source, timeout=timeout, headers={
                "User-Agent": "JobRadar/0.2 (single-page learning project)",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"HTTP 请求失败：{exc}") from exc
        content_type = response.headers.get("Content-Type", "").lower()
        if content_type and "html" not in content_type:
            raise ValueError(f"预期 HTML，实际 Content-Type 为 {content_type}")
        if not response.encoding or response.encoding.upper() == "ISO-8859-1":
            response.encoding = "utf-8"
        return response.text, response.url
    if parsed.scheme:
        raise ValueError("来源应为本地文件路径或 HTTP(S) 网页地址。")
    path = Path(source).expanduser().resolve()
    return path.read_text(encoding="utf-8"), path.as_uri()


def crawl(
    source: str, *, site: str = "auto", timeout: float = 15,
    save_html: str | None = None, base_url: str | None = None,
    keyword: str | None = None, title: list[str] | None = None,
    city: list[str] | None = None, company: list[str] | None = None,
    skill: list[str] | None = None, exclude_keyword: list[str] | None = None,
    remote_only: bool = False,
) -> list[dict]:
    if timeout <= 0:
        raise ValueError("timeout 必须大于 0。")
    html, fetched_url = fetch_html(source, timeout=timeout)
    if save_html:
        snapshot = Path(save_html)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(html, encoding="utf-8")
    jobs = deduplicate_jobs(parse_jobs(html, base_url=base_url or fetched_url, site=site))
    return filter_jobs(
        jobs, keyword=keyword, title=title, city=city, company=company,
        skill=skill, exclude_keyword=exclude_keyword, remote_only=remote_only,
    )
