"""Authored synthetic failures; not the unsaved historical model response."""
from datetime import datetime, timezone
import hashlib
import json

import pytest

from job_radar.report_analysis import (
    SYSTEM_PROMPT, analyze_job, build_analysis_request, validate_analysis,
)
from job_radar.report_models import (
    AnalysisClaim, AnalysisResponse, CallAudit, MatchAnalysisDraft, PROMPT_VERSION, ReportError,
)
from job_radar.report_evidence import build_evidence_packet
from job_radar.report_engine import generate_match_report
from job_radar.report_diagnostics import (
    AnalysisRejection, FileRejectionSink, build_rejection, MAX_REJECTED_DRAFT_BYTES,
)
from job_radar.report_prompt_v2 import CANDIDATE_PROMPT_VERSION, SYSTEM_PROMPT_V2
from job_radar.report_synthetic import synthetic_inputs, synthetic_manifest
from job_radar.rag_index import DerivedIndex
from job_radar.rag_engine import match_jobs_indexed
from job_radar.rag_models import canonical_hash

NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


@pytest.fixture
def case(tmp_path):
    doc, request = synthetic_inputs(descriptions=[
        '要求掌握 Python 和 FastAPI。熟悉 Docker 加分。',
        '要求掌握 Python 或 Java。熟悉 Git 加分。',
        '要求掌握 Python 和 SQL。熟悉 Redis 加分。',
    ])
    index = DerivedIndex(tmp_path / 'index')
    index.update(request.jobs, manifest=synthetic_manifest(request.jobs))
    result = match_jobs_indexed(request, index=index)
    packet = build_evidence_packet(request, result, doc)
    key = packet.selected_keys[0]
    jd = next(e for e in packet.evidence if e.key == key and e.path == 'job.description')
    scope = next(e for e in packet.evidence if e.kind == 'scope')
    claim = AnalysisClaim(
        claim_id='c1', kind='unknown', subject='Docker', status='unknown',
        text='简历未提及 Docker 使用经历。',
        requirement_kind='bonus', evidence_ids=[jd.evidence_id, scope.evidence_id,next(e.evidence_id for e in packet.evidence if e.kind=='rule' and e.key==key and 'Docker' in json.loads(e.value_json).get('alternatives',[]))],
    )
    draft = MatchAnalysisDraft(key=key, claims=[claim])
    return doc, request, result, packet, draft


def altered(draft, **changes):
    obj = draft.model_dump()
    obj['claims'][0].update(changes)
    return MatchAnalysisDraft.model_validate(obj)


class ScriptedClient:
    calls = 0
    halted = None

    def __init__(self, raw):
        self.raw = raw

    def complete(self, request):
        self.calls += 1
        return AnalysisResponse(
            status='completed', text=self.raw,
            audit=CallAudit(provider='scripted', model='no-model', attempts=1,
                            input_tokens=10, output_tokens=20, total_tokens=30))

    def halt(self, code='analysis_validation_failed'):
        self.halted = code


@pytest.mark.parametrize('subject,actual,reason,expected', [
    ('Docker', 'required', 'kind_mismatch', ['bonus']),
    ('Python', 'bonus', 'kind_mismatch', ['required']),
    ('FastAPI', 'mention', 'kind_mismatch', ['required']),
    ('Python 和 FastAPI', 'required', 'subject_not_matched', []),
    ('Python接口开发能力', 'required', 'subject_not_matched', []),
])
def test_exact_failure_code_and_specific_comparison(case, subject, actual, reason, expected):
    _, _, _, packet, draft = case
    bad = altered(draft, subject=subject, requirement_kind=actual)
    raw = bad.model_dump_json(indent=2)
    with pytest.raises(ReportError, match='^analysis_requirement_kind_mismatch$'):
        validate_analysis(bad, packet, bad.key)
    event = build_rejection(raw, 'analysis_requirement_kind_mismatch', packet, bad.key)
    comparison = event.requirement_comparisons[0]
    assert event.raw_draft_text == raw
    assert event.parsed_draft == bad.model_dump(mode='json')
    assert comparison.subject == subject
    assert comparison.actual_requirement_kind == actual
    assert comparison.expected_requirement_kinds == expected
    assert comparison.outcome == reason
    assert comparison.claim_id == 'c1'
    assert event.draft_sha256 == hashlib.sha256(raw.encode()).hexdigest()


