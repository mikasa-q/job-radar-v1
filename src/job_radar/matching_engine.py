"""Stage 2 composition over the unchanged Stage 1D MatchingRequest.

Default BM25+rules runs without model packages. Dense degradation is opt-in,
explicit in results, and never masquerades as a successful hybrid execution.
"""
from __future__ import annotations
from .matching_models import MatchingRequest
from .retrieval_models import (
    ChannelStatus, EmbeddingClient, EngineConfig, ExcludedJob, MatchingResult,
    RankedJob, RetrievalError,
)
from .retrieval_text import key_tuple, project_request
from .retrieval_fusion import fuse_rrf


def match_jobs(request: MatchingRequest, *, config: EngineConfig | None=None,
               embedding_client: EmbeddingClient | None=None) -> MatchingResult:
    # Lazy module imports permit independent implementation against frozen APIs.
    from .retrieval_bm25 import retrieve_bm25
    from .retrieval_embedding import retrieve_dense
    from .matching_rules import evaluate_rules
    request=MatchingRequest.model_validate(request)
    config=EngineConfig() if config is None else EngineConfig.model_validate(config)
    query,documents=project_request(request,config)
    p=request.profile
    dense_status=ChannelStatus(channel='dense',status='disabled',reason='not_requested' if config.mode=='bm25_rules' else 'not_executed')
    result=MatchingResult(profile_id=p.profile_id,profile_version=p.profile_version,
        source=p.source,profile_review_status=p.review_status,purpose=request.purpose,
        requested_mode=config.mode,actual_mode='bm25_rules',status='ok',config=config,
        channels=[ChannelStatus(channel='bm25',status='ok'),dense_status],warnings=list(p.warnings))
    if not documents:
        result.status='no_candidates';return result
    rules={};eligible=[]
    for candidate,doc in zip(request.jobs,documents):
        evaluated=evaluate_rules(p,candidate,hard_constraints=config.hard_constraints)
        identity=key_tuple(doc.key);rules[identity]=evaluated
        if any(c.hard and c.status=='not_satisfied' for c in evaluated.constraints):
            result.excluded.append(ExcludedJob(key=doc.key,rules=evaluated))
        else:eligible.append(doc)
    if not query.text or not query.terms:
        result.status='no_query';return result
    if not eligible:
        result.status='no_hits';return result
    bm25=retrieve_bm25(query,eligible,config);dense=[]
    if config.mode=='hybrid':
        try:
            if embedding_client is None:raise RetrievalError('embedding_client_missing')
            dense=retrieve_dense(query,eligible,config,client=embedding_client)
            dense_status=ChannelStatus(channel='dense',status='ok',
                model_id=embedding_client.model_id,revision=embedding_client.revision,
                dimension=embedding_client.dimension)
            result.actual_mode='hybrid'
        except RetrievalError as error:
            if not config.allow_dense_fallback or not error.code.startswith(('embedding_','dense_')):
                raise
            dense_status=ChannelStatus(channel='dense',status='degraded',reason=error.code)
            result.status='degraded';result.warnings.append('dense_channel_degraded:'+error.code)
    result.channels=[ChannelStatus(channel='bm25',status='ok'),dense_status]
    fused=fuse_rrf(bm25,dense,config)
    # Coverage is only a tie-breaker, never a chance of being hired.
    fused.sort(key=lambda pair:(-pair[1],-(rules[key_tuple(pair[0])].coverage if rules[key_tuple(pair[0])].coverage is not None else -1),key_tuple(pair[0])))
    bmap={key_tuple(h.key):h for h in bm25};dmap={key_tuple(h.key):h for h in dense}
    candidates={(j.dataset_id,j.job_id):j for j in request.jobs}
    for rank,(key,score) in enumerate(fused[:config.top_k],1):
        identity=key_tuple(key);job=candidates[identity]
        result.results.append(RankedJob(key=key,url=job.job.url,fact_sha256=job.fact_sha256,
            jd_sha256=job.jd_sha256,review_status=job.review_status,provenance=job.provenance,rank=rank,
            bm25=bmap.get(identity),dense=dmap.get(identity),fusion_score=score,
            rules=rules[identity],warnings=list(job.job.warnings)))
    if not result.results and result.status!='degraded':result.status='no_hits'
    return MatchingResult.model_validate(result)
