"""Structural chunk tests with an explicitly synthetic tokenizer, no vectors.

These tests exercise coordinates/budgets; they are not real tokenizer or model
quality evidence. Real tokenizer/model operability is measured separately.
"""
from datetime import datetime, timezone
import hashlib
import json

import pytest
from pydantic import ValidationError

from job_radar.import_models import ImportedJob
from job_radar.matching_models import JobCandidate
from job_radar.rag_chunks import aggregate_chunks, chunk_job, _sanitize
from job_radar.rag_models import ChunkConfig, ChunkHit, EvidenceChunk
from job_radar.retrieval_models import RetrievalError
from job_radar.retrieval_text import retrieval_text


class SyntheticCharacterTokenizer:
    """One codepoint per synthetic token plus explicit role/special overhead."""
    tokenizer_id = 'synthetic-character-test-only'
    revision = 'synthetic-v1'
    max_tokens = 64
    overhead = 2

    def count(self, text, *, kind):
        assert kind == 'passage'
        return len(text) + self.overhead

    def offsets(self, text):
        return [(index, index+1) for index in range(len(text))]


def job(description='岗位要求：Python。掌握接口测试。', *, title='职位', job_id=1, dataset_id='synthetic-chunks'):
    record = ImportedJob(source='manual', title=title,
                         url=f'https://example.org/chunks/{dataset_id}/{job_id}',
                         description=description)
    canonical = json.dumps(record.model_dump(), ensure_ascii=False, sort_keys=True,
                           separators=(',', ':'), allow_nan=False)
    return JobCandidate(dataset_id=dataset_id, job_id=job_id, job=record,
                        jd_sha256=hashlib.sha256(record.description.encode()).hexdigest(),
                        fact_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
                        review_status='unconfirmed', provenance=[dict(
                            provenance_id=job_id, source_file_sha256='1'*64,
                            input_format='txt', parser_version='synthetic-v1',
                            imported_at=datetime(2026, 11, 12, tzinfo=timezone.utc),
                            import_run_id='synthetic-chunk-run')])


def config(max_tokens=32, overlap_tokens=3, **updates):
    return ChunkConfig(max_tokens=max_tokens, overlap_tokens=overlap_tokens, **updates)


def check_mapping(candidate, chunks, budget, ceiling):
    for ordinal, chunk in enumerate(chunks):
        assert chunk.ordinal == ordinal
        assert chunk.span.field == 'description'
        assert candidate.job.description[chunk.span.start:chunk.span.end] == chunk.span.quote
        assert chunk.jd_sha256 == candidate.jd_sha256
        assert chunk.key.dataset_id == candidate.dataset_id and chunk.key.job_id == candidate.job_id
        assert chunk.encoding_sha256 == hashlib.sha256(chunk.encoding_text.encode()).hexdigest()
        assert chunk.token_count == budget.count(chunk.encoding_text, kind='passage') <= ceiling
        for mapping in chunk.mapping:
            original = getattr(candidate.job, mapping.field)
            assert original[mapping.source_start:mapping.source_end] == chunk.encoding_text[mapping.encoded_start:mapping.encoded_end]
            if mapping.field == 'description':
                assert chunk.span.start <= mapping.source_start < mapping.source_end <= chunk.span.end


def test_short_job_exact_evidence_title_and_roundtrip():
    candidate = job(); before = candidate.model_dump_json(); budget = SyntheticCharacterTokenizer()
    chunks = chunk_job(candidate, budget=budget, config=config(64))
    assert len(chunks) == 1
    assert chunks[0].encoding_text == candidate.job.title+'\n'+candidate.job.description
    assert EvidenceChunk.model_validate_json(chunks[0].model_dump_json()) == chunks[0]
    check_mapping(candidate, chunks, budget, 64)
    assert candidate.model_dump_json() == before


@pytest.mark.parametrize('text', [
    '中文English混排，Python3 和 C++。\n任职资格：需懂RAG。\n末尾要求：轮班。',
    '甲'*300+'末尾要求：Docker。',
    'Develop APIs. Write tests! Maintain services? Final requirement: Python.',
    '第一段。\n\n第二段；\r\n第三段！第四段？最后一条。'*3,
    '甲乙 丙丁\t戊己\n庚辛。'*20,
])
def test_long_mixed_sentence_paragraph_tail_coverage(text):
    candidate = job(text); budget = SyntheticCharacterTokenizer()
    chunks = chunk_job(candidate, budget=budget, config=config())
    check_mapping(candidate, chunks, budget, 32)
    covered = set()
    for chunk in chunks:
        for mapping in chunk.mapping:
            if mapping.field == 'description':
                covered.update(range(mapping.source_start, mapping.source_end))
    assert {i for i, char in enumerate(candidate.job.description) if not char.isspace()} <= covered
    assert chunks[-1].span.end == len(candidate.job.description)
    assert len(chunks) > 1


