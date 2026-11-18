"""Offline regression of retained real response; v3 finite output contract (not semantic negation)."""
import hashlib
import json
from pathlib import Path

import pytest

from job_radar.report_analysis import validate_analysis
from job_radar.report_models import EvidencePacket, MatchAnalysisDraft, ReportError

FIXTURE = Path(__file__).parent / 'fixtures/stage4_negation'


@pytest.fixture
def case():
    packet = EvidencePacket.model_validate_json((FIXTURE / 's1-j1-packet.json').read_text())
    raw = (FIXTURE / 'real-v2-rejected-draft.json').read_text()
    assert hashlib.sha256(raw.encode()).hexdigest() == json.loads(
        (FIXTURE / 'provenance.json').read_text())['response_sha256']
    draft = MatchAnalysisDraft.model_validate_json(raw)
    return packet, draft


def one_claim(case, text=None, **changes):
    packet, draft = case
    obj = draft.claims[0].model_dump()
    if text is not None:
        obj['text'] = text
    obj.update(changes)
    return packet, MatchAnalysisDraft.model_validate({'key': draft.key, 'claims': [obj]})


@pytest.mark.parametrize('index', [0, 1])
def test_exact_real_c1_and_c2_policy_rejected_semantically_not_inflated(case, index):
    packet, draft = case
    exact = MatchAnalysisDraft(key=draft.key, claims=[draft.claims[index]])
    with pytest.raises(ReportError, match='^analysis_unsupported_experience$'):
        validate_analysis(exact, packet, draft.key)


@pytest.mark.parametrize('text', [
    '课程项目使用记录不代表已确认掌握程度或生产经验。',
    '课程项目不意味着生产经验。',
    '课程项目不等于正式工作。',
    '材料未明确说明熟练度或使用年限。',
    '材料未证实生产经验。',
    '材料没有确认生产经验。',
    '材料不能据此推断生产经验。',
    '该记录不是正式工作。',
    '该记录并非生产经验。',
    '材料没有生产经验。',
    '材料不具备生产经验。',
    '材料不熟练。',
    'The course project does not imply production experience.',
    'There is no evidence of production experience.',
    'The candidate is not proficient.',
])
def test_semantically_noninflating_limitations_are_v3_contract_negatives(case, text):
    packet, draft = one_claim(case, text)
    with pytest.raises(ReportError, match='^analysis_unsupported_experience$'):
        validate_analysis(draft, packet, draft.key)


@pytest.mark.parametrize('text', [
    '具备生产经验。', '已有一年生产经验。', 'Python熟练。', '属于正式工作。',
    '是资深Python专家。', 'Built a production system.', 'Has 3 years of experience.',
    '没有材料证明，但具备生产经验。',
    '不代表生产经验，但是熟练Python。',
    '不代表生产经验；具备生产经验。',
    '不代表生产经验，具备生产经验。',
    '不代表生产经验\n具备生产经验。',
    '不代表生产经验且熟练Python。',
    '不代表生产经验同时熟练Python。',
    '不代表生产经验，不过正式工作已完成。',
    '不代表生产经验也已具备生产经验。',
    '不代表生产经验，并且拥有生产经验。',
    '不代表生产经验；生产经验未知但熟练Python。',
    'Python专家，不代表生产经验。',
    '不是没有生产经验。', '并非不熟练。', '不能否认生产经验。',
    '不代表不具备生产经验。', '未证明没有生产经验。', '并不是没有生产经验。',
    '课程项目，不代表其他事情，具备生产经验。',
    'It is not production experience but the candidate is proficient.',
    'Not production experience and proficient.',
    'Not no production experience.',
    '材料不代表他已经具备很丰富的生产经验。',  # unrecognized scope fails closed
])
def test_true_or_ambiguous_inflation_still_rejected(case, text):
    packet, draft = one_claim(case, text)
    with pytest.raises(ReportError, match='^analysis_unsupported_experience$'):
        validate_analysis(draft, packet, draft.key)


def test_negation_in_text_does_not_exempt_inflated_subject(case):
    packet, draft = one_claim(case, '材料不代表生产经验。', subject='生产经验',
        kind='match_interpretation', requirement_kind='unknown')
    # The preceding source/positive support guard remains authoritative.
    with pytest.raises(ReportError, match='^analysis_requirement_kind_mismatch$'):
        validate_analysis(draft, packet, draft.key)


def test_all_other_guards_still_apply_to_retained_full_real_response(case):
    packet, draft = case
    # All evidence checks precede any output text check: c4/c5 conflict first.
    with pytest.raises(ReportError, match='^analysis_unknown_evidence_conflict$'):
        validate_analysis(draft, packet, draft.key)


@pytest.mark.parametrize('changes,error', [
    ({'requirement_kind': 'bonus'}, 'analysis_requirement_kind_mismatch'),
    ({'text': '录用概率90%。'}, 'analysis_forbidden_probability'),
    ({'evidence_ids': ['e' + '0' * 24]}, 'analysis_evidence_scope'),
])
def test_negation_does_not_bypass_other_guards(case, changes, error):
    packet, draft = one_claim(case, **({'text':'课程项目使用 Python 开发接口。'} | changes))
    with pytest.raises(ReportError, match='^' + error + '$'):
        validate_analysis(draft, packet, draft.key)
