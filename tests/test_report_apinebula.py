"""Synthetic-only pinned SDK tests using in-memory MockTransport; no sockets."""
import copy
from dataclasses import replace
from decimal import Decimal
import importlib
import json
import socket
import sys
import traceback

import httpx2
import pytest

from job_radar.report_models import AnalysisRequest,MatchAnalysisDraft,PROMPT_VERSION
from job_radar.report_apinebula import (APINebulaAnalysisClient,AnalysisProviderError,
    RateCard,strict_schema,BASE_URL,MODEL,MAX_WIRE_BYTES,TIMEOUT_SECONDS)

# Fictional test allocation and tariff; no account prices or old batch approval.
RATES=RateCard('synthetic-report-batch','synthetic-unit','0.78','3.9','1')


def packet():
    return dict(schema_version='match-analysis-input-v1',key=dict(dataset_id='synthetic',job_id=1),purpose='debug',
        context={'rank':1,'title':'合成岗位','bm25':None,'dense':None,'fusion_score':0.01,'rules':{},'channels':[],'retrieval_status':'ok','actual_mode':'bm25_rules'},evidence=[dict(evidence_id='e'+'a'*24,kind='scope',path='profile',key=None,content={'coverage':'supplied_profile'})],limitations=['synthetic_fixture'])


def request():
    from job_radar.report_analysis import SYSTEM_PROMPT
    return AnalysisRequest(system_prompt=SYSTEM_PROMPT,packet_json=json.dumps(packet(),ensure_ascii=False),
        output_schema_json=json.dumps(MatchAnalysisDraft.model_json_schema(),ensure_ascii=False),
        timeout_seconds=TIMEOUT_SECONDS,max_output_bytes=128*1024,prompt_version=PROMPT_VERSION)


def provider_response():
    return dict(id='resp_synthetic',object='response',model=MODEL,status='completed',
        output=[dict(type='message',id='msg_synthetic',role='assistant',status='completed',content=[dict(type='output_text',text='{}')])],
        usage=dict(input_tokens=1000,output_tokens=100,total_tokens=1100))


@pytest.fixture(autouse=True)
def no_sockets(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-report-key')
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('socket used'))
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:pytest.fail('socket used'))


@pytest.fixture
def make_client():
    made=[]
    def make(*,result=None,handler=None,**kwargs):
        calls=[]
        def handle(req):
            calls.append(req)
            return handler(req) if handler else httpx2.Response(200,json=provider_response() if result is None else result)
        options=dict(budget_usd='1',max_input_tokens=32000,max_output_tokens=8192)
        options.update(kwargs)
        client=APINebulaAnalysisClient(RATES,_transport=httpx2.MockTransport(handle),**options)
        made.append(client)
        return client,calls
    yield make
    for client in made:client.close()


def fail(client,code,req=None):
    with pytest.raises(AnalysisProviderError) as caught:client.complete(request() if req is None else req)
    assert caught.value.code==str(caught.value)==code
    assert client.audit['halted']==code
    return caught.value


def test_fixed_request_config_and_audit(make_client,monkeypatch):
    monkeypatch.setenv('OPENAI_BASE_URL','https://wrong.invalid/v1')
    monkeypatch.setenv('OPENAI_ORG_ID','wrong-org')
    client,calls=make_client()
    result=client.complete(request())
    assert result.status=='completed' and result.text=='{}' and len(calls)==1
    body=json.loads(calls[0].content)
    assert body['model']==MODEL and body['reasoning']=={'effort':'low'}
    assert body['instructions']==request().system_prompt and body['input']==request().packet_json
    assert body['tools']==[] and body['store'] is False and body['stream'] is False
    assert body['truncation']=='disabled' and body['max_output_tokens']==8192
    assert body['text']['format']==dict(type='json_schema',name='match_analysis_draft',strict=True,schema=strict_schema())
    assert str(calls[0].url)==BASE_URL+'/responses'
    assert calls[0].headers['authorization']=='Bearer synthetic-report-key'
    assert not calls[0].headers.get('openai-organization')
    assert client._sdk.max_retries==0 and client._sdk._client.follow_redirects is False
    assert all(89<=v<=90 for v in calls[0].extensions['timeout'].values())
    audit=client.audit
    assert audit['generation_attempts']==1 and audit['count_api_requests']==0 and audit['usage_known']
    assert audit['reserved_usd']=='0.0569088'
    assert result.audit.input_tokens==1000 and result.audit.total_tokens==1100
    assert result.audit.attempts==1 and result.audit.elapsed_seconds>=0
    assert 'synthetic-report-key' not in json.dumps(audit)


