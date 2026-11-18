"""Serial freeze: deterministic draft JSON round-trip, no I/O or provider."""
import json
import pytest
from pydantic import ValidationError
from job_radar.report_models import *
from job_radar.report_core import parse_analysis

def draft():
    return dict(key=dict(dataset_id='synthetic',job_id=1),claims=[dict(claim_id='c1',kind='unknown',subject='Docker',status='unknown',text='当前材料未提供Docker能力证据，能力未知。',evidence_ids=['e'+'a'*24])])

def test_offline_draft_closed_loop():
    value=parse_analysis(json.dumps(draft(),ensure_ascii=False))
    assert value==parse_analysis(value.model_dump_json())
    assert value.claims[0].status=='unknown'

@pytest.mark.parametrize('text',['','{}','null','[]','{"key":1,"key":2}','{"x":NaN}','```json\n{}\n```'])
def test_bad_json_or_schema(text):
    with pytest.raises(ReportError): parse_analysis(text)

@pytest.mark.parametrize('patch',[{'score':100},{'claims':[]},{'key':{'dataset_id':'s','job_id':True}}])
def test_extra_and_strict_fields(patch):
    with pytest.raises(ValidationError): MatchAnalysisDraft.model_validate({**draft(),**patch})

def test_recommendation_requires_finding():
    d=draft();d['claims'][0].update(kind='recommendation',status='advisory')
    with pytest.raises(ValidationError): MatchAnalysisDraft.model_validate(d)

def test_no_probability_field():
    d=draft();d['claims'][0]['hire_probability']=0.8
    with pytest.raises(ValidationError): MatchAnalysisDraft.model_validate(d)

def test_limit_and_invalid_unicode():
    with pytest.raises(ReportError,match='analysis_output_limit'):parse_analysis(' '*300,max_output_bytes=256)
    with pytest.raises(ReportError):parse_analysis('\ud800')

def test_audit_usage():
    with pytest.raises(ValidationError):CallAudit(provider='p',model='m',attempts=1,input_tokens=3)
    with pytest.raises(ValidationError):CallAudit(provider='p',model='m',attempts=1,input_tokens=3,output_tokens=2,total_tokens=8)

def test_serial_full_report_closed_loop(tmp_path):
    from datetime import datetime,timezone
    from job_radar.report_synthetic import synthetic_inputs,synthetic_manifest
    from job_radar.rag_engine import match_jobs_indexed
    from job_radar.rag_index import DerivedIndex
    from job_radar.report_core import assemble_report
    doc,req=synthetic_inputs(descriptions=['要求掌握 Python。熟悉 Docker 加分。'])
    idx=DerivedIndex(tmp_path/'index');idx.update(req.jobs,manifest=synthetic_manifest(req.jobs))
    result=match_jobs_indexed(req,index=idx)
    key=result.matching.results[0].key
    from job_radar.report_evidence import build_evidence_packet
    packet=build_evidence_packet(req,result,doc,config=AnalysisConfig(top_n=1))
    d=draft();d['key']=key.model_dump()
    rule=next(e for e in packet.evidence if e.kind=='rule' and e.key==key and 'Docker' in json.loads(e.value_json).get('alternatives',[]))
    d['claims'][0].update(text='简历未提及 Docker 使用经历。',requirement_kind='bonus',evidence_ids=[e.evidence_id for e in packet.evidence if (e.kind=='scope' or (e.key==key and e.path=='job.description'))]+[rule.evidence_id])
    parsed=parse_analysis(json.dumps(d))
    analyses=[JobAnalysis(key=r.key,status='completed' if r.key==key else 'not_analyzed',draft=parsed if r.key==key else None) for r in result.matching.results]
    report=assemble_report(packet,analyses,report_id='synthetic-contract-only',generated_at=datetime(2026,11,14,tzinfo=timezone.utc))
    assert MatchReport.model_validate_json(report.model_dump_json())==report
    assert report.packet.retrieval==result
    assert report.review_status=='unconfirmed'

@pytest.mark.parametrize('mutate',[lambda d:d['claims'].append(dict(d['claims'][0])),lambda d:d['claims'][0].update(related_claim_ids=['c9']),lambda d:d['claims'][0].update(status='supported')])
def test_claim_relationships(mutate):
    d=draft();mutate(d)
    with pytest.raises(ValidationError):MatchAnalysisDraft.model_validate(d)

def test_evidence_id_recomputed_not_arbitrary():
    with pytest.raises(ValidationError,match='evidence_id_mismatch'):
        EvidenceRecord(evidence_id='e'+'a'*24,kind='scope',path='profile',source_sha256='a'*64,value_json='{}')

@pytest.mark.parametrize('text',['{"x":1e999}','{"x":-1e999}','{"x":Infinity}'])
def test_overflowing_json_number(text):
    with pytest.raises(ValueError):strict_json(text)
