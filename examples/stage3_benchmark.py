"""Explicit synthetic scaling benchmark. Not relevance or human quality proof.

Memory/latency budgets are immutable before a run. Scale only after previous
process completed below ALL budgets. Public matching limits stay unchanged.
"""
from __future__ import annotations
import argparse
import dataclasses
import json
import math
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from stage3_synthetic import synthetic_job,synthetic_request,synthetic_manifest
from job_radar.rag_index import DerivedIndex
from job_radar.retrieval_text import project_request
from job_radar.retrieval_models import EngineConfig
from job_radar.retrieval_bm25 import retrieve_bm25
from job_radar.rag_runtime import process_tree_rss

BUDGET = dict(max_process_tree_rss_bytes=2*1024**3,max_process_seconds=180,
    max_p95_search_seconds=5.0,max_index_bytes=1024**3,repetitions=7,warmup=1,
    scales=[100,1000,10000],candidate_limit=1000,cache='lexical_no_query_cache',
    title_weight=2.0,synthetic_description_chars=68)


def percentile(values,p):
    return sorted(values)[min(len(values)-1,math.ceil(p*len(values))-1)]


def worker(size,path):
    start=time.perf_counter()
    jobs=[synthetic_job(i,description=f'合成岗位编号 {i}。使用 Python FastAPI 构建接口；负责 SQL 数据分析。要求代码审查和单元测试。') for i in range(1,size+1)]
    creation=time.perf_counter()-start
    index=DerivedIndex(path)
    now=time.perf_counter();update=index.update(jobs,manifest=synthetic_manifest(jobs))
    build=time.perf_counter()-now
    # Large-scale index builder explicit 10k; matching request remains <=1000.
    chosen=jobs[:min(size,1000)]
    cfg=EngineConfig();query,_=project_request(synthetic_request(chosen),cfg)
    keys=[dict(dataset_id=j.dataset_id,job_id=j.job_id) for j in chosen]
    from job_radar.retrieval_models import JobKey
    keys=[JobKey(**key) for key in keys]
    samples=[];last=[]
    for n in range(BUDGET['warmup']+BUDGET['repetitions']):
        now=time.perf_counter();documents=index.documents(keys);last=retrieve_bm25(query,documents,cfg)
        elapsed=time.perf_counter()-now
        if n>=BUDGET['warmup']:samples.append(elapsed)
    now=time.perf_counter();DerivedIndex(path).documents(keys);restart=time.perf_counter()-now
    now=time.perf_counter();noop=index.update(jobs,manifest=synthetic_manifest(jobs,generation='ignored-noop'));noop_time=time.perf_counter()-now
    revised=list(jobs);revised[0]=synthetic_job(1,description='内容修改。Python 接口开发，SQL 分析。')
    now=time.perf_counter();incremental=index.update(revised,manifest=synthetic_manifest(revised,generation='g2'));incremental_time=time.perf_counter()-now
    # Independently rebuild identical revised snapshot; verify logical retrieval equivalence.
    rebuilt=DerivedIndex(str(path)+'-rebuilt')
    now=time.perf_counter();rebuilt.update(revised,manifest=synthetic_manifest(revised,generation='g2'));rebuild_time=time.perf_counter()-now
    assert index.documents(keys)==rebuilt.documents(keys)
    size_bytes=sum(p.stat().st_size for p in Path(path).rglob('*') if p.is_file())
    return dict(schema_version='stage3-benchmark-v1',mode='bm25_only',synthetic=True,jobs=size,
        candidate_jobs=len(chosen),chunks=0,dimension=None,description_chars=[len(j.job.description) for j in jobs[:3]],
        create_seconds=creation,build_seconds=build,restart_read_seconds=restart,query_seconds=samples,
        p50_seconds=statistics.median(samples),p95_seconds=percentile(samples,.95),
        throughput_queries_per_second=len(samples)/sum(samples),index_disk_bytes=size_bytes,
        noop_seconds=noop_time,noop= noop.model_dump(),incremental_seconds=incremental_time,
        incremental=incremental.model_dump(),rebuild_seconds=rebuild_time,rebuild_equivalent=True,
        document_encoding_seconds=None,query_encoding_seconds=None,model_load_seconds=None,
        cold_hot_note='lexical only; fresh process cold import and disk page cache not OS-flushed')


def main(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    (out/'frozen-budget.json').write_text(json.dumps(BUDGET,indent=2))
    results=[]
    for scale in BUDGET['scales']:
        result_path=out/f'worker-{scale}.json'
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        now=time.monotonic();peak=0;stop=None
        with (out/f'worker-{scale}.log').open('w') as log:
            process=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--worker',str(scale),
                '--index',str(out/f'index-{scale}'),'--result',str(result_path)],env=env,stdout=log,stderr=log,start_new_session=True)
            while process.poll() is None:
                peak=max(peak,process_tree_rss(process.pid))
                if peak>BUDGET['max_process_tree_rss_bytes']:stop='rss_budget'
                if time.monotonic()-now>BUDGET['max_process_seconds']:stop='time_budget'
                if stop:os.killpg(process.pid,signal.SIGKILL);break
                time.sleep(.1)
            process.wait()
        elapsed=time.monotonic()-now
        row=json.loads(result_path.read_text()) if result_path.exists() else dict(jobs=scale)
        row.update(process_tree_peak_rss_bytes=peak,process_seconds=elapsed,exit_status=process.returncode)
        if process.returncode:stop=stop or 'worker_exit'
        if row.get('p95_seconds',0)>BUDGET['max_p95_search_seconds']:stop='latency_budget'
        if row.get('index_disk_bytes',0)>BUDGET['max_index_bytes']:stop='disk_budget'
        row['stop_reason']=stop;results.append(row)
        (out/'results.json').write_text(json.dumps(dict(budget=BUDGET,results=results),indent=2))
        print(json.dumps({k:row.get(k) for k in ['jobs','candidate_jobs','build_seconds','p50_seconds','p95_seconds','index_disk_bytes','process_tree_peak_rss_bytes','exit_status','stop_reason']}),flush=True)
        if stop:break


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output');parser.add_argument('--worker',type=int);parser.add_argument('--index');parser.add_argument('--result');args=parser.parse_args()
    if args.worker:
        Path(args.result).write_text(json.dumps(worker(args.worker,args.index),indent=2))
    else:main(args.output)
