from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from .parser import deduplicate_jobs, parse_jobs


def fetch_html(source: str, timeout: int = 10) -> tuple[str, str]:
    """Read a local HTML fixture or fetch an HTTP(S) page with requests."""
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError("HTTP mode needs requests; run pip install -r requirements.txt") from exc
        response = requests.get(source, timeout=timeout)
        response.raise_for_status()
        return response.text, source
    path = Path(source).expanduser().resolve()
    return path.read_text(encoding="utf-8"), path.as_uri()


def crawl(source: str) -> list[dict]:
    html, base_url = fetch_html(source)
    return deduplicate_jobs(parse_jobs(html, base_url=base_url))
