"""Pure Stage 4 evidence boundary over one supplied retrieval/request snapshot.

This layer checks structural identity, lexical evidence and reproducible ranking.
It neither proves the truth of source documents nor reruns a dense encoder.
"""
from __future__ import annotations

import json
from pydantic import BaseModel
from .matching_models import MatchingRequest, _plain
from .resume_models import ResumeDocument
from .resume_profile import ResumeProfile, EvidenceRef, TextFact, SkillEvidence
from .resume_profile_rules import validate_profile_evidence
from .matching_engine import match_jobs
from .matching_rules import evaluate_rules
from .rag_models import IndexedMatchingResult, canonical_hash, PROJECTION_VERSION
from .retrieval_models import EngineConfig, JobKey, JobSpan, RankedJob, RetrievalHit, ChannelStatus
from .retrieval_text import key_tuple, project_request
from .retrieval_bm25 import retrieve_bm25
from .retrieval_fusion import fuse_rrf
from .report_models import AnalysisConfig, EvidencePacket, EvidenceRecord, ReportError


def _json(value):
    if isinstance(value, BaseModel): value = value.model_dump(mode='json')
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _key(candidate):
    return JobKey(dataset_id=candidate.dataset_id, job_id=candidate.job_id)


def _require(condition, code):
    if not condition: raise ReportError(code)


def _profile(request, document):
    data = request.profile.model_dump()
    data['schema_version'] = data.pop('profile_schema_version')
    data['resume_id'] = data.pop('profile_id')
    data.pop('preferences')
    profile = ResumeProfile.model_validate(data)
    validate_profile_evidence(profile, document)
    return profile


def _check_span(span, candidate):
    _require(span.field in {'title','description','company','city','salary','education','experience','attendance','posted_at'}, 'evidence_job_field')
    source = getattr(candidate.job, span.field)
    _require(span.end <= len(source) and source[span.start:span.end] == span.quote, 'evidence_job_quote')


def _walk(value, path=''):
    if isinstance(value, SkillEvidence):
        yield path, value
        return
    if isinstance(value, TextFact):
        yield path, value
        return
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            child = getattr(value, name)
            yield from _walk(child, f'{path}.{name}' if path else name)
    elif isinstance(value,list):
        for i,child in enumerate(value): yield from _walk(child, f'{path}[{i}]')


def _check_snapshot(request, result):
    m = result.matching; p = request.profile
    _require(m.engine_version == 'matching-engine-v1', 'evidence_engine_version')
    _require((m.profile_id,m.profile_version,m.source,m.profile_review_status,m.purpose) ==
             (p.profile_id,p.profile_version,p.source,p.review_status,request.purpose), 'evidence_profile_snapshot')
    _require(m.requested_mode == m.config.mode, 'evidence_requested_mode')
    _require(result.manifest.projection_version == PROJECTION_VERSION and
             result.manifest.lexical_version == 'char-bigram-alias-v1' and
             result.manifest.build_version == 'rag-v1', 'evidence_index_version')
    candidates = {(j.dataset_id,j.job_id):j for j in request.jobs}
    ranked_keys = [key_tuple(r.key) for r in m.results]
    excluded_keys = [key_tuple(r.key) for r in m.excluded]
    _require(len(set(ranked_keys)) == len(ranked_keys) and len(set(excluded_keys)) == len(excluded_keys)
             and not set(ranked_keys).intersection(excluded_keys), 'evidence_duplicate_job')
    _require([r.rank for r in m.results] == list(range(1,len(m.results)+1)), 'evidence_job_ranks')
    for row in [*m.results,*m.excluded]:
        candidate = candidates.get(key_tuple(row.key))
        _require(candidate is not None, 'evidence_job_scope')
        expected_rules = evaluate_rules(p,candidate,hard_constraints=m.config.hard_constraints)
        _require(row.rules == expected_rules, 'evidence_rule_snapshot')
        for check in [*row.rules.skills,*row.rules.constraints]:
            for span in check.job_evidence: _check_span(span,candidate)
        if isinstance(row,RankedJob):
            _require((row.fact_sha256,row.jd_sha256,row.url,row.provenance,row.review_status,row.warnings) ==
                     (candidate.fact_sha256,candidate.jd_sha256,candidate.job.url,candidate.provenance,candidate.review_status,candidate.job.warnings), 'evidence_job_snapshot')
            for hit,channel in [(row.bm25,'bm25'),(row.dense,'dense')]:
                _require(hit is None or (hit.key == row.key and hit.channel == channel), 'evidence_hit_scope')
    return candidates