def test_strict_schema_separate_and_recursive():
    before=MatchAnalysisDraft.model_json_schema()
    def walk(value):
        if isinstance(value,dict):
            assert 'default' not in value and 'title' not in value
            if value.get('type')=='object':
                assert value['additionalProperties'] is False
                assert set(value['required'])==set(value['properties'])
            for item in value.values():walk(item)
        if isinstance(value,list):
            for item in value:walk(item)
    walk(strict_schema())
    assert MatchAnalysisDraft.model_json_schema()==before


def test_explicit_budget_and_limits_required():
    with pytest.raises(TypeError):APINebulaAnalysisClient(RATES)
    with pytest.raises(AnalysisProviderError,match='tariff_required'):
        APINebulaAnalysisClient(None,budget_usd='1',max_input_tokens=32000,max_output_tokens=8192)


@pytest.mark.parametrize('value',[None,1,'','NaN','Infinity','0','-1','1e999','1e-999'])
def test_invalid_budget(value,make_client):
    with pytest.raises(AnalysisProviderError,match='invalid_budget'):make_client(budget_usd=value)


@pytest.mark.parametrize('field,value',[(f,v) for f in ['max_input_tokens','max_output_tokens','max_attempts'] for v in [0,-1,True,1.0,None,99999999]]+ [('timeout_seconds',v) for v in [0,91,True,float('nan'),float('inf')]])
def test_invalid_limits(field,value,make_client):
    with pytest.raises(AnalysisProviderError,match='invalid_limits'):make_client(**{field:value})


def test_key_only_explicit_constructor_and_sdk_version(monkeypatch,make_client):
    monkeypatch.delenv('OPENAI_API_KEY')
    import importlib.util
    from pathlib import Path
    source=Path(__file__).parents[1]/'src/job_radar/report_apinebula.py'
    spec=importlib.util.spec_from_file_location('job_radar._report_provider_import_test',source)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod) # isolated import with key absent and no socket
    with pytest.raises(mod.AnalysisProviderError,match='missing_api_key'):
        mod.APINebulaAnalysisClient(RATES,budget_usd='1',max_input_tokens=32000,max_output_tokens=8192)
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-report-key')
    monkeypatch.setattr(mod.openai,'__version__','0.0')
    with pytest.raises(mod.AnalysisProviderError,match='sdk_version_mismatch'):
        mod.APINebulaAnalysisClient(RATES,budget_usd='1',max_input_tokens=32000,max_output_tokens=8192)


def test_attempt_limit_and_reservation_no_refunds(make_client):
    client,calls=make_client()
    for _ in range(3):assert client.complete(request()).status=='completed'
    fail(client,'attempt_limit')
    assert len(calls)==3 and client.audit['generation_attempts']==3
    assert Decimal(client.audit['reserved_usd'])==Decimal('0.1707264')


def test_budget_preflight_and_input_preflight_no_http(make_client):
    client,calls=make_client(budget_usd='0.001')
    fail(client,'budget_limit');assert calls==[] and client.audit['generation_attempts']==0
    client,calls=make_client(max_input_tokens=1)
    fail(client,'input_allocation_limit');assert calls==[] and client.audit['generation_attempts']==0


def test_count_includes_complete_wire_body_and_allowance(make_client):
    client,calls=make_client();client.complete(request())
    expected=len(json.dumps(json.loads(calls[0].content),ensure_ascii=False,separators=(',',':')).encode())+1024
    assert client.audit['usage'][0]['local_input_allocation']==expected
    assert client.audit['count_api_requests']==0


@pytest.mark.parametrize('status',[301,302,307,400,401,403,429,500,503])
def test_http_failures_once_halt_and_no_body_leak(make_client,status):
    client,calls=make_client(handler=lambda req:httpx2.Response(status,headers={'location':'https://other.invalid/'},json={'error':{'message':'synthetic-private-body'}}))
    error=fail(client,f'provider_http_{status}')
    assert len(calls)==1 and client.audit['generation_attempts']==1
    assert client.audit['reserved_usd']=='0.0569088' and not client.audit['usage_known']
    assert client.last_call_audit.input_tokens is None
    assert 'synthetic-private-body' not in ''.join(traceback.format_exception(error))
    with pytest.raises(AnalysisProviderError,match='run_halted'):client.complete(request())


