"""Frozen Stage 5 step-one: actual PDF extraction + offline HTTP, no provider."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import threading
import time

import pymupdf
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from job_radar.api_v1 import create_app, MAX_UPLOAD
from job_radar.api_v1_runtime import ServiceSettings, OfflineService, extract_isolated
from job_radar.api_v1_store import ResourceStore, ServiceError
from job_radar.api_v1_models import Envelope, ApiError, Resource, MatchCreate, ERROR_HTTP
from job_radar.api_v1_budget import (BudgetState,reserve,record_usage,BudgetRejected,
    require_live_authorization,FROZEN_POLICY,money)
from job_radar.dataset_store import apply_records,dataset_snapshot
from job_radar.evidence_repository import read_job_candidates
from job_radar.rag_models import IndexManifest,canonical_hash
from job_radar.rag_index import DerivedIndex,snapshot_hash


def pdf(text='Course project: Python and FastAPI API development.'):
    with pymupdf.open() as doc:
        page=doc.new_page();page.insert_text((40,60),text)
        return doc.tobytes()


def fixtures(root):
    db=root/'evidence.sqlite3'
    records=[]
    for i,description in enumerate(['要求掌握 Python。Docker 加分。','要求掌握 FastAPI。','要求掌握 SQL。'],1):
        records.append({'job':{'source':'manual','title':f'Python合成岗{i}',
            'url':f'https://example.org/s5/{i}','description':description},
            'provenance':[{'source_file':'/synthetic/job.txt','source_file_sha256':'a'*64,
                           'input_format':'txt','parser_version':'synthetic-step1'}]})
    apply_records(db,records,{'run_id':'synthetic-step1','input_dir':'/synthetic',
        'report_path':'/synthetic/report.json','plan_sha256':'b'*64,'failed_count':0},
        dataset_snapshot(db)['state_sha256'])
    jobs=read_job_candidates(db,dataset_id='synthetic-s5')
    index=DerivedIndex((root/'index').resolve())
    index.update(jobs,manifest=IndexManifest(generation='synthetic',snapshot_sha256=snapshot_hash(jobs)))
    return ServiceSettings(store_root=root/'store',evidence_path=db,
                           dataset_id='synthetic-s5',index_root=root/'index')


@pytest.fixture
def settings(tmp_path):return fixtures(tmp_path)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings,legacy_database_path=settings.store_root.parent/'missing-legacy.db')) as client:
        yield client


def wait(client,url):
    for _ in range(400):
        response=client.get(url);assert response.status_code==200,response.text
        row=response.json()['data']
        if row['state'] not in ('queued','running'):return row
        time.sleep(.01)
    raise AssertionError('task_not_finished')


def create_profile(client,raw=None,key='upload-0001'):
    r=client.post('/v1/resumes',files={'file':('s.pdf',pdf() if raw is None else raw,'application/pdf')},
                  headers={'Idempotency-Key':key})
    assert r.status_code==201,r.text
    resume=r.json()['data'];rid=resume['resource_id']
    r=client.post(f'/v1/resumes/{rid}/profiles',json={'mode':'rules'},headers={'Idempotency-Key':'profile-0001'})
    assert r.status_code==202,r.text
    row=wait(client,r.json()['data']['status_url']);assert row['state']=='succeeded',row
    return resume,row


def create_match(client,pid,key='match-0001',**changes):
    body={'profile_id':pid,'candidate_keys':[{'dataset_id':'synthetic-s5','job_id':i} for i in (1,2,3)]}
    body.update(changes)
    response=client.post('/v1/matches',json=body,headers={'Idempotency-Key':key})
    assert response.status_code==202,response.text
    return wait(client,response.json()['data']['status_url']),body


def test_complete_http_core_equivalence_and_no_db_write(client,settings,monkeypatch):
    before=settings.evidence_path.read_bytes()
    # Only prohibit external client construction; ASGI TestClient transport stays in memory.
    def no_provider(*a,**k):raise AssertionError('provider_called')
    import job_radar.report_apinebula as provider
    monkeypatch.setattr(provider,'APINebulaAnalysisClient',no_provider)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    raw=pdf();resume,profile=create_profile(client,raw=raw)
    assert resume['payload']['provenance']['source_sha256']==hashlib.sha256(raw).hexdigest()
    assert [s['name'] for s in profile['payload']['profile']['skills']]==['Python','FastAPI']
    assert profile['payload']['profile']['review_status']=='unconfirmed'
    match,body=create_match(client,profile['resource_id'])
    assert match['state']=='succeeded',match
    snap=match['payload']
    from job_radar.matching_models import MatchingRequest
    from job_radar.rag_engine import match_jobs_indexed
    from job_radar.retrieval_models import EngineConfig
    direct=match_jobs_indexed(MatchingRequest.model_validate_json(json.dumps(snap['request'],ensure_ascii=False)),
        index=DerivedIndex(settings.index_root.resolve()),config=EngineConfig())
    assert snap['retrieval']==direct.model_dump(mode='json')
    r=client.post('/v1/reports',json={'match_id':match['resource_id']},headers={'Idempotency-Key':'report-0001'})
    assert r.status_code==202,r.text
    report=wait(client,r.json()['data']['status_url']);assert report['state']=='succeeded',report
    result=report['payload']['report']
    assert result['status']=='not_run' and result['review_status']=='unconfirmed'
    assert all(a['status']=='not_analyzed' and a['draft'] is None for a in result['analyses'])
    assert result['packet']['retrieval']==snap['retrieval']
    md=client.get('/v1/reports/'+report['resource_id']+'?format=markdown')
    assert md.status_code==200 and md.text==report['payload']['markdown']
    assert '未调用 LLM' in md.text and '预设响应只验证' not in md.text
    assert md.headers['cache-control']=='no-store'
    assert settings.evidence_path.read_bytes()==before
    assert not (settings.store_root.parent/'missing-legacy.db').exists()


def test_readiness_legacy_openapi(client):
    assert client.get('/health').json()=={'status':'ok'}
    assert client.get('/jobs?limit=0').status_code==422
    assert 'detail' in client.get('/jobs?limit=0').json()
    assert client.get('/v1/ready').json()['data']['status']=='ready'
    spec=client.get('/openapi.json').json()
    expected=['/v1/ready','/v1/jobs','/v1/jobs/{dataset_id}/{job_id}','/v1/resumes',
              '/v1/resumes/{resume_id}','/v1/resumes/{resume_id}/profiles',
              '/v1/profiles/{profile_id}','/v1/matches','/v1/matches/{match_id}',
              '/v1/reports','/v1/reports/{report_id}']
    assert all(p in spec['paths'] for p in expected)
    assert 'multipart/form-data' in spec['paths']['/v1/resumes']['post']['requestBody']['content']
    assert client.get('/v1/jobs?limit=2&offset=1').json()['data']['total']==3
    assert client.get('/v1/jobs/synthetic-s5/1').status_code==200


@pytest.mark.parametrize('path,code',[('/v1/jobs?limit=0',422),('/v1/jobs?offset=-1',422),
    ('/v1/profiles/no-such-id',404),('/v1/unknown',404),('/v1/jobs/unknown/1',404)])
def test_errors_are_enveloped(client,path,code):
    response=client.get(path);assert response.status_code==code
    envelope=Envelope.model_validate(response.json());assert envelope.error
    assert 'traceback' not in response.text.lower()
    assert response.headers['x-request-id']==envelope.request_id


@pytest.mark.parametrize('raw,mime,code',[ (b'', 'application/pdf','pdf_empty_pdf'),
    (b'not a PDF','application/pdf','pdf_invalid_pdf'),
    (b'other','text/plain','unsupported_media_type')])
def test_upload_errors(client,raw,mime,code):
    r=client.post('/v1/resumes',files={'file':('x.pdf',raw,mime)},headers={'Idempotency-Key':'upload-error'})
    assert r.json()['error']['code']==code,r.text


def test_upload_limits_and_extra_fields(client):
    r=client.post('/v1/resumes',content=b'x'*(MAX_UPLOAD+65537),headers={'content-type':'application/pdf'})
    assert r.status_code==413
    r=client.post('/v1/resumes',files={'file':('x.pdf',pdf(),'application/pdf')},data={'path':'/private/path'})
    assert r.status_code==422 and '/private/path' not in r.text
    r=client.post('/v1/resumes',files={'file':('x.pdf',pdf(),'application/pdf')})
    assert r.json()['error']['code']=='idempotency_key_required'


def test_no_llm_or_hybrid_backdoor(client,monkeypatch):
    monkeypatch.setenv('JOBRADAR_LLM_ENABLED','true')
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-not-a-key')
    r=client.post('/v1/resumes/rsm_'+'a'*32+'/profiles',json={'mode':'llm'})
    assert r.status_code==503 and r.json()['error']['code']=='llm_disabled'
    r=client.post('/v1/reports',json={'match_id':'mtc_'+'a'*32,'mode':'llm'})
    assert r.status_code==503 and r.json()['error']['code']=='llm_disabled'
    r=client.post('/v1/matches',json={'profile_id':'prf_'+'a'*32,'candidate_keys':[],
                                  'retrieval':{'mode':'hybrid','allow_dense_fallback':True}})
    assert r.status_code==503 and r.json()['error']['code']=='hybrid_not_enabled'


def test_idempotent_snapshot_replay_and_conflict(client,settings):
    raw=pdf();resume,profile=create_profile(client,raw=raw)
    repeat=client.post('/v1/resumes',files={'file':('renamed.pdf',raw,'application/pdf')},headers={'Idempotency-Key':'upload-0001'})
    assert repeat.json()['data']==resume
    conflict=client.post('/v1/resumes',files={'file':('x.pdf',pdf('Different Python'),'application/pdf')},headers={'Idempotency-Key':'upload-0001'})
    assert conflict.status_code==409
    match,body=create_match(client,profile['resource_id'])
    settings.evidence_path.unlink() # replay must not reread a changed/missing dependency
    r=client.post('/v1/matches',json=body,headers={'Idempotency-Key':'match-0001'})
    assert r.status_code==202 and r.json()['data']['resource_id']==match['resource_id']
    body['preferences']={'cities':['深圳']}
    assert client.post('/v1/matches',json=body,headers={'Idempotency-Key':'match-0001'}).status_code==409


def test_formal_gate_and_empty_candidates(client):
    _,profile=create_profile(client)
    body={'profile_id':profile['resource_id'],'candidate_keys':[],'purpose':'formal'}
    r=client.post('/v1/matches',json=body,headers={'Idempotency-Key':'formal-0001'})
    assert r.status_code==409 and r.json()['error']['code']=='formal_requires_confirmed'
    match,_=create_match(client,profile['resource_id'],candidate_keys=[])
    assert match['state']=='succeeded'
    assert match['payload']['retrieval']['matching']['status']=='no_candidates'


def test_journal_restart_integrity_and_private_mode(tmp_path):
    root=tmp_path/'store';store=ResourceStore(root);store.open()
    first,_=store.create('/test','key-00001',{'a':1},'profile','rules',snapshot={'source':'frozen'})
    rid=first['resource_id'];store.transition(rid,'running');store.close()
    store=ResourceStore(root);store.open()
    assert store.get(rid)['state']=='interrupted'
    assert store.lookup('/test','key-00001',{'a':1})['resource_id']==rid
    assert root.stat().st_mode & 0o077==0
    assert (root/'state.json').stat().st_mode & 0o077==0
    other=ResourceStore(root)
    with pytest.raises(ServiceError):other.open()
    store.close()
    data=json.loads((root/'state.json').read_text());data['content']['records'][rid]['snapshot']['source']='tamper'
    (root/'state.json').write_text(json.dumps(data))
    with pytest.raises(ServiceError):ResourceStore(root).open()


def test_concurrent_idempotence_and_transitions(tmp_path):
    store=ResourceStore(tmp_path/'store');store.open()
    try:
        def create(_):return store.create('/a','parallel-key',{'b':1},'profile','rules')
        with ThreadPoolExecutor(max_workers=8) as executor:rows=list(executor.map(create,range(16)))
        assert len({row[0]['resource_id'] for row in rows})==1
        assert sum(int(created) for row,created in rows)==1
        rid=rows[0][0]['resource_id']
        with pytest.raises(ServiceError):store.transition(rid,'succeeded',payload={'x':1})
        store.transition(rid,'running');store.transition(rid,'succeeded',payload={'x':1})
        with pytest.raises(ServiceError):store.transition(rid,'running')
        value=store.get(rid);value['payload']['x']=2
        assert store.get(rid)['payload']['x']==1
    finally:store.close()


@pytest.mark.parametrize('value',['NaN','Infinity','-1','nope',1,None])
def test_money_invalid(value):
    with pytest.raises(ValueError):money(value)


def test_budget_projection_is_conservative_and_not_authorization():
    state=BudgetState();next=reserve(state,'0.0569088')
    assert state.new_attempts==0 and next.new_attempts==1
    assert next.reserved_cny=='0.7398144'
    known=record_usage(next,'0.003')
    assert known.reserved_cny==next.reserved_cny and known.actual_cny=='0.05620458'
    unknown=record_usage(next,None)
    assert unknown.halted and unknown.reserved_cny==next.reserved_cny
    with pytest.raises(BudgetRejected,match='budget_halted'):reserve(unknown,'0.01')
    with pytest.raises(BudgetRejected,match='budget_exhausted'):reserve(state,'0.3')
    with pytest.raises(BudgetRejected,match='attempt_limit'):reserve(BudgetState(new_attempts=9),'0.01')
    with pytest.raises(BudgetRejected,match='llm_disabled'):require_live_authorization()
    assert FROZEN_POLICY['enabled'] is False


def test_serial_capacity_and_no_duplicate_execution(settings):
    from job_radar.api_v1_runtime import ServiceSettings
    settings=ServiceSettings(**{**settings.__dict__,'max_pending_tasks':1})
    service=OfflineService(settings);service.start()
    entered=threading.Event();release=threading.Event();calls=[]
    def execute(snapshot):entered.set();release.wait(5);calls.append(snapshot);return {'done':True}
    try:
        record=service.submit('/test','serial-key',{'a':1},'profile','rules',lambda:{'frozen':1},execute)
        assert entered.wait(2)
        duplicate=service.submit('/test','serial-key',{'a':1},'profile','rules',lambda:1/0,execute)
        assert duplicate['resource_id']==record['resource_id']
        with pytest.raises(ServiceError,match='queue_full'):
            service.submit('/test','other-key',{'a':2},'profile','rules',lambda:{},execute)
    finally:release.set();service.close()
    assert calls==[{'frozen':1}]


def test_actual_hash_and_restart_result(settings):
    raw=pdf()
    with TestClient(create_app(settings)) as client:
        r=client.post('/v1/resumes',files={'file':('x.pdf',raw,'application/pdf')},headers={'Idempotency-Key':'hash-00001'})
        assert r.status_code==201,r.text
        row=r.json()['data'];rid=row['resource_id']
        assert row['payload']['provenance']['source_sha256']==hashlib.sha256(raw).hexdigest()
    with TestClient(create_app(settings)) as client:
        assert client.get('/v1/resumes/'+rid).json()['data']==row
        assert client.post('/v1/resumes',files={'file':('x.pdf',raw,'application/pdf')},headers={'Idempotency-Key':'hash-00001'}).json()['data']==row


def test_envelope_and_resource_contracts():
    with pytest.raises(ValidationError):Envelope(request_id='req_'+'a'*32)
    with pytest.raises(ValidationError):Envelope(request_id='req_'+'a'*32,data={},error=ApiError(code='internal_error'))
    with pytest.raises(ValidationError):ApiError(code='unknown_code')
    with pytest.raises(ValidationError):MatchCreate(profile_id='prf_'+'a'*32,candidate_keys=[{'dataset_id':'x','job_id':1}]*2)


def test_pdf_blank_password_page_limit(client):
    with pymupdf.open() as doc:
        doc.new_page();blank=doc.tobytes()
    r=client.post('/v1/resumes',files={'file':('b.pdf',blank,'application/pdf')},headers={'Idempotency-Key':'blank-0001'})
    assert r.json()['error']['code']=='pdf_no_extractable_text'
    with pymupdf.open() as doc:
        doc.new_page().insert_text((20,40),'Python')
        encrypted=doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,owner_pw='owner',user_pw='user')
    r=client.post('/v1/resumes',files={'file':('e.pdf',encrypted,'application/pdf')},headers={'Idempotency-Key':'encrypt-0001'})
    assert r.json()['error']['code']=='pdf_password_required'
    with pymupdf.open() as doc:
        for _ in range(11):doc.new_page()
        large=doc.tobytes()
    r=client.post('/v1/resumes',files={'file':('l.pdf',large,'application/pdf')},headers={'Idempotency-Key':'pages-0001'})
    assert r.json()['error']['code']=='pdf_page_limit'


def test_error_fields_never_echo_unknown_names(client):
    response=client.post('/v1/reports',json={'match_id':'mtc_'+'a'*32,'secret-token-value':123})
    assert response.status_code==422
    assert 'secret-token-value' not in response.text and '123' not in response.json()['error']['message']
    assert '__field__' in str(response.json()['error']['fields'])
    assert client.put('/v1/reports').status_code==405


def test_report_uses_saved_match_after_dependencies_removed(client,settings):
    _,profile=create_profile(client)
    match,_=create_match(client,profile['resource_id'])
    settings.evidence_path.unlink()
    import shutil
    shutil.rmtree(settings.index_root)
    r=client.post('/v1/reports',json={'match_id':match['resource_id']},headers={'Idempotency-Key':'snapshot-report'})
    report=wait(client,r.json()['data']['status_url'])
    assert report['state']=='succeeded'
    assert report['payload']['report']['packet']['retrieval']==match['payload']['retrieval']


def test_output_schemas_are_concrete(client):
    schemas=client.get('/openapi.json').json()['components']['schemas']
    assert 'MatchPayload' in schemas and 'ResumeDocument' in schemas and 'MatchReport' in schemas
    assert schemas['ReportPayload']['properties']['report']['$ref'].endswith('/MatchReport')


def test_pdf_wallclock_timeout(settings):
    with pytest.raises(ServiceError,match='execution_timeout'):
        extract_isolated(pdf(),0.000001)


def test_service_readiness_missing_does_not_create_db(tmp_path):
    settings=ServiceSettings(store_root=tmp_path/'store',evidence_path=tmp_path/'absent.sqlite3',index_root=tmp_path/'absent-index')
    with TestClient(create_app(settings)) as client:
        r=client.get('/v1/ready');assert r.status_code==503
        assert r.json()['data']['evidence'] is False
    assert not settings.evidence_path.exists() and not settings.index_root.exists()


def test_snapshot_generation_change_before_execute_fails(settings):
    # Deliberately block serial queue, then switch index generation after preparation.
    service=OfflineService(settings);service.start()
    entered=threading.Event();release=threading.Event()
    try:
        # Seed a genuine rule profile through existing stages without HTTP fixture overhead.
        from job_radar.resume_pdf import extract_resume_pdf
        from job_radar.resume_profile_rules import build_rule_profile
        document=extract_resume_pdf(pdf())
        profile=build_rule_profile(document,resume_id='rsm_'+'a'*32)
        record,_=service.store.create('/seed','seed-profile',{},'profile','rules')
        rid=record['resource_id'];service.store.transition(rid,'running')
        service.store.transition(rid,'succeeded',payload={'document':document.model_dump(mode='json'),
            'profile':profile.model_dump(mode='json'),'resume_id':'rsm_'+'a'*32,'comparison':None})
        def block(snapshot):entered.set();release.wait(5);return {'done':True}
        service.submit('/block','block-0001',{},'profile','rules',lambda:{},block)
        assert entered.wait(1)
        body=MatchCreate(profile_id=rid,candidate_keys=[{'dataset_id':'synthetic-s5','job_id':1}])
        task=service.match(body,'generation-match')
        jobs=read_job_candidates(settings.evidence_path,dataset_id='synthetic-s5')
        from job_radar.rag_models import ChunkConfig
        replacement=settings.index_root.parent/'replacement-index'
        DerivedIndex(replacement.resolve()).update(jobs,manifest=IndexManifest(
            generation='changed',snapshot_sha256=snapshot_hash(jobs),
            chunk_config=ChunkConfig(overlap_tokens=31)))
        settings.index_root.rename(settings.index_root.parent/'previous-index')
        replacement.rename(settings.index_root)
        release.set()
    finally:release.set();service.close()
    row=service.store.get(task['resource_id'])
    assert row['state']=='failed' and row['error']['code']=='snapshot_conflict'


def test_frozen_openapi_matches_source(client):
    from pathlib import Path
    frozen=json.loads((Path(__file__).resolve().parents[1]/'docs/STAGE5_OPENAPI_V1_1.json').read_text())
    assert client.get('/openapi.json').json()==frozen


def test_journal_failed_write_keeps_memory_and_disk(tmp_path,monkeypatch):
    store=ResourceStore(tmp_path/'store');store.open()
    before=deepcopy(store.state);raw=(store.root/'state.json').read_bytes()
    import job_radar.api_v1_store as module
    def fail(*a,**k):raise OSError('synthetic disk failure')
    monkeypatch.setattr(module.os,'replace',fail)
    try:
        with pytest.raises(OSError):store.create('/test','disk-fail-key',{'a':1},'profile','rules')
        assert store.state==before and (store.root/'state.json').read_bytes()==raw
        assert not list(store.root.glob('.state-*'))
    finally:store.close()


def test_invalid_idempotency_key_fails_without_resource(client):
    response=client.post('/v1/resumes',files={'file':('x.pdf',pdf(),'application/pdf')},headers={'Idempotency-Key':'short'})
    assert response.status_code==422 and response.json()['error']['code']=='idempotency_key_invalid'


def test_snapshot_payload_config_is_hash_bound(client):
    _,profile=create_profile(client)
    match,_=create_match(client,profile['resource_id'],retrieval={'mode':'bm25_rules','top_k':1})
    assert match['state']=='succeeded',match
    payload=match['payload']
    preimage={k:payload[k] for k in ['document','request','index_manifest','engine_config']}
    assert canonical_hash(preimage)==payload['snapshot_sha256']
    assert payload['engine_config']['top_k']==payload['retrieval']['matching']['config']['top_k']==1
    preimage['engine_config']['top_k']=2
    assert canonical_hash(preimage)!=payload['snapshot_sha256']
