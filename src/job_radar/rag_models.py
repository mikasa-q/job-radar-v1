"""Stage 3 frozen public contracts. No runtime, database or network imports.

v1 metrics: relevance >= 1; gain 2**grade-1; discount log2(rank+1).
Identity ties: dataset_id, job_id. Unjudged != negative; empty relevant sets
have null Recall/MRR/nDCG and are reported separately. Human quality gating is
independent from synthetic correctness. Existing MatchingResult stays v1.
"""
from __future__ import annotations
import hashlib
import json
from typing import Literal, Protocol
from pydantic import Field, model_validator
from .matching_models import MatchingModel
from .retrieval_models import JobKey, JobSpan, MatchingResult, RetrievalHit, PROJECTION_VERSION

RAG_VERSION = 'rag-v1'
CHUNK_VERSION = 'evidence-chunks-v1'
INDEX_SCHEMA = 'derived-index-v1'
EVAL_VERSION = 'retrieval-eval-v1'
SHA = r'^[0-9a-f]{64}$'


def canonical_hash(value) -> str:
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class TokenBudget(Protocol):
    tokenizer_id: str
    revision: str
    max_tokens: int
    def count(self, text: str, *, kind: Literal['query', 'passage']) -> int:
        """Count exact prepared text, including role prefix and special tokens."""
        ...
    def offsets(self, text: str) -> list[tuple[int, int]]:
        """Real tokenizer character offsets without special tokens."""
        ...


class ChunkConfig(MatchingModel):
    version: Literal['evidence-chunks-v1'] = CHUNK_VERSION
    max_tokens: int = Field(default=512, ge=8, le=8192)
    overlap_tokens: int = Field(default=32, ge=0, le=1024)
    max_chunks_per_job: int = Field(default=2048, ge=1, le=10000)

    @model_validator(mode='after')
    def bounds(self):
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError('chunk_overlap_exceeds_budget')
        return self


class TextMap(MatchingModel):
    field: Literal['title', 'description']
    source_start: int = Field(ge=0)
    source_end: int = Field(ge=1)
    encoded_start: int = Field(ge=0)
    encoded_end: int = Field(ge=1)

    @model_validator(mode='after')
    def lengths(self):
        if self.source_end-self.source_start != self.encoded_end-self.encoded_start or self.source_end <= self.source_start:
            raise ValueError('invalid_text_map')
        return self


class EvidenceChunk(MatchingModel):
    version: Literal['evidence-chunks-v1'] = CHUNK_VERSION
    key: JobKey
    jd_sha256: str = Field(pattern=SHA)
    ordinal: int = Field(ge=0)
    span: JobSpan
    encoding_text: str = Field(min_length=1)
    encoding_sha256: str = Field(pattern=SHA)
    token_count: int = Field(ge=1)
    mapping: list[TextMap] = Field(default_factory=list)

    @model_validator(mode='after')
    def hash_text(self):
        if hashlib.sha256(self.encoding_text.encode()).hexdigest() != self.encoding_sha256:
            raise ValueError('chunk_encoding_hash_mismatch')
        return self


class ChunkHit(MatchingModel):
    chunk: EvidenceChunk
    score: float = Field(allow_inf_nan=False)
    rank: int = Field(ge=1)


class AggregatedChunks(MatchingModel):
    hits: list[RetrievalHit] = Field(default_factory=list)
    evidence: list[ChunkHit] = Field(default_factory=list)


class DenseSpec(MatchingModel):
    model_id: str
    revision: str = Field(pattern=r'^[0-9a-f]{40}$')
    dimension: int = Field(ge=1, le=65536)
    tokenizer_id: str
    tokenizer_revision: str
    pooling: Literal['mean', 'cls']
    normalize: Literal[True] = True
    query_prefix: str | None = None
    passage_prefix: str | None = None
    runtime_sha256: str = Field(pattern=SHA)


class IndexManifest(MatchingModel):
    schema_version: Literal['derived-index-v1'] = INDEX_SCHEMA
    generation: str
    snapshot_sha256: str = Field(pattern=SHA)
    projection_version: str = PROJECTION_VERSION
    lexical_version: str = 'char-bigram-alias-v1'
    chunk_config: ChunkConfig = Field(default_factory=ChunkConfig)
    dense: DenseSpec | None = None
    build_version: str = RAG_VERSION

    def compatibility_hash(self) -> str:
        data = self.model_dump(mode='json')
        data.pop('generation'); data.pop('snapshot_sha256')
        return canonical_hash(data)


class IndexUpdate(MatchingModel):
    generation: str
    added: int = Field(default=0, ge=0)
    changed: int = Field(default=0, ge=0)
    deleted: int = Field(default=0, ge=0)
    metadata_only: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)
    encoded_chunks: int = Field(default=0, ge=0)
    rebuilt: bool = False


class IndexedMatchingResult(MatchingModel):
    schema_version: Literal['indexed-matching-v1'] = 'indexed-matching-v1'
    matching: MatchingResult
    manifest: IndexManifest
    chunk_hits: list[ChunkHit] = Field(default_factory=list)


class EvalQuery(MatchingModel):
    query_id: str
    text: str
    profile_group: str
    split: Literal['dev', 'test']
    source: Literal['synthetic', 'redacted', 'human']


class EvalJudgment(MatchingModel):
    query_id: str
    key: JobKey
    job_group: str
    grade: int | None = Field(default=None, ge=0, le=2)
    status: Literal['unjudged', 'synthetic_expected', 'human_judged']
    evidence: list[str] = Field(default_factory=list)
    assessor: str | None = None
    review_note: str | None = None

    @model_validator(mode='after')
    def labels(self):
        if (self.status == 'unjudged') != (self.grade is None):
            raise ValueError('invalid_judgment_status')
        if self.status == 'human_judged' and not self.assessor:
            raise ValueError('human_assessor_required')
        return self


class EvaluationSet(MatchingModel):
    schema_version: Literal['retrieval-eval-v1'] = EVAL_VERSION
    data_version: str
    queries: list[EvalQuery] = Field(min_length=1)
    judgments: list[EvalJudgment] = Field(default_factory=list)


class MetricPolicy(MatchingModel):
    relevance_threshold: Literal[1] = 1
    gain: Literal['2**grade-1'] = '2**grade-1'
    discount: Literal['log2(rank+1)'] = 'log2(rank+1)'
    empty_relevant: Literal['null_separate'] = 'null_separate'
    unjudged: Literal['report_not_negative'] = 'report_not_negative'


class ExperimentSpec(MatchingModel):
    experiment_id: str
    config_sha256: str = Field(pattern=SHA)
    dataset_sha256: str = Field(pattern=SHA)
    index_sha256: str = Field(pattern=SHA)
    model_sha256: str | None = Field(default=None, pattern=SHA)
    variant: Literal['stage2_default', 'tuned_bm25', 'dense', 'hybrid', 'no_chunk', 'chunk_best']
    frozen_on_dev: bool = False
