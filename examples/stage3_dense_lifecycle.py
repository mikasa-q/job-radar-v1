"""Real embeddings only: dense delta/rebuild/cache/version lifecycle acceptance."""
import argparse,json,time
from pathlib import Path
from stage3_synthetic import *
from job_radar.rag_models import *
from job_radar.rag_chunks import chunk_job
from job_radar.rag_runtime import LocalTokenBudget
from job_radar.retrieval_embedding import LocalSnapshotEmbeddingClient
from job_radar.retrieval_models import EngineConfig,JobKey,RetrievalQuery,RetrievalError
from job_radar.rag_index import DerivedIndex


def main(a):
    import torch
    torch.set_num_threads(1)
    real=LocalSnapshotEmbeddingClient(a.snapshot,model_id=a.model,revision=a.revision)
    class Measured:
        model_id=real.model_id;revision=real.revision;dimension=real.dimension
        def __init__(self):self.calls=[]
        def encode(self,texts,*,kind):
            before=time.perf_counter();vectors=real.encode(texts,kind=kind)
            self.calls.append(dict(kind=kind,text_sha256=[__import__('hashlib').sha256(t.encode()).hexdigest() for t in texts],seconds=time.perf_counter()-before))
            return vectors
    client=Measured();budget=LocalTokenBudget(a.snapshot,model_id=a.model,revision=a.revision)
    cfg=ChunkConfig(max_tokens=128,overlap_tokens=16)
    pool=json.loads((Path(a.snapshot)/'1_Pooling/config.json').read_text())
    spec=DenseSpec(model_id=a.model,revision=a.revision,dimension=client.dimension,tokenizer_id=budget.tokenizer_id,
        tokenizer_revision=budget.revision,pooling='cls' if pool.get('pooling_mode_cls_token') else 'mean',
        query_prefix=real._query_prefix or None,passage_prefix=real._passage_prefix or None,runtime_sha256=a.runtime_sha)
    def chunks(js):return [c for j in js for c in chunk_job(j,budget=budget,config=cfg)]
    def manifest(js,version,dense=spec):return synthetic_manifest(js,generation=version,dense=dense,chunk_config=cfg)
    index=DerivedIndex(a.index)
    old=[synthetic_job(i) for i in range(1,4)]
    first=index.update(old,manifest=manifest(old,'initial'),chunks=chunks(old),embedding_client=client)
    newer=[synthetic_job(1,description='要求 Python、FastAPI 和 Docker 服务开发。'),old[2],synthetic_job(4)]
    before=len(client.calls);delta=index.update(newer,manifest=manifest(newer,'delta'),chunks=chunks(newer),embedding_client=client)
    assert (delta.added,delta.changed,delta.deleted)==(1,1,1)
    assert delta.encoded_chunks==2 and len(client.calls)-before==2
    keys=[JobKey(dataset_id=j.dataset_id,job_id=j.job_id) for j in newer]
    q=RetrievalQuery(text='Python 服务开发',terms=['python']);conf=EngineConfig(mode='hybrid')
    actual=index.dense_hits(q,keys,config=conf,embedding_client=client)
    assert all(h.key.job_id!=2 for h in actual.hits)
    rebuilt=DerivedIndex(a.index+'-rebuild');rebuilt.update(newer,manifest=manifest(newer,'delta'),chunks=chunks(newer),embedding_client=client)
    expected=rebuilt.dense_hits(q,keys,config=conf,embedding_client=client)
    assert actual==expected
    count=len(client.calls);index.dense_hits(q,keys,config=conf,embedding_client=client);assert count==len(client.calls)
    try:index.documents([JobKey(dataset_id=newer[0].dataset_id,job_id=2)])
    except RetrievalError as e:assert e.code=='index_candidate_missing'
    else:raise AssertionError('deleted_recalled')
    after=[*newer,synthetic_job(5)]
    try:index.update(after,manifest=manifest(after,'interrupted'),chunks=chunks(after),embedding_client=client,failpoint='before_switch')
    except RetrievalError as e:assert e.code=='index_build_interrupted'
    else:raise AssertionError('failpoint_ignored')
    assert DerivedIndex(a.index).manifest().snapshot_sha256==manifest(newer,'delta').snapshot_sha256
    # Same vectors only reusable with exact compatibility; change runtime hash => reencode.
    changed=spec.model_copy(update={'runtime_sha256':'c'*64})
    invalidated=index.update(newer,manifest=manifest(newer,'new-runtime',changed),chunks=chunks(newer),embedding_client=client)
    assert invalidated.rebuilt and invalidated.encoded_chunks==len(chunks(newer))
    count=len(client.calls);metadata=[j.model_copy(deep=True) for j in newer];metadata[0].review_status='confirmed'
    metadata_update=index.update(metadata,manifest=manifest(metadata,'metadata',changed))
    assert metadata_update.encoded_chunks==0 and len(client.calls)==count
    output=dict(status='passed',model=a.model,revision=a.revision,synthetic=True,delta=delta.model_dump(),
        independent_dense_rebuild_equal=True,deleted_not_recalled=True,query_cache_hit_no_encode=True,
        interrupted_previous_generation_retained=True,invalidation=invalidated.model_dump(),metadata=metadata_update.model_dump(),
        real_encode_calls=client.calls)
    Path(a.output).write_text(json.dumps(output,indent=2));print(json.dumps({k:v for k,v in output.items() if k!='real_encode_calls'}))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for n in ['model','revision','snapshot','runtime-sha','index','output']:p.add_argument('--'+n,required=True)
    main(p.parse_args())
