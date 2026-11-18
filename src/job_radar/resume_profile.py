"""Stage 1B contracts. Facts carry their own verbatim block references.

Constructing a model validates structure, not its relationship to a document.
Use validate_profile_evidence from resume_profile_rules at the trust boundary.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)

    @field_validator("*", mode="after")
    @classmethod
    def nonblank_strings(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("字符串不能为空白；未知字段请用 null。")
        return value


class EvidenceRef(ProfileModel):
    page: int = Field(ge=1)
    block_id: str = Field(pattern=r"^p[0-9]{3,}-b[0-9]{3,}$")
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def page_matches_id(self):
        if int(self.block_id.split("-", 1)[0][1:]) != self.page:
            raise ValueError("引用页码与 block_id 不一致。")
        return self


class TextFact(ProfileModel):
    """Value is extracted text, not a generated paraphrase or a capability score."""

    value: str = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(min_length=1)

    @model_validator(mode="after")
    def value_is_quoted(self):
        if not any(self.value in ref.quote for ref in self.evidence):
            raise ValueError("事实值必须逐字出现在至少一条引文中。")
        return self


_DATE = re.compile(
    r"(?P<year>[0-9]{4})(?:[./-](?P<month>[0-9]{1,2})|年(?:(?P<cjk_month>[0-9]{1,2})月)?)?"
)


def _explicit_date(value: str) -> str | None:
    match = _DATE.fullmatch(value.strip())
    if match is None:
        return None
    year = match.group("year")
    month = match.group("month") or match.group("cjk_month")
    if int(year) < 1 or (month is not None and not 1 <= int(month) <= 12):
        raise ValueError("日期中的年或月无效。")
    return year if month is None else f"{year}-{int(month):02d}"


class DateFact(TextFact):
    """Raw date plus optional explicit year/month normalization; no date guessing."""

    normalized: str | None = Field(default=None, pattern=r"^[0-9]{4}(?:-(?:0[1-9]|1[0-2]))?$")

    @model_validator(mode="after")
    def explicit_date_only(self):
        expected = _explicit_date(self.value)
        if self.normalized is not None and self.normalized != expected:
            raise ValueError("日期规范值必须与原文明确给出的年月一致。")
        return self


class ProfileSource(ProfileModel):
    document_schema_version: Literal["resume-document-v1"] = "resume-document-v1"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_version: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)


class SkillEvidence(ProfileModel):
    """A positive mention, not proof of proficiency or years of experience."""

    name: str = Field(min_length=1)
    mention: TextFact
    context: Literal["project", "course", "work", "internship", "skill_list", "unspecified"] = "unspecified"


class DatedEntry(ProfileModel):
    start_date: DateFact | None = None
    end_date: DateFact | None = None

    @model_validator(mode="after")
    def date_order(self):
        if self.start_date and self.end_date:
            start = _explicit_date(self.start_date.value)
            end = _explicit_date(self.end_date.value)
            if start and end:
                # Year-only precision must not invent a January/December date.
                if int(start[:4]) > int(end[:4]) or (
                    len(start) == len(end) == 7 and start > end
                ):
                    raise ValueError("结束日期早于开始日期。")
        return self

    @model_validator(mode="after")
    def nonempty_entry(self):
        if not any(getattr(self, name) for name in type(self).model_fields):
            raise ValueError("经历条目至少需要一项带证据的事实。")
        return self


class Education(DatedEntry):
    school: TextFact | None = None
    major: TextFact | None = None
    degree: TextFact | None = None
    status: TextFact | None = None


class ProjectExperience(DatedEntry):
    name: TextFact | None = None
    role: TextFact | None = None
    nature: TextFact | None = None
    description: list[TextFact] = Field(default_factory=list)
    technologies: list[SkillEvidence] = Field(default_factory=list)


class WorkExperience(DatedEntry):
    company: TextFact | None = None
    role: TextFact | None = None
    employment_type: TextFact | None = None
    description: list[TextFact] = Field(default_factory=list)
    technologies: list[SkillEvidence] = Field(default_factory=list)


class ResumeProfile(ProfileModel):
    schema_version: Literal["resume-profile-v1"] = "resume-profile-v1"
    resume_id: str = Field(min_length=1)
    profile_version: int = Field(default=1, ge=1)
    builder_version: str = "resume-profile-contract-v1"
    source: ProfileSource
    education: list[Education] = Field(default_factory=list)
    skills: list[SkillEvidence] = Field(default_factory=list)
    projects: list[ProjectExperience] = Field(default_factory=list)
    work_experience: list[WorkExperience] = Field(default_factory=list)
    summary: TextFact | None = None
    warnings: list[str] = Field(default_factory=list)
    review_status: Literal["unconfirmed", "confirmed", "rejected"] = "unconfirmed"

    @field_validator("warnings")
    @classmethod
    def nonblank_warnings(cls, value):
        if any(not item.strip() for item in value):
            raise ValueError("警告条目不能为空白。")
        return value
