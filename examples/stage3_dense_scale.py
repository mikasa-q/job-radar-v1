"""Single explicit real dense scale worker, supervised by the primary runner."""
from __future__ import annotations
import argparse,json,math,statistics,time
from pathlib import Path
from stage3_synthetic import synthetic_job,synthetic_manifest
from job_radar.retrieval_embedding import LocalSnapshotEmbeddingClient,CachedEmbeddingClient
from job_radar.rag_runtime import LocalTokenBudget
from job_radar.rag_chunks import chunk_job
from job_radar.rag_models import ChunkConfig,DenseSpec
from job_radar.rag_index import DerivedIndex
from job_radar.retrieval_models import EngineConfig,JobKey,RetrievalQuery
from job_radar.retrieval_text import tokenize


def main(a):
    import torch
    torch.set_num_threads(1)
    started=time.perf_counter();client=LocalSnapshotEmbeddingClient(a.snapshot,model_id=a.model,revision=a.revision)
    client._load();load=time.perf_counter()-started
    budget=LocalTokenBudget(a.snapshot,model_id=a.model,revision=a.revision)
    cfg=ChunkConfig(max_tokens=128,overlap_tokens=16)
    # Unique text avoids counting cache hits as real corpus encoding throughput.
    jobs=[synthetic_job(i,description=f'合成编号 {i}。Python FastAPI 后端接口开发，SQL 数据分析。负责单元测试。') for i in range(1,a.size+1)]
    now=time.perf_counter();chunks=[c for j in jobs for c in chunk_job(j,budget=budget,config=cfg)];chunk_seconds=time.perf_counter()-now
    pool=json.loads((Path(a.snapshot)/'1_Pooling/config.json').read_text())
    spec=DenseSpec(model_id=a.model,revision=a.revision,dimension=client.dimension,tokenizer_id=budget.tokenizer_id,
        tokenizer_revision=budget.revision,pooling='cls' if pool.get('pooling_mode_cls_token') else 'mean',
        query_prefix=client._query_prefix or None,passage_prefix=client._passage_prefix or None,runtime_sha256=a.runtime_sha)
    idx=DerivedIndex(a.index);manifest=synthetic_manifest(jobs,dense=spec,chunk_config=cfg)
    now=time.perf_counter();update=idx.update(jobs,manifest=manifest,chunks=chunks,embedding_client=client);build=time.perf_counter()-now
    keys=[JobKey(dataset_id=j.dataset_id,job_id=j.job_id) for j in jobs[:1000]]
    config=EngineConfig(mode='hybrid')
    times=[];encoding=[];cached=[]
    for repeat in range(8):
        query=RetrievalQuery(text=f'Python FastAPI 数据接口查询 {repeat}',terms=tokenize('Python FastAPI'))
        now=time.perf_counter();client.encode([query.text],kind='query');enc=time.perf_counter()-now
        now=time.perf_counter();idx.dense_hits(query,keys,config=config,embedding_client=client);elapsed=time.perf_counter()-now
        now=time.perf_counter();idx.dense_hits(query,keys,config=config,embedding_client=client);cache=time.perf_counter()-now
        if repeat:times.append(elapsed);encoding.append(enc);cached.append(cache)
    result=dict(status='passed',synthetic=True,jobs=a.size,query_candidate_jobs=len(keys),chunks=len(chunks),dimension=client.dimension,
        model=a.model,revision=a.revision,model_load_seconds=load,chunk_seconds=chunk_seconds,full_build_seconds=build,
        encoded_chunks=update.encoded_chunks,uncached_query_seconds=times,query_encoding_seconds=encoding,cached_query_seconds=cached,
        uncached_p50=statistics.median(times),uncached_p95=max(times),cached_p50=statistics.median(cached),cached_p95=max(cached),
        throughput_queries_per_second=len(times)/sum(times),index_disk_bytes=sum(p.stat().st_size for p in Path(a.index).rglob('*') if p.is_file()),
        total_seconds=time.perf_counter()-started)
    Path(a.output).write_text(json.dumps(result,indent=2));print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ['model','revision','snapshot','runtime-sha','index','output']:p.add_argument('--'+name,required=True)
    p.add_argument('--size',required=True,type=int);main(p.parse_args())
