"""Stage 5 step-one wire contracts. Domain schemas remain owned by stages 1–4."""
from __future__ import annotations

from typing import Generic, Literal, TypeVar
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .matching_models import SearchPreferences
from .retrieval_models import EngineConfig, JobKey

API_VERSION = 'jobradar-api-v1'
RESOURCE_PATTERN = r'^(?:rsm|prf|mtc|rpt)_[0-9a-f]{32}$'
T = TypeVar('T')
TaskState = Literal['queued', 'running', 'succeeded', 'failed', 'interrupted']


class WireModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


ERROR_HTTP = {
    'invalid_request': 422, 'method_not_allowed': 405, 'resource_not_found': 404,
    'idempotency_key_required': 422, 'idempotency_key_invalid': 422,
    'idempotency_conflict': 409, 'formal_requires_confirmed': 409,
    'snapshot_conflict': 409, 'payload_too_large': 413,
    'unsupported_media_type': 415, 'queue_full': 429,
    'llm_disabled': 503, 'hybrid_not_enabled': 503,
    'budget_exhausted': 503, 'attempt_limit': 503, 'budget_halted': 503,
    'dataset_unavailable': 503, 'index_unavailable': 503,
    'snapshot_corrupt': 503, 'store_unavailable': 503,
    'dependency_not_ready': 503, 'task_interrupted': 503,
    'execution_timeout': 504, 'upstream_error': 502, 'internal_error': 500,
    'pdf_empty_pdf': 422, 'pdf_invalid_pdf': 422, 'pdf_password_required': 422,
    'pdf_page_limit': 422, 'pdf_no_pages': 422, 'pdf_no_extractable_text': 422,
    'pdf_size_limit': 413, 'pdf_extract_failed': 422,
    'core_rejected': 422,
}


class ApiError(WireModel):
    code: str
    message: str = '请求未完成；请根据错误码检查输入或服务状态。'
    fields: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def known_code(self):
        if self.code not in ERROR_HTTP:
            raise ValueError('unknown_error_code')
        return self


class Envelope(WireModel, Generic[T]):
    schema_version: Literal['jobradar-api-v1'] = API_VERSION
    request_id: str = Field(pattern=r'^req_[0-9a-f]{32}$')
    data: T | None = None
    error: ApiError | None = None

    @model_validator(mode='after')
    def exclusive(self):
        if (self.data is None) == (self.error is None):
            raise ValueError('exactly_one_of_data_error')
        return self


class Resource(WireModel, Generic[T]):
    resource_id: str = Field(pattern=RESOURCE_PATTERN)
    kind: Literal['resume', 'profile', 'match', 'report']
    state: TaskState
    execution_mode: Literal['pdf', 'rules', 'bm25_rules', 'offline', 'llm']
    created_at: datetime
    updated_at: datetime
    input_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    payload_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    payload: T | None = None
    error: ApiError | None = None

    @model_validator(mode='after')
    def outcome(self):
        prefix = {'resume':'rsm', 'profile':'prf', 'match':'mtc', 'report':'rpt'}[self.kind]
        if not self.resource_id.startswith(prefix + '_'):
            raise ValueError('resource_kind_mismatch')
        modes = {'resume': {'pdf'}, 'profile': {'rules', 'llm'},
                 'match': {'bm25_rules'}, 'report': {'offline', 'llm'}}
        if self.execution_mode not in modes[self.kind]:
            raise ValueError('resource_mode_mismatch')
        for stamp in (self.created_at, self.updated_at):
            if stamp.utcoffset() is None:
                raise ValueError('aware_timestamp_required')
        if self.state == 'succeeded':
            if self.payload is None or self.payload_sha256 is None or self.error is not None:
                raise ValueError('success_requires_payload')
        elif self.state in ('failed','interrupted'):
            if self.payload is not None or self.payload_sha256 is not None or self.error is None:
                raise ValueError('failure_requires_error')
        elif self.payload is not None or self.payload_sha256 is not None or self.error is not None:
            raise ValueError('pending_has_no_result')
        return self


class Accepted(WireModel):
    resource_id: str = Field(pattern=RESOURCE_PATTERN)
    state: TaskState
    status_url: str


class ProfileCreate(WireModel):
    mode: Literal['rules', 'llm'] = 'rules'


class MatchCreate(WireModel):
    profile_id: str = Field(pattern=r'^prf_[0-9a-f]{32}$')
    purpose: Literal['debug', 'formal'] = 'debug'
    preferences: SearchPreferences = Field(default_factory=SearchPreferences)
    candidate_keys: list[JobKey] = Field(max_length=1000)
    retrieval: EngineConfig = Field(default_factory=EngineConfig)

    @model_validator(mode='after')
    def bounded_unique(self):
        keys = [(k.dataset_id,k.job_id) for k in self.candidate_keys]
        if len(set(keys)) != len(keys):
            raise ValueError('duplicate_candidate_key')
        if self.retrieval.max_jobs > 1000:
            raise ValueError('api_candidate_limit')
        return self


class ReportCreate(WireModel):
    match_id: str = Field(pattern=r'^mtc_[0-9a-f]{32}$')
    mode: Literal['offline', 'llm'] = 'offline'
    top_n: int = Field(default=3, ge=1, le=20)


class Ready(WireModel):
    status: Literal['ready', 'not_ready']
    evidence: bool
    index: bool
    real_llm: bool = False
    hybrid: Literal[False] = False


# Explicit public payload types reuse the unchanged domain JSON schemas.
from .resume_models import ResumeDocument
from .resume_profile import ResumeProfile
from .matching_models import MatchingRequest, JobCandidate
from .rag_models import IndexManifest, IndexedMatchingResult
from .report_models import MatchReport
from .resume_structure import SkillComparison


class ProfilePayload(WireModel):
    resume_id: str = Field(pattern=r'^rsm_[0-9a-f]{32}$')
    document: ResumeDocument
    profile: ResumeProfile
    comparison: SkillComparison | None = None


class MatchPayload(WireModel):
    document: ResumeDocument
    request: MatchingRequest
    index_manifest: IndexManifest
    engine_config: EngineConfig
    retrieval: IndexedMatchingResult
    snapshot_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class ReportPayload(WireModel):
    match_id: str = Field(pattern=r'^mtc_[0-9a-f]{32}$')
    snapshot_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    report: MatchReport
    markdown: str


class JobsPage(WireModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1,le=100)
    offset: int = Field(ge=0)
    items: list[JobCandidate]