def _check_chunks(result,candidates):
    seen=set()
    for n,hit in enumerate(result.chunk_hits,1):
        c=hit.chunk; ident=key_tuple(c.key)
        candidate=candidates.get(ident)
        _require(candidate is not None and candidate.jd_sha256 == c.jd_sha256, 'evidence_chunk_snapshot')
        _require(ident not in seen and hit.rank == n,'evidence_chunk_ranks')
        seen.add(ident)
        _require(c.span.field == 'description','evidence_chunk_field')
        _check_span(c.span,candidate)
        _require(c.token_count <= result.manifest.chunk_config.max_tokens and
                 c.ordinal < result.manifest.chunk_config.max_chunks_per_job,'evidence_chunk_budget')
        _require(bool(c.mapping),'evidence_chunk_mapping')
        last=0
        for mapping in c.mapping:
            source=getattr(candidate.job,mapping.field)
            _require(mapping.source_end <= len(source) and mapping.encoded_end <= len(c.encoding_text) and
                     mapping.encoded_start >= last and
                     source[mapping.source_start:mapping.source_end] == c.encoding_text[mapping.encoded_start:mapping.encoded_end] and
                     not c.encoding_text[last:mapping.encoded_start].strip(), 'evidence_chunk_mapping')
            if mapping.field=='description':
                _require(c.span.start <= mapping.source_start < mapping.source_end <= c.span.end, 'evidence_chunk_mapping')
            last=mapping.encoded_end
        _require(not c.encoding_text[last:].strip(),'evidence_chunk_mapping')
    _require(len(result.chunk_hits) <= result.matching.config.recall_k,'evidence_chunk_ranks')
    _require(result.chunk_hits == sorted(result.chunk_hits,key=lambda h:(-h.score,key_tuple(h.chunk.key))), 'evidence_chunk_order')


