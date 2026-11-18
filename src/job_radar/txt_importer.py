"""Deterministic jobradar-txt-v1 parsing; no files, clocks, or external calls."""
import re

from pydantic import ValidationError

from .import_models import ImportedJob
from .importer import ImportParseError, _boss_url
from .parser import extract_skills


_VERSION = "jobradar-txt-v1"
_FIELDS = {
    "公司": "company",
    "城市": "city",
    "薪资": "salary",
    "学历": "education",
    "经验": "experience",
    "实习安排": "attendance",
}
_HEADER_KEYS = {"格式版本", "来源", "岗位名称", "岗位链接", *_FIELDS}
_TEMPLATE_HINT = (
    "TXT 需要 jobradar-txt-v1 模板头部；请补齐格式版本、来源、"
    "岗位名称、岗位链接及独占一行的正文: 标记。"
)


def parse_job_txt(text: str) -> ImportedJob:
    """Parse one labelled job document; unlabelled JD backups are rejected.

    Split only the first colon in a header line, so URL colons survive. After
    the first standalone body marker, every remaining line is JD evidence,
    including lines that look like metadata or another body marker.
    """
    # Also accept BOM/newlines for callers that already loaded the string.
    lines = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    headers: dict[str, str] = {}
    description: str | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in {"正文:", "正文："}:
            description = "\n".join(lines[index + 1:]).strip()
            break

        pair = re.split(r"[:：]", stripped, maxsplit=1)
        if len(pair) != 2:
            raise ImportParseError(f"TXT 第 {index + 1} 行不是字段: 值。{_TEMPLATE_HINT}")
        key, value = (part.strip() for part in pair)
        if not headers and key != "格式版本":
            raise ImportParseError(_TEMPLATE_HINT)
        if key == "正文":
            raise ImportParseError("正文: 标记必须独占一行，正文写在下一行。")
        if key not in _HEADER_KEYS:
            raise ImportParseError(f"未知 TXT 头部字段：{key}。")
        if key in headers:
            raise ImportParseError(f"重复 TXT 头部字段：{key}。")
        headers[key] = value

    if headers.get("格式版本") != _VERSION:
        raise ImportParseError("格式版本必须为 jobradar-txt-v1。" + _TEMPLATE_HINT)
    for key in ("来源", "岗位名称", "岗位链接"):
        if not headers.get(key):
            raise ImportParseError(f"TXT 缺少必需字段或字段为空：{key}。")
    if description is None:
        raise ImportParseError("TXT 缺少独占一行的正文: 标记。")
    if not description:
        raise ImportParseError("TXT 正文不能为空。")

    source = headers["来源"]
    if source not in {"boss", "manual"}:
        raise ImportParseError("TXT 来源只接受 boss 或 manual。")
    # Reuse the unchanged HTML parser's identity rules, including canonical
    # host spelling and removal of tracking parameters for BOSS documents.
    url = _boss_url(headers["岗位链接"]) if source == "boss" else headers["岗位链接"]
    fields = {field: headers.get(key, "") for key, field in _FIELDS.items()}
    warnings = [f"missing_{field}" for field, value in fields.items() if not value]
    try:
        return ImportedJob(
            source=source, title=headers["岗位名称"], url=url,
            description=description, **fields, warnings=warnings,
            skills=extract_skills(headers["岗位名称"] + " " + description),
        )
    except ValidationError as exc:
        raise ImportParseError("TXT 字段校验失败：" + exc.errors()[0]["msg"]) from exc

