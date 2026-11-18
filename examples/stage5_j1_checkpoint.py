"""J1 checkpoint after S1 human review; one shared journal, no profile resend.

prepare is entirely offline. Ranking drift freezes a review artifact and never
appends paid authorization. A later human approval must name that frozen hash.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
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
from job_radar.api_v1_billing import BillingJournal
from job_radar.api_v1_provider import build_wire,input_allocation
from job_radar.api_v1_reports import _match_snapshot
from job_radar.dataset_store import apply_records,dataset_snapshot
from job_radar.evidence_repository import read_job_candidates
from job_radar.rag_index import DerivedIndex,snapshot_hash
from job_radar.rag_models import IndexManifest,canonical_hash
from job_radar.matching_models import MatchingRequest
from job_radar.report_models import AnalysisConfig
from job_radar.report_evidence import build_evidence_packet
from job_radar.report_analysis import build_analysis_request

SCOPE='stage5-s1-j1-live-20261118'
MATCH_KEY='stage5-real-s1-match-j1-20261118'
REPORT_KEY='stage5-real-j1-report-20261118'
DATASET='synthetic-stage4-acceptance-v1'
ACCEPTED_HIT_ORDER=(1,3,4,2)
ACCEPTED_CANDIDATES=(1,2,3,4)
ORIGINAL=Path('/Users/thc/.codex/visualizations/2026/11/14/01a1d829-b8da-75f3-851c-f4c6466777e7/jobradar-stage4-live-acceptance-v1/env-continuation/preflight/S1')


def write(path,value):
    path=Path(path);data=(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
    temporary=path.with_name('.'+path.name+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path);dfd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        if temporary.exists():temporary.unlink()


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def settings(s1_root,root):
    return ServiceSettings(store_root=Path(s1_root)/'api-store',evidence_path=Path(root)/'evidence.sqlite3',
        dataset_id='synthetic-stage4-acceptance-v1',index_root=Path(root)/'derived',max_pending_tasks=1)


def wait(client,url):
    for _ in range(2400):
        response=client.get(url)
        if response.status_code!=200:raise RuntimeError('status_read_failed')
        row=response.json()['data']
        if row['state'] not in ('queued','running'):return row
        time.sleep(.05)
    raise RuntimeError('task_wait_expired')


def ranks(retrieval):
    return [dict(key=r['key'],rank=r['rank'],score=r['bm25']['score']) for r in retrieval['matching']['results']]


def ranking_review(saved,old_request,old_retrieval):
    from job_radar.retrieval_text import project_request
    from job_radar.retrieval_models import EngineConfig
    old=MatchingRequest.model_validate_json(json.dumps(old_request,ensure_ascii=False))
    config=EngineConfig.model_validate_json(json.dumps(saved['engine_config']))
    new=_match_snapshot(saved)
    old_query,_=project_request(old,config);new_query,_=project_request(new.request,config)
    old_rows,new_rows=ranks(old_retrieval),ranks(saved['retrieval'])
    same=[x['key'] for x in old_rows]==[x['key'] for x in new_rows]
    return dict(status='unchanged' if same else 'ranking_changed_requires_human_confirmation',
        old_ranking=old_rows,new_ranking=new_rows,candidate_order_unchanged=same,
        j1_still_first=bool(new_rows) and new_rows[0]['key']=={'dataset_id':'synthetic-stage4-acceptance-v1','job_id':1},
        old_query=old_query.model_dump(mode='json'),new_query=new_query.model_dump(mode='json'),
        reason='Original projection includes model-profile project descriptions and technologies; no matching/query logic was changed.',
        human_review='pending' if not same else 'not_required',real_new_requests=0)


def accepted_checkpoint(saved,original_jobs,old_request,old_retrieval):
    """The user's refreeze accepts this one order, not arbitrary hash approval.

    New import provenance is declared separately. Candidate facts/identity/review
    and common jobs' rule semantics must still match the original frozen packet.
    """
    def fact(job):
        return {name:job[name] for name in ('dataset_id','job_id','job','fact_sha256','jd_sha256','review_status')}
    def identity(row):return row['key']['dataset_id'],row['key']['job_id']
    def rule(row):
        value=row['rules']
        return {'skills':[{name:item[name] for name in ('alternatives','kind','status','matched')} for item in value['skills']],
                **{name:value[name] for name in ('coverage','covered_required','total_required')}}
    candidates=saved['request']['jobs'];rows=saved['retrieval']['matching']['results']
    previous={identity(row):rule(row) for row in old_retrieval['matching']['results']}
    current={identity(row):rule(row) for row in rows}
    checks={
        'candidate_ids_exactly_original_four':[(j['dataset_id'],j['job_id']) for j in candidates]==[(DATASET,i) for i in ACCEPTED_CANDIDATES],
        'candidate_facts_unchanged':[fact(j) for j in candidates]==[fact(j) for j in original_jobs]==[fact(j) for j in old_request['jobs']],
        'accepted_hit_order':[(r['key']['dataset_id'],r['key']['job_id']) for r in rows]==[(DATASET,i) for i in ACCEPTED_HIT_ORDER],
        'ranks_contiguous':[r['rank'] for r in rows]==list(range(1,len(rows)+1)),
        'common_skill_rules_and_coverage_unchanged':all(key in current and current[key]==value for key,value in previous.items()),
        'j1_only_report_target':bool(rows) and identity(rows[0])==(DATASET,1),
    }
    assert all(checks.values()),'accepted_checkpoint_drift'
    return checks


def prepare(s1_root,root):
    s1_root=Path(s1_root).resolve();root=Path(root).resolve();root.mkdir(mode=0o700,parents=True,exist_ok=False)
    # The original store must already exist; never initialize billing here.
    assert (s1_root/'api-store/OWNER').is_file() and (s1_root/'api-store/state.json').is_file()
    s1=json.loads((s1_root/'RESULT.json').read_text());assert s1['task_state']=='succeeded' and s1['generation_attempts']==1 and s1['report_requests']==0
    original_jobs=json.loads((s1_root/'original-jobs.json').read_text())
    database=root/'evidence.sqlite3'
    records=[{'job':j['job'],'provenance':[{'source_file':'/synthetic/stage4-jobs.txt',
        'source_file_sha256':p['source_file_sha256'],'input_format':p['input_format'],'parser_version':p['parser_version']} for p in j['provenance']]} for j in original_jobs]
    apply_records(database,records,{'run_id':'stage5-j1-reimport-20261118','input_dir':'/synthetic',
        'report_path':'/synthetic/report.json','plan_sha256':canonical_hash(original_jobs),'failed_count':0},dataset_snapshot(database)['state_sha256'])
    imported=read_job_candidates(database,dataset_id='synthetic-stage4-acceptance-v1')
    # New database import provenance is declared, not disguised as old identity.
    assert [(j.job_id,j.fact_sha256,j.jd_sha256,j.job.model_dump(mode='json'),j.review_status) for j in imported]==[(j['job_id'],j['fact_sha256'],j['jd_sha256'],j['job'],j['review_status']) for j in original_jobs]
    index=DerivedIndex(root/'derived');index.update(imported,manifest=IndexManifest(generation='stage5-j1-20261118',snapshot_sha256=snapshot_hash(imported)))
    write(root/'jobs.json',[j.model_dump(mode='json') for j in imported])
    write(root/'provenance-note.json',{'original_jobs_sha256':digest(s1_root/'original-jobs.json'),
        'job_ids_facts_jd_hashes_review_unchanged':True,'new_import_provenance':True,
        'note':'A new isolated SQLite import has new imported_at/import_run_id; historical facts/provenance remain untouched.'})
    with TestClient(create_app(settings(s1_root,root))) as client:
        service=client.app.state.offline_service;ledger=BillingJournal(service.store);before=ledger.status()
        assert before['new_attempts']==1 and not before['halted'] and before['inflight'] is None
        assert before['approval']['scope']==SCOPE and before['approval']['max_calls']==2
        assert before['actual_cny']==s1['cumulative_cost_cny'] and before['retained_reserve_cny']==s1['cumulative_reserved_cny']
        assert len([a for a in before['attempts'].values() if a['kind']=='profile'])==1
        parent=client.get('/v1/profiles/'+s1['profile_id']);assert parent.status_code==200
        profile=parent.json()['data'];assert profile['state']=='succeeded' and profile['payload']['profile']['review_status']=='unconfirmed'
        write(root/'profile-resource.json',profile)
        body={'profile_id':s1['profile_id'],'purpose':'debug','candidate_keys':[{'dataset_id':j.dataset_id,'job_id':j.job_id} for j in imported],
            'retrieval':{'mode':'bm25_rules'}}
        response=client.post('/v1/matches',json=body,headers={'Idempotency-Key':MATCH_KEY});assert response.status_code==202
        row=wait(client,response.json()['data']['status_url']);assert row['state']=='succeeded'
        saved=row['payload'];checked=_match_snapshot(saved)
        assert checked.document.model_dump(mode='json')==profile['payload']['document']
        assert checked.request.profile.profile_id==profile['payload']['profile']['resume_id']
        write(root/'match-resource.json',row);write(root/'match-input.json',body)
        old_request=json.loads((ORIGINAL/'request.json').read_text());old_retrieval=json.loads((ORIGINAL/'retrieval.json').read_text())
        review=ranking_review(saved,old_request,old_retrieval)
        accepted_checks=accepted_checkpoint(saved,original_jobs,old_request,old_retrieval)
        write(root/'RANKING_REVIEW.json',review)
        assert review['j1_still_first']
        config=AnalysisConfig(top_n=1);packet=build_evidence_packet(checked.request,checked.retrieval,checked.document,config=config)
        assert [k.model_dump(mode='json') for k in packet.selected_keys]==[{'dataset_id':'synthetic-stage4-acceptance-v1','job_id':1}]
        request=build_analysis_request(packet,packet.selected_keys[0],config=config);wire=build_wire('report',request)
        assert input_allocation(wire)<=20000 and wire['max_output_tokens']==8192 and request.prompt_version=='match-analysis-prompt-v3'
        value={'match_id':row['resource_id'],'mode':'llm','top_n':1}
        context=canonical_hash({'kind':'report','input':value,'snapshot':saved})
        write(root/'report-input.json',value);write(root/'wire.json',wire);write(root/'evidence.json',packet.model_dump(mode='json'))
        write(root/'billing-before.json',before)
        manifest=dict(schema='stage5-j1-checkpoint-v1',scope=SCOPE,s1_root=str(s1_root),store_root=str(s1_root/'api-store'),
            profile_id=s1['profile_id'],match_id=row['resource_id'],idempotency_key=REPORT_KEY,
            context_sha256=context,payload_sha256=canonical_hash(wire),input_allocation=input_allocation(wire),input_limit=20000,output_limit=8192,
            model='gpt-6.1-sol',reasoning='low',prompt_version=request.prompt_version,top_n=1,
            inherited_actual=before['actual_cny'],inherited_reserve=before['retained_reserve_cny'],inherited_attempts=1,
            new_reservation='0.0670488',double_occupancy_projection=str(Decimal(before['actual_cny'])+Decimal(before['retained_reserve_cny'])+Decimal('0.1340976')),
            ranking_review_sha256=digest(root/'RANKING_REVIEW.json'),authorization_appended=False,
            accepted_hit_order=list(ACCEPTED_HIT_ORDER),accepted_checkpoint_checks=accepted_checks,
            status='frozen_waiting_ranking_confirmation' if not review['candidate_order_unchanged'] else 'frozen_ready_for_explicit_authorization',
            artifact_sha256={n:digest(root/n) for n in ['jobs.json','provenance-note.json','profile-resource.json','match-resource.json','match-input.json','report-input.json','wire.json','evidence.json','RANKING_REVIEW.json','billing-before.json']})
        write(root/'MANIFEST.json',manifest)
        assert ledger.status()==before
    print('J1_PREPARED status='+manifest['status']+' input_allocation='+str(manifest['input_allocation'])+' attempts=1 authorization_appended=false real_new_requests=0')
    return manifest


def check_files(s1_root,root):
    s1_root=Path(s1_root).resolve();root=Path(root).resolve();m=json.loads((root/'MANIFEST.json').read_text())
    assert m['scope']==SCOPE and m['s1_root']==str(s1_root) and m['store_root']==str(s1_root/'api-store') and m['idempotency_key']==REPORT_KEY
    for name,h in m['artifact_sha256'].items():assert digest(root/name)==h,name
    saved=_match_snapshot(json.loads((root/'match-resource.json').read_text())['payload'])
    checked=accepted_checkpoint(saved.model_dump(mode='json'),json.loads((s1_root/'original-jobs.json').read_text()),
        json.loads((ORIGINAL/'request.json').read_text()),json.loads((ORIGINAL/'retrieval.json').read_text()))
    assert m['accepted_hit_order']==list(ACCEPTED_HIT_ORDER) and m['accepted_checkpoint_checks']==checked
    assert m['model']=='gpt-6.1-sol' and m['reasoning']=='low' and m['prompt_version']=='match-analysis-prompt-v3' and m['top_n']==1
    value=json.loads((root/'report-input.json').read_text())
    assert value=={'match_id':m['match_id'],'mode':'llm','top_n':1}
    config=AnalysisConfig(top_n=1);packet=build_evidence_packet(saved.request,saved.retrieval,saved.document,config=config)
    assert [k.model_dump(mode='json') for k in packet.selected_keys]==[{'dataset_id':DATASET,'job_id':1}]
    wire=build_wire('report',build_analysis_request(packet,packet.selected_keys[0],config=config))
    assert canonical_hash(wire)==m['payload_sha256']==canonical_hash(json.loads((root/'wire.json').read_text()))
    assert canonical_hash({'kind':'report','input':json.loads((root/'report-input.json').read_text()),'snapshot':saved.model_dump(mode='json')})==m['context_sha256']
    return m


def authorize(s1_root,root,ranking_sha256):
    # Explicit administrative follow-up only after human accepts the named
    # ranking review; it cannot reinitialize or extend budget/expiry/max_calls.
    root=Path(root).resolve();m=check_files(s1_root,root)
    assert ranking_sha256==m['ranking_review_sha256'] and not m['authorization_appended']
    with TestClient(create_app(settings(s1_root,root))) as client:
        ledger=BillingJournal(client.app.state.offline_service.store);before=ledger.status()
        assert before['new_attempts']==1 and not before['halted'] and before['inflight'] is None
        assert before['actual_cny']==m['inherited_actual'] and before['retained_reserve_cny']==m['inherited_reserve']
        try:
            ledger.authorize([dict(kind='report',context_sha256=m['context_sha256'],payload_sha256=m['payload_sha256'],input_limit=20000,output_limit=8192)])
            after=ledger.status();assert after['new_attempts']==1 and after['actual_cny']==before['actual_cny'] and after['retained_reserve_cny']==before['retained_reserve_cny']
            write(root/'billing-authorized.json',after)
            m.update(authorization_appended=True,status='authorized_for_j1_once',accepted_ranking_sha256=ranking_sha256,
                human_confirmation='2026-11-18: user accepted J1->J3->J4->J2, unchanged four candidates/common rules/coverage, J1 report once')
            write(root/'MANIFEST.json',m)
        except Exception:
            try:ledger.halt('store_unavailable')
            except Exception:service=client.app.state.offline_service;service.store.write_failed=True
            raise
    print('J1_AUTHORIZED attempts=1 no_new_generation=true')
    return m


def review_report(report):
    analyses=report['analyses'];selected=[a for a in analyses if a['key']=={'dataset_id':'synthetic-stage4-acceptance-v1','job_id':1}]
    claims=selected[0]['draft']['claims'] if selected and selected[0]['draft'] else []
    def found(subject,status,kind):return any(c['subject']==subject and c['status']==status and c['requirement_kind']==kind for c in claims)
    checks=dict(completed=report['status']=='completed',v3=report['prompt_version']=='match-analysis-prompt-v3',
        unconfirmed=report['review_status']=='unconfirmed',only_j1_analyzed=len([a for a in analyses if a['status']=='completed'])==1 and bool(selected) and selected[0]['status']=='completed',
        j1_rank1=report['packet']['retrieval']['matching']['results'][0]['key']=={'dataset_id':'synthetic-stage4-acceptance-v1','job_id':1},
        python_supported_required=found('Python','supported','required'),fastapi_supported_required=found('FastAPI','supported','required'),
        docker_unknown_bonus=found('Docker','unknown','bonus'),no_explicit_gap=all(c['status']!='explicit_gap' for c in claims),citations_present=bool(claims) and all(c['evidence_ids'] for c in claims))
    return dict(status='passed' if all(checks.values()) else 'failed',checks=checks,claims=claims,human_review='pending')


def result_value(root,m,before,after,row,review,status,started,exit_status):
    attempts=[a for a in after['attempts'].values() if a['kind']=='report'] if after is not None else []
    attempt=attempts[0] if attempts else None;usage=attempt['usage'] if attempt else None
    cost=Decimal(after['actual_cny'])-Decimal(before['actual_cny']) if usage is not None and before is not None else None
    return dict(status=status,report_id=row['resource_id'] if row else (attempt['resource_id'] if attempt else None),task_state=row['state'] if row else None,
        error=row['error'] if row else None,model='gpt-6.1-sol',reasoning='low',prompt_version='match-analysis-prompt-v3',
        generation_attempts=after['new_attempts'] if after else None,new_generation_attempts=len(attempts),profile_requests=0,report_requests=len(attempts),retries=0,
        usage=usage,usage_known=usage is not None,actual_usage_cost_cny=str(cost) if cost is not None else None,
        cumulative_cost_cny=after['actual_cny'] if after else None,cumulative_reserved_cny=after['retained_reserve_cny'] if after else None,
        pending_usage_ceiling_cny=after['pending_usage_ceiling_cny'] if after else None,
        conservative_exposure_cny=str(sum(Decimal(after[k]) for k in ['actual_cny','retained_reserve_cny','pending_usage_ceiling_cny'])) if after else None,
        budget_cny='1.00',halted=after['halted'] if after else None,inflight=after['inflight'] if after else None,ledger_snapshot_known=after is not None,
        ledger_path=m['store_root']+'/state.json',context_sha256=m['context_sha256'],payload_sha256=m['payload_sha256'],review=review,
        credential_environment_cleared='OPENAI_API_KEY' not in os.environ,elapsed_seconds=round(time.monotonic()-started,3),
        cost_note='Provider usage times approved conservative rates including cache-create surcharge; not an account invoice.',
        next='STOP; no further generation; await human J1 report review',exit_status=exit_status)


def save_evidence(root,after,row,result,*,best_effort=False):
    items=[('RESULT.json',result)]
    if after is not None:
        items.append(('billing-after.json',after))
        report_aids={aid for aid,a in after['attempts'].items() if a['kind']=='report'}
        items.extend((f'provider-response-{i}.json',e['raw']) for i,e in enumerate((e for e in after['events'] if e['type']=='settled' and e['attempt_id'] in report_aids),1))
    if row is not None:items.append(('report-resource.json',row))
    for name,value in items:
        try:write(root/name,value)
        except Exception:
            if not best_effort:raise


def execute(s1_root,root):
    s1_root=Path(s1_root).resolve();root=Path(root).resolve();m=check_files(s1_root,root)
    assert m['authorization_appended'] and m['status']=='authorized_for_j1_once'
    marker=os.open(root/'J1_LIVE_STARTED',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(marker,'w') as out:out.write(datetime.now(timezone.utc).isoformat());out.flush();os.fsync(out.fileno())
    key=os.environ.pop('OPENAI_API_KEY','');assert key.strip()
    from job_radar.api_v1_live import LiveBackend
    backend=LiveBackend(s1_root/'api-store',api_key=key,scope=SCOPE,allow_report=True);key=''
    started=time.monotonic();result=None;exit_status=2
    try:
        with TestClient(create_app(settings(s1_root,root),live_backend=backend)) as client:
            service=client.app.state.offline_service;ledger=BillingJournal(service.store);before=None;row=None;review=None
            try:
                before=ledger.status();assert before['new_attempts']==1 and not before['halted'] and before['inflight'] is None
                assert before['actual_cny']==m['inherited_actual'] and before['retained_reserve_cny']==m['inherited_reserve']
                value=json.loads((root/'report-input.json').read_text())
                response=client.post('/v1/reports',json=value,headers={'Idempotency-Key':REPORT_KEY})
                write(root/'http-submit.json',{'status':response.status_code,'body':response.json()});assert response.status_code==202
                row=wait(client,response.json()['data']['status_url']);write(root/'report-resource.json',row)
                if row['state']=='succeeded':
                    report=row['payload']['report'];write(root/'match-report.json',report)
                    markdown=client.get('/v1/reports/'+row['resource_id']+'?format=markdown');assert markdown.status_code==200
                    assert markdown.text==row['payload']['markdown'] and '本报告包含真实模型响应' in markdown.text
                    (root/'match-report.md').write_text(markdown.text);(root/'match-report.md').chmod(0o600)
                    review=review_report(report);write(root/'REPORT_REVIEW.json',review)
                    if review['status']!='passed':ledger.halt('core_rejected')
                    status='completed_pending_human_review' if review['status']=='passed' else 'completed_semantic_check_failed'
                    exit_status=0 if review['status']=='passed' else 2
                else:ledger.halt(row['error']['code']);status='failed_stopped'
                after=ledger.status();assert after['new_attempts']==2
                result=result_value(root,m,before,after,row,review,status,started,exit_status);save_evidence(root,after,row,result)
            except Exception as error:
                fail_stop=False
                try:
                    with service.gate:
                        if service.executor is not None:service.executor.shutdown(wait=True,cancel_futures=False);service.executor=None
                except Exception:fail_stop=True
                try:ledger.halt('store_unavailable' if isinstance(error,OSError) else 'internal_error')
                except Exception:service.store.write_failed=True;fail_stop=True
                after=None
                try:after=ledger.status()
                except Exception:fail_stop=True
                if after is not None:
                    attempts=[a for a in after['attempts'].values() if a['kind']=='report']
                    if attempts:
                        try:row=service.store.get(attempts[0]['resource_id'])
                        except Exception:pass
                result=result_value(root,m,before,after,row,review,'checkpoint_failed_stopped',started,2)
                result.update(checkpoint_error={'code':'store_unavailable' if isinstance(error,OSError) else 'internal_error','exception_type':type(error).__name__},fail_stop=fail_stop)
                try:save_evidence(root,after,row,result,best_effort=True)
                except Exception:pass
                exit_status=2
    finally:backend.close();os.environ.pop('OPENAI_API_KEY',None)
    print('J1_CHECKPOINT '+json.dumps({k:result[k] for k in ['status','generation_attempts','new_generation_attempts','profile_requests','report_requests','usage','actual_usage_cost_cny','cumulative_cost_cny','cumulative_reserved_cny','conservative_exposure_cny','halted']},ensure_ascii=False))
    return exit_status


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['prepare','check','authorize','execute']);parser.add_argument('s1_root',type=Path);parser.add_argument('root',type=Path)
    parser.add_argument('--accept-ranking-sha256');args=parser.parse_args()
    if args.action=='prepare':prepare(args.s1_root,args.root)
    elif args.action=='check':print('J1_FROZEN_INPUTS_OK '+check_files(args.s1_root,args.root)['payload_sha256'])
    elif args.action=='authorize':authorize(args.s1_root,args.root,args.accept_ranking_sha256)
    else:return execute(args.s1_root,args.root)
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:print('J1_CHECKPOINT_STOPPED '+type(error).__name__);sys.exit(2)
