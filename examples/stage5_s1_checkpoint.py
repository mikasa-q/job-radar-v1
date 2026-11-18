"""Explicit Stage5 S1 one-generation checkpoint; prepare never reads a key.

The approved two-call batch has one persistent root. This runner never creates
or authorizes J1, and never retries an already submitted profile.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient
from job_radar.api_v1 import create_app
from job_radar.api_v1_runtime import ServiceSettings
from job_radar.api_v1_billing import Approval,BillingJournal,SEED,initialize
from job_radar.api_v1_provider import build_wire,input_allocation
from job_radar.resume_models import ResumeDocument
from job_radar.resume_structure import build_structure_request
from job_radar.rag_models import canonical_hash

SCOPE='stage5-s1-j1-live-20261118'
TEXT='课程项目：使用 Python 和 FastAPI 开发接口。'
UPLOAD_KEY='stage5-real-s1-upload-20261118'
PROFILE_KEY='stage5-real-s1-profile-20261118'
HISTORY=Path('/Users/thc/.codex/visualizations/2026/11/16/01a1e35d-b2f1-7250-ad25-64904f393ec4/jobradar-phase3-v3-remaining8')
ORIGINAL=Path('/Users/thc/.codex/visualizations/2026/11/14/01a1d829-b8da-75f3-851c-f4c6466777e7/jobradar-stage4-live-acceptance-v1/env-continuation/preflight/S1')


def write(path,value):
    path=Path(path)
    data=(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
    temporary=path.with_name('.'+path.name+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        dfd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        if temporary.exists():temporary.unlink()


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def settings(root):return ServiceSettings(store_root=Path(root)/'api-store',max_pending_tasks=1)


def prepare(root):
    root=Path(root).resolve();root.mkdir(mode=0o700,parents=True,exist_ok=False)
    # Explicit one-time approved initialization. A reused/missing root is not a
    # retry path; execute below only accepts this frozen approval.
    prior=json.loads((HISTORY/'RESULT.json').read_text())
    assert prior['cumulative_cost_cny']==SEED['historical_actual_cny']
    assert prior['cumulative_reserved_cny']==SEED['historical_reserved_cny']
    assert prior['retries']==0 and prior['attempts']==8
    original=ResumeDocument.model_validate_json((ORIGINAL/'document.json').read_text())
    assert [b.text for page in original.pages for b in page.blocks]==[TEXT]
    import pymupdf
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((36,72),TEXT,fontname='china-s',fontsize=11)
        raw=pdf.tobytes()
    (root/'S1.pdf').write_bytes(raw);(root/'S1.pdf').chmod(0o600)
    with TestClient(create_app(settings(root))) as client:
        response=client.post('/v1/resumes',files={'file':('S1.pdf',raw,'application/pdf')},headers={'Idempotency-Key':UPLOAD_KEY})
        assert response.status_code==201,response.text
        resume=response.json()['data'];doc=ResumeDocument.model_validate_json(json.dumps(resume['payload'],ensure_ascii=False))
        blocks=[b.text for p in doc.pages for b in p.blocks]
        assert blocks==[TEXT+'\n'],blocks
        assert doc.provenance.source_sha256==hashlib.sha256(raw).hexdigest()
        request=build_structure_request(doc,timeout_seconds=90)
        wire=build_wire('profile',request)
        assert input_allocation(wire)<=12000 and wire['max_output_tokens']==8192
        context=canonical_hash({'kind':'profile','input':{'mode':'llm'},'snapshot':doc.model_dump(mode='json')})
        allowed=dict(kind='profile',context_sha256=context,payload_sha256=canonical_hash(wire),input_limit=12000,output_limit=8192)
        approval=Approval(root=str((root/'api-store').resolve()),scope=SCOPE,max_calls=2,
            expires_at=datetime.now(timezone.utc)+timedelta(days=7),allowed=[allowed],
            seed_refs={str(HISTORY/'RESULT.json'):digest(HISTORY/'RESULT.json'),str(HISTORY/'SAMPLES.json'):digest(HISTORY/'SAMPLES.json')})
        service=client.app.state.offline_service
        ledger=initialize(service.store,approval)
        assert ledger.status()['new_attempts']==0
        write(root/'resume.json',resume);write(root/'document.json',doc.model_dump(mode='json'))
        write(root/'wire.json',wire);write(root/'billing-before.json',ledger.status())
        write(root/'original-jobs.json',json.loads((ORIGINAL/'request.json').read_text())['jobs'])
        manifest=dict(schema='stage5-s1-checkpoint-v1',scope=SCOPE,store_root=str(service.store.root),
            resume_id=resume['resource_id'],profile_route=f"/v1/resumes/{resume['resource_id']}/profiles",
            idempotency_key=PROFILE_KEY,context_sha256=context,payload_sha256=canonical_hash(wire),
            input_allocation=input_allocation(wire),input_limit=12000,output_limit=8192,model='gpt-6.1-sol',reasoning='low',
            original_document_sha256=canonical_hash(original),document_sha256=canonical_hash(doc),
            source_pdf_sha256=hashlib.sha256(raw).hexdigest(),text=TEXT,extraction_note='New PDF adds one terminal newline; content unchanged; new provenance/hash not historical identity.',
            history_actual=SEED['historical_actual_cny'],history_reserve=SEED['historical_reserved_cny'],cap='1.00',max_calls=2,
            approved_this_turn='profile once only; J1 pending human check',retries=0,
            artifact_sha256={name:digest(root/name) for name in ['S1.pdf','resume.json','document.json','wire.json','original-jobs.json','billing-before.json']})
        write(root/'MANIFEST.json',manifest)
    print('S1_PREPARED input_allocation='+str(manifest['input_allocation'])+' output_limit=8192 attempts=0 actual=0.05320458 reserve=0.6829056 real_requests=0')
    return manifest


def check_files(root):
    root=Path(root).resolve();manifest=json.loads((root/'MANIFEST.json').read_text())
    assert manifest['scope']==SCOPE and manifest['store_root']==str(root/'api-store')
    for name,expected in manifest['artifact_sha256'].items():assert digest(root/name)==expected,name
    doc=ResumeDocument.model_validate_json((root/'document.json').read_text())
    wire=build_wire('profile',build_structure_request(doc,timeout_seconds=90))
    assert canonical_hash(wire)==manifest['payload_sha256']==canonical_hash(json.loads((root/'wire.json').read_text()))
    assert manifest['idempotency_key']==PROFILE_KEY
    return manifest


def review(payload):
    profile=payload['profile'];skills=list(profile['skills'])
    for entry in profile['projects']+profile['work_experience']:skills+=entry['technologies']
    names=sorted(set(s['name'] for s in skills))
    facts=[]
    def collect(value,path='profile'):
        if isinstance(value,dict):
            if 'evidence' in value:
                facts.append({'path':path,'value':value.get('value'),'evidence':value['evidence']})
            for name,item in value.items():collect(item,path+'.'+name)
        elif isinstance(value,list):
            for index,item in enumerate(value):collect(item,path+f'[{index}]')
    collect(profile)
    checks={'skills_exactly_python_fastapi':names==['FastAPI','Python'],
        'no_invented_education_or_work':profile['education']==[] and profile['work_experience']==[],
        'unconfirmed':profile['review_status']=='unconfirmed',
        'citations_present':bool(facts) and all(f['evidence'] for f in facts),
        'comparison_no_unexplained_missing':payload['comparison']['rule_only_skills']==[],
        'comparison_no_extra_skills':payload['comparison']['model_only_skills']==[]}
    return dict(status='passed' if all(checks.values()) else 'failed',checks=checks,skills=names,facts=facts,
                comparison=payload['comparison'],human_review='pending')


def checkpoint_result(root,manifest,before,after,row,checked,status,exit_status,started):
    """Use only validated ledger facts; missing usage/ledger is never zero cost."""
    attempt=next(iter(after['attempts'].values()),None) if after is not None else None
    usage=attempt.get('usage') if attempt is not None else None
    cost=(Decimal(after['actual_cny'])-Decimal(before['actual_cny'])) if usage is not None and before is not None else None
    return dict(status=status,profile_id=row['resource_id'] if row is not None else (attempt['resource_id'] if attempt else None),
        task_state=row['state'] if row is not None else None,error=row['error'] if row is not None else None,
        model='gpt-6.1-sol',reasoning='low',generation_attempts=after['new_attempts'] if after is not None else None,
        retries=0,report_requests=0,usage=usage,usage_known=usage is not None,
        actual_usage_cost_cny=str(cost) if cost is not None else None,
        cumulative_cost_cny=after['actual_cny'] if after is not None else None,
        cumulative_reserved_cny=after['retained_reserve_cny'] if after is not None else None,
        pending_usage_ceiling_cny=after['pending_usage_ceiling_cny'] if after is not None else None,
        conservative_exposure_cny=str(sum(Decimal(after[k]) for k in ['actual_cny','retained_reserve_cny','pending_usage_ceiling_cny'])) if after is not None else None,
        budget_cny='1.00',halted=after['halted'] if after is not None else None,
        inflight=after['inflight'] if after is not None else None,ledger_snapshot_known=after is not None,
        ledger_path=str(root/'api-store/state.json'),context_sha256=manifest['context_sha256'],
        payload_sha256=manifest['payload_sha256'],review=checked,
        credential_environment_cleared='OPENAI_API_KEY' not in os.environ,
        elapsed_seconds=round(time.monotonic()-started,3),
        cost_note='Provider usage times approved conservative rates, including cache-create surcharge; not an account invoice.',
        next='STOP; await human S1 profile review before any J1 report',exit_status=exit_status)


def failed_checkpoint(root,manifest,service,ledger,before,row,checked,started,error):
    """Stop the batch inside its open store, preserving an existing call's usage.

    No new task is submitted. Drain the one already accepted worker before halt:
    settle correctly rejects a halted journal, so halting a call mid-flight would
    discard its otherwise known usage. The runner has no report/second-call path.
    """
    fail_stop=False
    try:
        with service.gate:
            if service.executor is not None:
                service.executor.shutdown(wait=True,cancel_futures=False)
                service.executor=None
    except Exception:
        fail_stop=True
    try:
        ledger.halt('store_unavailable' if isinstance(error,OSError) else 'internal_error')
    except Exception:
        service.store.write_failed=True
        fail_stop=True
    after=None
    try:after=ledger.status()
    except Exception:fail_stop=True
    if after is not None and after['attempts']:
        try:row=service.store.get(next(iter(after['attempts'].values()))['resource_id'])
        except Exception:pass
    result=checkpoint_result(root,manifest,before,after,row,checked,'checkpoint_failed_stopped',2,started)
    result.update(checkpoint_error={'code':'store_unavailable' if isinstance(error,OSError) else 'internal_error',
                                    'exception_type':type(error).__name__},fail_stop=fail_stop)
    # The halt precedes diagnostic writes. A broken diagnostic path must not
    # prevent other evidence, and never resets the marker, attempt or resource.
    evidence=[('RESULT.json',result)]
    if after is not None:
        evidence.append(('billing-after.json',after))
        evidence.extend((f'provider-response-{i}.json',event['raw'])
            for i,event in enumerate((e for e in after['events'] if e['type']=='settled'),1))
    if row is not None:evidence.append(('profile-resource.json',row))
    for name,value in evidence:
        try:write(root/name,value)
        except Exception:pass
    return result


def execute(root):
    root=Path(root).resolve();manifest=check_files(root)
    # A durable once-only local marker supplements persisted send_intent. It is
    # never removed automatically, including on pre-send failures.
    marker=os.open(root/'LIVE_STARTED',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(marker,'w') as out:out.write(datetime.now(timezone.utc).isoformat());out.flush();os.fsync(out.fileno())
    key=os.environ.pop('OPENAI_API_KEY','')
    if not key.strip():raise RuntimeError('missing_api_key')
    from job_radar.api_v1_live import LiveBackend
    backend=LiveBackend(root/'api-store',api_key=key,scope=SCOPE);key=''
    started=time.monotonic();result=None;exit_status=2
    try:
        with TestClient(create_app(settings(root),live_backend=backend)) as client:
            service=client.app.state.offline_service;ledger=BillingJournal(service.store)
            before=None;row=None;checked=None
            try:
                before=ledger.status()
                assert before['new_attempts']==0 and not before['halted'] and before['inflight'] is None
                assert before['actual_cny']==manifest['history_actual'] and before['retained_reserve_cny']==manifest['history_reserve']
                assert ledger.store.root==Path(manifest['store_root'])
                response=client.post(manifest['profile_route'],json={'mode':'llm'},headers={'Idempotency-Key':PROFILE_KEY})
                write(root/'http-submit.json',{'status':response.status_code,'body':response.json()})
                if response.status_code!=202:raise RuntimeError('profile_submission_failed')
                accepted=response.json()['data']
                for _ in range(2400):
                    status=client.get(accepted['status_url'])
                    if status.status_code!=200:raise RuntimeError('status_read_failed')
                    row=status.json()['data']
                    if row['state'] not in ('queued','running'):break
                    time.sleep(.05)
                else:raise RuntimeError('profile_task_wait_expired')
                write(root/'profile-resource.json',row)
                if row['state']=='succeeded':
                    write(root/'profile.json',row['payload']['profile']);write(root/'comparison.json',row['payload']['comparison'])
                    checked=review(row['payload']);write(root/'PROFILE_REVIEW.json',checked)
                    if checked['status']!='passed':ledger.halt('core_rejected')
                    status='completed_pending_human_review' if checked['status']=='passed' else 'completed_semantic_check_failed'
                    exit_status=0 if checked['status']=='passed' else 2
                else:
                    ledger.halt(row['error']['code']);status='failed_stopped'
                after=ledger.status();write(root/'billing-after.json',after)
                settlements=[e for e in after['events'] if e['type']=='settled']
                for i,event in enumerate(settlements,1):write(root/f'provider-response-{i}.json',event['raw'])
                assert after['new_attempts']==1
                result=checkpoint_result(root,manifest,before,after,row,checked,status,exit_status,started)
                write(root/'RESULT.json',result)
            except Exception as error:
                result=failed_checkpoint(root,manifest,service,ledger,before,row,checked,started,error)
                exit_status=2
    finally:
        backend.close() if callable(getattr(backend,'close',None)) else None
        os.environ.pop('OPENAI_API_KEY',None)
    print('S1_CHECKPOINT '+json.dumps({k:result[k] for k in ['status','generation_attempts','report_requests','usage','actual_usage_cost_cny','cumulative_cost_cny','cumulative_reserved_cny','conservative_exposure_cny','halted']},ensure_ascii=False))
    return exit_status


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['prepare','execute','check']);parser.add_argument('root',type=Path)
    args=parser.parse_args()
    if args.action=='prepare':prepare(args.root)
    elif args.action=='check':print('S1_FROZEN_INPUTS_OK '+check_files(args.root)['payload_sha256'])
    else:return execute(args.root)
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:
        # Do not print exception strings/tracebacks from auth/HTTP/SDK internals.
        print('S1_CHECKPOINT_STOPPED '+type(error).__name__)
        sys.exit(2)
