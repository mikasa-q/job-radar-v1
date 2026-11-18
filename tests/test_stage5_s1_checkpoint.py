"""Frozen S1 prepare/check and live-injected HTTP orchestration offline only."""
import importlib.util
import json
from pathlib import Path
import sys
import httpx2
import pytest
from fastapi.testclient import TestClient
from job_radar.api_v1 import create_app
from job_radar.api_v1_billing import BillingJournal
from test_api_v1 import wait

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('s1_checkpoint',ROOT/'examples/stage5_s1_checkpoint.py')
checkpoint=importlib.util.module_from_spec(spec);spec.loader.exec_module(checkpoint)


def response(text):
    return {'model':'gpt-6.1-sol','status':'completed','output':[{'type':'message','role':'assistant','status':'completed',
        'content':[{'type':'output_text','text':text}]}],'usage':{'input_tokens':1000,'output_tokens':100,'total_tokens':1100}}


def draft():
    from test_resume_structure import empty,skill
    value=empty(skills=[skill('Python',quote=checkpoint.TEXT),skill('FastAPI',quote=checkpoint.TEXT)])
    for s in value['skills']:s['context']='course'
    return value


def test_prepare_frozen_material_budget_and_replay_marker(tmp_path):
    root=tmp_path/'real';m=checkpoint.prepare(root)
    assert checkpoint.check_files(root)==m
    assert m['input_allocation']<=12000 and m['original_document_sha256']!=m['document_sha256']
    assert m['model']=='gpt-6.1-sol' and m['reasoning']=='low'
    with TestClient(create_app(checkpoint.settings(root))) as client:
        ledger=BillingJournal(client.app.state.offline_service.store)
        assert ledger.status()['new_attempts']==0 and ledger.status()['actual_cny']=='.05320458'.replace('.', '0.',1)
        r=client.post(m['profile_route'],json={'mode':'llm'},headers={'Idempotency-Key':checkpoint.PROFILE_KEY})
        assert r.status_code==503 and ledger.status()['new_attempts']==0
    with pytest.raises(FileExistsError):checkpoint.prepare(root)


def test_review_requires_exact_s1_skills_and_no_invented_work():
    from job_radar.report_synthetic import synthetic_inputs
    from job_radar.resume_structure import ClientResponse,structure_resume
    from job_radar.resume_structure_fake import FakeStructureClient
    doc,_=synthetic_inputs(text=checkpoint.TEXT)
    result=structure_resume(doc,resume_id='s1',client=FakeStructureClient([ClientResponse(status='completed',text=json.dumps(draft()))]))
    payload={'profile':result.profile.model_dump(mode='json'),'comparison':result.comparison.model_dump(mode='json')}
    assert checkpoint.review(payload)['status']=='passed'
    payload['profile']['skills']=[];assert checkpoint.review(payload)['status']=='failed'


def test_explicit_live_http_profile_only_shared_real_journal_mocked_transport(tmp_path,monkeypatch):
    # Patch only the native transport factory. Public LiveBackend stays exact
    # type so all root/scope/billing/domain checks really execute.
    from job_radar.api_v1_live import LiveBackend
    import job_radar.api_v1_live as live
    root=tmp_path/'real';m=checkpoint.prepare(root);calls=[]
    def handler(request):
        calls.append(request)
        persisted=json.loads((root/'api-store/state.json').read_text())['content']['billing']['totals']
        assert persisted['new_attempts']==1 and persisted['inflight']
        return httpx2.Response(200,json=response(json.dumps(draft(),ensure_ascii=False)))
    monkeypatch.setattr(live.httpx2,'HTTPTransport',lambda **kwargs:httpx2.MockTransport(handler))
    backend=LiveBackend(root/'api-store',api_key='synthetic-test-no-real',scope=checkpoint.SCOPE)
    with TestClient(create_app(checkpoint.settings(root),live_backend=backend)) as client:
        r=client.post(m['profile_route'],json={'mode':'llm'},headers={'Idempotency-Key':checkpoint.PROFILE_KEY})
        assert r.status_code==202,r.text
        row=wait(client,r.json()['data']['status_url']);assert row['state']=='succeeded',row
        assert checkpoint.review(row['payload'])['status']=='passed'
        assert row['payload']['profile']['review_status']=='unconfirmed'
        ledger=BillingJournal(client.app.state.offline_service.store)
        s=ledger.status();assert s['new_attempts']==1 and s['actual_cny']=='0.05534958'
        assert not s['halted'] and s['inflight'] is None
        assert client.post(m['profile_route'],json={'mode':'llm'},headers={'Idempotency-Key':checkpoint.PROFILE_KEY}).json()['data']['resource_id']==row['resource_id']
        assert len(calls)==1
    backend.close()


