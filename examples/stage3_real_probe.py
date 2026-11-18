"""Opt-in pinned local model probe. Synthetic corpus; not human quality proof."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import time
from stage3_synthetic import synthetic_job, synthetic_request, synthetic_manifest
from job_radar.rag_models import ChunkConfig, DenseSpec
from job_radar.rag_runtime import LocalTokenBudget
from job_radar.retrieval_embedding import LocalSnapshotEmbeddingClient,CachedEmbeddingClient
from job_radar.rag_chunks import chunk_job
from job_radar.rag_index import DerivedIndex
from job_radar.rag_engine import match_jobs_indexed
from job_radar.retrieval_models import EngineConfig,RetrievalError


def run(args):
    import torch
    torch.set_num_threads(1)
    budget_start=time.perf_counter()
    budget=LocalTokenBudget(args.snapshot,model_id=args.model,revision=args.revision)
    tokenizer_seconds=time.perf_counter()-budget_start
    client=LocalSnapshotEmbeddingClient(args.snapshot,model_id=args.model,revision=args.revision)
    now=time.perf_counter();client._load();load_seconds=time.perf_counter()-now
    query='Python 后端接口开发'
    now=time.perf_counter();qvector=client.encode([query],kind='query')[0];query_seconds=time.perf_counter()-now
    now=time.perf_counter();pvectors=client.encode(['使用 Python FastAPI 开发后端服务。','Swift iOS 界面开发。'],kind='passage');passage_seconds=time.perf_counter()-now
    assert len(qvector)==client.dimension and all(math.isfinite(x) for x in qvector)
    cosines=[sum(a*b for a,b in zip(qvector,row)) for row in pvectors]
    common=ChunkConfig(max_tokens=128,overlap_tokens=16)
    native=ChunkConfig(max_tokens=budget.max_tokens,overlap_tokens=16)
    longjob=synthetic_job(1,description=('岗位职责：使用 Python 开发接口。负责 SQL 数据分析和单元测试。'*60)+'末尾要求：熟悉 FastAPI。')
    native_chunks=chunk_job(longjob,budget=budget,config=native)
    common_chunks=chunk_job(longjob,budget=budget,config=common)
    assert 'FastAPI' in native_chunks[-1].span.quote and 'FastAPI' in common_chunks[-1].span.quote
    jobs=[synthetic_job(i) for i in range(1,5)]+[longjob.model_copy(update={'job_id':5})]
    # Avoid copied URL/fact inconsistency: create independent synthetic job 5.
    jobs[-1]=synthetic_job(5,description=longjob.job.description)
    chunks=[c for j in jobs for c in chunk_job(j,budget=budget,config=native)]
    pooling=json.loads((Path(args.snapshot)/'1_Pooling/config.json').read_text())
    dense=DenseSpec(model_id=args.model,revision=args.revision,dimension=client.dimension,
        tokenizer_id=budget.tokenizer_id,tokenizer_revision=budget.revision,
        pooling='cls' if pooling.get('pooling_mode_cls_token') else 'mean',
        query_prefix=client._query_prefix or None,passage_prefix=client._passage_prefix or None,
        runtime_sha256=args.runtime_sha)
    index=DerivedIndex(args.index)
    manifest=synthetic_manifest(jobs,chunk_config=native,dense=dense)
    now=time.perf_counter();update=index.update(jobs,manifest=manifest,chunks=chunks,embedding_client=client);build_seconds=time.perf_counter()-now
    request=synthetic_request(jobs)
    cfg=EngineConfig(mode='hybrid')
    times=[]
    for _ in range(6):
        now=time.perf_counter();result=match_jobs_indexed(request,index=index,config=cfg,embedding_client=client);times.append(time.perf_counter()-now)
    assert result.matching.actual_mode=='hybrid' and result.matching.channels[1].status=='ok'
    # Reopen must reproduce ranking; metadata-only + idempotence never encode.
    reopened=DerivedIndex(args.index)
    assert match_jobs_indexed(request,index=reopened,config=cfg,embedding_client=client).matching==result.matching
    metadata=[j.model_copy(deep=True) for j in jobs];metadata[0].review_status='confirmed'
    metadata_update=index.update(metadata,manifest=synthetic_manifest(metadata,generation='metadata',chunk_config=native,dense=dense))
    assert metadata_update.encoded_chunks==0
    noop=index.update(metadata,manifest=synthetic_manifest(metadata,generation='noop',chunk_config=native,dense=dense))
    assert noop.encoded_chunks==0
    # Both length failure behaviors with a real tokenizer/client, no silent truncation.
    too_long=synthetic_request(metadata)
    from job_radar.matching_models import SearchPreferences
    too_long.profile.preferences=SearchPreferences(target_roles=['Python FastAPI SQL '*600])
    length_cfg=EngineConfig(mode='hybrid',max_text_chars=128000)
    try:match_jobs_indexed(too_long,index=index,config=length_cfg,embedding_client=client)
    except RetrievalError as e:assert e.code=='dense_input_too_long'
    else:raise AssertionError('length_not_rejected')
    degraded=match_jobs_indexed(too_long,index=index,config=length_cfg.model_copy(update={'allow_dense_fallback':True}),embedding_client=client)
    assert degraded.matching.actual_mode=='bm25_rules' and degraded.matching.status=='degraded'
    warm=times[1:]
    return dict(status='passed',synthetic=True,human_quality_ready=False,model_id=args.model,revision=args.revision,
        dimension=client.dimension,native_max_tokens=budget.max_tokens,pooling=dense.pooling,
        tokenizer_load_seconds=tokenizer_seconds,model_load_seconds=load_seconds,query_encode_seconds=query_seconds,
        two_passages_encode_seconds=passage_seconds,synthetic_cosines=cosines,common_budget_tokens=128,
        common_chunks=len(common_chunks),native_chunks=len(native_chunks),chunk_total=len(chunks),
        build_seconds=build_seconds,index_update=update.model_dump(),metadata_update=metadata_update.model_dump(),noop=noop.model_dump(),
        cold_query_seconds=times[0],cached_query_p50_seconds=statistics.median(warm),cached_query_p95_seconds=max(warm),
        ranking=[r.key.model_dump() for r in result.matching.results],index_disk_bytes=sum(p.stat().st_size for p in Path(args.index).rglob('*') if p.is_file()),
        actual_mode=result.matching.actual_mode,real_length_degradation=degraded.matching.channels[1].reason)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--revision',required=True);p.add_argument('--snapshot',required=True);p.add_argument('--runtime-sha',required=True);p.add_argument('--index',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();result=run(a);Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
