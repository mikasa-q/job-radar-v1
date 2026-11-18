"""Pinned SDK + MockTransport only; no real credentials, datasets or sockets."""
from copy import deepcopy
from dataclasses import replace
import json
import socket

import httpx2
import pytest

from job_radar.api_v1_provider import (BillingClient, MockBackend, MockProvider, build_wire,
    input_allocation, INPUT_LIMITS, MAX_OUTPUT_TOKENS, RESERVATIONS,
    SYNTHETIC_API_KEY, MAX_RESPONSE_BYTES)
from job_radar.api_v1_store import ServiceError
from job_radar.rag_models import canonical_hash
from job_radar.report_models import AnalysisRequest, MatchAnalysisDraft, PROMPT_VERSION
from job_radar.report_analysis import SYSTEM_PROMPT
from job_radar.resume_structure import build_structure_request
from job_radar.report_synthetic import synthetic_inputs


def request(kind):
    if kind == 'profile':
        doc,_ = synthetic_inputs()
        return build_structure_request(doc, timeout_seconds=90)
    packet = dict(schema_version='match-analysis-input-v1',
        key=dict(dataset_id='synthetic',job_id=1),purpose='debug',
        context=dict(title='合成岗',rank=1,bm25=None,dense=None,fusion_score=.01,
                     rules={},channels=[],retrieval_status='ok',actual_mode='bm25_rules'),
        evidence=[dict(evidence_id='e'+'a'*24,kind='scope',path='profile',key=None,
                       content=dict(coverage='supplied_profile'))],limitations=['synthetic'])
    return AnalysisRequest(system_prompt=SYSTEM_PROMPT,
        packet_json=json.dumps(packet,ensure_ascii=False),
        output_schema_json=json.dumps(MatchAnalysisDraft.model_json_schema()),
        timeout_seconds=90,max_output_bytes=128*1024,prompt_version=PROMPT_VERSION)


def response():
    return dict(id='resp_synthetic',model='gpt-6.1-sol',status='completed',
        output=[dict(type='message',role='assistant',status='completed',
                     content=[dict(type='output_text',text='{}')])],
        usage=dict(input_tokens=1000,output_tokens=100,total_tokens=1100))


class Ledger:
    def __init__(self):self.events=[];self.raw=None;self.fail=None
    def reserve(self,rid,kind,context,wire):
        self.events.append(('reserve',rid,kind,context,deepcopy(wire)))
        if self.fail=='reserve':raise ServiceError('budget_exhausted')
        return 'attempt-synthetic'
    def settle(self,attempt,usage,raw):
        self.events.append(('settle',attempt,deepcopy(usage)))
        self.raw=deepcopy(raw)
        if self.fail=='settle':raise ServiceError('store_unavailable')
    def halt(self,code):self.events.append(('halt',code))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('network'))
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:pytest.fail('network'))


@pytest.fixture
def factory():
    clients=[]
    def make(kind='report',body=None,handler=None):
        ledger=Ledger();calls=[]
        def handle(req):
            calls.append(req);ledger.events.append(('http',))
            return handler(req) if handler else httpx2.Response(200,json=response() if body is None else body)
        client=BillingClient(kind,ledger,'prf_'+'a'*32,'b'*64,httpx2.MockTransport(handle))
        clients.append(client)
        return client,ledger,calls
    yield make
    for c in clients:c.close()


@pytest.mark.parametrize('kind',['profile','report'])
def test_wire_exact_old_builder_without_constructor_or_environment(kind,monkeypatch):
    from job_radar.report_apinebula import APINebulaAnalysisClient
    from job_radar.resume_structure_apinebula import APINebulaStructureClient
    def fail(*a,**k):raise AssertionError('old client constructed')
    monkeypatch.setattr(APINebulaAnalysisClient,'__init__',fail)
    monkeypatch.setattr(APINebulaStructureClient,'__init__',fail)
    wire=build_wire(kind,request(kind))
    assert wire['model']=='gpt-6.1-sol' and wire['reasoning']=={'effort':'low'}
    assert wire['max_output_tokens']==8192 and wire['truncation']=='disabled'
    assert wire['store'] is False and wire['stream'] is False and wire['tools']==[]
    assert wire['text']['format']['strict'] is True
    assert input_allocation(wire)<=INPUT_LIMITS[kind]
    assert wire['input']==(request(kind).blocks_json if kind=='profile' else request(kind).packet_json)
    assert canonical_hash(wire)==canonical_hash(build_wire(kind,request(kind)))


