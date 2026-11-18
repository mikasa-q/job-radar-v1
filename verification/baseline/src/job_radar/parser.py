from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

SKILLS = (
    "Python", "FastAPI", "MySQL", "Redis", "Git", "Linux", "Docker",
    "pytest", "LLM", "Tool Calling", "RAG", "LangGraph", "MCP", "SQL",
)


def _clean(value: str) -> str:
    return " ".join(value.split())


def extract_skills(text: str) -> list[str]:
    """Return known skills in stable, configured order."""
    normalized = text.casefold()
    found = []
    for skill in SKILLS:
        pattern = r"(?<![a-z0-9_])" + re.escape(skill.casefold()) + r"(?![a-z0-9_])"
        if re.search(pattern, normalized):
            found.append(skill)
    return found


class _JobHTMLParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.jobs: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._field: str | None = None
        self._in_card = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "article" and "job-card" in classes:
            self._current = {"source": attributes.get("data-source", "unknown")}
            self._in_card = True
        if not self._in_card or self._current is None:
            return
        mapping = {
            "job-title": "title", "company": "company", "location": "city",
            "description": "description",
        }
        for class_name, field in mapping.items():
            if class_name in classes:
                self._field = field
                return
        if tag == "a" and "job-link" in classes:
            href = attributes.get("href", "")
            self._current["url"] = urljoin(self.base_url, href)

    def handle_data(self, data: str) -> None:
        if self._in_card and self._current is not None and self._field:
            self._current[self._field] = _clean(self._current.get(self._field, "") + " " + data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h2", "p"}:
            self._field = None
        if tag == "article" and self._in_card and self._current is not None:
            description = self._current.get("description", "")
            self._current["skills"] = extract_skills(
                " ".join([self._current.get("title", ""), description])
            )
            self._current["crawled_at"] = datetime.now(timezone.utc).isoformat()
            self.jobs.append(self._current)
            self._current = None
            self._in_card = False
            self._field = None


def parse_jobs(html: str, base_url: str = "https://example.com/") -> list[dict[str, Any]]:
    parser = _JobHTMLParser(base_url)
    parser.feed(html)
    return parser.jobs


def deduplicate_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate by canonical URL while preserving first-seen order."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for job in jobs:
        url = job.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(job)
    return unique
