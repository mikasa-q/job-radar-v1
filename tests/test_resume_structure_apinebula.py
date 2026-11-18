"""Entirely synthetic: real pinned SDK + in-memory HTTP transport; no sockets."""
import copy
import hashlib
import json
import socket
import traceback
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest

from job_radar.resume_models import ResumeDocument, ResumePage, ResumeBlock, ResumeProvenance
from job_radar.resume_structure import (ResumeStructureError, ResumeDraft,
                                       build_structure_request, structure_resume)
from job_radar.resume_structure_apinebula import (
    APINebulaStructureClient, APINebulaError, RateCard, strict_schema,
    BASE_URL, MODEL, REASONING_EFFORT, MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS,
    BUDGET_USD, MAX_WIRE_BYTES, TIMEOUT_SECONDS,
)

GOLDEN = json.loads((Path(__file__).parent/'fixtures/resume_structure_golden.json').read_text())['cases']
EMPTY = dict(education=[], skills=[], projects=[], work_experience=[], summary=None, warnings=[])
# Fictional test-only USD tariff. Not APINebula's account or published tariff.
RATES = RateCard('synthetic-test-group', 'synthetic-unit', '0.78', '3.9', '1')


def document(pages=None):
    pages = pages or [['合成大学；本科在读。']]
    return ResumeDocument(provenance=ResumeProvenance(
        source_sha256=hashlib.sha256(json.dumps(pages).encode()).hexdigest(),
        parser_version='synthetic-v1', extractor_version='synthetic-v1'),
        pages=[ResumePage(page_number=i,width=600.0,height=800.0,blocks=[
            ResumeBlock(block_id=f'p{i:03d}-b{j:03d}',text=t,bbox=(0.0,0.0,500.0,20.0))
            for j,t in enumerate(lines,1)]) for i,lines in enumerate(pages,1)])


def response(draft=None):
    return {'id':'resp_synthetic','object':'response','model':MODEL,'status':'completed',
            'output':[{'type':'message','id':'msg_synthetic','role':'assistant','status':'completed',
                       'content':[{'type':'output_text','text':json.dumps(EMPTY if draft is None else draft,ensure_ascii=False)}]}],
            'usage':{'input_tokens':1000,'output_tokens':100,'total_tokens':1100}}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-secret-never-real')
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('socket used'))
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:pytest.fail('socket used'))


@pytest.fixture
def make_client():
    clients=[]
    def make(*,result=None,rates=RATES,handler=None):
        calls=[]
        def handle(req):
            calls.append(req)
            if handler is not None:
                return handler(req)
            return httpx2.Response(200,json=response() if result is None else result)
        c=APINebulaStructureClient(rates,_transport=httpx2.MockTransport(handle))
        clients.append(c)
        return c,calls
    yield make
    for c in clients: c.close()


def request():
    return build_structure_request(document(), timeout_seconds=TIMEOUT_SECONDS)


def fail(c,code):
    with pytest.raises(APINebulaError) as err: c.complete(request())
    assert err.value.code==code and str(err.value)==code
    assert c.audit['halted']==code
    return err


def test_pinned_sdk_fixed_host_env_key_and_identical_count_payload(make_client,monkeypatch):
    monkeypatch.setenv('OPENAI_BASE_URL','https://wrong.invalid/v1')
    monkeypatch.setenv('OPENAI_ORG_ID','unexpected-org')
    c,calls=make_client()
    assert c.complete(request()).status=='completed'
    assert len(calls)==1
    create=json.loads(calls[0].content)
    assert create['max_output_tokens']==8192 and create['store'] is False
    assert create['tools']==[] and create['stream'] is False
    assert create['model']==MODEL and create['reasoning']=={'effort':'low'}
    assert create['instructions']==request().system_prompt
    assert create['input']==request().blocks_json
    assert create['text']['format']['strict'] is True
    assert create['text']['format']['schema']==strict_schema()
    assert create['truncation']=='disabled'
    assert str(calls[0].url)==BASE_URL+'/responses'
    assert calls[0].headers['authorization']=='Bearer synthetic-secret-never-real'
    assert not calls[0].headers.get('openai-organization')
    assert all(89 <= v <= 90 for v in calls[0].extensions['timeout'].values())
    assert TIMEOUT_SECONDS == 90.0
    assert c._sdk.timeout == 90.0
    assert c.audit['count_api_requests']==0
    assert c._sdk.max_retries==0
    assert c._sdk._client.follow_redirects is False
    assert c.audit['reserved_usd']=='0.0413088'
    assert c.audit['reported_generation_cost_ceiling_units']=='0.00117'
    assert 'synthetic-secret' not in json.dumps(c.audit)


