"""Synthetic complete indexed retrieval -> evidence -> analysis -> export integration."""
from datetime import datetime,timezone
import json
from pathlib import Path
import pytest
from job_radar.report_synthetic import synthetic_inputs,synthetic_manifest
from job_radar.rag_engine import match_jobs_indexed
from job_radar.rag_index import DerivedIndex
from job_radar.report_models import *
from job_radar.report_engine import generate_match_report
from job_radar.report_render import render_match_report,export_match_report

NOW=datetime(2026,11,14,tzinfo=timezone.utc)

def inputs(tmp_path,**kwargs):
    doc,req=synthetic_inputs(**kwargs);idx=DerivedIndex(tmp_path/'idx');idx.update(req.jobs,manifest=synthetic_manifest(req.jobs))
    return doc,req,match_jobs_indexed(req,index=idx)

from job_radar.report_demo_client import SyntheticReportClient

class Fake(SyntheticReportClient):
    def __init__(self,fail=False):super().__init__();self.fail=fail
    def complete(self,request):
        if self.fail:
            self.calls+=1
            raise TimeoutError('secret response never exposed')
        return super().complete(request)

def run(req,result,doc,**kwargs):return generate_match_report(req,result,doc,report_id='s4',generated_at=NOW,**kwargs)

def test_default_offline_preserves_all(tmp_path):
    doc,req,res=inputs(tmp_path);before=(req.model_dump_json(),res.model_dump_json(),doc.model_dump_json())
    report=run(req,res,doc)
    assert report.status=='not_run' and report.packet.retrieval==res
    assert all(a.status=='not_analyzed' for a in report.analyses)
    assert before==(req.model_dump_json(),res.model_dump_json(),doc.model_dump_json())

def test_scripted_full_report_roundtrip_export(tmp_path):
    doc,req,res=inputs(tmp_path);client=Fake();report=run(req,res,doc,client=client,config=AnalysisConfig(top_n=2))
    assert report.status=='completed';assert client.calls==2
    assert [a.status for a in report.analyses]==['completed','completed']+['not_analyzed']*(len(res.matching.results)-2)
    jp,mp=export_match_report(report,tmp_path/'out')
    assert MatchReport.model_validate_json(jp.read_text())==report
    assert mp.read_text()==render_match_report(report)
    assert '录用概率' in mp.read_text() and 'unknown' in mp.read_text()
    with pytest.raises(FileExistsError):export_match_report(report,tmp_path/'out')

def test_fail_stops_batch_and_no_body_leak(tmp_path):
    doc,req,res=inputs(tmp_path);client=Fake(True);report=run(req,res,doc,client=client)
    assert report.status=='failed' and client.calls==1
    assert report.analyses[0].status=='failed'
    assert all(a.status=='not_analyzed' for a in report.analyses[1:])
    assert 'secret' not in report.model_dump_json()
    assert report.packet.retrieval==res

@pytest.mark.parametrize('kwargs',[{'text':'没有相关信息。'},{'descriptions':[]},{'descriptions':['要求使用 Rust。']}])
def test_empty_paths_zero_calls(tmp_path,kwargs):
    doc,req,res=inputs(tmp_path,**kwargs);client=Fake();report=run(req,res,doc,client=client)
    assert report.status=='not_run' and client.calls==0

@pytest.mark.parametrize('identity',[{'report_id':''},{'generated_at':datetime(2026,11,14)}])
def test_local_metadata_before_call(tmp_path,identity):
    doc,req,res=inputs(tmp_path);client=Fake();kw=dict(report_id='r',generated_at=NOW);kw.update(identity)
    with pytest.raises(ValueError):generate_match_report(req,res,doc,client=client,**kw)
    assert client.calls==0

def test_render_escapes_injection(tmp_path):
    doc,req,res=inputs(tmp_path);report=run(req,res,doc)
    report.report_id='<script>alert(1)</script> [x](https://evil.invalid)'
    output=render_match_report(report)
    assert '<script>' not in output and '[x](https' not in output
    assert '&lt;script&gt;' in output

def test_mutated_citation_rejected_on_render(tmp_path):
    doc,req,res=inputs(tmp_path);report=run(req,res,doc,client=Fake())
    report.analyses[0].draft.claims[0].evidence_ids=['e'+'f'*24]
    with pytest.raises((ValueError,ReportError)):render_match_report(report)

