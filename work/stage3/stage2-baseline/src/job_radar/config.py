"""Load and validate saved filter configurations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FILTER_KEYS = {
    "keyword",
    "title",
    "city",
    "company",
    "skill",
    "exclude_keyword",
    "remote_only",
}
LIST_KEYS = {"title", "city", "company", "skill", "exclude_keyword"}


def _list_value(key: str, value: Any) -> list[str]:
    """Accept a string or a list so a beginner can write either form."""
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"配置项 {key} 必须是字符串或字符串数组。")
    return value


def load_filter_config(path: str | None) -> dict[str, Any]:
    """Read a JSON object containing only filter settings."""
    if not path:
        return {}
    config_path = Path(path).expanduser()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件不是有效 JSON：{exc.msg}（第 {exc.lineno} 行）") from exc
    except OSError as exc:
        raise ValueError(f"无法读取配置文件：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("配置文件顶层必须是 JSON 对象，例如 {\"city\": [\"深圳\"]}。")

    unknown = sorted(set(data) - FILTER_KEYS)
    if unknown:
        raise ValueError(f"配置文件包含未知字段：{', '.join(unknown)}")
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key in LIST_KEYS:
            result[key] = _list_value(key, value)
        elif key == "keyword":
            if not isinstance(value, str):
                raise ValueError("配置项 keyword 必须是字符串。")
            result[key] = value
        elif key == "remote_only":
            if not isinstance(value, bool):
                raise ValueError("配置项 remote_only 必须是 true 或 false。")
            result[key] = value
    return result