@pytest.mark.parametrize('outcome',['success','timeout','bad_json','bad_draft'])
def test_runner_executes_one_only_and_persists_stop_checkpoint(tmp_path,monkeypatch,outcome):
    import os
    import job_radar.api_v1_live as live
    root=tmp_path/'real';checkpoint.prepare(root);calls=[]
    def handler(request):
        calls.append(request)
        if outcome=='timeout':raise httpx2.ReadTimeout('synthetic timeout',request=request)
        if outcome=='bad_json':return httpx2.Response(200,text='{not-json')
        return httpx2.Response(200,json=response('invalid draft' if outcome=='bad_draft' else json.dumps(draft())))
    monkeypatch.setattr(live.httpx2,'HTTPTransport',lambda **kwargs:httpx2.MockTransport(handler))
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-checkpoint-test')
    rc=checkpoint.execute(root)
    assert rc==(0 if outcome=='success' else 2)
    assert len(calls)==1 and (root/'LIVE_STARTED').is_file()
    assert 'OPENAI_API_KEY' not in os.environ
    result=json.loads((root/'RESULT.json').read_text())
    assert result['generation_attempts']==1 and result['report_requests']==0
    assert result['halted']==(outcome!='success')
    assert result['retries']==0
    assert result['cumulative_reserved_cny']=='0.7359144'
    assert result['usage_known']==(outcome in ('success','bad_draft'))
    with pytest.raises(FileExistsError):checkpoint.execute(root)
    assert len(calls)==1


@pytest.mark.parametrize('failure',['profile_write','status_during_call','result_write'])
def test_checkpoint_diagnostic_failure_halts_after_existing_call_and_keeps_usage(tmp_path,monkeypatch,failure):
    import os
    import threading
    from decimal import Decimal
    import job_radar.api_v1_live as live
    root=tmp_path/'real';checkpoint.prepare(root);calls=[]
    started=threading.Event();release=threading.Event()
    def handler(request):
        calls.append(request);started.set()
        if failure=='status_during_call':assert release.wait(5)
        return httpx2.Response(200,json=response(json.dumps(draft())))
    monkeypatch.setattr(live.httpx2,'HTTPTransport',lambda **kwargs:httpx2.MockTransport(handler))
    original_write=checkpoint.write
    def write_fault(path,value):
        name=Path(path).name
        if name=={'profile_write':'profile.json','result_write':'RESULT.json'}.get(failure):
            # Repeated best-effort diagnostic failure must still leave halt.
            raise OSError('synthetic sidecar failure')
        return original_write(path,value)
    monkeypatch.setattr(checkpoint,'write',write_fault)
    original_get=TestClient.get
    def get_fault(self,url,*args,**kwargs):
        if failure=='status_during_call' and str(url).startswith('/v1/profiles/'):
            assert started.wait(5)
            # Release the still-existing call only after the status failure is
            # about to occur. Runner drains it before setting persistent halt.
            release.set()
            raise RuntimeError('synthetic status failure')
        return original_get(self,url,*args,**kwargs)
    monkeypatch.setattr(TestClient,'get',get_fault)
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-checkpoint-fault-test')
    assert checkpoint.execute(root)==2
    assert len(calls)==1 and (root/'LIVE_STARTED').is_file()
    assert 'OPENAI_API_KEY' not in os.environ
    after=json.loads((root/'billing-after.json').read_text())
    assert after['halted'] and after['new_attempts']==1 and after['inflight'] is None
    assert after['actual_cny']=='0.05534958' and after['retained_reserve_cny']=='0.7359144'
    assert Decimal(after['pending_usage_ceiling_cny'])==0
    attempt=next(iter(after['attempts'].values()))
    assert attempt['state']=='completed' and attempt['usage']['total_tokens']==1100
    row=json.loads((root/'profile-resource.json').read_text())
    assert row['state']=='succeeded' and row['error'] is None
    assert row['payload']['profile']['review_status']=='unconfirmed'
    if failure!='result_write':
        result=json.loads((root/'RESULT.json').read_text())
        assert result['status']=='checkpoint_failed_stopped' and result['task_state']=='succeeded'
        assert result['usage_known'] and result['actual_usage_cost_cny']=='0.00214500'
        assert result['report_requests']==0 and result['halted'] and not result['fail_stop']
    with pytest.raises(FileExistsError):checkpoint.execute(root)
    assert len(calls)==1
