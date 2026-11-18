"""Reciprocal-rank fusion, not raw-score addition or a learned reranker."""
from __future__ import annotations
from .retrieval_models import EngineConfig, JobKey, RetrievalError, RetrievalHit
from .retrieval_text import key_tuple


def fuse_rrf(bm25: list[RetrievalHit], dense: list[RetrievalHit], config: EngineConfig) -> list[tuple[JobKey,float]]:
    config=EngineConfig.model_validate(config)
    combined={}
    for channel,hits in [('bm25',bm25),('dense',dense)]:
        seen=set()
        for rank,item in enumerate(hits,1):
            hit=RetrievalHit.model_validate(item)
            identity=key_tuple(hit.key)
            if hit.channel!=channel or hit.rank!=rank or identity in seen:
                raise RetrievalError('invalid_channel_ranking')
            seen.add(identity)
            key,score=combined.get(identity,(hit.key,0.0))
            combined[identity]=(key,score+1.0/(config.rrf_k+rank))
    return sorted(combined.values(),key=lambda pair:(-pair[1],key_tuple(pair[0])))