def test_diagnostic_preserves_complete_multi_claim_text_and_ids(case, tmp_path):
    _, _, _, packet, draft = case
    c2 = draft.claims[0].model_dump()
    c2.update(claim_id='c2', kind='recommendation', status='advisory',
              text='建议先补充Docker材料，再考虑是否学习。', related_claim_ids=['c1'])
    raw = json.dumps({'key':draft.key.model_dump(), 'claims':[
        altered(draft, requirement_kind='required').claims[0].model_dump(), c2]},
        ensure_ascii=False, indent=4) + '\n'
    sink = FileRejectionSink(tmp_path/'private-rejections')
    client = ScriptedClient(raw)
    result = analyze_job(packet, draft.key, client=client, rejection_sink=sink)
    assert result.status == 'failed' and result.draft is None
    assert result.error_code == 'analysis_requirement_kind_mismatch'
    assert client.calls == 1 and client.halted == 'analysis_validation_failed'
    assert result.audit.total_tokens == 30
    saved = AnalysisRejection.model_validate_json(sink.saved_paths[0].read_text())
    assert saved.raw_draft_text == raw
    assert len(saved.parsed_draft['claims']) == 2
    assert saved.parsed_draft['claims'][1]['related_claim_ids'] == ['c1']
    assert saved.parsed_draft['claims'][1]['evidence_ids'] == c2['evidence_ids']
    assert saved.requirement_comparisons[0].matching_rules[0]['evidence_ids']
    assert not sink.saved_paths[0].stat().st_mode & 0o077
    assert not sink.directory.stat().st_mode & 0o077


@pytest.mark.parametrize('raw', ['{bad json', '{}', 'not JSON'])
def test_invalid_json_or_schema_saved_exactly_without_repair(case, raw):
    *_, packet, draft = case
    events = []
    result = analyze_job(packet, draft.key, client=ScriptedClient(raw), rejection_sink=events.append)
    assert result.status == 'failed' and result.draft is None
    assert events[0].raw_draft_text == raw
    assert events[0].parsed_draft is None


def test_default_path_no_diagnostic_persistence(case, tmp_path):
    *_, packet, draft = case
    before = set(tmp_path.rglob('*'))
    result = analyze_job(packet, draft.key, client=ScriptedClient(
        altered(draft, requirement_kind='required').model_dump_json()))
    assert result.error_code == 'analysis_requirement_kind_mismatch'
    assert set(tmp_path.rglob('*')) == before


def test_success_never_saves_rejection(case):
    *_, packet, draft = case
    events = []
    result = analyze_job(packet, draft.key, client=ScriptedClient(draft.model_dump_json()),
                         rejection_sink=events.append)
    assert result.status == 'completed' and events == []


def test_sink_failure_stops_with_fixed_error_no_body_in_report(case):
    doc, req, retrieval, _, draft = case
    def broken_sink(event):
        raise RuntimeError('PRIVATE-DIAGNOSTIC-ERROR')
    client = ScriptedClient(altered(draft, requirement_kind='required').model_dump_json())
    report = generate_match_report(req, retrieval, doc, report_id='failure',
        generated_at=NOW, client=client, rejection_sink=broken_sink)
    assert client.calls == 1
    assert report.analyses[0].error_code == 'analysis_diagnostic_write_failed'
    assert all(a.status == 'not_analyzed' for a in report.analyses[1:])
    assert 'PRIVATE-DIAGNOSTIC-ERROR' not in report.model_dump_json()


