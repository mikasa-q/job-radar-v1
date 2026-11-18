"""Frozen Stage 2 contracts shared by lexical, dense and rule modules."""
from __future__ import annotations
from typing import Literal, Protocol
from pydantic import Field, field_validator, model_validator
from .matching_models import JobProvenance, MatchingModel, ReviewStatus
from .resume_profile import EvidenceRef, ProfileSource

PROJECTION_VERSION = 'retrieval-text-v1'
ENGINE_VERSION = 'matching-engine-v1'


class RetrievalError(ValueError):
    """Fixed code only; no credentials, source text or underlying exception."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class JobKey(MatchingModel):
    dataset_id: str = Field(min_length=1)
    job_id: int = Field(ge=1)


class JobSpan(MatchingModel):
    field: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    quote: str = Field(min_length=1)

    @model_validator(mode='after')
    def length(self):
        if self.end <= self.start or len(self.quote) != self.end-self.start:
            raise ValueError('invalid_job_span')
        return self


class RetrievalQuery(MatchingModel):
    version: Literal['retrieval-text-v1'] = PROJECTION_VERSION
    text: str  # may be empty; no invented query from missing facts
    terms: list[str] = Field(default_factory=list)
    resume_paths: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @field_validator('*', mode='after')
    @classmethod
    def nonblank(cls, value):
        # Query text alone may be empty, representing no query rather than success.
        return value

    @classmethod
    def empty(cls):
        return cls(text='', terms=[], resume_paths=[], evidence=[])


class RetrievalDocument(MatchingModel):
    key: JobKey
    version: Literal['retrieval-text-v1'] = PROJECTION_VERSION
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    title_terms: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    fact_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class RetrievalHit(MatchingModel):
    key: JobKey
    channel: Literal['bm25', 'dense']
    score: float = Field(allow_inf_nan=False)
    rank: int = Field(ge=1)
    matched_terms: list[str] = Field(default_factory=list)


class EngineConfig(MatchingModel):
    schema_version: Literal['engine-config-v1'] = 'engine-config-v1'
    mode: Literal['bm25_rules', 'hybrid'] = 'bm25_rules'
    allow_dense_fallback: bool = False
    top_k: int = Field(default=10, ge=1, le=1000)
    recall_k: int = Field(default=50, ge=1, le=1000)
    k1: float = Field(default=1.5, gt=0, le=10, allow_inf_nan=False)
    b: float = Field(default=0.75, ge=0, le=1, allow_inf_nan=False)
    title_weight: float = Field(default=2.0, ge=0, le=10, allow_inf_nan=False)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    hard_constraints: list[Literal['city','attendance','duration']] = Field(default_factory=list)
    max_text_chars: int = Field(default=16000, ge=1, le=128000)
    max_jobs: int = Field(default=1000, ge=1, le=10000)

    @model_validator(mode='after')
    def bounds(self):
        if self.top_k > self.recall_k:
            raise ValueError('top_k_exceeds_recall_k')
        if len(set(self.hard_constraints)) != len(self.hard_constraints):
            raise ValueError('duplicate_hard_constraint')
        return self


class EmbeddingClient(Protocol):
    model_id: str
    revision: str
    dimension: int
    def encode(self, texts: list[str], *, kind: Literal['query','passage']) -> list[list[float]]: ...


class ConstraintCheck(MatchingModel):
    name: str
    status: Literal['satisfied','not_satisfied','unknown']
    hard: bool = False
    reason: str
    resume_paths: list[str] = Field(default_factory=list)
    resume_evidence: list[EvidenceRef] = Field(default_factory=list)
    job_evidence: list[JobSpan] = Field(default_factory=list)


class SkillCheck(MatchingModel):
    alternatives: list[str] = Field(min_length=1)
    kind: Literal['required','bonus','mention']
    status: Literal['evidence_found','not_found']
    matched: list[str] = Field(default_factory=list)
    resume_paths: list[str] = Field(default_factory=list)
    resume_evidence: list[EvidenceRef] = Field(default_factory=list)
    job_evidence: list[JobSpan] = Field(default_factory=list)


class RuleResult(MatchingModel):
    version: Literal['matching-rules-v1'] = 'matching-rules-v1'
    constraints: list[ConstraintCheck] = Field(default_factory=list)
    skills: list[SkillCheck] = Field(default_factory=list)
    covered_required: int = Field(default=0, ge=0)
    total_required: int = Field(default=0, ge=0)
    coverage: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def coverage_math(self):
        required=[s for s in self.skills if s.kind=='required']
        covered=sum(s.status=='evidence_found' for s in required)
        if self.total_required != len(required) or self.covered_required != covered:
            raise ValueError('invalid_rule_counts')
        expected=covered/len(required) if required else None
        if self.coverage != expected:
            raise ValueError('invalid_rule_coverage')
        return self


class ChannelStatus(MatchingModel):
    channel: Literal['bm25','dense']
    status: Literal['ok','disabled','degraded']
    reason: str | None = None
    model_id: str | None = None
    revision: str | None = None
    dimension: int | None = Field(default=None, ge=1)


class RankedJob(MatchingModel):
    key: JobKey
    url: str
    fact_sha256: str
    jd_sha256: str
    review_status: ReviewStatus
    provenance: list[JobProvenance] = Field(min_length=1)
    rank: int = Field(ge=1)
    bm25: RetrievalHit | None = None
    dense: RetrievalHit | None = None
    fusion_score: float = Field(ge=0, allow_inf_nan=False)
    rules: RuleResult
    warnings: list[str] = Field(default_factory=list)


class ExcludedJob(MatchingModel):
    key: JobKey
    rules: RuleResult
    reason: Literal['hard_constraint_conflict'] = 'hard_constraint_conflict'


class MatchingResult(MatchingModel):
    schema_version: Literal['matching-result-v1'] = 'matching-result-v1'
    engine_version: str = ENGINE_VERSION
    profile_id: str
    profile_version: int = Field(ge=1)
    source: ProfileSource
    profile_review_status: ReviewStatus
    purpose: Literal['debug','formal']
    requested_mode: Literal['bm25_rules','hybrid']
    actual_mode: Literal['bm25_rules','hybrid']
    status: Literal['ok','degraded','no_query','no_candidates','no_hits']
    config: EngineConfig
    results: list[RankedJob] = Field(default_factory=list)
    excluded: list[ExcludedJob] = Field(default_factory=list)
    channels: list[ChannelStatus] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
