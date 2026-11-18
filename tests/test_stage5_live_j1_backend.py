"""J1 opt-in boundary: synthetic stores/credentials and MockTransport only."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import socket

from fastapi.testclient import TestClient
import httpx2
import pytest

from job_radar.api_v1 import create_app
from job_radar.api_v1_billing import Approval, BillingJournal, initialize
from job_radar.api_v1_live import DEFAULT_SCOPE, LiveBackend
from job_radar.api_v1_provider import build_wire
from job_radar.api_v1_runtime import ServiceSettings
from job_radar.api_v1_store import ResourceStore, ServiceError
from job_radar.rag_models import canonical_hash
from test_api_v1 import wait
from test_api_v1_billed_integration import planned_report, provider_response, scripted_report
from test_api_v1_reports import snapshot_case

FAKE_KEY='synthetic-j1-live-test-no-real'


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('network'))
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('network'))


def prepare(store, root):
    snapshot=snapshot_case(root, descriptions=['要求掌握 Python。熟悉 Docker 加分。'])
    row,_=store.create('/synthetic/matches', 'j1-synthetic-match', {}, 'match', 'bm25_rules')
    store.transition(row['resource_id'], 'running')
    store.transition(row['resource_id'], 'succeeded', payload=snapshot)
    value,request,allowed=planned_report(row['resource_id'], snapshot)
    seed=root/'history.json';seed.write_text('{"synthetic":"history"}')
    ledger=initialize(store, Approval(root=str(store.root.resolve()), scope=DEFAULT_SCOPE,
        seed_refs={str(seed.resolve()): hashlib.sha256(seed.read_bytes()).hexdigest()},
        expires_at=datetime.now(timezone.utc)+timedelta(hours=1), max_calls=2, allowed=[allowed]))
    return value,request,allowed,ledger


def patch_transport(monkeypatch, handler):
    constructors=[]
    def transport(**kwargs):
        constructors.append(kwargs)
        return httpx2.MockTransport(handler)
    monkeypatch.setattr('job_radar.api_v1_live.httpx2.HTTPTransport', transport)
    return constructors


@pytest.mark.parametrize('allow_report', [None, 0, 1, 'true'])
def test_report_optin_requires_strict_boolean(tmp_path, allow_report):
    with pytest.raises(ServiceError, match='llm_disabled'):
        LiveBackend(tmp_path/'store', api_key=FAKE_KEY, allow_report=allow_report)


def test_default_live_backend_report_stays_disabled_before_transport(tmp_path, monkeypatch):
    store=ResourceStore(tmp_path/'store');store.open()
    try:
        value,request,allowed,ledger=prepare(store,tmp_path)
        patch_transport(monkeypatch, lambda request: pytest.fail('must not send'))
        backend=LiveBackend(store.root, api_key=FAKE_KEY)
        with pytest.raises(ServiceError, match='llm_disabled'):
            backend.client('report',ledger,'rpt_'+'a'*32,allowed['context_sha256'])
        assert ledger.status()['new_attempts']==0
        backend.close()
    finally:store.close()


def test_report_client_preserves_v3_wire_and_durable_reservation(tmp_path, monkeypatch):
    store=ResourceStore(tmp_path/'store');store.open()
    try:
        value,request,allowed,ledger=prepare(store,tmp_path)
        snapshot=store.get(value['match_id'])['payload']
        row,_=store.create('/v1/reports','j1-direct-client',value,'report','llm',snapshot=snapshot)
        store.transition(row['resource_id'],'running')
        calls=[]
        def handle(sent):
            calls.append(sent)
            persisted=json.loads((store.root/'state.json').read_text())['content']['billing']
            assert persisted['events'][-1]['type']=='send_intent'
            assert persisted['totals']['new_attempts']==1
            assert persisted['totals']['pending_usage_ceiling_cny']=='0.0670488'
            assert str(sent.url)=='https://apinebula.ai/v1/responses'
            assert sent.method=='POST' and sent.headers['authorization']=='Bearer '+FAKE_KEY
            assert json.loads(sent.content)==build_wire('report',request)
            return httpx2.Response(200,json=provider_response('{}'))
        constructors=patch_transport(monkeypatch,handle)
        backend=LiveBackend(store.root,api_key=FAKE_KEY,allow_report=True)
        client=backend.client('report',ledger,row['resource_id'],allowed['context_sha256'])
        try:
            assert not calls and constructors==[{'retries':0,'trust_env':False}]
            assert client.kind=='report' and client._sdk.max_retries==0
            assert not client._sdk._client.follow_redirects
            wire=build_wire('report',request)
            assert wire['model']=='gpt-6.1-sol' and wire['reasoning']=={'effort':'low'}
            assert request.prompt_version=='match-analysis-prompt-v3'
            assert wire['max_output_tokens']==8192 and wire['truncation']=='disabled'
            result=client.complete(request)
            assert result.status=='completed' and result.text=='{}'
            assert result.audit.input_tokens==1000
            assert client.audit['mode']=='live' and client.audit['count_api_requests']==0
            assert ledger.status()['actual_cny']=='0.05534958'
            assert ledger.status()['retained_reserve_cny']=='0.7499544'
            with pytest.raises(ServiceError):client.complete(request)
            assert len(calls)==1
        finally:client.close();backend.close()
    finally:store.close()


def test_default_live_http_report_is_disabled_without_consuming_approval(tmp_path, monkeypatch):
    settings=ServiceSettings(store_root=tmp_path/'store')
    backend=LiveBackend(settings.store_root,api_key=FAKE_KEY)
    patch_transport(monkeypatch,lambda request:pytest.fail('must not send'))
    with TestClient(create_app(settings,live_backend=backend)) as client:
        value,request,allowed,ledger=prepare(client.app.state.offline_service.store,tmp_path)
        response=client.post('/v1/reports',json=value,headers={'Idempotency-Key':'j1-not-opted-in'})
        assert response.status_code==503 and response.json()['error']['code']=='llm_disabled'
        assert ledger.status()['new_attempts']==0
        assert not any(r['resource']['kind']=='report' for r in ledger.store.state['records'].values())
    backend.close()


def test_live_http_report_uses_real_banner_and_replay_does_not_resend(tmp_path, monkeypatch):
    calls=[];pending=[]
    def handler(request):
        calls.append(request)
        return httpx2.Response(200,json=provider_response(pending[0]))
    patch_transport(monkeypatch,handler)
    settings=ServiceSettings(store_root=tmp_path/'store')
    backend=LiveBackend(settings.store_root,api_key=FAKE_KEY,allow_report=True)
    with TestClient(create_app(settings,live_backend=backend)) as client:
        value,request,allowed,ledger=prepare(client.app.state.offline_service.store,tmp_path)
        pending.append(scripted_report(request))
        response=client.post('/v1/reports',json=value,headers={'Idempotency-Key':'j1-http-live-once'})
        assert response.status_code==202,response.text
        row=wait(client,response.json()['data']['status_url'])
        assert row['state']=='succeeded',row
        report=row['payload']['report']
        assert report['report_id'].startswith('live-') and report['status']=='completed'
        assert report['review_status']=='unconfirmed' and report['prompt_version']=='match-analysis-prompt-v3'
        assert '本报告包含真实模型响应；结论仍需人工复核。' in row['payload']['markdown']
        assert '模拟响应' not in row['payload']['markdown'] and '预设响应只验证' not in row['payload']['markdown']
        assert any(a['audit'] and a['audit']['provider']=='apinebula' for a in report['analyses'])
        assert client.get(response.json()['data']['status_url']+'?format=markdown').text==row['payload']['markdown']
        assert client.get('/v1/ready').json()['data']['real_llm'] is False
        status=ledger.status()
        assert status['new_attempts']==1 and not status['halted'] and status['inflight'] is None
        assert Decimal(status['pending_usage_ceiling_cny'])==0
        replay=client.post('/v1/reports',json=value,headers={'Idempotency-Key':'j1-http-live-once'})
        assert replay.json()['data']['resource_id']==row['resource_id']
        assert len(calls)==1
    backend.close()


@pytest.mark.parametrize('failure',['invalid_draft','wrong_model','unknown_usage','bad_json','timeout','http500'])
def test_live_report_failure_keeps_known_or_unknown_fee_and_globally_stops(tmp_path,monkeypatch,failure):
    calls=[]
    def handler(request):
        calls.append(request)
        if failure=='timeout':raise httpx2.ReadTimeout('synthetic',request=request)
        if failure=='http500':return httpx2.Response(500,text='synthetic')
        if failure=='bad_json':return httpx2.Response(200,text='{not-json')
        body=provider_response('invalid draft')
        if failure=='unknown_usage':body.pop('usage')
        if failure=='wrong_model':body['model']='wrong-model'
        return httpx2.Response(200,json=body)
    patch_transport(monkeypatch,handler)
    settings=ServiceSettings(store_root=tmp_path/'store')
    backend=LiveBackend(settings.store_root,api_key=FAKE_KEY,allow_report=True)
    with TestClient(create_app(settings,live_backend=backend)) as client:
        value,request,allowed,ledger=prepare(client.app.state.offline_service.store,tmp_path)
        response=client.post('/v1/reports',json=value,headers={'Idempotency-Key':'j1-failure-once'})
        assert response.status_code==202,response.text
        row=wait(client,response.json()['data']['status_url'])
        assert row['state']=='failed' and row['payload'] is None
        assert client.get(response.json()['data']['status_url']+'?format=markdown').status_code==409
        status=ledger.status();known=failure in ('invalid_draft','wrong_model')
        assert status['new_attempts']==1 and status['halted']
        assert status['retained_reserve_cny']=='0.7499544'
        assert status['actual_cny']==('0.05534958' if known else '0.05320458')
        assert Decimal(status['pending_usage_ceiling_cny'])==(Decimal(0) if known else Decimal('.0670488'))
        assert client.post('/v1/reports',json=value,headers={'Idempotency-Key':'j1-after-failure'}).status_code==503
        assert len(calls)==1
    backend.close()