def test_engine_wires_sink_before_fail_stop_and_does_not_leak_draft(case, tmp_path):
    doc, req, retrieval, packet, draft = case
    hashes = (canonical_hash(req), canonical_hash(retrieval), canonical_hash(doc))
    bad = altered(draft, requirement_kind='required')
    sink = FileRejectionSink(tmp_path/'rejections')
    client = ScriptedClient(bad.model_dump_json())
    report = generate_match_report(req, retrieval, doc, report_id='rejection',
        generated_at=NOW, client=client, rejection_sink=sink)
    assert report.status == 'failed' and report.analyses[0].draft is None
    assert client.calls == 1 and len(sink.saved_paths) == 1
    assert all(a.status == 'not_analyzed' for a in report.analyses[1:])
    assert 'raw_draft_text' not in report.model_dump_json()
    assert report.packet.retrieval == retrieval and report.review_status == 'unconfirmed'
    assert hashes == (canonical_hash(req), canonical_hash(retrieval), canonical_hash(doc))


def test_corrected_authored_draft_passes_without_weakening_validator(case):
    *_, packet, draft = case
    bad = altered(draft, requirement_kind='required')
    with pytest.raises(ReportError, match='analysis_requirement_kind_mismatch'):
        validate_analysis(bad, packet, bad.key)
    # Author fixes only input category; production code never repairs a draft.
    assert validate_analysis(draft, packet, draft.key) == draft
    assert draft.claims[0].status == 'unknown' and draft.claims[0].requirement_kind == 'bonus'
    with pytest.raises(ReportError, match='analysis_requirement_kind_mismatch'):
        validate_analysis(bad, packet, bad.key)


@pytest.mark.parametrize('subject,category', [('Python','required'),('FastAPI','required')])
def test_atomic_supported_skill_examples(case, subject, category):
    *_, packet, draft = case
    row = next(r for r in packet.retrieval.matching.results if r.key == draft.key)
    rule = next(s for s in row.rules.skills if s.alternatives == [subject])
    rule_e = next(e for e in packet.evidence if e.kind=='rule' and e.key==draft.key
                  and json.loads(e.value_json)==rule.model_dump(mode='json'))
    resume = next(e for e in packet.evidence if e.kind=='resume' and e.resume_ref in rule.resume_evidence)
    jd = next(e for e in packet.evidence if e.key==draft.key and e.path=='job.description')
    claim = AnalysisClaim(claim_id='c1',kind='strength',subject=subject,status='supported',
        text=f'课程项目材料记录了{subject}使用。',requirement_kind=category,
        evidence_ids=[jd.evidence_id,resume.evidence_id,rule_e.evidence_id])
    assert validate_analysis(MatchAnalysisDraft(key=draft.key,claims=[claim]),packet,draft.key)


def test_unknown_requirement_kind_now_rejected_but_diagnostic_preserves_value(case):
    *_, packet, draft = case
    valid = altered(draft, requirement_kind='unknown')
    with pytest.raises(ReportError, match='analysis_requirement_kind_mismatch'):
        validate_analysis(valid,packet,valid.key)
    event = build_rejection(valid.model_dump_json(),'analysis_requirement_kind_mismatch',packet,valid.key)
    assert event.requirement_comparisons[0].outcome == 'unknown_not_checked'
    assert event.requirement_comparisons[0].expected_requirement_kinds == ['bonus']


def test_sink_does_not_overwrite_or_follow_preexisting_file(case, tmp_path):
    *_, packet, draft = case
    directory = tmp_path/'sink'
    sink = FileRejectionSink(directory)
    event = build_rejection(draft.model_dump_json(),'analysis_requirement_kind_mismatch',packet,draft.key)
    target = tmp_path/'protected';target.write_text('user data')
    (directory/'rejected-0001.json').symlink_to(target)
    with pytest.raises(FileExistsError):sink(event)
    assert target.read_text()=='user data'
    assert sink.saved_paths == []
    with pytest.raises(FileExistsError):FileRejectionSink(directory)


def test_complete_text_limit_is_explicit_not_truncated(case):
    *_, packet, draft = case
    with pytest.raises(ReportError,match='analysis_diagnostic_output_limit'):
        build_rejection('x'*(MAX_REJECTED_DRAFT_BYTES+1),'analysis_output_limit',packet,draft.key)