def _check_ranking(request,result,candidates):
    m=result.matching
    lexical_data=m.config.model_dump();lexical_data['mode']='bm25_rules'
    lexical_cfg=EngineConfig.model_validate(lexical_data)
    baseline=match_jobs(request,config=lexical_cfg)
    if m.actual_mode == 'bm25_rules':
        _require(not result.chunk_hits and all(r.dense is None for r in m.results), 'evidence_unexpected_dense')
        expected=baseline.model_dump()
        expected['config']=m.config.model_dump();expected['requested_mode']=m.requested_mode
        if m.status == 'degraded':
            _require(m.requested_mode=='hybrid' and m.config.allow_dense_fallback and baseline.status in {'ok','no_hits'}, 'evidence_degradation')
            _require(len(m.channels)==2 and m.channels[0]==baseline.channels[0] and
                     m.channels[1].channel=='dense' and m.channels[1].status=='degraded' and
                     bool(m.channels[1].reason) and m.channels[1].reason.startswith(('dense_','embedding_')),
                     'evidence_degradation')
            expected['status']='degraded';expected['channels']=[c.model_dump() for c in m.channels]
            expected['warnings'].append('dense_channel_degraded:'+m.channels[1].reason)
        elif m.requested_mode=='hybrid':
            # Indexed engine returns before dense execution for empty input/query/eligible set.
            _require(baseline.status in {'no_candidates','no_query','no_hits'} and not baseline.results,
                     'evidence_missing_dense_execution')
            if baseline.status=='no_hits':
                _require(len(baseline.excluded)==len(request.jobs),'evidence_missing_dense_execution')
            expected['channels'][1]['reason']='not_executed'
        _require(m.model_dump()==expected,'evidence_lexical_result')
        return
    _require(m.requested_mode=='hybrid' and result.manifest.dense is not None, 'evidence_dense_metadata')
    spec=result.manifest.dense
    expected_channels=[ChannelStatus(channel='bm25',status='ok'),ChannelStatus(channel='dense',status='ok',model_id=spec.model_id,revision=spec.revision,dimension=spec.dimension)]
    _require(m.channels==expected_channels,'evidence_dense_metadata')
    query,docs=project_request(request,m.config)
    excluded={key_tuple(x.key) for x in baseline.excluded}
    eligible=[d for d in docs if key_tuple(d.key) not in excluded]
    _require(bool(query.text and query.terms and eligible),'evidence_dense_execution')
    bm25=retrieve_bm25(query,eligible,m.config)
    dense=[RetrievalHit(key=h.chunk.key,channel='dense',score=h.score,rank=h.rank) for h in result.chunk_hits]
    _require(all(key_tuple(h.key) not in excluded for h in dense),'evidence_dense_excluded')
    fused=fuse_rrf(bm25,dense,m.config)
    rules={key_tuple(d.key):evaluate_rules(request.profile,candidates[key_tuple(d.key)],hard_constraints=m.config.hard_constraints) for d in eligible}
    fused.sort(key=lambda pair:(-pair[1],-(rules[key_tuple(pair[0])].coverage if rules[key_tuple(pair[0])].coverage is not None else -1),key_tuple(pair[0])))
    bmap={key_tuple(h.key):h for h in bm25};dmap={key_tuple(h.key):h for h in dense}
    expected_rows=[]
    for n,(key,score) in enumerate(fused[:m.config.top_k],1):
        j=candidates[key_tuple(key)]
        expected_rows.append(RankedJob(key=key,url=j.job.url,fact_sha256=j.fact_sha256,jd_sha256=j.jd_sha256,review_status=j.review_status,provenance=j.provenance,rank=n,bm25=bmap.get(key_tuple(key)),dense=dmap.get(key_tuple(key)),fusion_score=score,rules=rules[key_tuple(key)],warnings=j.job.warnings))
    _require(m.results==expected_rows and m.excluded==baseline.excluded and
             m.status==('ok' if expected_rows else 'no_hits') and m.warnings==baseline.warnings,
             'evidence_hybrid_result')


