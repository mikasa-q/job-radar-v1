"""Small, reusable filters for the normalized job dictionaries."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _contains(value: Any, needle: str) -> bool:
    return needle.casefold() in str(value or "").casefold()


def _any_contains(value: Any, needles: Iterable[str]) -> bool:
    return any(_contains(value, needle) for needle in needles)


def filter_jobs(
    jobs: list[dict[str, Any]],
    *,
    keyword: str | None = None,
    title: list[str] | None = None,
    city: list[str] | None = None,
    company: list[str] | None = None,
    skill: list[str] | None = None,
    exclude_keyword: list[str] | None = None,
    remote_only: bool = False,
) -> list[dict[str, Any]]:
    """Keep jobs matching all supplied filter categories.

    Values repeated within one option are alternatives (OR). Different
    options are combined (AND), which makes commands such as
    ``--title Python --city Remote --skill FastAPI`` predictable.
    """
    titles = title or []
    cities = city or []
    companies = company or []
    skills = skill or []
    excluded = exclude_keyword or []
    result: list[dict[str, Any]] = []
    for job in jobs:
        searchable = " ".join((job.get("title", ""), job.get("description", "")))
        if keyword and not _contains(searchable, keyword):
            continue
        if titles and not _any_contains(job.get("title"), titles):
            continue
        if cities and not _any_contains(job.get("city"), cities):
            continue
        if companies and not _any_contains(job.get("company"), companies):
            continue
        if skills and not any(
            _any_contains(candidate, skills) for candidate in job.get("skills", [])
        ):
            continue
        if excluded and _any_contains(searchable, excluded):
            continue
        if remote_only and not _contains(job.get("city"), "remote"):
            continue
        result.append(job)
    return result