def test_sink_detached_payload_and_multiple_events(case, tmp_path):
    *_, packet, draft = case
    event = build_rejection(draft.model_dump_json(),'analysis_requirement_kind_mismatch',packet,draft.key)
    sink = FileRejectionSink(tmp_path/'sink');sink(event);sink(event)
    assert len(sink.saved_paths)==2
    assert sink.saved_paths[0].read_bytes()==sink.saved_paths[1].read_bytes()
    event.parsed_draft['claims'][0]['text']='mutated after write'
    assert 'mutated after write' not in sink.saved_paths[0].read_text()


def test_v3_active_and_original_v1_v2_retained(case):
    *_, packet, draft = case
    assert PROMPT_VERSION == 'match-analysis-prompt-v3'
    assert CANDIDATE_PROMPT_VERSION == 'match-analysis-prompt-v2'
    from job_radar.report_prompt_v1 import SYSTEM_PROMPT_V1
    assert SYSTEM_PROMPT_V2.startswith(SYSTEM_PROMPT_V1)
    from job_radar.report_prompt_v3 import SYSTEM_PROMPT_V3
    assert SYSTEM_PROMPT == SYSTEM_PROMPT_V3
    request=build_analysis_request(packet,draft.key)
    assert request.system_prompt == SYSTEM_PROMPT and request.prompt_version == PROMPT_VERSION
    for phrase in ['单个名称','二者独立','requirement_kind="bonus", status="unknown"',
                   '相同规范subject','多条不同kind','不把Java缺证据写成必补缺口']:
        assert phrase in SYSTEM_PROMPT_V2


def test_diagnostic_never_embedded_in_external_wire(case):
    *_, packet, draft = case
    before=build_analysis_request(packet,draft.key)
    events=[]
    analyze_job(packet,draft.key,client=ScriptedClient(
        altered(draft,requirement_kind='required').model_dump_json()),rejection_sink=events.append)
    after=build_analysis_request(packet,draft.key)
    assert before==after and 'raw_draft_text' not in after.packet_json


def test_multiple_matching_kinds_are_reported_without_guessing_one(tmp_path):
    doc, req = synthetic_inputs(descriptions=['要求掌握 Python。Python 加分。'])
    index = DerivedIndex(tmp_path/'multiple-index')
    index.update(req.jobs,manifest=synthetic_manifest(req.jobs))
    packet=build_evidence_packet(req,match_jobs_indexed(req,index=index),doc)
    key=packet.selected_keys[0]
    jd=next(e for e in packet.evidence if e.key==key and e.path=='job.description')
    scope=next(e for e in packet.evidence if e.kind=='scope')
    draft=MatchAnalysisDraft(key=key,claims=[AnalysisClaim(
        claim_id='c1',kind='unknown',subject='Python',status='unknown',
        text='能力仍待复核。',requirement_kind='mention',evidence_ids=[jd.evidence_id,scope.evidence_id])])
    event=build_rejection(draft.model_dump_json(),'analysis_requirement_kind_mismatch',packet,key)
    comparison=event.requirement_comparisons[0]
    assert comparison.expected_requirement_kinds==['required','bonus']
    assert len(comparison.matching_rules)==2 and comparison.outcome=='kind_mismatch'


def test_schema_invalid_claim_value_still_preserved_in_full_raw(case):
    *_, packet, draft = case
    data=draft.model_dump();data['claims'][0]['requirement_kind']='unexpected-category'
    raw=json.dumps(data,ensure_ascii=False)
    events=[]
    result=analyze_job(packet,draft.key,client=ScriptedClient(raw),rejection_sink=events.append)
    assert result.error_code=='analysis_schema_error'
    assert events[0].raw_draft_text==raw and events[0].parsed_draft is None


def test_diagnostics_still_capture_cross_job_failure_without_cross_job_rule_scope(case):
    *_, packet, draft = case
    other=next(e for e in packet.evidence if e.key and e.key!=draft.key and e.kind=='job')
    bad=altered(draft,evidence_ids=[other.evidence_id],requirement_kind='bonus')
    events=[]
    result=analyze_job(packet,draft.key,client=ScriptedClient(bad.model_dump_json()),rejection_sink=events.append)
    assert result.error_code=='analysis_evidence_scope'
    assert events[0].parsed_draft==bad.model_dump(mode='json')
    assert events[0].requirement_comparisons[0].expected_requirement_kinds==['bonus']