@pytest.mark.parametrize('kind',['profile','report'])
def test_one_sdk_call_after_reservation_and_settlement_before_domain(kind,factory):
    client,ledger,calls=factory(kind)
    out=client.complete(request(kind))
    assert out.status=='completed' and out.text=='{}'
    assert [e[0] for e in ledger.events]==['reserve','http','settle']
    assert len(calls)==1 and client.attempt_id=='attempt-synthetic'
    assert json.loads(calls[0].content)==build_wire(kind,request(kind))
    assert str(calls[0].url)=='https://apinebula.ai/v1/responses'
    assert calls[0].headers['authorization']=='Bearer '+SYNTHETIC_API_KEY
    assert client._sdk.max_retries==0 and not client._sdk._client.follow_redirects
    assert client.last_call_audit.reserved_units==RESERVATIONS[kind]
    assert client.last_call_audit.input_tokens==1000
    assert client.audit['count_api_requests']==0
    with pytest.raises(ServiceError,match='budget_halted'):client.complete(request(kind))
    assert len(calls)==1 and all(e[0]!='complete' for e in ledger.events)


def test_constructor_rejects_real_transport_and_real_key_without_env(factory):
    ledger=Ledger()
    with pytest.raises(ServiceError,match='llm_disabled'):
        BillingClient('report',ledger,'r','c',None)
    transport=httpx2.MockTransport(lambda r:httpx2.Response(200,json=response()))
    with pytest.raises(ServiceError,match='llm_disabled'):
        BillingClient('report',ledger,'r','c',transport,api_key='not-the-synthetic-marker')
    with pytest.raises(ServiceError,match='llm_disabled'):MockProvider(None)


def test_mock_factory_only_provides_programmatic_mock_clients():
    transport=httpx2.MockTransport(lambda r:httpx2.Response(200,json=response()))
    provider=MockProvider(transport)
    assert MockProvider is MockBackend and type(provider) is MockBackend
    client=provider.client('report',Ledger(),'r','c')
    try:
        assert client._sdk.max_retries==0 and client.attempt_ids==()
        client.complete(request('report'))
        assert client.attempt_ids==(client.attempt_id,)
        with pytest.raises(AttributeError):client.attempt_ids=()
    finally:client.close()


@pytest.mark.parametrize('field,value',[
    ('system_prompt','different'),('prompt_version','match-analysis-prompt-v2'),
    ('timeout_seconds',91),('max_output_bytes',0),('output_schema_json','{}')])
def test_invalid_request_never_reserves(field,value,factory):
    client,ledger,calls=factory()
    invalid=replace(request('report'),**{field:value})
    with pytest.raises(ServiceError,match='core_rejected'):client.complete(invalid)
    assert ledger.events==[] and calls==[]


def test_oversized_input_never_reserves_or_truncates(factory):
    client,ledger,calls=factory('profile')
    req=request('profile');payload=json.loads(req.blocks_json)
    payload['blocks'][0]['text']='x'*20000
    req=replace(req,blocks_json=json.dumps(payload))
    with pytest.raises(ServiceError,match='core_rejected'):client.complete(req)
    assert not ledger.events and not calls