def build_evidence_packet(request: MatchingRequest, result: IndexedMatchingResult,
                          document: ResumeDocument, *, config: AnalysisConfig|None=None) -> EvidencePacket:
    """Bind validated original evidence to immutable retrieval; no I/O/model calls."""
    try:
        request=MatchingRequest.model_validate(_plain(request))
        result=IndexedMatchingResult.model_validate(_plain(result))
        document=ResumeDocument.model_validate(_plain(document))
        config=AnalysisConfig() if config is None else AnalysisConfig.model_validate(_plain(config))
        profile=_profile(request,document)
        candidates=_check_snapshot(request,result)
        _check_chunks(result,candidates)
        _check_ranking(request,result,candidates)
        records=[];record_ids=set()
        profile_hash=canonical_hash(request.profile)
        blocks={(page.page_number,block.block_id):block.text for page in document.pages for block in page.blocks}
        def add(kind,path,source,value,*,key=None,quote=None,resume_ref=None,job_span=None):
            data=dict(kind=kind,path=path,key=key.model_dump() if key else None,source_sha256=source,
                quote=quote,resume_ref=resume_ref.model_dump() if resume_ref else None,
                job_span=job_span.model_dump() if job_span else None,value_json=_json(value))
            eid='e'+canonical_hash(data)[:24]
            if eid not in record_ids:
                records.append(EvidenceRecord(evidence_id=eid,**data));record_ids.add(eid)
        for path,fact in _walk(profile):
            refs=fact.mention.evidence if isinstance(fact,SkillEvidence) else fact.evidence
            for ref in refs:
                value=fact.model_dump(mode='json')
                value['block_text']=blocks[(ref.page,ref.block_id)]
                add('resume',path,document.provenance.source_sha256,value,quote=ref.quote,resume_ref=ref)
        # Original full blocks are needed to inspect negation/plans not represented as positive profile facts.
        for page in document.pages:
            for block in page.blocks:
                ref=EvidenceRef(page=page.page_number,block_id=block.block_id,quote=block.text)
                add('resume',f'document.pages[{page.page_number-1}].blocks[{block.block_id}]',document.provenance.source_sha256,
                    {'block_text':block.text,'scope':'original_document_block_not_extracted_fact'},quote=block.text,resume_ref=ref)
        for name,value in request.profile.preferences.model_dump(mode='json').items():
            add('preference','preferences.'+name,profile_hash,value)
        add('scope','profile',profile_hash,{'scope':'supplied_matching_profile_only',
            'profile':request.profile.model_dump(mode='json'),
            'document_source_sha256':document.provenance.source_sha256,
            'absence_means':'not_found_in_supplied_profile_not_absent_ability',
            'original_document_rechecked':True})
        selected=[r.key for r in result.matching.results[:config.top_n]]
        for row in result.matching.results:
            j=candidates[key_tuple(row.key)];key=row.key
            for name in ['title','description','company','city','salary','education','experience','attendance','posted_at']:
                text=getattr(j.job,name)
                if text:
                    span=JobSpan(field=name,start=0,end=len(text),quote=text)
                    add('job','job.'+name,j.jd_sha256 if name=='description' else j.fact_sha256,
                        {'field':name,'text':text},key=key,quote=text,job_span=span)
            for kind in ['skills','constraints']:
                for i,check in enumerate(getattr(row.rules,kind)):
                    add('rule',f'rules.{kind}[{i}]',canonical_hash(row.rules),check,key=key)
                    for span in check.job_evidence:
                        add('job',f'job.{span.field}[{span.start}:{span.end}]',j.jd_sha256 if span.field=='description' else j.fact_sha256,
                            {'field':span.field,'text':span.quote},key=key,quote=span.quote,job_span=span)
            add('rule','rules',canonical_hash(row.rules),row.rules,key=key)
            add('retrieval','retrieval',canonical_hash(result),
                {'rank':row.rank,'bm25':row.bm25.model_dump(mode='json') if row.bm25 else None,
                 'dense':row.dense.model_dump(mode='json') if row.dense else None,'fusion_score':row.fusion_score,
                 'requested_mode':result.matching.requested_mode,'actual_mode':result.matching.actual_mode,
                 'channels':[c.model_dump(mode='json') for c in result.matching.channels]},key=key)
        for hit in result.chunk_hits:
            c=hit.chunk
            add('job',f'chunk[{c.ordinal}].description',c.jd_sha256,
                {'field':'description','text':c.span.quote,'chunk_ordinal':c.ordinal,'token_count':c.token_count},
                key=c.key,quote=c.span.quote,job_span=c.span)
        limitations=['citations_validate_location_not_semantic_entailment','retrieval_scores_are_not_hiring_probabilities',
            'scope_absence_is_not_ability_absence','source_truth_and_recruiting_status_not_verified',
            'index_snapshot_may_include_jobs_outside_request','contact_minimization_is_not_full_anonymization']
        if result.matching.actual_mode=='hybrid': limitations.append('dense_query_computation_not_replayed')
        if result.matching.status=='degraded': limitations.append('retrieval_dense_degraded')
        if request.purpose=='debug': limitations.append('debug_inputs_may_be_unconfirmed')
        return EvidencePacket(request_sha256=canonical_hash(request),document_sha256=canonical_hash(document),
            profile_sha256=profile_hash,retrieval_sha256=canonical_hash(result),retrieval=result,
            selected_keys=selected,evidence=records,limitations=limitations)
    except ReportError: raise
    except (ValueError,TypeError,AttributeError,KeyError,RecursionError,OverflowError):
        raise ReportError('evidence_invalid_input') from None


def validate_evidence_packet(packet, request, result, document, *, config=None) -> EvidencePacket:
    """Rebuild from originals and compare every field; never trust an editable ID."""
    expected=build_evidence_packet(request,result,document,config=config)
    try: packet=EvidencePacket.model_validate(_plain(packet))
    except (ValueError,TypeError,AttributeError,RecursionError): raise ReportError('evidence_invalid_packet') from None
    if packet.model_dump()!=expected.model_dump(): raise ReportError('evidence_packet_mismatch')
    return expected