def test_natural_sentence_boundary_is_preferred_before_long_sentence():
    candidate = job('第一短句。'+'中'*60+'。')
    chunks = chunk_job(candidate, budget=SyntheticCharacterTokenizer(), config=config(24, 0))
    assert chunks[0].span.quote == '第一短句。'
    assert chunks[-1].span.end == len(candidate.job.description)


def test_title_role_instruction_special_overhead_in_budget():
    budget = SyntheticCharacterTokenizer(); budget.overhead = 13
    candidate = job('x'*60, title='长标题')
    chunks = chunk_job(candidate, budget=budget, config=config(24, 2))
    check_mapping(candidate, chunks, budget, 24)
    assert all(len(chunk.encoding_text) <= 11 for chunk in chunks)


def test_native_limit_is_not_silently_raised():
    budget = SyntheticCharacterTokenizer(); budget.max_tokens = 17
    chunks = chunk_job(job('中'*80), budget=budget, config=config(64, 2))
    assert len(chunks) > 5
    assert max(chunk.token_count for chunk in chunks) <= 17


@pytest.mark.parametrize('text', [
    '联系人 a+b@example.org 工作内容：Python。电话 +86 138-0013-8000。',
    '岗位详情 https://example.org/foo?x=123；要求 SQL。',
    '  abc@example.com  中文 12345678901 xyz  ',
    'https://example.org/x\n第一行。\nfoo@example.com\n最后要求：Go。',
])
def test_sanitization_matches_existing_projection_and_preserves_coordinates(text):
    assert _sanitize(text)[0] == retrieval_text(text)
    candidate = job(text, title='职位 jobs@example.org')
    budget = SyntheticCharacterTokenizer()
    chunks = chunk_job(candidate, budget=budget, config=config(40, 3))
    check_mapping(candidate, chunks, budget, 40)
    assert all('@' not in chunk.encoding_text and 'https://' not in chunk.encoding_text for chunk in chunks)
    for chunk in chunks:
        assert any(mapping.field == 'description' for mapping in chunk.mapping)


def test_contact_only_body_returns_empty_not_fabricated_quote():
    assert chunk_job(job('https://example.org'), budget=SyntheticCharacterTokenizer()) == []


def test_contact_only_title_does_not_invent_title_evidence():
    candidate = job('必须掌握 Python。', title='title@example.org')
    chunks = chunk_job(candidate, budget=SyntheticCharacterTokenizer())
    assert chunks[0].encoding_text == '\n必须掌握 Python。'
    assert all(mapping.field != 'title' for mapping in chunks[0].mapping)


def test_zero_overlap_has_disjoint_original_ranges_and_full_tail():
    chunks = chunk_job(job('甲'*100), budget=SyntheticCharacterTokenizer(), config=config(20, 0))
    assert all(right.span.start == left.span.end for left, right in zip(chunks, chunks[1:]))
    assert chunks[-1].span.end == 100


def test_overlap_uses_token_offsets_and_advances():
    chunks = chunk_job(job('甲'*100), budget=SyntheticCharacterTokenizer(), config=config(20, 3))
    assert all(right.span.start == left.span.end-3 for left, right in zip(chunks, chunks[1:]))
    assert all(right.span.start > left.span.start for left, right in zip(chunks, chunks[1:]))
    assert len(chunks) < 12


def test_large_requested_overlap_never_repeats_identical_coverage_forever():
    chunks = chunk_job(job('甲'*80), budget=SyntheticCharacterTokenizer(), config=config(20, 19))
    assert chunks[-1].span.end == 80
    assert all(b.span.end > a.span.end for a, b in zip(chunks, chunks[1:]))


def test_overlap_does_not_loop_on_previously_covered_sentence():
    chunks = chunk_job(job('abcde. '+'x'*50), budget=SyntheticCharacterTokenizer(), config=config(16, 8))
    assert chunks[-1].span.end == 57
    assert all(b.span.end > a.span.end for a, b in zip(chunks, chunks[1:]))


def test_chunk_limit_has_explicit_error():
    with pytest.raises(RetrievalError, match='chunk_limit_exceeded'):
        chunk_job(job('甲'*80), budget=SyntheticCharacterTokenizer(), config=config(10, 0, max_chunks_per_job=2))


@pytest.mark.parametrize('title,overhead', [('职位'*30, 2), ('职位', 50)])
def test_exhausted_title_budget_errors_without_truncation(title, overhead):
    budget = SyntheticCharacterTokenizer(); budget.overhead = overhead
    with pytest.raises(RetrievalError, match='chunk_title_budget_exhausted'):
        chunk_job(job(title=title), budget=budget, config=config(24))