@pytest.mark.parametrize('case',['wrong_model','refused','incomplete','empty_output','multiple_texts','oversized_text'])
def test_rejected_output_retains_known_settlement_and_halts_once(factory,case):
    body=response()
    if case=='wrong_model':body['model']='other-model'
    elif case=='refused':body['output'][0]['content']=[dict(type='refusal',refusal='private supplier text')]
    elif case=='incomplete':body['status']='incomplete'
    elif case=='empty_output':body['output']=[]
    elif case=='multiple_texts':body['output'][0]['content']*=2
    else:body['output'][0]['content'][0]['text']='x'*(128*1024+1)
    client,ledger,calls=factory(body=body)
    with pytest.raises(ServiceError,match='upstream_error') as caught:client.complete(request('report'))
    assert str(caught.value)=='upstream_error'
    assert [e[0] for e in ledger.events]==['reserve','http','settle','halt']
    assert ledger.raw==body and len(calls)==1


@pytest.mark.parametrize('usage',[None,{},dict(input_tokens=True,output_tokens=1,total_tokens=2),
    dict(input_tokens=1,output_tokens=2,total_tokens=4),
    dict(input_tokens=20001,output_tokens=2,total_tokens=20003),
    dict(input_tokens=1,output_tokens=8193,total_tokens=8194)])
def test_unknown_or_out_of_range_usage_stops_no_retry(factory,usage):
    body=response();body['usage']=usage
    client,ledger,calls=factory(body=body)
    with pytest.raises(ServiceError,match='upstream_error'):client.complete(request('report'))
    assert ledger.events[-1]==('halt','upstream_error') and len(calls)==1
    assert client.failure_code=='upstream_error' and ledger.raw==body


@pytest.mark.parametrize('kind',['timeout','http500','redirect','bad_json','duplicate_key','oversized_body'])
def test_network_and_parse_failures_redact_stop_and_never_retry(factory,kind):
    def handler(req):
        if kind=='timeout':raise httpx2.ReadTimeout('private token/response',request=req)
        if kind=='http500':return httpx2.Response(500,text='private provider body')
        if kind=='redirect':return httpx2.Response(307,headers={'location':'https://other.invalid'})
        if kind=='bad_json':return httpx2.Response(200,content=b'{private')
        if kind=='duplicate_key':return httpx2.Response(200,content=b'{"usage":{},"usage":{}}')
        return httpx2.Response(200,content=b'x'*(MAX_RESPONSE_BYTES+1))
    client,ledger,calls=factory(handler=handler)
    code='execution_timeout' if kind=='timeout' else 'upstream_error'
    with pytest.raises(ServiceError,match=code) as caught:client.complete(request('report'))
    assert str(caught.value)==code and len(calls)==1
    expected=['reserve','http']+(['settle'] if kind in ('bad_json','duplicate_key') else [])+['halt']
    assert [e[0] for e in ledger.events]==expected
    if kind in ('bad_json','duplicate_key'):
        assert isinstance(ledger.raw,str)


def test_reservation_rejects_before_http_and_settlement_failure_prevents_publication(factory):
    client,ledger,calls=factory();ledger.fail='reserve'
    with pytest.raises(ServiceError,match='budget_exhausted'):client.complete(request('report'))
    assert calls==[] and all(e[0]!='halt' for e in ledger.events)
    client,ledger,calls=factory();ledger.fail='settle'
    with pytest.raises(ServiceError,match='store_unavailable'):client.complete(request('report'))
    assert len(calls)==1 and ledger.events[-1]==('halt','store_unavailable')


def test_closed_client_and_semantic_halt_do_not_send(factory):
    client,ledger,calls=factory();client.close()
    with pytest.raises(ServiceError,match='budget_halted'):client.complete(request('report'))
    assert not calls
    client,ledger,calls=factory();client.halt('analysis_validation_failed')
    with pytest.raises(ServiceError,match='budget_halted'):client.complete(request('report'))
    assert not calls and ledger.events==[('halt','upstream_error')]


def test_semantic_halt_preserves_first_transport_failure(factory):
    def handler(req):raise httpx2.ReadTimeout('not exposed',request=req)
    client,ledger,calls=factory(handler=handler)
    with pytest.raises(ServiceError,match='execution_timeout'):client.complete(request('report'))
    client.halt('analysis_validation_failed')
    assert client.failure_code=='execution_timeout'
    assert [e for e in ledger.events if e[0]=='halt']==[('halt','execution_timeout')]


