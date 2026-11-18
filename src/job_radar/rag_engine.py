"""Explicit Stage 3 indexed entry; Stage 2 in-memory baseline remains intact."""
from __future__ import annotations
from .matching_models import MatchingRequest
from .retrieval_models import (EngineConfig, EmbeddingClient, RetrievalError,
    MatchingResult, ChannelStatus, ExcludedJob, RankedJob)
from .rag_models import IndexedMatchingResult
from .retrieval_text import project_request, key_tuple
from .retrieval_bm25 import retrieve_bm25
from .retrieval_fusion import fuse_rrf
from .matching_rules import evaluate_rules


def _match_bound(request: MatchingRequest, *, index, config: EngineConfig | None = None,
                       embedding_client: EmbeddingClient | None = None) -> IndexedMatchingResult:
    request = MatchingRequest.model_validate(request)
    config = EngineConfig() if config is None else EngineConfig.model_validate(config)
    query, projected = project_request(request, config)
    index.assert_candidates(request.jobs)
    manifest = index.manifest()
    docs = index.documents([doc.key for doc in projected])
    if [d.model_dump() for d in docs] != [d.model_dump() for d in projected]:
        raise RetrievalError('index_projection_mismatch')
    profile = request.profile
    dense_status = ChannelStatus(channel='dense', status='disabled',
        reason='not_requested' if config.mode == 'bm25_rules' else 'not_executed')
    result = MatchingResult(profile_id=profile.profile_id, profile_version=profile.profile_version,
        source=profile.source, profile_review_status=profile.review_status, purpose=request.purpose,
        requested_mode=config.mode, actual_mode='bm25_rules', status='ok', config=config,
        channels=[ChannelStatus(channel='bm25', status='ok'), dense_status], warnings=list(profile.warnings))
    chunks = []
    def finish():
        return IndexedMatchingResult(matching=MatchingResult.model_validate(result), manifest=manifest, chunk_hits=chunks)
    if not docs:
        result.status = 'no_candidates'
        return finish()
    rules = {}; eligible = []
    for candidate, doc in zip(request.jobs, docs):
        evaluated = evaluate_rules(profile, candidate, hard_constraints=config.hard_constraints)
        identity = key_tuple(doc.key); rules[identity] = evaluated
        if any(check.hard and check.status == 'not_satisfied' for check in evaluated.constraints):
            result.excluded.append(ExcludedJob(key=doc.key, rules=evaluated))
        else:
            eligible.append(doc)
    if not query.text or not query.terms:
        result.status = 'no_query'
        return finish()
    if not eligible:
        result.status = 'no_hits'
        return finish()
    bm25 = retrieve_bm25(query, eligible, config)
    dense = []
    if config.mode == 'hybrid':
        try:
            if manifest.dense is None:
                raise RetrievalError('dense_index_disabled')
            if embedding_client is None:
                raise RetrievalError('dense_client_missing')
            aggregated = index.dense_hits(query, [doc.key for doc in eligible],
                config=config, embedding_client=embedding_client)
            dense, chunks = aggregated.hits, aggregated.evidence
            dense_status = ChannelStatus(channel='dense', status='ok',
                model_id=manifest.dense.model_id, revision=manifest.dense.revision,
                dimension=manifest.dense.dimension)
            result.actual_mode = 'hybrid'
        except RetrievalError as error:
            if not config.allow_dense_fallback or not error.code.startswith(('dense_', 'embedding_')):
                raise
            dense_status = ChannelStatus(channel='dense', status='degraded', reason=error.code)
            result.status = 'degraded'
            result.warnings.append('dense_channel_degraded:' + error.code)
    result.channels = [ChannelStatus(channel='bm25', status='ok'), dense_status]
    fused = fuse_rrf(bm25, dense, config)
    fused.sort(key=lambda row: (-row[1], -(rules[key_tuple(row[0])].coverage
        if rules[key_tuple(row[0])].coverage is not None else -1), key_tuple(row[0])))
    candidates = {(j.dataset_id, j.job_id): j for j in request.jobs}
    bmap = {key_tuple(h.key): h for h in bm25}; dmap = {key_tuple(h.key): h for h in dense}
    for rank, (key, score) in enumerate(fused[:config.top_k], 1):
        identity = key_tuple(key); candidate = candidates[identity]
        result.results.append(RankedJob(key=key, url=candidate.job.url,
            fact_sha256=candidate.fact_sha256, jd_sha256=candidate.jd_sha256,
            review_status=candidate.review_status, provenance=candidate.provenance,
            rank=rank, bm25=bmap.get(identity), dense=dmap.get(identity),
            fusion_score=score, rules=rules[identity], warnings=list(candidate.job.warnings)))
    if not result.results and result.status != 'degraded':
        result.status = 'no_hits'
    return finish()


def match_jobs_indexed(request: MatchingRequest, *, index, config: EngineConfig | None = None,
                       embedding_client: EmbeddingClient | None = None) -> IndexedMatchingResult:
    # Revalidate the request before opening even the derived index. One reader
    # binds validation, projection, manifest and vectors despite concurrent switch.
    request = MatchingRequest.model_validate(request)
    config = EngineConfig() if config is None else EngineConfig.model_validate(config)
    with index.reader() as snapshot:
        return _match_bound(request, index=snapshot, config=config, embedding_client=embedding_client)