def test_timeout_once_and_fails_remaining_deadline(make_client,monkeypatch):
    def timeout(req):raise httpx2.ReadTimeout('private-body',request=req)
    client,calls=make_client(handler=timeout)
    with pytest.raises(TimeoutError,match='provider_timeout'):client.complete(request())
    assert len(calls)==1 and client.audit['halted']=='provider_timeout'
    import job_radar.report_apinebula as mod
    now=[100.0];monkeypatch.setattr(mod.time,'monotonic',lambda:now[0])
    def slow(req):now[0]=191.0;return httpx2.Response(200,json=provider_response())
    client,calls=make_client(handler=slow)
    with pytest.raises(TimeoutError):client.complete(request())
    assert len(calls)==1


@pytest.mark.parametrize('seconds',[30.0,60.0,90.0])
def test_shorter_timeout_propagated(make_client,seconds):
    client,calls=make_client();client.complete(replace(request(),timeout_seconds=seconds))
    assert all(seconds-1<=v<=seconds for v in calls[0].extensions['timeout'].values())


@pytest.mark.parametrize('usage',[None,{},dict(input_tokens=1,output_tokens=True,total_tokens=2),dict(input_tokens=1,output_tokens=2,total_tokens=4)])
def test_unknown_usage_halts(make_client,usage):
    result=provider_response();result['usage']=usage
    client,calls=make_client(result=result);fail(client,'unknown_usage')
    assert len(calls)==1 and client.audit['reported_generation_cost_ceiling_units'] is None


@pytest.mark.parametrize('inp,out',[(32001,1),(1,8193)])
def test_usage_over_limit(make_client,inp,out):
    result=provider_response();result['usage']=dict(input_tokens=inp,output_tokens=out,total_tokens=inp+out)
    client,_=make_client(result=result);fail(client,'usage_limit')


@pytest.mark.parametrize('status',['failed','queued',None,'in_progress'])
def test_bad_status(make_client,status):
    result=provider_response();result['status']=status
    client,_=make_client(result=result);fail(client,'invalid_provider_status')


def test_incomplete_and_refusal_stop_batch(make_client):
    for mode in ['incomplete','refusal']:
        result=provider_response()
        if mode=='incomplete':result['status']='incomplete'
        else:result['output'][0]['content']=[dict(type='refusal',refusal='private')]
        client,calls=make_client(result=result)
        assert client.complete(request()).status==('incomplete' if mode=='incomplete' else 'refused')
        with pytest.raises(AnalysisProviderError,match='run_halted'):client.complete(request())
        assert len(calls)==1


def test_wrong_model_no_fallback(make_client):
    result=provider_response();result['model']='other'
    client,_=make_client(result=result);fail(client,'response_model_mismatch')


@pytest.mark.parametrize('output',[None,[],[dict(type='function_call')], [dict(type='message',role='user',status='completed',content=[])],
    [dict(type='message',role='assistant',status='completed',content=[dict(type='output_text',text='{}'),dict(type='output_text',text='{}')])]])
def test_bad_output_not_joined(make_client,output):
    result=provider_response();result['output']=output
    client,_=make_client(result=result);fail(client,'invalid_provider_output')


def test_output_and_wire_size_limits(make_client):
    result=provider_response();result['output'][0]['content'][0]['text']='x'*(128*1024+1)
    client,_=make_client(result=result);fail(client,'output_byte_limit')
    class Large(httpx2.SyncByteStream):
        def __iter__(self):
            for _ in range(300):yield b'x'*4096
    client,_=make_client(handler=lambda req:httpx2.Response(200,stream=Large()))
    fail(client,'provider_body_limit')


@pytest.mark.parametrize('body',[b'{"model":1,"model":2}',b'{"x":NaN}',b'[]',b'\xff'])
def test_provider_json_strict(make_client,body):
    client,_=make_client(handler=lambda req:httpx2.Response(200,content=body))
    fail(client,'invalid_provider_json')