def test_indivisible_token_exhausts_remaining_budget_explicitly():
    class SyntheticWholeSentence(SyntheticCharacterTokenizer):
        def offsets(self, text): return [(0, len(text))]
    with pytest.raises(RetrievalError, match='chunk_token_budget_exhausted'):
        chunk_job(job('x'*100), budget=SyntheticWholeSentence(), config=config(24))


@pytest.mark.parametrize('attr,value', [('tokenizer_id', ''), ('revision', ' '), ('max_tokens', True), ('max_tokens', 0), ('count', None), ('offsets', 7)])
def test_invalid_tokenizer_metadata(attr, value):
    budget = SyntheticCharacterTokenizer(); setattr(budget, attr, value)
    with pytest.raises(RetrievalError, match='chunk_tokenizer_metadata'):
        chunk_job(job(), budget=budget)


@pytest.mark.parametrize('value', [0, -1, True, '12', None])
def test_invalid_token_counts(value):
    budget = SyntheticCharacterTokenizer(); budget.count = lambda *args, **kwargs: value
    with pytest.raises(RetrievalError, match='chunk_invalid_token_count'):
        chunk_job(job(), budget=budget)


@pytest.mark.parametrize('offsets', [[], None, [(0, 1000)], [(1, 1)], [(-1, 1)], [(1, 3), (0, 1)], [(0, True)], [(0, 1, 2)], ['0:1']])
def test_invalid_offsets(offsets):
    budget = SyntheticCharacterTokenizer(); budget.offsets = lambda text: offsets
    with pytest.raises(RetrievalError, match='chunk_invalid_token_offsets'):
        chunk_job(job(), budget=budget)


def test_repeated_byte_style_offsets_are_valid():
    class SyntheticRepeated(SyntheticCharacterTokenizer):
        def offsets(self, text):
            return [(i, i+1) for i in range(len(text)) for _ in range(2)]
    chunks = chunk_job(job('中文'*40), budget=SyntheticRepeated(), config=config(24))
    assert chunks[-1].span.end == 80


def test_tokenizer_exception_is_fixed_code_no_source_text():
    class Broken(SyntheticCharacterTokenizer):
        def offsets(self, text): raise RuntimeError('private source text')
    with pytest.raises(RetrievalError, match='^chunk_tokenizer_error$'):
        chunk_job(job(), budget=Broken())


def test_tokenizer_mutation_is_rejected():
    class Mutating(SyntheticCharacterTokenizer):
        def count(self, text, *, kind):
            self.revision = 'changed'
            return len(text)+2
    with pytest.raises(RetrievalError, match='chunk_tokenizer_changed'):
        chunk_job(job(), budget=Mutating())


def test_candidate_and_config_bypass_are_revalidated():
    candidate = job().model_copy(update={'jd_sha256': '0'*64})
    with pytest.raises(ValidationError):
        chunk_job(candidate, budget=SyntheticCharacterTokenizer())
    bad_config = config().model_copy(update={'overlap_tokens': 40})
    with pytest.raises(ValidationError):
        chunk_job(job(), budget=SyntheticCharacterTokenizer(), config=bad_config)


def hit(candidate=None, *, ordinal=0, score=0.5, rank=1):
    chunks = chunk_job(job() if candidate is None else candidate, budget=SyntheticCharacterTokenizer(), config=config(16, 0))
    return ChunkHit(chunk=chunks[ordinal], score=score, rank=rank)


def test_best_chunk_aggregation_dedups_jobs_does_not_sum():
    a = job(); b = job(job_id=2)
    result = aggregate_chunks([hit(a, score=0.4), hit(a, ordinal=1, score=0.4, rank=2), hit(b, score=0.7, rank=3)], recall_k=10)
    assert [item.key.job_id for item in result.hits] == [2, 1]
    assert [item.score for item in result.hits] == [0.7, 0.4]
    assert [item.rank for item in result.hits] == [1, 2]
    assert [item.rank for item in result.evidence] == [1, 2]
    assert result.evidence[1].chunk.ordinal == 0


def test_job_identity_ties_stable_across_input_order():
    a, b = hit(job(job_id=2), score=0.7), hit(job(job_id=1), score=0.7)
    first = aggregate_chunks([a, b], recall_k=10)
    second = aggregate_chunks([b, a], recall_k=10)
    assert first == second
    assert [item.key.job_id for item in first.hits] == [1, 2]


def test_aggregation_separates_datasets_and_limits_recall():
    result = aggregate_chunks([hit(job(dataset_id='z'), score=0.9), hit(job(dataset_id='a'), score=0.9), hit(job(job_id=2), score=-0.3)], recall_k=2)
    assert [item.key.dataset_id for item in result.hits] == ['a', 'z']
    assert all(item.channel == 'dense' for item in result.hits)


