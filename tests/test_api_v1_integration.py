"""Primary-owned Stage 5 integration: synthetic inputs and no live providers."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from job_radar.api_v1 import create_app
from job_radar.api_v1_budget import BudgetRejected, BudgetState, FROZEN_POLICY, reserve
from job_radar.api_v1_runtime import OfflineService, ServiceSettings
from job_radar.api_v1_store import ResourceStore, ServiceError
from job_radar.api_v1_presentation import render_api_report, OLD_BANNER, BANNERS
from job_radar.rag_models import canonical_hash
from job_radar.report_models import AnalysisConfig, ReportError
from job_radar.report_engine import generate_match_report
from job_radar.report_demo_client import SyntheticReportClient
from job_radar.report_render import render_match_report
from job_radar.rag_engine import match_jobs_indexed
from job_radar.rag_index import DerivedIndex
from job_radar.report_synthetic import synthetic_inputs, synthetic_manifest
from test_api_v1 import fixtures, create_profile, create_match, wait


def report_fixture(tmp_path, client=None):
    document, request = synthetic_inputs()
    index = DerivedIndex(tmp_path / 'index')
    index.update(request.jobs, manifest=synthetic_manifest(request.jobs))
    result = match_jobs_indexed(request, index=index)
    return generate_match_report(request, result, document, report_id='display-test',
        generated_at=datetime(2026,11,17,tzinfo=timezone.utc), client=client,
        config=AnalysisConfig(top_n=1))


def test_display_changes_only_header_and_preserves_core(tmp_path):
    report=report_fixture(tmp_path)
    before=report.model_dump_json(); original=render_match_report(report)
    output=render_api_report(report)
    assert output==original.replace(OLD_BANNER,BANNERS['offline'],1)
    assert report.model_dump_json()==before
    assert report.status=='not_run' and report.review_status=='unconfirmed'
    assert OLD_BANNER in render_match_report(report)


def test_completed_scripted_is_never_assumed_live(tmp_path):
    report=report_fixture(tmp_path,SyntheticReportClient())
    assert report.status=='completed'
    before=report.model_dump_json()
    assert BANNERS['scripted'] in render_api_report(report,mode='scripted')
    with pytest.raises(ReportError):render_api_report(report,mode='live')
    with pytest.raises(ReportError):render_api_report(report,mode='offline')
    with pytest.raises(ValueError):render_api_report(report,mode='automatic')
    assert report.model_dump_json()==before


def test_live_display_with_manufactured_audit_only_no_call(tmp_path):
    # This is a display branch fixture, not evidence of any real provider call.
    report=report_fixture(tmp_path,SyntheticReportClient())
    selected=next(a for a in report.analyses if a.audit)
    selected.audit.provider='apinebula'
    before=report.model_dump_json()
    assert BANNERS['live'] in render_api_report(report,mode='live')
    assert report.model_dump_json()==before and report.review_status=='unconfirmed'
    selected.audit.attempts=0
    with pytest.raises(ReportError):render_api_report(report,mode='live')


def test_display_does_not_replace_evidence_banner(tmp_path):
    report=report_fixture(tmp_path)
    report.report_id=OLD_BANNER
    output=render_api_report(report)
    assert OLD_BANNER in output and BANNERS['offline'] in output


@pytest.mark.parametrize('code',['llm_disabled','budget_exhausted','attempt_limit','budget_halted'])
def test_all_live_routes_use_single_gate_before_dependencies(tmp_path,monkeypatch,code):
    import job_radar.api_v1_runtime as runtime
    calls=[]
    def gate():calls.append(code);raise BudgetRejected(code)
    monkeypatch.setattr(runtime,'require_live_authorization',gate)
    settings=ServiceSettings(store_root=tmp_path/'store')
    with TestClient(create_app(settings)) as client:
        for path,body in [('/v1/resumes/rsm_'+'a'*32+'/profiles',{'mode':'llm'}),
                          ('/v1/reports',{'mode':'llm','match_id':'mtc_'+'a'*32})]:
            response=client.post(path,json=body,headers={'Idempotency-Key':'central-gate'})
            assert response.status_code==503 and response.json()['error']['code']==code
        assert client.app.state.offline_service.store.state['records']=={}
        assert client.app.state.offline_service.store.state['policy']==FROZEN_POLICY
    assert calls==[code,code]


def test_budget_exact_cap_is_pure_and_failed_projection_preserves_input():
    state=BudgetState();before=state.model_dump_json()
    projected=reserve(state,'0.26388982')
    assert projected.actual_cny=='0.05320458' and projected.reserved_cny=='0.94679542'
    with pytest.raises(BudgetRejected,match='budget_exhausted'):reserve(state,'0.26388983')
    assert state.model_dump_json()==before


@pytest.mark.parametrize('field',['input','snapshot','payload','resource_id'])
def test_store_read_revalidates_all_bindings(tmp_path,field):
    store=ResourceStore(tmp_path/'store');store.open()
    try:
        row,_=store.create('/x','snapshot-test',{},'profile','rules',snapshot={'frozen':1})
        rid=row['resource_id'];store.transition(rid,'running');store.transition(rid,'succeeded',payload={'result':1})
        item=store.state['records'][rid]
        if field=='payload':item['resource']['payload']['result']=2
        elif field=='resource_id':item['resource']['resource_id']='prf_'+'b'*32
        else:item[field]['extra']=2
        with pytest.raises(ServiceError,match='snapshot_corrupt'):store.get(rid)
    finally:store.close()


def test_queue_dispatch_rejection_is_persisted_interrupted(tmp_path,monkeypatch):
    service=OfflineService(ServiceSettings(store_root=tmp_path/'store',max_pending_tasks=1));service.start()
    def reject(*a,**k):raise RuntimeError('executor stopped private detail')
    monkeypatch.setattr(service.executor,'submit',reject)
    try:
        with pytest.raises(ServiceError,match='dependency_not_ready'):
            service.submit('/x','dispatch-key',{},'profile','rules',lambda:{},lambda s:1)
        row=service.store.lookup('/x','dispatch-key',{})
        assert row['state']=='interrupted' and row['error']['code']=='task_interrupted'
        assert service.slots.acquire(blocking=False);service.slots.release()
        assert service.submit('/x','dispatch-key',{},'profile','rules',lambda:1/0,lambda s:1)==row
    finally:service.close()


def test_worker_persistence_failure_halts_then_restart_interrupts(tmp_path,monkeypatch):
    settings=ServiceSettings(store_root=tmp_path/'store',max_pending_tasks=1)
    service=OfflineService(settings);service.start()
    original=service.store._save;entered=threading.Event();release=threading.Event()
    def execute(snapshot):entered.set();release.wait(3);return {'done':True}
    row=service.submit('/x','durable-key',{},'profile','rules',lambda:{},execute)
    assert entered.wait(2)
    def fail():raise OSError('synthetic fsync error')
    monkeypatch.setattr(service.store,'_save',fail);release.set();service.close()
    assert service.store.write_failed
    with pytest.raises(ServiceError,match='store_unavailable'):service.store.get(row['resource_id'])
    recovered=ResourceStore(settings.store_root);recovered.open()
    try:
        restored=recovered.get(row['resource_id'])
        assert restored['state']=='interrupted' and restored['payload'] is None
    finally:recovered.close()


def test_storage_http_error_is_enveloped_and_fail_closed(tmp_path,monkeypatch):
    settings=fixtures(tmp_path)
    with TestClient(create_app(settings)) as client:
        service=client.app.state.offline_service
        def fail(*a,**k):raise OSError('private path never exposed')
        monkeypatch.setattr(service.store,'_save',fail)
        response=client.post('/v1/resumes',files={'file':('bad.pdf',b'not-pdf','application/pdf')},
                             headers={'Idempotency-Key':'write-error'})
        assert response.status_code==503 and response.json()['error']['code']=='store_unavailable'
        assert 'private path' not in response.text
        assert response.headers['x-request-id']==response.json()['request_id']
        assert service.store.write_failed


def test_integrated_parallel_replay_saved_report_and_restart(tmp_path,monkeypatch):
    settings=fixtures(tmp_path);db_before=settings.evidence_path.read_bytes()
    import job_radar.report_apinebula as provider
    def no_provider(*a,**k):raise AssertionError('unexpected provider')
    monkeypatch.setattr(provider,'APINebulaAnalysisClient',no_provider)
    with TestClient(create_app(settings)) as client:
        _,profile=create_profile(client)
        body={'profile_id':profile['resource_id'],'candidate_keys':[{'dataset_id':'synthetic-s5','job_id':1}]}
        def post_match(_):return client.post('/v1/matches',json=body,headers={'Idempotency-Key':'parallel-match'})
        with ThreadPoolExecutor(max_workers=4) as pool:responses=list(pool.map(post_match,range(8)))
        assert all(r.status_code==202 for r in responses)
        assert len({r.json()['data']['resource_id'] for r in responses})==1
        match=wait(client,responses[0].json()['data']['status_url']);assert match['state']=='succeeded'
        request={'match_id':match['resource_id']}
        def post_report(_):return client.post('/v1/reports',json=request,headers={'Idempotency-Key':'parallel-report'})
        with ThreadPoolExecutor(max_workers=4) as pool:reports=list(pool.map(post_report,range(8)))
        assert all(r.status_code==202 for r in reports)
        assert len({r.json()['data']['resource_id'] for r in reports})==1
        report=wait(client,reports[0].json()['data']['status_url']);assert report['state']=='succeeded'
        assert report['payload']['report']['status']=='not_run'
        rid=report['resource_id'];markdown=client.get('/v1/reports/'+rid+'?format=markdown').text
        store=client.app.state.offline_service.store
        assert len(store.state['records'])==4 and store.state['policy']==FROZEN_POLICY
    assert settings.evidence_path.read_bytes()==db_before
    settings.evidence_path.unlink()
    import shutil
    shutil.rmtree(settings.index_root)
    with TestClient(create_app(settings)) as client:
        assert client.get('/v1/reports/'+rid).json()['data']==report
        assert client.get('/v1/reports/'+rid+'?format=markdown').text==markdown
        assert client.post('/v1/reports',json=request,headers={'Idempotency-Key':'parallel-report'}).json()['data']['resource_id']==rid
        assert client.post('/v1/matches',json=body,headers={'Idempotency-Key':'parallel-match'}).json()['data']['resource_id']==match['resource_id']
        assert client.app.state.offline_service.store.state['policy']==FROZEN_POLICY


def test_resource_limit_and_failed_journal_commit_never_consumes_binding(tmp_path,monkeypatch):
    import job_radar.api_v1_store as module
    store=ResourceStore(tmp_path/'store');store.open()
    try:
        monkeypatch.setattr(module,'MAX_RESOURCES',1)
        store.create('/x','one-limit',{},'profile','rules')
        before=deepcopy(store.state)
        with pytest.raises(ServiceError,match='store_unavailable'):
            store.create('/x','two-limit',{},'profile','rules')
        assert store.state==before
    finally:store.close()


def test_prepare_snapshot_is_detached_before_queued_execution(tmp_path):
    service=OfflineService(ServiceSettings(store_root=tmp_path/'store'));service.start()
    entered=threading.Event();release=threading.Event();source={'frozen':[1]};seen=[]
    def blocking(s):entered.set();release.wait(3);return {'done':True}
    try:
        service.submit('/x','block-copy',{},'profile','rules',lambda:{},blocking)
        assert entered.wait(2)
        row=service.submit('/x','frozen-copy',{},'profile','rules',lambda:source,
                           lambda s:seen.append(s) or {'done':True})
        source['frozen'].append(2)
    finally:release.set();service.close()
    assert seen==[{'frozen':[1]}]
    assert service.store.state['records'][row['resource_id']]['snapshot']=={'frozen':[1]}


def test_frozen_policy_is_not_aliased_and_tampering_blocks_commit(tmp_path):
    store=ResourceStore(tmp_path/'store');store.open()
    try:
        store.state['policy']['enabled']=True
        assert FROZEN_POLICY['enabled'] is False
        with pytest.raises(ServiceError,match='snapshot_corrupt'):
            store.create('/x','policy-tamper',{},'profile','rules')
    finally:store.close()


def test_unexpected_error_has_request_headers_and_no_private_details(tmp_path,monkeypatch):
    settings=fixtures(tmp_path)
    with TestClient(create_app(settings)) as client:
        def fail():raise RuntimeError('private detail /secret/path token')
        monkeypatch.setattr(client.app.state.offline_service,'jobs',fail)
        response=client.get('/v1/jobs')
        assert response.status_code==500 and response.json()['error']['code']=='internal_error'
        assert response.headers['x-request-id']==response.json()['request_id']
        assert response.headers['cache-control']=='no-store' and 'private detail' not in response.text


@pytest.mark.parametrize('failure',['parse','commit'])
def test_upload_failure_uses_shared_terminal_persistence_boundary(tmp_path,monkeypatch,failure):
    import job_radar.api_v1_resumes as module
    settings=fixtures(tmp_path)
    with TestClient(create_app(settings)) as client:
        service=client.app.state.offline_service
        if failure=='parse':
            def fail(*a,**k):raise OSError('synthetic extraction OS failure')
            monkeypatch.setattr(module,'extract_isolated',fail)
        else:
            from test_api_v1 import pdf
            from job_radar.resume_pdf import extract_resume_pdf
            parsed=extract_resume_pdf(pdf()).model_dump(mode='json')
            def parse(*a,**k):
                def fail():raise OSError('synthetic result fsync failure')
                monkeypatch.setattr(service.store,'_save',fail)
                return parsed
            monkeypatch.setattr(module,'extract_isolated',parse)
        response=client.post('/v1/resumes',files={'file':('x.pdf',b'PDF','application/pdf')},
                             headers={'Idempotency-Key':'upload-failure'})
        assert response.status_code==503 and response.json()['error']['code']=='store_unavailable'
        row=next(iter(service.store.state['records'].values()))['resource']
        assert row['state']==('failed' if failure=='parse' else 'running')
        assert row['payload'] is None
        assert service.slots.acquire(blocking=False);service.slots.release()
    if failure=='commit':
        recovered=ResourceStore(settings.store_root);recovered.open()
        try:assert recovered.get(row['resource_id'])['state']=='interrupted'
        finally:recovered.close()
