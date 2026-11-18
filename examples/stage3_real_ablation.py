"""Small real-vector synthetic ablation, explicitly not human relevance gold."""
from pathlib import Path
import argparse,json,time
from stage3_synthetic import synthetic_job,synthetic_manifest
from job_radar.rag_models import *
from job_radar.rag_runtime import LocalTokenBudget
from job_radar.retrieval_embedding import LocalSnapshotEmbeddingClient,CachedEmbeddingClient,retrieve_dense
from job_radar.rag_chunks import chunk_job
from job_radar.rag_index import DerivedIndex
from job_radar.rag_evaluation import run_experiments
from job_radar.retrieval_bm25 import retrieve_bm25
from job_radar.retrieval_fusion import fuse_rrf
from job_radar.retrieval_models import EngineConfig,JobKey,RetrievalQuery
from job_radar.retrieval_text import tokenize


def main(a):
    import torch
    torch.set_num_threads(1)
    budget=LocalTokenBudget(a.snapshot,model_id=a.model,revision=a.revision)
    real=LocalSnapshotEmbeddingClient(a.snapshot,model_id=a.model,revision=a.revision)
    client=CachedEmbeddingClient(real)
    jobs=[synthetic_job(i) for i in range(1,5)]
    keys=[JobKey(dataset_id=j.dataset_id,job_id=j.job_id) for j in jobs]
    queries=[EvalQuery(query_id=n,text=t,profile_group='synth-'+n,split='dev',source='synthetic') for n,t in
        [('backend','Python FastAPI 后端接口'),('mobile','Swift iOS SwiftUI 界面开发'),('data','SQL Python 数据分析')]]
    grades=[[2,0,1,0],[0,2,0,0],[1,0,2,0]]
    labels=[EvalJudgment(query_id=q.query_id,key=keys[i],job_group=f'synth-job-{i}',grade=grades[qi][i],
        status='synthetic_expected',evidence=['machine-authored synthetic intent expectation']) for qi,q in enumerate(queries) for i in range(4)]
    dataset=EvaluationSet(data_version='real-vector-synthetic-ablation-v1',queries=queries,judgments=labels)
    pool=json.loads((Path(a.snapshot)/'1_Pooling/config.json').read_text())
    dense=DenseSpec(model_id=a.model,revision=a.revision,dimension=client.dimension,tokenizer_id=budget.tokenizer_id,
        tokenizer_revision=budget.revision,pooling='cls' if pool.get('pooling_mode_cls_token') else 'mean',
        query_prefix=real._query_prefix or None,passage_prefix=real._passage_prefix or None,runtime_sha256=a.runtime_sha)
    cfg=ChunkConfig(max_tokens=128,overlap_tokens=16)
    idx=DerivedIndex(a.index)
    chunks=[c for j in jobs for c in chunk_job(j,budget=budget,config=cfg)]
    idx.update(jobs,manifest=synthetic_manifest(jobs,dense=dense,chunk_config=cfg),chunks=chunks,embedding_client=client)
    docs=idx.documents(keys)
    base=EngineConfig();tuned=EngineConfig(k1=.9,b=.5,title_weight=1.,recall_k=20,rrf_k=30)
    variants=['stage2_default','tuned_bm25','dense','hybrid','no_chunk','chunk_best']
    specs=[ExperimentSpec(experiment_id=v,config_sha256=canonical_hash(tuned if v=='tuned_bm25' else base),
        dataset_sha256=canonical_hash(dataset),index_sha256=canonical_hash(idx.manifest()),model_sha256=a.model_sha if v not in {'stage2_default','tuned_bm25'} else None,
        variant=v) for v in variants]
    def ranker(query,spec):
        q=RetrievalQuery(text=query.text,terms=tokenize(query.text));v=spec.variant
        if v in {'stage2_default','tuned_bm25'}:return [h.key for h in retrieve_bm25(q,docs,tuned if v=='tuned_bm25' else base)]
        if v=='no_chunk':return [h.key for h in retrieve_dense(q,docs,base,client=client)]
        hits=idx.dense_hits(q,keys,config=base,embedding_client=client).hits
        if v in {'dense','chunk_best'}:return [h.key for h in hits]
        return [key for key,_ in fuse_rrf(retrieve_bm25(q,docs,base),hits,base)]
    reports=run_experiments(dataset,specs,ranker,split='dev')
    result=dict(synthetic=True,human_quality_ready=False,model_id=a.model,revision=a.revision,
        note='Short 4-job corpus: no-chunk and chunk-best are a wiring ablation, not evidence of long-JD gain.',reports=reports)
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'status':'passed','variants':variants,'queries':3,'human_quality_ready':False}))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for n in ['model','revision','snapshot','runtime-sha','model-sha','index','output']:p.add_argument('--'+n,required=True)
    main(p.parse_args())