@pytest.mark.parametrize('outcome',['settled','wrong_model','timeout','unknown_usage'])
def test_real_journal_hooks_persist_before_send_and_survive_restart(tmp_path,outcome):
    """Real journal, synthetic approval/HTTP; no business success is fabricated."""
    from datetime import datetime, timedelta, timezone
    import hashlib
    from job_radar.api_v1_billing import Approval, AllowedCall, BillingJournal, SEED, initialize
    from job_radar.api_v1_store import ResourceStore
    store=ResourceStore(tmp_path/'store');store.open()
    value={'synthetic':'report'};snapshot={'material':'synthetic'}
    row,_=store.create('/synthetic/report','synthetic-report-key',value,
                       'report','llm',snapshot=snapshot)
    rid=row['resource_id']
    context=canonical_hash({'kind':'report','input':value,'snapshot':snapshot})
    req=request('report');wire=build_wire('report',req)
    seed_file=tmp_path/'synthetic-seed.json'
    seed_file.write_text(json.dumps(SEED),encoding='utf-8')
    approval=Approval(root=str(store.root.resolve()),scope='synthetic-provider-only',
        seed_refs={str(seed_file.resolve()):hashlib.sha256(seed_file.read_bytes()).hexdigest()},
        expires_at=datetime.now(timezone.utc)+timedelta(hours=1),max_calls=1,
        allowed=[AllowedCall(kind='report',context_sha256=context,
                             payload_sha256=canonical_hash(wire),input_limit=20000)])
    ledger=initialize(store,approval);calls=[]
    def handler(req):
        calls.append(req)
        persisted=json.loads((store.root/'state.json').read_text())['content']['billing']
        assert persisted['totals']['new_attempts']==1
        assert persisted['totals']['pending_usage_ceiling_cny']=='0.0670488'
        assert persisted['events'][-1]['type']=='send_intent'
        assert json.loads(req.content)==wire
        if outcome=='timeout':raise httpx2.ReadTimeout('synthetic timeout',request=req)
        body=response()
        if outcome=='wrong_model':body['model']='other-model'
        if outcome=='unknown_usage':body.pop('usage')
        return httpx2.Response(200,json=body)
    client=MockBackend(httpx2.MockTransport(handler)).client('report',ledger,rid,context)
    try:
        if outcome=='settled':
            assert client.complete(req).text=='{}'
            status=ledger.status()
            assert not status['halted'] and status['inflight']==client.attempt_id
            assert status['attempts'][client.attempt_id]['state']=='settled'
        else:
            code='execution_timeout' if outcome=='timeout' else 'upstream_error'
            with pytest.raises(ServiceError,match=code):client.complete(req)
        status=ledger.status()
        assert len(calls)==1 and status['new_attempts']==1
        assert status['retained_reserve_cny']=='0.7499544'
        if outcome in ('settled','wrong_model'):
            assert status['actual_cny']=='0.05534958'
            assert float(status['pending_usage_ceiling_cny'])==0
        else:
            assert status['actual_cny']=='0.05320458'
            assert status['pending_usage_ceiling_cny']=='0.0670488'
        if outcome!='timeout':
            settled=[e for e in status['events'] if e['type']=='settled']
            assert len(settled)==1 and settled[0]['raw']['id']=='resp_synthetic'
        assert store.get(rid)['payload'] is None
    finally:
        client.close();store.close()
    reopened=ResourceStore(tmp_path/'store');reopened.open()
    try:
        ledger=BillingJournal(reopened);after=ledger.status()
        for key in ('actual_cny','retained_reserve_cny','pending_usage_ceiling_cny','new_attempts'):
            assert after[key]==status[key]
        assert after['halted'] and reopened.get(rid)['state']=='interrupted'
        blocked=MockBackend(httpx2.MockTransport(handler)).client('report',ledger,rid,context)
        try:
            with pytest.raises(ServiceError,match='budget_halted'):blocked.complete(req)
        finally:blocked.close()
        assert len(calls)==1
    finally:reopened.close()
