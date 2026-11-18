"""Primary: same HTTP/profile/match/report + mock SDK + durable billing."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json
import threading
import time

import httpx2
import pytest
from fastapi.testclient import TestClient

from job_radar.api_v1 import create_app
from job_radar.api_v1_runtime import OfflineService
from job_radar.api_v1_store import ResourceStore,ServiceError
from job_radar.api_v1_billing import BillingJournal,Approval,initialize
from job_radar.api_v1_provider import MockBackend,build_wire
from job_radar.resume_models import ResumeDocument
from job_radar.resume_structure import build_structure_request
from job_radar.report_models import AnalysisConfig
from job_radar.report_analysis import build_analysis_request
from job_radar.report_evidence import build_evidence_packet
from job_radar.report_demo_client import SyntheticReportClient
from job_radar.rag_models import canonical_hash
from test_api_v1 import fixtures,pdf,wait,create_match

EMPTY=dict(education=[],skills=[],projects=[],work_experience=[],summary=None,warnings=[])


def provider_response(text):
    return {'model':'gpt-6.1-sol','status':'completed','output':[{'type':'message',
        'role':'assistant','status':'completed','content':[{'type':'output_text','text':text}]}],
        'usage':{'input_tokens':1000,'output_tokens':100,'total_tokens':1100}}


def context(kind,value,snapshot):return canonical_hash({'kind':kind,'input':value,'snapshot':snapshot})


def seed_resume(service,raw=None):
    from job_radar.resume_pdf import extract_resume_pdf
    doc=extract_resume_pdf(pdf() if raw is None else raw).model_dump(mode='json')
    row,_=service.store.create('/synthetic','seed-resume',{},'resume','pdf')
    service.store.transition(row['resource_id'],'running')
    service.store.transition(row['resource_id'],'succeeded',payload=doc)
    return row['resource_id'],doc


def planned_profile(rid,doc):
    request=build_structure_request(ResumeDocument.model_validate_json(json.dumps(doc)),timeout_seconds=90)
    return {'kind':'profile','context_sha256':context('profile',{'mode':'llm'},doc),
            'payload_sha256':canonical_hash(build_wire('profile',request)),
            'input_limit':12000,'output_limit':8192}


def approve(service,allowed):
    import hashlib
    seed=service.settings.store_root.parent/'seed-evidence.json'
    seed.write_text(json.dumps({'historical_actual_cny':'0.05320458',
        'historical_reserved_cny':'0.6829056'}))
    return initialize(service.store,Approval(root=str(service.store.root.resolve()),
        scope='synthetic-stage5-offline',seed_refs={str(seed.resolve()):hashlib.sha256(seed.read_bytes()).hexdigest()},
        expires_at=datetime.now(timezone.utc)+timedelta(hours=1),max_calls=2,allowed=allowed))


def profile_submit(client,rid,key='live-profile-0001'):
    return client.post(f'/v1/resumes/{rid}/profiles',json={'mode':'llm'},
        headers={'Idempotency-Key':key})


def planned_report(mid,snap):
    from job_radar.api_v1_reports import _match_snapshot
    saved=_match_snapshot(snap);config=AnalysisConfig(top_n=1)
    packet=build_evidence_packet(saved.request,saved.retrieval,saved.document,config=config)
    request=build_analysis_request(packet,packet.selected_keys[0],config=config)
    value={'match_id':mid,'top_n':1,'mode':'llm'}
    return value,request,{'kind':'report','context_sha256':context('report',value,snap),
        'payload_sha256':canonical_hash(build_wire('report',request)),
        'input_limit':20000,'output_limit':8192}


def scripted_report(request):
    # Author only the provable Docker unknown, not an unverified usage strength.
    data=json.loads(SyntheticReportClient().complete(request).text)
    claims=[c for c in data['claims'] if c['kind']!='strength']
    mapping={c['claim_id']:'c'+str(i) for i,c in enumerate(claims,1)}
    for c in claims:
        c['claim_id']=mapping[c['claim_id']]
        c['related_claim_ids']=[mapping[v] for v in c['related_claim_ids']]
    data['claims']=claims
    return json.dumps(data,ensure_ascii=False)


def test_http_profile_match_report_shared_persistent_billing_and_replay(tmp_path):
    from test_resume_structure import empty,skill
    draft=empty(skills=[skill('Python',quote='Course project: Python and FastAPI API development.')])
    calls=[];responses=[json.dumps(draft)]
    def handler(request):
        calls.append(json.loads(request.content));return httpx2.Response(200,json=provider_response(responses.pop(0)))
    settings=fixtures(tmp_path);backend=MockBackend(httpx2.MockTransport(handler))
    app=create_app(settings,mock_backend=backend)
    with TestClient(app) as client:
        service=app.state.offline_service;rid,doc=seed_resume(service)
        ledger=approve(service,[planned_profile(rid,doc)])
        r=profile_submit(client,rid);assert r.status_code==202,r.text
        row=wait(client,r.json()['data']['status_url']);assert row['state']=='succeeded',row
        pid=row['resource_id'];assert row['execution_mode']=='llm'
        assert row['payload']['profile']['review_status']=='unconfirmed'
        assert [s['name'] for s in row['payload']['profile']['skills']]==['Python']
        assert row['payload']['comparison']['rule_only_skills']==['FastAPI']
        match,_=create_match(client,pid);assert match['state']=='succeeded',match
        value,request,allowed=planned_report(match['resource_id'],match['payload'])
        ledger.authorize([allowed]);responses.append(scripted_report(request))
        r=client.post('/v1/reports',json=value,headers={'Idempotency-Key':'live-report-0001'})
        assert r.status_code==202,r.text
        report=wait(client,r.json()['data']['status_url']);assert report['state']=='succeeded',report
        assert report['payload']['report']['status']=='completed'
        assert report['payload']['report']['review_status']=='unconfirmed'
        assert report['payload']['report']['packet']['retrieval']==match['payload']['retrieval']
        assert '本报告使用模拟响应' in report['payload']['markdown'] and '预设响应只验证' not in report['payload']['markdown']
        assert client.get(f"/v1/reports/{report['resource_id']}?format=markdown").text==report['payload']['markdown']
        assert client.get('/v1/ready').json()['data']['real_llm'] is False
        state=ledger.status();assert state['new_attempts']==2 and not state['halted'] and state['inflight'] is None
        assert state['actual_cny']=='0.05749458'
        assert state['retained_reserve_cny']=='0.8029632'
        from decimal import Decimal
        assert Decimal(state['pending_usage_ceiling_cny'])==0
        assert len(calls)==2 and all(w['model']=='gpt-6.1-sol' and w['reasoning']['effort']=='low' for w in calls)
        assert all(w['max_output_tokens']==8192 and not w['store'] and not w['stream'] and w['tools']==[] for w in calls)
        # Parent/dependency removal must not force a paid replay.
        ledger.halt('budget_halted');settings.evidence_path.unlink()
        settings.index_root.rename(tmp_path/'removed-index')
        assert profile_submit(client,rid).json()['data']['resource_id']==pid
        assert client.post('/v1/reports',json=value,headers={'Idempotency-Key':'live-report-0001'}).json()['data']['resource_id']==report['resource_id']
        assert len(calls)==2
    # Startup with terminal facts preserves spend, no automatic reauthorization.
    with TestClient(create_app(settings,mock_backend=backend)) as client:
        assert profile_submit(client,rid).json()['data']['resource_id']==pid
        assert client.post('/v1/reports',json=value,headers={'Idempotency-Key':'live-report-0001'}).status_code==202
        assert client.app.state.offline_service.store.state['billing']['totals']['new_attempts']==2
        assert len(calls)==2


def test_concurrent_same_key_is_one_generation(tmp_path):
    calls=[];entered=threading.Event();release=threading.Event()
    def handler(request):
        calls.append(request);entered.set();assert release.wait(5)
        return httpx2.Response(200,json=provider_response(json.dumps(EMPTY)))
    app=create_app(fixtures(tmp_path),mock_backend=MockBackend(httpx2.MockTransport(handler)))
    with TestClient(app) as client:
        service=app.state.offline_service;rid,doc=seed_resume(service);ledger=approve(service,[planned_profile(rid,doc)])
        first=profile_submit(client,rid);assert entered.wait(5)
        with ThreadPoolExecutor(max_workers=4) as pool:
            replays=list(pool.map(lambda _:profile_submit(client,rid),range(4)))
        assert all(r.status_code==202 for r in replays)
        assert len({r.json()['data']['resource_id'] for r in [first]+replays})==1
        other=profile_submit(client,rid,'other-profile-key')
        assert other.status_code==503 and other.json()['error']['code']=='budget_halted'
        release.set();assert wait(client,first.json()['data']['status_url'])['state']=='succeeded'
        assert len(calls)==1 and ledger.status()['new_attempts']==1
        # A new key cannot buy the same approved payload twice.
        rejected=profile_submit(client,rid,'after-profile-key')
        assert rejected.status_code==503 and rejected.json()['error']['code']=='attempt_limit'
        assert len(calls)==1


@pytest.mark.parametrize('mode',['unknown_usage','domain_invalid','wrong_model','refused','bad_json','http_500','timeout'])
def test_failures_keep_fees_reserves_and_halt_globally(tmp_path,mode):
    calls=[]
    def handler(request):
        calls.append(request)
        if mode=='timeout':raise httpx2.ReadTimeout('synthetic',request=request)
        if mode=='http_500':return httpx2.Response(500,json={'error':{'message':'synthetic'}})
        if mode=='bad_json':return httpx2.Response(200,text='{not-json')
        value=provider_response('not a valid draft' if mode=='domain_invalid' else json.dumps(EMPTY))
        if mode=='unknown_usage':value.pop('usage')
        if mode=='wrong_model':value['model']='wrong-model'
        if mode=='refused':value['status']='incomplete'
        return httpx2.Response(200,json=value)
    settings=fixtures(tmp_path);backend=MockBackend(httpx2.MockTransport(handler));app=create_app(settings,mock_backend=backend)
    with TestClient(app) as client:
        service=app.state.offline_service;rid,doc=seed_resume(service);ledger=approve(service,[planned_profile(rid,doc)])
        r=profile_submit(client,rid);assert r.status_code==202,r.text
        row=wait(client,r.json()['data']['status_url']);assert row['state']=='failed' and row['payload'] is None,row
        assert len(calls)==1
        s=ledger.status();assert s['halted'] and s['new_attempts']==1
        assert s['retained_reserve_cny']=='0.7359144'
        known=mode in ('domain_invalid','wrong_model','refused')
        assert s['actual_cny']==('0.05534958' if known else '0.05320458')
        from decimal import Decimal
        assert Decimal(s['pending_usage_ceiling_cny'])==(Decimal(0) if known else Decimal('.0530088'))
        if mode in ('unknown_usage','domain_invalid','wrong_model','refused','bad_json'):
            assert any(e['type']=='settled' and e['raw'] for e in s['events'])
        assert profile_submit(client,rid,'after-failure-key').status_code==503
        assert profile_submit(client,rid).json()['data']['resource_id']==row['resource_id']
        saved=s
    with TestClient(create_app(settings,mock_backend=backend)) as client:
        s=BillingJournal(client.app.state.offline_service.store).status()
        assert s['actual_cny']==saved['actual_cny'] and s['retained_reserve_cny']==saved['retained_reserve_cny']
        assert s['halted'] and len(calls)==1
        assert profile_submit(client,rid).status_code==202


def test_absent_approval_or_different_root_never_auto_seeds(tmp_path):
    calls=[]
    backend=MockBackend(httpx2.MockTransport(lambda request:calls.append(request)))
    for name in ('first','different'):
        root=tmp_path/name;root.mkdir()
        app=create_app(fixtures(root),mock_backend=backend)
        with TestClient(app) as client:
            rid,doc=seed_resume(app.state.offline_service)
            assert profile_submit(client,rid).status_code==503
            assert 'billing' not in app.state.offline_service.store.state
    assert calls==[]


@pytest.mark.parametrize('boundary',['reserve','settle','publish'])
def test_persistence_failure_never_resends_and_startup_halts(tmp_path,monkeypatch,boundary):
    calls=[]
    backend=MockBackend(httpx2.MockTransport(lambda request:(calls.append(request) or httpx2.Response(200,json=provider_response(json.dumps(EMPTY))))))
    settings=fixtures(tmp_path);app=create_app(settings,mock_backend=backend)
    with TestClient(app) as client:
        service=app.state.offline_service;rid,doc=seed_resume(service);approve(service,[planned_profile(rid,doc)])
        store=service.store;oldsave=store._save
        def fail():
            b=store.state.get('billing',{});events=b.get('events',[])
            types=[e['type'] for e in events]
            trigger=(boundary=='reserve' and 'send_intent' in types or
                boundary=='settle' and 'settled' in types or boundary=='publish' and 'complete' in types)
            if trigger:raise OSError('synthetic disk failure')
            return oldsave()
        monkeypatch.setattr(store,'_save',fail)
        response=profile_submit(client,rid);assert response.status_code==202,response.text
        for _ in range(500):
            if store.write_failed:break
            time.sleep(.005)
        assert store.write_failed
        assert len(calls)==(0 if boundary=='reserve' else 1)
        assert client.get('/v1/profiles/'+response.json()['data']['resource_id']).status_code==503
    with TestClient(create_app(settings,mock_backend=backend)) as client:
        store=client.app.state.offline_service.store
        row=store.get(response.json()['data']['resource_id']);assert row['state']=='interrupted'
        state=BillingJournal(store).status()
        assert state['new_attempts']==(0 if boundary=='reserve' else 1)
        assert state['halted']==(boundary!='reserve')
        assert profile_submit(client,rid).json()['data']['resource_id']==row['resource_id']
        assert len(calls)==(0 if boundary=='reserve' else 1)


def test_existing_store_missing_journal_is_not_a_fresh_budget(tmp_path):
    root=tmp_path/'store';store=ResourceStore(root);store.open();store.close()
    (root/'state.json').unlink()
    with pytest.raises(ServiceError) as caught:ResourceStore(root).open()
    assert caught.value.code=='snapshot_corrupt'
    assert not (root/'state.json').exists()


def test_profile_preserves_citations_and_comparison_not_skill_union(tmp_path):
    from test_resume_structure import empty,skill
    raw=pdf('Python course project. FastAPI API development.')
    draft=empty(skills=[skill('Python',quote='Python course project. FastAPI API development.')])
    calls=[]
    def handler(request):
        calls.append(request);return httpx2.Response(200,json=provider_response(json.dumps(draft)))
    app=create_app(fixtures(tmp_path),mock_backend=MockBackend(httpx2.MockTransport(handler)))
    with TestClient(app) as client:
        service=app.state.offline_service;rid,doc=seed_resume(service,raw)
        approve(service,[planned_profile(rid,doc)])
        response=profile_submit(client,rid);assert response.status_code==202,response.text
        row=wait(client,response.json()['data']['status_url']);assert row['state']=='succeeded',row
        payload=row['payload']
        assert [s['name'] for s in payload['profile']['skills']]==['Python']
        assert payload['comparison']['rule_only_skills']==['FastAPI']
        assert payload['profile']['review_status']=='unconfirmed'
        from job_radar.resume_structure import structure_resume,ClientResponse
        from job_radar.resume_structure_fake import FakeStructureClient
        direct=structure_resume(ResumeDocument.model_validate_json(json.dumps(doc)),resume_id=rid,
            client=FakeStructureClient([ClientResponse(status='completed',text=json.dumps(draft))]))
        expected=direct.profile.model_dump(mode='json');actual=payload['profile']
        assert actual==expected and payload['comparison']==direct.comparison.model_dump(mode='json')
        assert len(calls)==1


def test_report_domain_rejection_has_no_success_or_live_banner(tmp_path):
    from job_radar.api_v1_models import ProfileCreate
    calls=[]
    def handler(request):
        calls.append(request);return httpx2.Response(200,json=provider_response('invalid draft'))
    settings=fixtures(tmp_path);app=create_app(settings,mock_backend=MockBackend(httpx2.MockTransport(handler)))
    with TestClient(app) as client:
        service=app.state.offline_service;rid,doc=seed_resume(service)
        r=client.post(f'/v1/resumes/{rid}/profiles',json={'mode':'rules'},headers={'Idempotency-Key':'rules-before-report'})
        profile=wait(client,r.json()['data']['status_url']);assert profile['state']=='succeeded'
        match,_=create_match(client,profile['resource_id']);value,request,allowed=planned_report(match['resource_id'],match['payload'])
        ledger=approve(service,[allowed])
        r=client.post('/v1/reports',json=value,headers={'Idempotency-Key':'invalid-llm-report'})
        row=wait(client,r.json()['data']['status_url']);assert row['state']=='failed' and row['payload'] is None
        assert row['error']['code']=='upstream_error'
        assert client.get('/v1/reports/'+row['resource_id']+'?format=markdown').status_code==409
        state=ledger.status();assert state['halted'] and state['new_attempts']==1
        assert state['actual_cny']=='0.05534958' and state['retained_reserve_cny']=='0.7499544'
        assert any('invalid draft' in json.dumps(e.get('raw')) for e in state['events'])
        assert len(calls)==1


def test_mock_report_topn_rejected_before_any_billing(tmp_path):
    backend=MockBackend(httpx2.MockTransport(lambda request:pytest.fail('must not send')))
    with TestClient(create_app(fixtures(tmp_path),mock_backend=backend)) as client:
        r=client.post('/v1/reports',json={'match_id':'mtc_'+'a'*32,'mode':'llm','top_n':2},
            headers={'Idempotency-Key':'unsupported-topn'})
        assert r.status_code==422 and r.json()['error']['code']=='core_rejected'
        assert 'billing' not in client.app.state.offline_service.store.state


def test_report_approval_checkpoint_does_not_reset_seed_expiry_or_limits(tmp_path):
    settings=fixtures(tmp_path);service=OfflineService(settings);service.start()
    try:
        rid,doc=seed_resume(service);first=planned_profile(rid,doc);ledger=approve(service,[first])
        original=deepcopy(service.store.state['billing']['approval'])
        fake={'kind':'report','context_sha256':'a'*64,'payload_sha256':'b'*64,'input_limit':20000,'output_limit':8192}
        before=deepcopy(service.store.state)
        with pytest.raises(ServiceError):ledger.authorize([fake])
        assert service.store.state==before
        # Report approval requires a completed first checkpoint, not merely usage.
        row,_=service.store.create('/virtual/profile','virtual-before-checkpoint',{'mode':'llm'},'profile','llm',snapshot=doc)
        request=build_structure_request(ResumeDocument.model_validate_json(json.dumps(doc)),timeout_seconds=90)
        service.store.transition(row['resource_id'],'running')
        aid=ledger.reserve(row['resource_id'],'profile',first['context_sha256'],build_wire('profile',request))
        ledger.settle(aid,dict(input_tokens=1000,output_tokens=100,total_tokens=1100),{'synthetic':True})
        with pytest.raises(ServiceError):ledger.authorize([fake])
        service.store.transition(row['resource_id'],'succeeded',payload={'synthetic':True},billing_finish=lambda state:ledger.finish_in_state(state,[aid]))
        ledger.authorize([fake]);status=ledger.status()
        assert status['approval']==original and status['new_attempts']==1
        assert status['actual_cny']=='0.05534958' and status['retained_reserve_cny']=='0.7359144'
        before=deepcopy(service.store.state)
        with pytest.raises(ServiceError):ledger.authorize([fake])
        assert service.store.state==before
    finally:service.close()


def test_seed_source_hash_is_checked_once_before_mutation(tmp_path):
    import hashlib
    store=ResourceStore(tmp_path/'store');store.open()
    try:
        source=tmp_path/'seed.json';source.write_text('synthetic provenance\n')
        approval=Approval(root=str(store.root.resolve()),scope='synthetic-hash-check',
            expires_at=datetime.now(timezone.utc)+timedelta(hours=1),max_calls=1,
            seed_refs={str(source.resolve()):'a'*64},allowed=[dict(kind='profile',context_sha256='c'*64,payload_sha256='d'*64,input_limit=12000)])
        before=deepcopy(store.state)
        with pytest.raises(ServiceError,match='snapshot_conflict'):initialize(store,approval)
        assert store.state==before and 'billing' not in store.state
        fixed=approval.model_copy(update={'seed_refs':{str(source.resolve()):hashlib.sha256(source.read_bytes()).hexdigest()}})
        ledger=initialize(store,fixed);source.unlink()
        assert ledger.status()['new_attempts']==0
    finally:store.close()
