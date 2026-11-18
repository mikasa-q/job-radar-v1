"""Explicit live-boundary tests: synthetic credential and MockTransport only."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket

import httpx2
import pytest

from job_radar.api_v1_billing import Approval, BillingJournal, initialize
from job_radar.api_v1_live import LiveBackend, DEFAULT_SCOPE
from job_radar.api_v1_provider import build_wire, SYNTHETIC_API_KEY
from job_radar.api_v1_store import ResourceStore, ServiceError
from job_radar.rag_models import canonical_hash
from job_radar.report_synthetic import synthetic_inputs
from job_radar.resume_structure import build_structure_request
from test_api_v1_provider import response

FAKE_KEY = 'synthetic-live-boundary-test-only'


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.setattr(socket, 'create_connection', lambda *a,**k: pytest.fail('network'))
    monkeypatch.setattr(socket.socket, 'connect', lambda *a,**k: pytest.fail('network'))


@pytest.fixture
def setup(tmp_path):
    store=ResourceStore(tmp_path/'store');store.open()
    doc,_=synthetic_inputs();snapshot=doc.model_dump(mode='json');value={'mode':'llm'}
    row,_=store.create('/synthetic/profile','live-profile-setup',value,'profile','llm',snapshot=snapshot)
    store.transition(row['resource_id'],'running')
    ctx=canonical_hash({'kind':'profile','input':value,'snapshot':snapshot})
    req=build_structure_request(doc,timeout_seconds=90);wire=build_wire('profile',req)
    seed=tmp_path/'history.json';seed.write_text('{"synthetic":"history"}')
    ledger=initialize(store,Approval(root=str(store.root.resolve()),scope=DEFAULT_SCOPE,
        seed_refs={str(seed.resolve()):hashlib.sha256(seed.read_bytes()).hexdigest()},
        expires_at=datetime.now(timezone.utc)+timedelta(hours=1),max_calls=2,
        allowed=[dict(kind='profile',context_sha256=ctx,payload_sha256=canonical_hash(wire),input_limit=12000)]))
    yield store,ledger,row['resource_id'],ctx,req,wire
    store.close()


def mock_transport(monkeypatch, handler):
    constructions=[]
    def build(**kwargs):
        constructions.append(kwargs)
        return httpx2.MockTransport(handler)
    # This is the only injected seam: no production constructor takes transport.
    monkeypatch.setattr('job_radar.api_v1_live.httpx2.HTTPTransport',build)
    return constructions


def test_constructor_does_not_construct_sdk_or_network_and_repr_redacts(setup,monkeypatch):
    store,ledger,rid,ctx,req,wire=setup
    monkeypatch.setattr('job_radar.api_v1_live._transport',lambda:pytest.fail('unexpected transport'))
    backend=LiveBackend(store.root,api_key=FAKE_KEY)
    assert FAKE_KEY not in repr(backend)
    assert FAKE_KEY not in str(backend)
    backend.close()
    assert backend._api_key is None
    with pytest.raises(ServiceError,match='llm_disabled'):backend.client('profile',ledger,rid,ctx)


@pytest.mark.parametrize('key',['', ' ', 'has space', '\nsecret', SYNTHETIC_API_KEY, None])
def test_invalid_keys_fail_without_network(tmp_path,key):
    with pytest.raises(ServiceError,match='llm_disabled'):
        LiveBackend(tmp_path/'store',api_key=key)


@pytest.mark.parametrize('bad_scope',[True,'', 'x'*129])
def test_invalid_scope(tmp_path,bad_scope):
    with pytest.raises(ServiceError,match='llm_disabled'):
        LiveBackend(tmp_path/'store',api_key=FAKE_KEY,scope=bad_scope)


def test_wrong_root_scope_and_report_refused_before_transport(setup,tmp_path,monkeypatch):
    store,ledger,rid,ctx,req,wire=setup
    monkeypatch.setattr('job_radar.api_v1_live._transport',lambda:pytest.fail('unexpected transport'))
    cases=[(LiveBackend(tmp_path/'wrong',api_key=FAKE_KEY),'profile'),
           (LiveBackend(store.root,api_key=FAKE_KEY,scope='wrong-scope'),'profile'),
           (LiveBackend(store.root,api_key=FAKE_KEY),'report')]
    for backend,kind in cases:
        with pytest.raises(ServiceError,match='llm_disabled'):backend.client(kind,ledger,rid,ctx)
    assert ledger.status()['new_attempts']==0


def test_canonical_root_only(tmp_path):
    linked=tmp_path/'link';target=tmp_path/'target';target.mkdir();linked.symlink_to(target)
    with pytest.raises(ServiceError,match='llm_disabled'):LiveBackend(linked,api_key=FAKE_KEY)
    with pytest.raises(ServiceError,match='llm_disabled'):LiveBackend(target/'..'/'target',api_key=FAKE_KEY)


def test_live_one_shot_preserves_exact_wire_and_durable_billing_before_http(setup,monkeypatch):
    store,ledger,rid,ctx,req,wire=setup;calls=[]
    def handler(request):
        calls.append(request)
        persisted=json.loads((store.root/'state.json').read_text())['content']['billing']
        assert persisted['events'][-1]['type']=='send_intent'
        assert persisted['totals']['new_attempts']==1
        assert persisted['totals']['pending_usage_ceiling_cny']=='0.0530088'
        assert str(request.url)=='https://apinebula.ai/v1/responses'
        assert request.method=='POST'
        assert request.headers['authorization']=='Bearer '+FAKE_KEY
        assert json.loads(request.content)==wire
        return httpx2.Response(200,json=response())
    builds=mock_transport(monkeypatch,handler)
    backend=LiveBackend(store.root,api_key=FAKE_KEY)
    client=backend.client('profile',ledger,rid,ctx)
    try:
        assert calls==[] and builds==[{'retries':0,'trust_env':False}]
        assert client._sdk.max_retries==0 and not client._sdk._client.follow_redirects
        out=client.complete(req);assert out.status=='completed' and out.text=='{}'
        status=ledger.status()
        assert status['new_attempts']==1 and status['inflight']==client.attempt_id
        assert status['actual_cny']=='0.05534958' and status['retained_reserve_cny']=='0.7359144'
        assert client.audit['mode']=='live' and client.audit['count_api_requests']==0
        assert FAKE_KEY not in repr(client) and FAKE_KEY not in repr(client.audit)
        with pytest.raises(ServiceError,match='budget_halted'):client.complete(req)
        assert len(calls)==1
    finally:client.close();backend.close()


@pytest.mark.parametrize('failure',['timeout','http500','redirect','unknown_usage','wrong_model','bad_json'])
def test_live_failures_never_retry_and_persist_halt(setup,monkeypatch,failure):
    store,ledger,rid,ctx,req,wire=setup;calls=[]
    def handler(request):
        calls.append(request)
        if failure=='timeout':raise httpx2.ReadTimeout('synthetic',request=request)
        if failure=='http500':return httpx2.Response(500,text='synthetic failure')
        if failure=='redirect':return httpx2.Response(307,headers={'location':'https://unexpected.invalid'})
        if failure=='bad_json':return httpx2.Response(200,text='{invalid')
        body=response()
        if failure=='unknown_usage':body.pop('usage')
        if failure=='wrong_model':body['model']='wrong-model'
        return httpx2.Response(200,json=body)
    mock_transport(monkeypatch,handler);backend=LiveBackend(store.root,api_key=FAKE_KEY)
    client=backend.client('profile',ledger,rid,ctx)
    try:
        with pytest.raises(ServiceError):client.complete(req)
        status=ledger.status()
        assert len(calls)==1 and status['new_attempts']==1 and status['halted']
        assert status['retained_reserve_cny']=='0.7359144'
        assert status['actual_cny']==('0.05534958' if failure=='wrong_model' else '0.05320458')
        with pytest.raises(ServiceError):backend.client('profile',ledger,rid,ctx)
        assert len(calls)==1
    finally:client.close();backend.close()


def test_bad_context_does_not_send_or_consume_allowance(setup,monkeypatch):
    store,ledger,rid,ctx,req,wire=setup;calls=[]
    mock_transport(monkeypatch,lambda request:calls.append(request))
    backend=LiveBackend(store.root,api_key=FAKE_KEY)
    client=backend.client('profile',ledger,rid,'f'*64)
    try:
        with pytest.raises(ServiceError,match='core_rejected'):client.complete(req)
        assert not calls and ledger.status()['new_attempts']==0
    finally:client.close();backend.close()


def test_backend_close_cancels_unstarted_client(setup,monkeypatch):
    store,ledger,rid,ctx,req,wire=setup;calls=[]
    mock_transport(monkeypatch,lambda request:calls.append(request))
    backend=LiveBackend(store.root,api_key=FAKE_KEY);client=backend.client('profile',ledger,rid,ctx)
    backend.close()
    try:
        with pytest.raises(ServiceError,match='llm_disabled'):client.complete(req)
        assert not calls and ledger.status()['new_attempts']==0
    finally:client.close()
