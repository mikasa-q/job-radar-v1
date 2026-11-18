"""Pure parsing of the observed BOSS saved-detail HTML layout."""
import re
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .import_models import ImportedJob
from .parser import extract_skills


class ImportParseError(ValueError):
    """The HTML has missing, conflicting, or unsupported detail content."""


def _single_node(parent, selector: str, field: str):
    nodes = parent.select(selector)
    if len(nodes) != 1:
        raise ImportParseError(f"{field}需要唯一节点，实际找到 {len(nodes)} 个。")
    return nodes[0]


def _inline_text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split())


def _optional_text(soup, selector: str, field: str, warnings: list[str]) -> str:
    values = list(dict.fromkeys(
        value for node in soup.select(selector) if (value := _inline_text(node))
    ))
    if len(values) > 1:
        raise ImportParseError(f"{field}存在多个不同值。")
    if not values:
        warnings.append(f"missing_{field}")
        return ""
    return values[0]


def _boss_url(value: str) -> str:
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError as exc:
        raise ImportParseError("岗位 URL 格式无效。") from exc
    if (parts.scheme not in {"http", "https"}
            or parts.hostname not in {"zhipin.com", "www.zhipin.com"}
            or parts.username is not None or parts.password is not None
            or port is not None
            or any(char.isspace() for char in value)
            or not re.fullmatch(r"/job_detail/[A-Za-z0-9_~\-]+\.html", parts.path)):
        raise ImportParseError("需要有效的 Boss 岗位详情 URL。")
    # Both accepted host spellings identify the same BOSS detail document.
    return urlunsplit(("https", "www.zhipin.com", parts.path, "", ""))


def _resolve_url(soup, source_url: str | None) -> str:
    nodes = soup.select('link[rel="canonical"]')
    if len(nodes) > 1:
        raise ImportParseError("canonical URL 存在多个候选。")
    canonical = _boss_url(nodes[0].get("href", "")) if nodes else None
    explicit = _boss_url(source_url) if source_url is not None else None
    if canonical and explicit and canonical != explicit:
        raise ImportParseError("canonical 与 source_url 指向不同岗位。")
    if not canonical and not explicit:
        raise ImportParseError("缺少 canonical；请显式提供 source_url。")
    return canonical or explicit


def parse_boss_detail_html(
    html: str, *, source_url: str | None = None,
) -> ImportedJob:
    """Return one detail job without I/O, clocks, or database operations.

    Optional missing fields produce warning codes; missing or ambiguous identity,
    title and main description raise ImportParseError. Only the observed layout
    is supported. Page tags remain separate from skills mentioned in the JD.
    """
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and "安全验证" in soup.title.get_text():
        raise ImportParseError("输入是验证页，不是岗位详情页。")
    for node in soup.select("script, style, template, noscript"):
        node.decompose()

    title = _inline_text(_single_node(
        soup, ".job-banner .info-primary .name h1", "title",
    ))
    body = _single_node(
        soup, ".job-detail > .job-detail-section > .job-sec-text", "description",
    )
    # Preserve br/paragraph boundaries and Unicode spelling as evidence.
    description = "\n".join(
        line.strip() for line in body.get_text("\n", strip=True).splitlines()
        if line.strip()
    )
    if not title or not description:
        raise ImportParseError("标题和职位正文都必须包含有效文字。")
    url = _resolve_url(soup, source_url)

    warnings: list[str] = []
    primary = ".job-banner .info-primary "
    fields = {
        field: _optional_text(soup, selector, field, warnings)
        for field, selector in {
            "company": ".sider-company .company-info a",
            "city": primary + ".text-city",
            "salary": primary + ".salary",
            "education": primary + ".text-degree",
        }.items()
    }
    raw_experience = _optional_text(
        soup, primary + ".text-experiece", "experience_or_attendance", warnings,
    )
    is_attendance = bool(re.search(r"天\s*/\s*周|个?月", raw_experience))
    tags = list(dict.fromkeys(
        value for node in body.parent.select(".job-keyword-list li")
        if (value := _inline_text(node))
    ))
    return ImportedJob(
        title=title, url=url, description=description, **fields,
        attendance=raw_experience if is_attendance else "",
        experience="" if is_attendance else raw_experience,
        source_tags=tags,
        skills=extract_skills(title + " " + description),
        warnings=warnings,
    )

