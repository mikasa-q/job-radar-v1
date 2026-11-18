"""Reproducible synthetic PDF -> rules -> indexed BM25 -> offline report HTTP demo."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import time

from job_radar.api_v1 import create_app
from job_radar.api_v1_runtime import ServiceSettings
from job_radar.dataset_store import apply_records,dataset_snapshot
from job_radar.evidence_repository import read_job_candidates
from job_radar.rag_index import DerivedIndex,snapshot_hash
from job_radar.rag_models import IndexManifest


def prepare(root):
    import pymupdf
    with pymupdf.open() as document:
        document.new_page().insert_text((40,60),'Course project: Python and FastAPI API development.')
        raw=document.tobytes()
    (root/'synthetic-resume.pdf').write_bytes(raw)
    database=root/'synthetic-evidence.sqlite3'
    records=[]
    for number,description in enumerate(['要求掌握 Python。Docker 加分。','要求掌握 FastAPI。','要求掌握 SQL。'],1):
        records.append({'job':{'source':'manual','title':f'合成开发岗位{number}',
            'url':f'https://example.org/stage5/{number}','description':description},
            'provenance':[{'source_file':'/synthetic/job.txt','source_file_sha256':'a'*64,
                           'input_format':'txt','parser_version':'synthetic-stage5'}]})
    apply_records(database,records,{'run_id':'synthetic-stage5','input_dir':'/synthetic',
        'report_path':'/synthetic/report.json','plan_sha256':'b'*64,'failed_count':0},
        dataset_snapshot(database)['state_sha256'])
    jobs=read_job_candidates(database,dataset_id='synthetic-stage5')
    index=DerivedIndex((root/'derived').resolve())
    index.update(jobs,manifest=IndexManifest(generation='synthetic',snapshot_sha256=snapshot_hash(jobs)))
    return ServiceSettings(store_root=root/'api-store',evidence_path=database,
                           dataset_id='synthetic-stage5',index_root=root/'derived')


def run(client,root):
    trace=[]
    def call(method,url,**kwargs):
        response=client.request(method,url,**kwargs)
        trace.append({'method':method,'url':url,'status':response.status_code,
            'input':kwargs.get('json',{'file_sha256':hashlib.sha256((root/'synthetic-resume.pdf').read_bytes()).hexdigest()} if 'files' in kwargs else None),
            'response':response.json() if 'json' in response.headers.get('content-type','') else response.text})
        if response.status_code>=400:raise RuntimeError('demo_http_failure:'+str(response.status_code))
        return response
    def complete(url):
        for _ in range(500):
            row=call('GET',url).json()['data']
            if row['state']=='succeeded':return row
            if row['state'] in ('failed','interrupted'):raise RuntimeError('demo_task_failure:'+row['error']['code'])
            time.sleep(.02)
        raise RuntimeError('demo_timeout')
    call('GET','/health');call('GET','/v1/ready');jobs=call('GET','/v1/jobs').json()['data']['items']
    raw=(root/'synthetic-resume.pdf').read_bytes()
    resume=call('POST','/v1/resumes',files={'file':('synthetic.pdf',raw,'application/pdf')},
                headers={'Idempotency-Key':'demo-upload-0001'}).json()['data']
    accepted=call('POST','/v1/resumes/'+resume['resource_id']+'/profiles',json={'mode':'rules'},
                  headers={'Idempotency-Key':'demo-profile-0001'}).json()['data']
    profile=complete(accepted['status_url'])
    accepted=call('POST','/v1/matches',json={'profile_id':profile['resource_id'],'purpose':'debug',
        'candidate_keys':[{'dataset_id':j['dataset_id'],'job_id':j['job_id']} for j in jobs],
        'retrieval':{'mode':'bm25_rules'}},headers={'Idempotency-Key':'demo-match-0001'}).json()['data']
    match=complete(accepted['status_url'])
    accepted=call('POST','/v1/reports',json={'match_id':match['resource_id'],'mode':'offline','top_n':3},
                  headers={'Idempotency-Key':'demo-report-0001'}).json()['data']
    report=complete(accepted['status_url'])
    markdown=call('GET',accepted['status_url']+'?format=markdown').text
    report=report['payload']['report']
    assert report['status']=='not_run' and report['review_status']=='unconfirmed'
    assert all(a['status']=='not_analyzed' for a in report['analyses'])
    (root/'http-trace.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2)+'\n')
    (root/'match-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (root/'match-report.md').write_text(markdown)
    result={'mode':'offline','real_llm_requests':0,'report_status':report['status'],
            'ranked_jobs':len(match['payload']['retrieval']['matching']['results']),
            'review_status':report['review_status'],'http_requests':len(trace)}
    (root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('OFFLINE_HTTP_CLOSED_LOOP_OK report=not_run review=unconfirmed real_llm_requests=0')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();root=args.output.expanduser().absolute()
    root.mkdir(parents=True,exist_ok=False,mode=0o700)
    settings=prepare(root)
    from fastapi.testclient import TestClient
    with TestClient(create_app(settings)) as client:run(client,root)
    return 0


if __name__=='__main__':raise SystemExit(main())