def test_missing_key_no_construction_or_network(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY')
    with pytest.raises(APINebulaError,match='missing_api_key'): APINebulaStructureClient(RATES)


def test_sdk_version_must_match(monkeypatch):
    import job_radar.resume_structure_apinebula as module
    monkeypatch.setattr(module.openai,'__version__','0.0')
    with pytest.raises(APINebulaError,match='sdk_version_mismatch'): APINebulaStructureClient(RATES)


@pytest.mark.parametrize('field,value',[(f,v) for f in ['input_per_million','output_per_million','usd_reserve_per_unit','cache_create_per_million']
    for v in ['NaN','Infinity','-1','x',1,None,'1e999','1e-999'] if not (f=='cache_create_per_million' and v is None)]+[('usd_reserve_per_unit','0'),('input_per_million','0'),('group',''),('unit',' ')])
def test_tariff_strict_positive_finite_explicit_units(field,value):
    with pytest.raises(APINebulaError,match='invalid_tariff'): replace(RATES,**{field:value})


def test_tariff_required_no_public_price_fallback():
    with pytest.raises(APINebulaError,match='tariff_required'): APINebulaStructureClient(None)


def test_strict_schema_all_nested_fields_required_without_local_mutation():
    before=ResumeDraft.model_json_schema()
    def check(node):
        if isinstance(node,dict):
            assert 'default' not in node
            if node.get('type')=='object':
                assert node['additionalProperties'] is False
                assert set(node['required'])==set(node['properties'])
            for v in node.values():check(v)
        elif isinstance(node,list):
            for v in node:check(v)
    check(strict_schema())
    assert ResumeDraft.model_json_schema()==before
    assert {'type':'null'} in strict_schema()['$defs']['Education']['properties']['school']['anyOf']
    assert 'context' in strict_schema()['$defs']['SkillEvidence']['required']


@pytest.mark.parametrize('case',GOLDEN,ids=[c['name'] for c in GOLDEN])
def test_synthetic_golden_through_real_sdk_transport_and_existing_evidence_chain(case,make_client):
    # Fill local defaults to imitate strict-schema wire output, not LLM intelligence.
    draft=ResumeDraft.model_validate(case['response']).model_dump()
    c,calls=make_client(result=response(draft))
    out=structure_resume(document(case['pages']),client=c,resume_id='synthetic')
    p=out.profile; expected=case['expected']
    val=lambda v:None if v is None else v.value
    assert [s.name for s in p.skills]==expected['skills']
    assert [val(e.school) for e in p.education]==expected['schools']
    assert [val(e.status) for e in p.education]==expected['education_status']
    assert [val(e.nature) for e in p.projects]==expected['project_nature']
    assert [val(e.employment_type) for e in p.work_experience]==expected['employment_type']
    assert val(p.summary)==expected['summary']
    assert [len(p.education),len(p.projects),len(p.work_experience)]==expected['counts']
    assert p.review_status=='unconfirmed' and len(calls)==1


def test_tampered_quote_still_fails_local_evidence(make_client):
    draft=copy.deepcopy(GOLDEN[0]['response'])
    draft['skills'][0]['mention']['evidence'][0]['quote']='Python fabricated quote'
    c,calls=make_client(result=response(draft))
    with pytest.raises(ResumeStructureError) as e:
        structure_resume(document(GOLDEN[0]['pages']),client=c,resume_id='synthetic')
    assert e.value.code=='evidence_error' and len(calls)==1




def test_three_attempts_no_refunds_and_fourth_no_http(make_client):
    c,calls=make_client()
    for _ in range(3): assert c.complete(request()).status=='completed'
    assert c.audit['local_count_checks']==c.audit['generation_attempts']==3
    assert Decimal(c.audit['reserved_usd'])==Decimal('0.1239264')
    fail(c,'attempt_limit'); assert len(calls)==3




@pytest.mark.parametrize('status',[301,302,307,400,401,403,404,429,500,503])
def test_http_errors_no_redirect_retry_or_fallback(make_client,status):
    c,calls=make_client(handler=lambda req:httpx2.Response(status,headers={'location':'https://other.invalid/'},
        json={'error':{'message':'synthetic-secret-never-real body'}}))
    err=fail(c,f'provider_http_{status}')
    assert len(calls)==1 and 'synthetic-secret' not in ''.join(traceback.format_exception(err.value))


def test_generation_http_failure_consumes_full_reservation(make_client):
    c,calls=make_client(handler=lambda req:httpx2.Response(500,json={'error':'x'}))
    fail(c,'provider_http_500')
    assert len(calls)==1 and c.audit['reserved_usd']=='0.0413088'


def test_timeout_maps_once_and_halts(make_client):
    def handle(req):raise httpx2.ReadTimeout('synthetic-secret-never-real',request=req)
    c,calls=make_client(handler=handle)
    with pytest.raises(TimeoutError) as err:c.complete(request())
    assert str(err.value)=='provider_timeout' and len(calls)==1
    assert c.audit['halted']=='provider_timeout'


@pytest.mark.parametrize('usage',[None,{}, {'input_tokens':1,'output_tokens':True,'total_tokens':2},
    {'input_tokens':1,'output_tokens':2,'total_tokens':4}])
def test_unknown_usage_stops_next_call(make_client,usage):
    r=response();r['usage']=usage;c,calls=make_client(result=r)
    fail(c,'unknown_usage')
    with pytest.raises(APINebulaError,match='run_halted'):c.complete(request())
    assert len(calls)==1


@pytest.mark.parametrize('inp,out',[(12001,1),(1,8193)])
def test_over_ceiling_provider_usage_halts(make_client,inp,out):
    r=response();r['usage']={'input_tokens':inp,'output_tokens':out,'total_tokens':inp+out}
    c,calls=make_client(result=r);fail(c,'usage_limit');assert len(calls)==1


@pytest.mark.parametrize('status',['failed','queued',None,'in_progress'])
def test_invalid_provider_status(make_client,status):
    r=response();r['status']=status;c,_=make_client(result=r);fail(c,'invalid_provider_status')


def test_incomplete_and_refusal_map_and_stop(make_client):
    for kind,status in [('incomplete','incomplete'),('refusal','refused')]:
        r=response()
        if kind=='incomplete':r['status']='incomplete'
        else:r['output'][0]['content']=[{'type':'refusal','refusal':'synthetic'}]
        c,calls=make_client(result=r)
        assert c.complete(request()).status==status
        with pytest.raises(APINebulaError,match='run_halted'):c.complete(request())
        assert len(calls)==1


def test_model_switch_detected(make_client):
    r=response();r['model']='different-model';c,_=make_client(result=r)
    fail(c,'response_model_mismatch')


@pytest.mark.parametrize('output',[None,[],[{'type':'function_call'}],[{'type':'message','role':'user','status':'completed','content':[]}],
    [{'type':'message','role':'assistant','status':'incomplete','content':[]}],
    [{'type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'{}'},{'type':'output_text','text':'{}'}]}]])
def test_malformed_output_is_not_silently_joined(make_client,output):
    r=response();r['output']=output;c,_=make_client(result=r);fail(c,'invalid_provider_output')


def test_output_byte_cap_with_wire_overhead_and_reasoning_usage(make_client):
    r=response();r['output'][0]['content'][0]['text']='x'*(128*1024+1)
    c,_=make_client(result=r);fail(c,'output_byte_limit')
    r=response();r['output'].insert(0,{'type':'reasoning','summary':[]})
    r['usage']={'input_tokens':12000,'output_tokens':8192,'total_tokens':20192,
                'output_tokens_details':{'reasoning_tokens':8000}}
    c,_=make_client(result=r);assert c.complete(request()).status=='completed'
    assert c.audit['reported_generation_cost_ceiling_units']=='0.0413088'



def test_stream_body_is_bounded(make_client):
    class Large(httpx2.SyncByteStream):
        def __iter__(self):
            for _ in range(300):yield b'x'*4096
    def handle(req):
        if req.url.path.endswith('/input_tokens'):
            return httpx2.Response(200,json={'object':'response.input_tokens','input_tokens':1})
        return httpx2.Response(200,stream=Large())
    c,_=make_client(handler=handle);fail(c,'provider_body_limit')


@pytest.mark.parametrize('field,value',[('system_prompt','changed'),('prompt_version','changed'),('output_schema_json','{}'),
    ('max_output_bytes',131073),('timeout_seconds',91),('timeout_seconds',float('nan')),
    ('blocks_json','{}'),('blocks_json','{"blocks":[]}'),('blocks_json','x'*131073)])
def test_adapter_revalidates_request_before_http(make_client,field,value):
    c,calls=make_client()
    with pytest.raises(APINebulaError,match='invalid_request'):c.complete(replace(request(),**{field:value}))
    assert calls==[]


def test_closed_client_no_request(make_client):
    c,calls=make_client();c.close()
    with pytest.raises(APINebulaError,match='client_closed'):c.complete(request())
    assert calls==[]




def test_budget_lock_serializes_shared_client(make_client):
    from concurrent.futures import ThreadPoolExecutor
    c,calls=make_client()
    def execute(_):
        try:return c.complete(request()).status
        except APINebulaError as error:return error.code
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(execute,range(4)))
    assert results.count('completed')==3 and results.count('attempt_limit')==1
    assert len(calls)==3 and c.audit['generation_attempts']==3


def test_detached_audit_and_close_idempotent(make_client):
    c,calls=make_client();c.complete(request())
    audit=c.audit;audit['usage'][0]['input_tokens']=999999;audit['local_count_checks']=99
    assert c.audit['usage'][0]['input_tokens']==1000 and c.audit['local_count_checks']==1
    c.close();c.close()


def test_empty_text_and_invalid_json_remain_existing_local_errors(make_client):
    for text,code in [('', 'empty_response'),('not json','invalid_json'),('{"education":[]}','schema_error')]:
        r=response();r['output'][0]['content'][0]['text']=text
        c,calls=make_client(result=r)
        with pytest.raises(ResumeStructureError) as e:
            structure_resume(document(),client=c,resume_id='synthetic')
        assert e.value.code==code and len(calls)==1



def test_cli_offline_and_live_preflight_never_read_auth_files(monkeypatch,capsys):
    import importlib.util
    example=Path(__file__).parents[1]/'examples/resume_structure_apinebula_synthetic.py'
    spec=importlib.util.spec_from_file_location('apinebula_demo',example)
    demo=importlib.util.module_from_spec(spec);spec.loader.exec_module(demo)
    assert demo.main([])==0
    output=capsys.readouterr().out
    assert 'mode=offline; real_requests=0' in output and output.count('result=validated')==3
    assert demo.main(['--live'])==2
    assert 'tariff_required; real_requests=0' in capsys.readouterr().out
    monkeypatch.delenv('OPENAI_API_KEY')
    assert demo.main(['--live','--group','synthetic','--unit','synthetic-unit',
        '--input-rate','0.78','--output-rate','3.9','--usd-reserve-per-unit','1','--cache-create-rate','0.975'])==2
    assert 'missing_api_key; real_requests=0' in capsys.readouterr().out


def test_local_utf8_allocation_includes_prompt_schema_no_count_endpoint(make_client):
    c,calls=make_client();r=request();c.complete(r)
    payload=json.loads(calls[0].content)
    for key in ['max_output_tokens','store','tools','stream']:payload.pop(key)
    expected=len(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode())+1024
    assert c.audit['usage'][0]['local_input_allocation']==expected<=12000
    assert c.audit['count_api_requests']==0 and all(r.url.path=='/v1/responses' for r in calls)
    assert expected>len(r.blocks_json.encode())+len(r.system_prompt.encode())


def test_local_input_boundary_before_any_http(make_client):
    c,calls=make_client()
    r=build_structure_request(document([['x']]))
    initial=len(json.dumps(c._payload(r),ensure_ascii=False,separators=(',',':')).encode())+1024
    exact=build_structure_request(document([['x'*(12000-initial+1)]]))
    assert c.complete(exact).status=='completed'
    assert c.audit['usage'][0]['local_input_allocation']==12000
    c,calls=make_client()
    too_large=build_structure_request(document([['x'*(12000-initial+2)]]))
    with pytest.raises(APINebulaError,match='input_token_limit'):c.complete(too_large)
    assert calls==[]


def test_currency_reservation_cache_creation_and_budget_preflight(make_client):
    c,calls=make_client(rates=replace(RATES,usd_reserve_per_unit='10'))
    fail(c,'budget_limit');assert calls==[] and c.audit['generation_attempts']==0
    rates=replace(RATES,unit='CNY',cache_create_per_million='0.975')
    assert rates.generation_units(12000,8192)==Decimal('0.0530088')
    c,calls=make_client(rates=rates)
    for _ in range(3):c.complete(request())
    assert Decimal(c.audit['reserved_usd'])==Decimal('0.1590264')<BUDGET_USD
    assert c.audit['billing_unit']=='CNY' and c.audit['count_api_requests']==0


def test_generation_deadline_checked_after_transport(make_client,monkeypatch):
    import job_radar.resume_structure_apinebula as module
    clock=[100.0];monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    def handler(req):clock[0]=191.0;return httpx2.Response(200,json=response())
    c,calls=make_client(handler=handler)
    with pytest.raises(TimeoutError):c.complete(request())
    assert len(calls)==1 and c.audit['generation_attempts']==1


@pytest.mark.parametrize('seconds',[30.0,31.0,60.0,90.0])
def test_timeout90_cap_respects_explicit_shorter_requests(make_client,seconds):
    c,calls=make_client()
    r=replace(request(),timeout_seconds=seconds)
    assert c.complete(r).status=='completed'
    assert all(seconds-1 <= value <= seconds for value in calls[0].extensions['timeout'].values())
    assert c.audit['generation_attempts']==1 and c._sdk.max_retries==0


def test_response_after_old30_deadline_before90_is_accepted(make_client,monkeypatch):
    import job_radar.resume_structure_apinebula as module
    clock=[100.0];monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    def handler(req):
        assert all(value==90.0 for value in req.extensions['timeout'].values())
        clock[0]=160.0
        return httpx2.Response(200,json=response())
    c,calls=make_client(handler=handler)
    assert c.complete(request()).status=='completed'
    assert len(calls)==1 and c.audit['halted'] is None


def _load_demo():
    import importlib.util
    p=Path(__file__).parents[1]/'examples/resume_structure_apinebula_synthetic.py'
    spec=importlib.util.spec_from_file_location('apinebula_timeout90_demo',p)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_runner_first_success_unlocks_later_two_and_passes90(make_client,capsys):
    demo=_load_demo();outputs=iter(GOLDEN)
    def handler(req):
        assert all(89<=value<=90 for value in req.extensions['timeout'].values())
        case=next(outputs)
        return httpx2.Response(200,json=response(ResumeDraft.model_validate(case['response']).model_dump()))
    c,calls=make_client(handler=handler)
    assert demo.exercise(GOLDEN,c)==0
    assert len(calls)==3 and c.audit['generation_attempts']==3 and c.audit['count_api_requests']==0
    text=capsys.readouterr().out
    assert text.count('result=validated')==3 and text.index(GOLDEN[0]['name'])<text.index(GOLDEN[1]['name'])<text.index(GOLDEN[2]['name'])


@pytest.mark.parametrize('first_failure',['timeout','golden_mismatch','evidence_error'])
def test_runner_first_failure_stops_without_later_calls(make_client,capsys,first_failure):
    demo=_load_demo()
    def handler(req):
        if first_failure=='timeout':raise httpx2.ReadTimeout('synthetic',request=req)
        data=copy.deepcopy(GOLDEN[0]['response']) if first_failure=='evidence_error' else copy.deepcopy(EMPTY)
        if first_failure=='evidence_error':data['skills'][0]['mention']['evidence'][0]['quote']='Python fabricated quote'
        return httpx2.Response(200,json=response(data))
    c,calls=make_client(handler=handler)
    assert demo.exercise(GOLDEN,c)==1
    assert len(calls)==1 and c.audit['generation_attempts']==1 and c.audit['count_api_requests']==0
    text=capsys.readouterr().out
    assert GOLDEN[0]['name'] in text and GOLDEN[1]['name'] not in text and GOLDEN[2]['name'] not in text
    assert 'stopped' in text


def test_neutral_offline_default_stays30():
    from job_radar.resume_structure import DEFAULT_TIMEOUT_SECONDS
    assert DEFAULT_TIMEOUT_SECONDS==30.0
    assert build_structure_request(document()).timeout_seconds==30.0