@pytest.mark.parametrize('field,value',[('system_prompt','changed'),('prompt_version','changed'),('output_schema_json','{}'),
    ('output_schema_json','{"x":1,"x":2}'),('packet_json','{}'),('packet_json','[]'),('timeout_seconds',91),('timeout_seconds',True),('max_output_bytes',0),('max_output_bytes',1048577)])
def test_request_immutable_contract_rechecked(make_client,field,value):
    client,calls=make_client();fail(client,'invalid_analysis_request',replace(request(),**{field:value}))
    assert calls==[]


@pytest.mark.parametrize('mutate',[
    lambda p:p.update(extra='x'),lambda p:p.update(schema_version='wrong'),lambda p:p.update(purpose='wrong'),
    lambda p:p.update(key={'dataset_id':'x','job_id':True}),lambda p:p.update(evidence=[]),lambda p:p['context'].update(rank=True),
    lambda p:p['context'].update(actual_mode='unknown'),
    lambda p:p['evidence'][0].update(quote='original'),lambda p:p['evidence'][0].update(content={'email':'person@example.org'}),
    lambda p:p['evidence'][0].update(content={'json':'{"email":"person@example.org"}'}),
    lambda p:p['evidence'][0].update(key={'dataset_id':'other','job_id':2}),
    lambda p:p['evidence'][0].update(content={'file':'/Users/test/private.txt'}),
    lambda p:p['context'].update(url='https://example.org/private'),
    lambda p:p['evidence'][0].update(content={'quote':'original local quote'}),
    lambda p:p['evidence'][0].update(content={'ref':{'block_id':'p001-b001'}}),
    lambda p:p['evidence'][0].update(content={'file':'/private/tmp/private.txt'}),
])
def test_wire_minimization_and_shape_checked(make_client,mutate):
    data=packet();mutate(data)
    client,calls=make_client();fail(client,'invalid_analysis_request',replace(request(),packet_json=json.dumps(data)))
    assert calls==[]


def test_explicit_halt_after_local_validation_and_close(make_client):
    client,calls=make_client();client.complete(request());client.halt()
    with pytest.raises(AnalysisProviderError,match='run_halted'):client.complete(request())
    assert len(calls)==1
    client.close();client.close()
    with pytest.raises(AnalysisProviderError,match='client_closed'):client.complete(request())


def test_audit_detached_and_lock_serializes(make_client):
    from concurrent.futures import ThreadPoolExecutor
    client,calls=make_client()
    def run(_):
        try:return client.complete(request()).status
        except AnalysisProviderError as e:return e.code
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(run,range(4)))
    assert results.count('completed')==3 and results.count('attempt_limit')==1
    assert len(calls)==3
    audit=client.audit;audit['usage'][0]['input_tokens']=99999
    assert client.audit['usage'][0]['input_tokens']==1000
    last=client.last_call_audit
    assert last is None # rejected fourth call has no attempted generation


def test_real_packet_builder_through_provider_mock_and_local_chain(tmp_path,make_client):
    from job_radar.report_synthetic import synthetic_inputs,synthetic_manifest
    from job_radar.rag_index import DerivedIndex
    from job_radar.rag_engine import match_jobs_indexed
    from job_radar.report_evidence import build_evidence_packet
    from job_radar.report_analysis import build_analysis_request
    doc,req=synthetic_inputs()
    idx=DerivedIndex(tmp_path/'derived');idx.update(req.jobs,manifest=synthetic_manifest(req.jobs))
    ranked=match_jobs_indexed(req,index=idx)
    packet_value=build_evidence_packet(req,ranked,doc)
    built=build_analysis_request(packet_value,packet_value.selected_keys[0])
    client,calls=make_client(max_input_tokens=128*1024)
    assert client.complete(built).status=='completed'
    assert len(calls)==1
    outgoing=json.loads(json.loads(calls[0].content)['input'])
    assert outgoing['key']==packet_value.selected_keys[0].model_dump()
    assert all(set(e)=={'evidence_id','kind','path','key','content'} for e in outgoing['evidence'])


def test_structural_evidence_ids_not_mistaken_for_contacts(make_client):
    data=packet();data['evidence'][0]['evidence_id']='e'+'1234567890abcdef12345678'
    data['evidence'][0]['path']='document.pages[0].blocks[p001-b12345678]'
    client,calls=make_client()
    out=client.complete(replace(request(),packet_json=json.dumps(data)))
    assert out.status=='completed' and len(calls)==1
