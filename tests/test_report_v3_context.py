"""Full original-block context is preserved before finite evidence acceptance.

These authored source fixtures are not generated model responses or a general
language-understanding benchmark. They isolate the reviewed cross-sentence gap.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from job_radar.rag_models import canonical_hash
from job_radar.report_analysis import validate_analysis
from job_radar.report_models import EvidenceRecord, MatchAnalysisDraft, ReportError
from job_radar.resume_profile import EvidenceRef

_SPEC = importlib.util.spec_from_file_location(
    'context_evaluation', Path(__file__).parents[1] / 'examples/stage4_v3_offline_evaluate.py')
ev = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ev)


@pytest.fixture
def packet(tmp_path):
    return ev.make_case(tmp_path / 'index')[3]


def original_record(source, quote=None):
    quote = source if quote is None else quote
    ref = EvidenceRef(page=1, block_id='p001-b002', quote=quote)
    data = dict(kind='resume', path='document.pages[0].blocks[p001-b002]',
                key=None, source_sha256='a' * 64, quote=quote,
                resume_ref=ref.model_dump(mode='json'), job_span=None,
                value_json=json.dumps({'block_text': source}, ensure_ascii=False))
    return EvidenceRecord(evidence_id='e' + canonical_hash(data)[:24], **data)


def gap_with_source(packet, source, quote=None):
    packet = packet.model_copy(deep=True)
    key = packet.selected_keys[0]
    draft = MatchAnalysisDraft(key=key, claims=[ev.claim(packet, key, 'Docker', 'unknown')])
    record = original_record(source, quote)
    packet.evidence.append(record)
    draft = ev.alter(draft, kind='skill_gap', status='explicit_gap',
                     text='简历注明从未使用 Docker。',
                     evidence_ids=draft.claims[0].evidence_ids + [record.evidence_id])
    return packet, draft


@pytest.mark.parametrize('source', [
    '同事的原话如下。从未使用 Docker。',
    '以下是假设情形。从未使用 Docker。',
    '如果没有项目经验；从未使用 Docker。',
    '朋友的经历如下。本人未使用 Docker。',
    '这是一段引用。“从未使用 Docker。”',
    'Suppose this scenario. I have not used Docker.',
])
def test_cross_sentence_framing_does_not_certify_explicit_gap(packet, source):
    p, draft = gap_with_source(packet, source)
    with pytest.raises(ReportError, match='^analysis_gap_requires_negative_evidence$'):
        validate_analysis(draft, p, draft.key)


@pytest.mark.parametrize('source', [
    '同事的原话如下。从未使用 Docker。',
    '以下是假设情形。从未使用 Docker。',
    '如果没有项目经验；从未使用 Docker。',
])
def test_truncated_negative_quote_cannot_hide_original_block_frame(packet, source):
    p, draft = gap_with_source(packet, source, quote='从未使用 Docker。')
    with pytest.raises(ReportError, match='^analysis_gap_requires_negative_evidence$'):
        validate_analysis(draft, p, draft.key)


@pytest.mark.parametrize('source', [
    '从未使用 Docker。',
    '个人说明：从未使用 Docker。',
    '课程项目使用 Python 开发接口。个人说明：从未使用 Docker。',
    '课程项目使用 Python 开发接口。本人未使用 Docker。',
])
def test_direct_personal_negative_survives_ordinary_course_context(packet, source):
    p, draft = gap_with_source(packet, source)
    assert validate_analysis(draft, p, draft.key) == draft


@pytest.mark.parametrize('source', [
    '同事的原话如下。计划学习 Docker。',
    '以下是假设情形。计划学习 Docker。',
    '如果没有项目经验；计划学习 Docker。',
])
def test_ambiguous_full_block_is_not_certified_as_unknown_absence(packet, source):
    p = packet.model_copy(deep=True)
    key = p.selected_keys[0]
    p.evidence.append(original_record(source))
    draft = MatchAnalysisDraft(key=key, claims=[ev.claim(p, key, 'Docker', 'unknown')])
    with pytest.raises(ReportError, match='^analysis_unknown_evidence_conflict$'):
        validate_analysis(draft, p, key)


def test_other_person_usage_frame_does_not_certify_supported(tmp_path):
    _, _, _, p = ev.make_case(tmp_path / 'other',
                              text='同事的原话如下。项目使用 Python。')
    key = p.selected_keys[0]
    draft = MatchAnalysisDraft(key=key, claims=[ev.claim(p, key, 'Python', 'supported')])
    with pytest.raises(ReportError, match='^analysis_resume_support_mismatch$'):
        validate_analysis(draft, p, key)


def test_s3_course_plan_and_personal_sql_negative_remain_valid(tmp_path):
    _, _, result, p = ev.make_case(tmp_path / 's3', ev.SAMPLES['S3'])
    row = next(row for row in result.matching.results if row.key.job_id == 3)
    draft = MatchAnalysisDraft(key=row.key,
                              claims=[ev.claim(p, row.key, 'SQL', 'explicit_gap')])
    assert validate_analysis(draft, p, row.key) == draft
