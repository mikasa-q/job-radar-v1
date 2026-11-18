"""Validated import records; source tags and skill mentions are not requirements."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ImportedJob(BaseModel):
    """One detail document, before file provenance or database persistence."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source: str = Field(default="boss", min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    description: str = Field(min_length=1)
    description_kind: Literal["full_jd"] = "full_jd"
    company: str = ""
    city: str = ""
    salary: str = ""
    education: str = ""
    experience: str = ""
    attendance: str = ""
    posted_at: str = ""
    source_tags: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator(
        "source", "title", "url", "description", "company", "city",
        "salary", "education", "experience", "attendance", "posted_at",
    )
    @classmethod
    def strip_edges(cls, value: str) -> str:
        return value.strip()

    @field_validator("source", "title", "description")
    @classmethod
    def require_content(cls, value: str) -> str:
        if not value:
            raise ValueError("字段不能只有空白。")
        return value

    @field_validator("url")
    @classmethod
    def require_web_url(cls, value: str) -> str:
        try:
            parts = urlsplit(value)
            port = parts.port
        except ValueError as exc:
            raise ValueError("岗位 URL 格式无效。") from exc
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or any(char.isspace() for char in value)):
            raise ValueError("岗位 URL 必须是无账户信息的 HTTP(S) 地址。")
        return value