def test_missing_confirmation_never_promoted(tmp_path):
    doc,req,res=inputs(tmp_path);report=run(req,res,doc)
    assert report.review_status=='unconfirmed'
    assert report.packet.retrieval.matching.profile_review_status=='unconfirmed'
    req.__dict__['purpose']='formal'
    with pytest.raises(ValueError):run(req,res,doc)

def test_partial_batch_and_explicit_halt(tmp_path):
    class SecondFails(Fake):
        halted=None
        def complete(self,request):
            if self.calls==1:
                self.calls+=1;raise TimeoutError('not logged')
            return super().complete(request)
        def halt(self,code):self.halted=code
    doc,req,res=inputs(tmp_path);client=SecondFails();report=run(req,res,doc,client=client)
    assert report.status=='partial' and client.calls==2
    assert client.halted=='analysis_validation_failed'
    assert [a.status for a in report.analyses]==['completed','failed','not_analyzed']
    assert 'analysis_client_timeout' in render_match_report(report)

def test_input_limit_stops_before_provider(tmp_path):
    doc,req,res=inputs(tmp_path);client=Fake()
    report=run(req,res,doc,client=client,config=AnalysisConfig(max_input_bytes=256))
    assert client.calls==0 and report.status=='failed'
    assert report.analyses[0].error_code=='analysis_input_limit'

def test_default_report_without_socket_or_key(tmp_path,monkeypatch):
    import socket,os
    def fail(*a,**k):raise AssertionError('unexpected network')
    monkeypatch.setattr(socket,'socket',fail)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    doc,req,res=inputs(tmp_path);report=run(req,res,doc)
    assert report.status=='not_run' and report.review_status=='unconfirmed'

def test_tampered_report_rank_and_hash_rejected(tmp_path):
    doc,req,res=inputs(tmp_path);report=run(req,res,doc)
    report.packet.retrieval.matching.results[0].rank=100
    with pytest.raises(ValueError):render_match_report(report)

def test_empty_no_query_with_target_role_still_consistent(tmp_path):
    doc,req,res=inputs(tmp_path,text='没有相关技能信息。')
    assert not res.matching.results
    report=run(req,res,doc,client=Fake())
    assert report.status=='not_run' and report.analyses==[]

@pytest.mark.parametrize('mode',['valid','invalid_json','timeout','unknown_usage'])
def test_sdk_mock_entire_pipeline_and_audit(tmp_path,monkeypatch,mode):
    import httpx2
    from job_radar.report_apinebula import APINebulaAnalysisClient,MODEL
    from job_radar.resume_structure_apinebula import RateCard
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-test-key-not-real')
    scripted=SyntheticReportClient();calls=[]
    def handle(http_request):
        calls.append(http_request)
        if mode=='timeout':raise httpx2.ReadTimeout('private body',request=http_request)
        wire=json.loads(http_request.content)
        from job_radar.report_analysis import SYSTEM_PROMPT
        ar=AnalysisRequest(system_prompt=SYSTEM_PROMPT,packet_json=wire['input'],output_schema_json=json.dumps(MatchAnalysisDraft.model_json_schema()),timeout_seconds=90,max_output_bytes=128*1024,prompt_version=PROMPT_VERSION)
        text=scripted.complete(ar).text if mode=='valid' else '{bad json'
        result=dict(model=MODEL,status='completed',output=[dict(type='message',role='assistant',status='completed',content=[dict(type='output_text',text=text)])])
        if mode!='unknown_usage':result['usage']=dict(input_tokens=1000,output_tokens=500,total_tokens=1500)
        return httpx2.Response(200,json=result)
    client=APINebulaAnalysisClient(RateCard('synthetic','CNY','0.78','3.90','1','0.975'),budget_usd='1',max_input_tokens=65536,max_output_tokens=8192,_transport=httpx2.MockTransport(handle))
    try:
        doc,req,res=inputs(tmp_path);report=run(req,res,doc,client=client)
        if mode=='valid':
            assert report.status=='completed' and len(calls)==3
            assert all(a.audit and a.audit.total_tokens==1500 for a in report.analyses)
        else:
            assert report.status=='failed' and len(calls)==1
            assert report.analyses[0].audit.reserved_units is not None
            assert client.audit['halted'] is not None
            assert all(a.status=='not_analyzed' for a in report.analyses[1:])
        assert report.packet.retrieval==res
        assert 'private body' not in report.model_dump_json()
        assert 'synthetic-test-key' not in report.model_dump_json()
    finally:client.close()
