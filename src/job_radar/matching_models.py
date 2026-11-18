"""Stage 1D data contracts only: no repository, retrieval or scoring engine.

Facts retain their original evidence and review state. Hashes bind supplied job
content, not the truth of a JD or the authenticity of a supplied source record.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .import_models import ImportedJob
from .resume_profile import (
    Education, ProfileSource, ProjectExperience, SkillEvidence, TextFact,
    WorkExperience,
)

ReviewStatus = Literal["unconfirmed", "confirmed", "rejected"]
MatchingPurpose = Literal["debug", "formal"]


def _plain(value):
    """Detach nested instances and force their validators to run at boundaries."""
    if isinstance(value, BaseModel):
        return _plain({**vars(value), **(value.model_extra or {})})
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


class MatchingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True,
                              revalidate_instances="always")

    @field_validator("*", mode="before")
    @classmethod
    def revalidate_nested(cls, value):
        # Existing 1B/Importer classes accept model instances without rechecking
        # mutable lists. Convert to plain data before any nested construction.
        return _plain(value)

    @field_validator("*", mode="after")
    @classmethod
    def nonblank(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("blank_string")
        return value


class SearchPreferences(MatchingModel):
    """Explicit user choices, not facts inferred from a resume or JD.

    None means unspecified. Nonempty city/role lists express alternatives; no
    filtering or hard-constraint evaluation is performed at this stage.
    """

    cities: list[str] | None = Field(default=None, min_length=1)
    target_roles: list[str] | None = Field(default=None, min_length=1)
    available_days_per_week: int | None = Field(default=None, ge=1, le=7)
    available_duration_months: int | None = Field(default=None, ge=1)

    @field_validator("cities", "target_roles")
    @classmethod
    def nonblank_choices(cls, value):
        if value is not None and any(not item.strip() for item in value):
            raise ValueError("blank_preference")
        return value


class MatchingInput(MatchingModel):
    """Lossless projection of matching facts, not an evidence-check certificate."""

    schema_version: Literal["matching-input-v1"] = "matching-input-v1"
    profile_schema_version: Literal["resume-profile-v1"] = "resume-profile-v1"
    profile_id: str = Field(min_length=1)
    profile_version: int = Field(ge=1)
    builder_version: str = Field(min_length=1)
    source: ProfileSource
    skills: list[SkillEvidence] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    projects: list[ProjectExperience] = Field(default_factory=list)
    work_experience: list[WorkExperience] = Field(default_factory=list)
    summary: TextFact | None = None
    preferences: SearchPreferences
    warnings: list[str] = Field(default_factory=list)
    review_status: ReviewStatus

    @field_validator("warnings")
    @classmethod
    def nonblank_warnings(cls, value):
        if any(not item.strip() for item in value):
            raise ValueError("blank_warning")
        return value


class JobProvenance(MatchingModel):
    """One stored source version; absolute local source paths stay outside."""

    provenance_id: int = Field(ge=1)
    source_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_format: Literal["html", "txt"]
    parser_version: str = Field(min_length=1)
    imported_at: datetime
    import_run_id: str = Field(min_length=1)

    @field_validator("imported_at", mode="before")
    @classmethod
    def utc_timestamp(cls, value):
        # SQLite rows and JSON use ISO strings; Python callers may pass datetime.
        # Parse only this explicit wire field, never epoch numbers or local time.
        if isinstance(value, str):
            try:
                if "T" not in value:
                    raise ValueError
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("imported_at_requires_utc_iso") from None
        if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
            raise ValueError("imported_at_requires_utc")
        return value


class JobCandidate(MatchingModel):
    """Imported facts plus caller-supplied storage identity and source versions.

    The future read adapter must verify the dataset and row associations. This
    pure model neither opens that dataset nor checks source files on disk.
    """

    schema_version: Literal["job-candidate-v1"] = "job-candidate-v1"
    dataset_id: str = Field(min_length=1)
    job_id: int = Field(ge=1)
    job: ImportedJob
    jd_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: list[JobProvenance] = Field(min_length=1)
    review_status: ReviewStatus

    @model_validator(mode="after")
    def content_and_sources(self):
        if self.job.source not in {"boss", "manual"}:
            raise ValueError("unsupported_dataset_source")
        # Same UTF-8/canonical JSON recipe as dataset_store.job_fingerprints.
        # Deliberately do not import that I/O module or normalize supplied facts.
        jd_hash = hashlib.sha256(self.job.description.encode("utf-8")).hexdigest()
        canonical = json.dumps(self.job.model_dump(), ensure_ascii=False,
                               sort_keys=True, separators=(",", ":"), allow_nan=False)
        fact_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if self.jd_sha256 != jd_hash:
            raise ValueError("jd_hash_mismatch")
        if self.fact_sha256 != fact_hash:
            raise ValueError("fact_hash_mismatch")
        ids = [item.provenance_id for item in self.provenance]
        versions = [(item.source_file_sha256, item.parser_version) for item in self.provenance]
        if len(set(ids)) != len(ids) or len(set(versions)) != len(versions):
            raise ValueError("duplicate_provenance")
        return self


class MatchingRequest(MatchingModel):
    """An unranked handoff. 'formal' is a review gate, not a match result."""

    schema_version: Literal["matching-request-v1"] = "matching-request-v1"
    purpose: MatchingPurpose
    profile: MatchingInput
    jobs: list[JobCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def identity_and_review_gate(self):
        ids = [(item.dataset_id, item.job_id) for item in self.jobs]
        urls = [(item.dataset_id, item.job.source, item.job.url) for item in self.jobs]
        if len(set(ids)) != len(ids) or len(set(urls)) != len(urls):
            raise ValueError("duplicate_job_identity")
        if self.purpose == "formal" and (
            self.profile.review_status != "confirmed"
            or any(item.review_status != "confirmed" for item in self.jobs)
        ):
            raise ValueError("formal_requires_confirmed")
        return self
