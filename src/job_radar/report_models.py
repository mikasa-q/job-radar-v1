"""Frozen Stage 4 contracts; local metadata and model-authored claims are separate."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
from typing import Literal, Protocol
from pydantic import Field, model_validator, field_validator
from .matching_models import MatchingModel
from .resume_profile import EvidenceRef
from .retrieval_models import JobKey, JobSpan
from .rag_models import IndexedMatchingResult, canonical_hash

REPORT_VERSION = 'match-report-v1'
PROMPT_VERSION = 'match-analysis-prompt-v3'
SHA = r'^[0-9a-f]{64}$'

class ReportError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

def strict_json(text: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    def constant(_): raise ValueError('nonfinite_json')
    def finite(value):
        number=float(value)
        if not math.isfinite(number): raise ValueError('nonfinite_json')
        return number
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant,parse_float=finite)

class EvidenceRecord(MatchingModel):
    evidence_id: str = Field(pattern=r'^e[0-9a-f]{24}$')
    kind: Literal['resume','job','rule','retrieval','preference','scope']
    path: str = Field(min_length=1, max_length=1024)
    key: JobKey | None = None
    source_sha256: str = Field(pattern=SHA)
    quote: str | None = None
    resume_ref: EvidenceRef | None = None
    job_span: JobSpan | None = None
    value_json: str = Field(min_length=1)

    @model_validator(mode='after')
    def shape(self):
        strict_json(self.value_json)
        payload=self.model_dump(mode='json',exclude={'evidence_id'})
        if self.evidence_id != 'e'+canonical_hash(payload)[:24]: raise ValueError('evidence_id_mismatch')
        if self.kind == 'resume' and (self.resume_ref is None or self.quote != self.resume_ref.quote):
            raise ValueError('resume_evidence_shape')
        if self.kind == 'job' and (self.key is None or self.job_span is None or self.quote != self.job_span.quote):
            raise ValueError('job_evidence_shape')
        if self.kind in {'resume','preference','scope'} and self.key is not None: raise ValueError('global_evidence_key')
        if self.kind != 'resume' and self.resume_ref is not None: raise ValueError('unexpected_resume_ref')
        if self.kind != 'job' and self.job_span is not None: raise ValueError('unexpected_job_span')
        if self.kind in {'rule','retrieval'} and self.key is None: raise ValueError('job_scope_required')
        return self

class EvidencePacket(MatchingModel):
    schema_version: Literal['match-evidence-v1'] = 'match-evidence-v1'
    request_sha256: str = Field(pattern=SHA)
    document_sha256: str = Field(pattern=SHA)
    profile_sha256: str = Field(pattern=SHA)
    retrieval_sha256: str = Field(pattern=SHA)
    retrieval: IndexedMatchingResult
    selected_keys: list[JobKey] = Field(default_factory=list, max_length=20)
    evidence: list[EvidenceRecord] = Field(default_factory=list, max_length=10000)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def consistency(self):
        if any(not isinstance(v,str) or not v.strip() for v in self.limitations): raise ValueError('invalid_limitation')
        if canonical_hash(self.retrieval) != self.retrieval_sha256: raise ValueError('retrieval_hash_mismatch')
        ids = [r.evidence_id for r in self.evidence]
        if len(ids) != len(set(ids)): raise ValueError('duplicate_evidence_id')
        keys = [k.model_dump() for k in self.selected_keys]
        ranked = [r.key.model_dump() for r in self.retrieval.matching.results]
        if keys != ranked[:len(keys)]: raise ValueError('selection_must_be_ranked_prefix')
        return self

class AnalysisClaim(MatchingModel):
    claim_id: str = Field(pattern=r'^c[1-9][0-9]{0,3}$')
    kind: Literal['match_interpretation','strength','skill_gap','unknown','recommendation']
    subject: str = Field(min_length=1, max_length=120)
    status: Literal['supported','explicit_gap','unknown','advisory']
    text: str = Field(min_length=1, max_length=1600)
    requirement_kind: Literal['required','bonus','mention','unknown'] = 'unknown'
    evidence_ids: list[str] = Field(min_length=1, max_length=24)
    related_claim_ids: list[str] = Field(default_factory=list, max_length=24)

    @model_validator(mode='after')
    def claim_shape(self):
        allowed = {'match_interpretation': {'supported','unknown'}, 'strength': {'supported'},
            'skill_gap': {'explicit_gap','unknown'}, 'unknown': {'unknown'}, 'recommendation': {'advisory'}}
        if self.status not in allowed[self.kind]: raise ValueError('claim_status_mismatch')
        if len(set(self.evidence_ids)) != len(self.evidence_ids): raise ValueError('duplicate_claim_evidence')
        if len(set(self.related_claim_ids)) != len(self.related_claim_ids): raise ValueError('duplicate_related_claim')
        if self.kind == 'recommendation' and not self.related_claim_ids: raise ValueError('recommendation_requires_target')
        return self

class MatchAnalysisDraft(MatchingModel):
    key: JobKey
    claims: list[AnalysisClaim] = Field(min_length=1, max_length=24)

    @model_validator(mode='after')
    def relationships(self):
        ids = [c.claim_id for c in self.claims]
        if len(ids) != len(set(ids)): raise ValueError('duplicate_claim_id')
        by_id = {c.claim_id:c for c in self.claims}
        for c in self.claims:
            if any(i not in by_id or i == c.claim_id for i in c.related_claim_ids): raise ValueError('invalid_related_claim')
            if c.kind == 'recommendation' and any(by_id[i].kind == 'recommendation' for i in c.related_claim_ids):
                raise ValueError('recommendation_target_is_not_finding')
        return self

class AnalysisConfig(MatchingModel):
    top_n: int = Field(default=3, ge=1, le=20)
    timeout_seconds: float = Field(default=90.0, gt=0, le=90.0, allow_inf_nan=False)
    max_input_bytes: int = Field(default=128*1024, ge=256, le=1024*1024)
    max_output_bytes: int = Field(default=128*1024, ge=256, le=1024*1024)

@dataclass(frozen=True)
class AnalysisRequest:
    system_prompt: str
    packet_json: str
    output_schema_json: str
    timeout_seconds: float
    max_output_bytes: int
    prompt_version: str

class CallAudit(MatchingModel):
    provider: str
    model: str
    attempts: int = Field(ge=0, le=20)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    reserved_units: str | None = None
    billing_unit: str | None = None
    elapsed_seconds: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    @model_validator(mode='after')
    def usage(self):
        values = (self.input_tokens,self.output_tokens,self.total_tokens)
        if any(v is None for v in values) and not all(v is None for v in values): raise ValueError('partial_usage')
        if all(v is not None for v in values) and self.total_tokens != self.input_tokens+self.output_tokens:
            raise ValueError('invalid_usage_total')
        return self

class AnalysisResponse(MatchingModel):
    status: Literal['completed','refused','incomplete']
    text: str | None = None
    audit: CallAudit | None = None
    @model_validator(mode='after')
    def body(self):
        if self.status == 'completed' and self.text is None: raise ValueError('missing_response_text')
        if self.status != 'completed' and self.text is not None: raise ValueError('unexpected_response_text')
        return self

class AnalysisClient(Protocol):
    def complete(self, request: AnalysisRequest) -> AnalysisResponse: ...

class JobAnalysis(MatchingModel):
    key: JobKey
    status: Literal['not_analyzed','completed','failed']
    draft: MatchAnalysisDraft | None = None
    error_code: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_]{0,79}$')
    audit: CallAudit | None = None
    @model_validator(mode='after')
    def outcome(self):
        if self.status == 'completed':
            if self.draft is None or self.draft.key != self.key or self.error_code is not None: raise ValueError('invalid_analysis_success')
        elif self.draft is not None: raise ValueError('draft_on_unsuccessful_analysis')
        if (self.status == 'failed') != (self.error_code is not None): raise ValueError('invalid_analysis_error')
        return self

class MatchReport(MatchingModel):
    schema_version: Literal['match-report-v1'] = REPORT_VERSION
    prompt_version: Literal['match-analysis-prompt-v1', 'match-analysis-prompt-v2', 'match-analysis-prompt-v3'] = PROMPT_VERSION
    report_id: str = Field(min_length=1, max_length=160)
    generated_at: datetime
    packet: EvidencePacket
    packet_sha256: str = Field(pattern=SHA)
    status: Literal['not_run','completed','partial','failed']
    analyses: list[JobAnalysis] = Field(default_factory=list)
    review_status: Literal['unconfirmed'] = 'unconfirmed'
    @field_validator('generated_at', mode='before')
    @classmethod
    def utc(cls,v):
        if isinstance(v,str): v=datetime.fromisoformat(v.replace('Z','+00:00'))
        if not isinstance(v,datetime) or v.utcoffset()!=timedelta(0): raise ValueError('utc_required')
        return v
    @model_validator(mode='after')
    def report_consistency(self):
        if self.packet_sha256 != canonical_hash(self.packet): raise ValueError('packet_hash_mismatch')
        if [a.key for a in self.analyses] != [r.key for r in self.packet.retrieval.matching.results]:
            raise ValueError('analysis_ranking_changed')
        selected = self.packet.selected_keys
        chosen = [a for a in self.analyses if a.key in selected]
        if any(a.status!='not_analyzed' for a in self.analyses if a.key not in selected): raise ValueError('unselected_analysis')
        if not chosen or all(a.status=='not_analyzed' for a in chosen): expected='not_run'
        elif all(a.status=='completed' for a in chosen): expected='completed'
        elif any(a.status=='completed' for a in chosen): expected='partial'
        else: expected='failed'
        if self.status != expected: raise ValueError('report_status_mismatch')
        return self