def test_aggregation_keeps_negative_and_zero_scores_not_invented_threshold():
    result = aggregate_chunks([hit(score=-0.9), hit(job(job_id=2), score=0.0)], recall_k=10)
    assert [item.score for item in result.hits] == [0.0, -0.9]


def test_aggregation_empty():
    assert aggregate_chunks([], recall_k=10).model_dump() == {'hits': [], 'evidence': []}


def test_aggregation_does_not_mutate_or_alias_inputs():
    original = hit(); before = original.model_dump_json()
    result = aggregate_chunks([original], recall_k=10)
    result.evidence[0].chunk.mapping.clear()
    assert original.model_dump_json() == before


@pytest.mark.parametrize('value', [0, -1, True, 1.5, 10001])
def test_invalid_aggregation_recall(value):
    with pytest.raises(RetrievalError, match='chunk_invalid_recall_k'):
        aggregate_chunks([], recall_k=value)


def test_aggregation_rejects_conflicting_job_hash():
    first = hit(); second = hit(job('不同内容。'))
    with pytest.raises(RetrievalError, match='chunk_conflicting_job_hash'):
        aggregate_chunks([first, second], recall_k=10)


def test_aggregation_rejects_conflicting_chunk_identity():
    first = hit(); second = first.model_copy(deep=True)
    second.chunk.span = second.chunk.span.model_copy(update={'start': second.chunk.span.start+1, 'end': second.chunk.span.end+1})
    with pytest.raises(RetrievalError, match='chunk_conflicting_identity'):
        aggregate_chunks([first, second], recall_k=10)


def test_aggregation_nan_model_copy_bypass_rejected():
    with pytest.raises(ValidationError):
        aggregate_chunks([hit().model_copy(update={'score': float('nan')})], recall_k=10)


def test_aggregation_bad_input_types():
    with pytest.raises(TypeError): aggregate_chunks([{}], recall_k=10)


class SyntheticWordTokenizer(SyntheticCharacterTokenizer):
    """Sparse word offsets, used to verify gaps are retained rather than lost."""
    tokenizer_id = 'synthetic-word-test-only'

    def count(self, text, *, kind):
        import re
        return len(re.findall(r'\S+', text))+4

    def offsets(self, text):
        import re
        return [(match.start(), match.end()) for match in re.finditer(r'\S+', text)]


def test_sparse_word_offsets_keep_whole_tail_and_original_spacing():
    candidate = job('One two three four five six seven.\nLast special requirement: Kubernetes.')
    budget = SyntheticWordTokenizer()
    chunks = chunk_job(candidate, budget=budget, config=config(9, 1))
    check_mapping(candidate, chunks, budget, 9)
    assert chunks[-1].span.quote.endswith('Kubernetes.')
    assert all(not chunk.encoding_text.endswith(' ') for chunk in chunks)
    covered = set()
    for chunk in chunks:
        covered.update(range(chunk.span.start, chunk.span.end))
    assert {i for i, char in enumerate(candidate.job.description) if not char.isspace()} <= covered


def test_repeated_unicode_offsets_do_not_exceed_requested_overlap_tokens():
    class SyntheticBytes(SyntheticCharacterTokenizer):
        def offsets(self, text):
            return [(i, i+1) for i in range(len(text)) for _ in range(3)]
    chunks = chunk_job(job('甲'*70), budget=SyntheticBytes(), config=config(18, 4))
    assert all(a.span.end-b.span.start <= 1 for a, b in zip(chunks, chunks[1:]))
    assert chunks[-1].span.end == 70


def test_randomized_structural_coverage_and_mapping_is_deterministic():
    import random
    randomizer = random.Random(731)
    budget = SyntheticCharacterTokenizer()
    for _ in range(30):
        text = ''.join(randomizer.choice('中文Python接口。；!? \n') for _ in range(250))+'末尾要求Kubernetes。'
        candidate = job(text)
        cfg = config(randomizer.choice([16, 24, 40]), randomizer.choice([0, 1, 3, 6]))
        first = chunk_job(candidate, budget=budget, config=cfg)
        assert first == chunk_job(candidate, budget=budget, config=cfg)
        check_mapping(candidate, first, budget, cfg.max_tokens)
        covered = set()
        for chunk in first:
            for mapping in chunk.mapping:
                if mapping.field == 'description':
                    covered.update(range(mapping.source_start, mapping.source_end))
        assert {i for i, char in enumerate(candidate.job.description) if not char.isspace()} <= covered
        assert all(b.span.end > a.span.end for a, b in zip(first, first[1:]))
        assert first[-1].span.end == len(candidate.job.description)
