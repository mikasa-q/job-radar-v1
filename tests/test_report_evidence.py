"""Stage 4 evidence checks use authored synthetic material, no network/model."""
from copy import deepcopy
import hashlib
import json
from datetime import datetime,timezone
import pytest
from job_radar.report_evidence import build_evidence_packet,validate_evidence_packet
from job_radar.report_models import AnalysisConfig,ReportError, EvidencePacket
from job_radar.report_synthetic import synthetic_inputs,synthetic_manifest
from job_radar.rag_index import DerivedIndex
from job_radar.rag_engine import match_jobs_indexed
from job_radar.rag_models import canonical_hash,ChunkHit,EvidenceChunk,TextMap,DenseSpec
from job_radar.retrieval_models import EngineConfig, JobSpan, RetrievalHit,ChannelStatus,RankedJob
from job_radar.retrieval_text import key_tuple,project_request
from job_radar.retrieval_bm25 import retrieve_bm25
from job_radar.retrieval_fusion import fuse_rrf
from job_radar.matching_rules import evaluate_rules


def run(tmp_path,*,text=None,descriptions=None,cfg=None):
    kw={}
    if text is not None:kw['text']=text
    if descriptions is not None:kw['descriptions']=descriptions
    doc,req=synthetic_inputs(**kw)
    idx=DerivedIndex(tmp_path/'idx');idx.update(req.jobs,manifest=synthetic_manifest(req.jobs))
    result=match_jobs_indexed(req,index=idx,config=cfg)
    return doc,req,result


def test_roundtrip_deterministic_detached(tmp_path):
    doc,req,res=run(tmp_path)
    originals=[deepcopy(o.model_dump()) for o in [doc,req,res]]
    p=build_evidence_packet(req,res,doc)
    assert p==build_evidence_packet(req,res,doc)
    assert p==validate_evidence_packet(p,req,res,doc)
    assert p==EvidencePacket.model_validate_json(p.model_dump_json())
    assert [o.model_dump() for o in [doc,req,res]]==originals
    assert p.retrieval==res and p.retrieval is not res
    p.evidence.clear()
    assert res.model_dump()==originals[2]


@pytest.mark.parametrize('n',[1,2,3,20])
def test_ranked_prefix_selection(tmp_path,n):
    doc,req,res=run(tmp_path)
    cfg=AnalysisConfig(top_n=n)
    p=build_evidence_packet(req,res,doc,config=cfg)
    assert p.selected_keys==[r.key for r in res.matching.results[:n]]
    assert p==validate_evidence_packet(p,req,res,doc,config=cfg)


def test_every_id_is_content_bound(tmp_path):
    doc,req,res=run(tmp_path)
    p=build_evidence_packet(req,res,doc)
    for r in p.evidence:
        data=r.model_dump();data.pop('evidence_id')
        assert r.evidence_id=='e'+canonical_hash(data)[:24]
    assert len({r.evidence_id for r in p.evidence})==len(p.evidence)


def test_full_requirements_and_qualifiers_kept(tmp_path):
    desc='要求掌握 Python 或 Java。Docker 加分。暂不要求 Redis。'
    doc,req,res=run(tmp_path,descriptions=[desc])
    p=build_evidence_packet(req,res,doc)
    r=next(e for e in p.evidence if e.kind=='job' and e.path=='job.description')
    assert r.quote==desc
    assert r.job_span.start==0 and r.job_span.end==len(desc)
    checks=[json.loads(e.value_json) for e in p.evidence if e.kind=='rule' and e.path.startswith('rules.skills[')]
    assert checks[0]['alternatives']==['Python','Java'] and checks[0]['status']=='evidence_found'
    assert checks[1]['kind']=='bonus'
    assert any(e.kind=='job' and 'Docker 加分' in (e.quote or '') for e in p.evidence)


def test_profile_and_original_block_context(tmp_path):
    text='课程项目：使用 Python 和 FastAPI 开发接口。'
    doc,req,res=run(tmp_path,text=text)
    p=build_evidence_packet(req,res,doc)
    e=next(e for e in p.evidence if e.path=='skills[0]')
    value=json.loads(e.value_json)
    assert value['name']=='Python' and value['context']=='course' and value['block_text']==text
    assert e.resume_ref.quote in doc.pages[0].blocks[0].text
    block=next(e for e in p.evidence if e.path.startswith('document.'))
    assert block.quote==text and json.loads(block.value_json)['scope']=='original_document_block_not_extracted_fact'


def test_unknown_scope_not_ability_absence(tmp_path):
    doc,req,res=run(tmp_path)
    p=build_evidence_packet(req,res,doc)
    scope=json.loads(next(e.value_json for e in p.evidence if e.kind=='scope'))
    assert scope['scope']=='supplied_matching_profile_only'
    assert scope['absence_means']=='not_found_in_supplied_profile_not_absent_ability'
    assert scope['profile']==req.profile.model_dump(mode='json')
    assert len([e for e in p.evidence if e.kind=='preference'])==4
    assert 'scope_absence_is_not_ability_absence' in p.limitations
    for e in p.evidence:
        if e.path.startswith('rules.skills['):
            v=json.loads(e.value_json)
            if v['alternatives']==['Docker']:assert v['status']=='not_found'


@pytest.mark.parametrize('mode',['no_query','no_hits','no_candidates'])
def test_empty_results_local_packet(tmp_path,mode):
    kw={}
    if mode=='no_query':kw={'text':'未提供正向技能。'}
    if mode=='no_hits':kw={'text':'课程项目：使用 SQL。','descriptions':['要求掌握 Python。']}
    if mode=='no_candidates':kw={'descriptions':[]}
    doc,req,res=run(tmp_path,**kw)
    assert res.matching.status==mode
    p=build_evidence_packet(req,res,doc)
    assert not p.selected_keys and p.retrieval.matching.status==mode


def test_degraded_result_preserved(tmp_path):
    cfg=EngineConfig(mode='hybrid',allow_dense_fallback=True)
    doc,req,res=run(tmp_path,cfg=cfg)
    p=build_evidence_packet(req,res,doc)
    assert p.retrieval==res and p.retrieval.matching.status=='degraded'
    assert p.retrieval.matching.channels[1].reason=='dense_index_disabled'
    assert 'retrieval_dense_degraded' in p.limitations


@pytest.mark.parametrize('mode',['no_query','no_hits','no_candidates'])
def test_hybrid_pre_dense_return(tmp_path,mode):
    kwargs={'cfg':EngineConfig(mode='hybrid')}
    if mode=='no_query':kwargs['text']='未提供正向技能。'
    elif mode=='no_candidates':kwargs['descriptions']=[]
    else:
        # no_hits before dense only when all candidates have hard conflict.
        kwargs['cfg']=EngineConfig(mode='hybrid',hard_constraints=['city'])
    if mode=='no_hits':
        doc,req=synthetic_inputs();req.profile.preferences.cities=['北京']
        idx=DerivedIndex(tmp_path/'idx');idx.update(req.jobs,manifest=synthetic_manifest(req.jobs))
        res=match_jobs_indexed(req,index=idx,config=kwargs['cfg'])
    else:doc,req,res=run(tmp_path,**kwargs)
    assert res.matching.status==mode
    assert build_evidence_packet(req,res,doc).retrieval==res


@pytest.mark.parametrize('field,value',[('profile_id','stale'),('profile_version',2),('profile_review_status','confirmed'),('purpose','formal')])
def test_stale_profile_identity_rejected(tmp_path,field,value):
    doc,req,res=run(tmp_path)
    res.matching.__dict__[field]=value
    with pytest.raises(ReportError,match='evidence_profile_snapshot'):build_evidence_packet(req,res,doc)


@pytest.mark.parametrize('field,value',[('jd_sha256','f'*64),('fact_sha256','f'*64),('review_status','confirmed'),('url','https://example.org/other'),('warnings',['invented'])])
def test_stale_job_snapshot_rejected(tmp_path,field,value):
    doc,req,res=run(tmp_path)
    res.matching.results[0].__dict__[field]=value
    with pytest.raises(ReportError,match='evidence_job_snapshot'):build_evidence_packet(req,res,doc)


def test_stale_provenance_rejected(tmp_path):
    doc,req,res=run(tmp_path)
    res.matching.results[0].provenance[0].__dict__['parser_version']='changed'
    with pytest.raises(ReportError,match='evidence_job_snapshot'):build_evidence_packet(req,res,doc)


@pytest.mark.parametrize('change',['rank','bm25','fusion','omit','duplicate','channel'])
def test_lexical_ranking_decomposition_rejected(tmp_path,change):
    doc,req,res=run(tmp_path)
    row=res.matching.results[0]
    if change=='rank':row.__dict__['rank']=2
    elif change=='bm25':row.bm25.__dict__['score']+=0.1
    elif change=='fusion':row.__dict__['fusion_score']+=0.1
    elif change=='omit':res.matching.results.pop()
    elif change=='duplicate':res.matching.results.append(row)
    else:res.matching.channels[0].__dict__['status']='disabled'
    with pytest.raises(ReportError):build_evidence_packet(req,res,doc)


@pytest.mark.parametrize('change',['quote','classification','constraint','coverage'])
def test_rules_recomputed_not_accepted_by_id(tmp_path,change):
    doc,req,res=run(tmp_path)
    rules=res.matching.results[0].rules
    if change=='quote':
        old=rules.skills[0].job_evidence[0];old.__dict__['quote']='x'*len(old.quote)
    elif change=='classification':rules.skills[0].__dict__['kind']='bonus' if rules.skills[0].kind!='bonus' else 'mention'
    elif change=='constraint':rules.constraints[0].__dict__['status']='satisfied'
    else:rules.__dict__['coverage']=0.0
    with pytest.raises(ReportError):build_evidence_packet(req,res,doc)


@pytest.mark.parametrize('change',['quote','block','source','parser','positive_context','nested_fact'])
def test_profile_document_boundary(tmp_path,change):
    doc,req,res=run(tmp_path)
    if change=='quote':req.profile.skills[0].mention.evidence[0].__dict__['quote']='伪造Python句子'
    elif change=='block':req.profile.skills[0].mention.evidence[0].__dict__['block_id']='p001-b099'
    elif change=='source':doc.provenance.__dict__['source_sha256']='b'*64
    elif change=='parser':doc.provenance.__dict__['parser_version']='new'
    elif change=='positive_context':doc.pages[0].blocks[0].__dict__['text']='计划学习 Python 和 FastAPI。'
    else:req.profile.skills[0].mention.__dict__['value']='Java'
    with pytest.raises(ReportError):build_evidence_packet(req,res,doc)


@pytest.mark.parametrize('change',['evidence','quote','id','scope','selection','retrieval','limitation','source'])
def test_rebuild_rejects_edited_packet(tmp_path,change):
    doc,req,res=run(tmp_path)
    p=build_evidence_packet(req,res,doc)
    if change=='evidence':p.evidence.pop()
    elif change=='quote':p.evidence[0].__dict__['quote']='changed'
    elif change=='id':p.evidence[0].__dict__['evidence_id']='e'+'f'*24
    elif change=='scope':next(e for e in p.evidence if e.kind=='scope').__dict__['value_json']='{}'
    elif change=='selection':p.selected_keys.pop()
    elif change=='retrieval':p.retrieval.matching.results[0].__dict__['fusion_score']=0.0
    elif change=='limitation':p.limitations.clear()
    else:p.__dict__['request_sha256']='b'*64
    with pytest.raises(ReportError):validate_evidence_packet(p,req,res,doc)


def test_stale_request_same_id_different_warning(tmp_path):
    doc,req,res=run(tmp_path);p=build_evidence_packet(req,res,doc)
    req.profile.warnings.append('new_warning')
    with pytest.raises(ReportError):validate_evidence_packet(p,req,res,doc)


def test_formal_gate_rechecked(tmp_path):
    doc,req,res=run(tmp_path)
    req.__dict__['purpose']='formal'
    with pytest.raises(ReportError):build_evidence_packet(req,res,doc)


def test_excluded_preserved_and_no_analysis_evidence(tmp_path):
    doc,req=synthetic_inputs()
    req.profile.preferences.cities=['北京']
    idx=DerivedIndex(tmp_path/'idx');idx.update(req.jobs,manifest=synthetic_manifest(req.jobs))
    res=match_jobs_indexed(req,index=idx,config=EngineConfig(hard_constraints=['city']))
    p=build_evidence_packet(req,res,doc)
    assert len(p.retrieval.matching.excluded)==len(req.jobs)
    assert not [e for e in p.evidence if e.key is not None]


def authored_hybrid(tmp_path):
    """Authored scalar ranks/chunk mappings test structure only, no fake vectors."""
    doc,req,res=run(tmp_path,descriptions=['要求掌握 Python。','要求掌握 Python 和 SQL。'])
    m=res.matching
    cfg=EngineConfig(mode='hybrid')
    res.manifest.dense=DenseSpec(model_id='synthetic-structure',revision='a'*40,dimension=2,
        tokenizer_id='synthetic-structure',tokenizer_revision='a'*40,pooling='cls',runtime_sha256='a'*64)
    m.config=cfg;m.requested_mode='hybrid';m.actual_mode='hybrid'
    m.channels=[ChannelStatus(channel='bm25',status='ok'),ChannelStatus(channel='dense',status='ok',model_id='synthetic-structure',revision='a'*40,dimension=2)]
    hits=[]
    for n,j in enumerate(reversed(req.jobs),1):
        text=j.job.description
        chunk=EvidenceChunk(key=dict(dataset_id=j.dataset_id,job_id=j.job_id),jd_sha256=j.jd_sha256,
            ordinal=0,span=JobSpan(field='description',start=0,end=len(text),quote=text),encoding_text=text,
            encoding_sha256=hashlib.sha256(text.encode()).hexdigest(),token_count=8,
            mapping=[TextMap(field='description',source_start=0,source_end=len(text),encoded_start=0,encoded_end=len(text))])
        hits.append(ChunkHit(chunk=chunk,score=1.0/n,rank=n))
    res.chunk_hits=hits
    query,docs=project_request(req,cfg);bm25=retrieve_bm25(query,docs,cfg)
    dense=[RetrievalHit(key=h.chunk.key,channel='dense',score=h.score,rank=h.rank) for h in hits]
    fused=fuse_rrf(bm25,dense,cfg)
    candidates={(j.dataset_id,j.job_id):j for j in req.jobs}
    rules={k:evaluate_rules(req.profile,j,hard_constraints=[]) for k,j in candidates.items()}
    fused.sort(key=lambda pair:(-pair[1],-(rules[key_tuple(pair[0])].coverage if rules[key_tuple(pair[0])].coverage is not None else -1),key_tuple(pair[0])))
    bmap={key_tuple(h.key):h for h in bm25};dmap={key_tuple(h.key):h for h in dense}
    m.results=[]
    for n,(key,score) in enumerate(fused,1):
        j=candidates[key_tuple(key)]
        m.results.append(RankedJob(key=key,url=j.job.url,fact_sha256=j.fact_sha256,jd_sha256=j.jd_sha256,review_status=j.review_status,provenance=j.provenance,rank=n,bm25=bmap.get(key_tuple(key)),dense=dmap.get(key_tuple(key)),fusion_score=score,rules=rules[key_tuple(key)],warnings=j.job.warnings))
    return doc,req,res


def test_authored_hybrid_structure_and_limitation(tmp_path):
    doc,req,res=authored_hybrid(tmp_path)
    p=build_evidence_packet(req,res,doc)
    assert 'dense_query_computation_not_replayed' in p.limitations
    assert p.retrieval==res
    assert len([e for e in p.evidence if e.path.startswith('chunk[')])==2


@pytest.mark.parametrize('change',['span','jd_hash','map','map_missing','map_gap','token_budget','ordinal','rank','key','score','dense_hit','metadata','excluded'])
def test_authored_hybrid_bad_structure_rejected(tmp_path,change):
    doc,req,res=authored_hybrid(tmp_path)
    h=res.chunk_hits[0];c=h.chunk
    if change=='span':c.span.__dict__['quote']='X'*len(c.span.quote)
    elif change=='jd_hash':c.__dict__['jd_sha256']='b'*64
    elif change=='map':c.mapping[0].__dict__['source_start']=1
    elif change=='map_missing':c.mapping.clear()
    elif change=='map_gap':c.mapping[0].__dict__['encoded_start']=1;c.mapping[0].__dict__['source_start']=1
    elif change=='token_budget':c.__dict__['token_count']=8192
    elif change=='ordinal':c.__dict__['ordinal']=10000
    elif change=='rank':h.__dict__['rank']=2
    elif change=='key':c.key.__dict__['job_id']=999
    elif change=='score':h.__dict__['score']=0.0
    elif change=='dense_hit':res.matching.results[0].dense.__dict__['score']=0.1
    elif change=='metadata':res.matching.channels[1].__dict__['model_id']='other'
    else:res.matching.__dict__['status']='no_hits'
    with pytest.raises(ReportError):build_evidence_packet(req,res,doc)


def test_wrong_engine_version_rejected(tmp_path):
    doc,req,res=run(tmp_path)
    res.matching.__dict__['engine_version']='other'
    with pytest.raises(ReportError,match='evidence_engine_version'):build_evidence_packet(req,res,doc)


def test_only_supplied_candidates_without_db_read(tmp_path,monkeypatch):
    import builtins
    doc,req,res=run(tmp_path)
    original=builtins.__import__
    def guard(name,*a,**kw):
        assert name not in {'torch','numpy','sentence_transformers','openai'}
        return original(name,*a,**kw)
    monkeypatch.setattr(builtins,'__import__',guard)
    p=build_evidence_packet(req,res,doc)
    assert all(e.key is None or e.key.job_id in {j.job_id for j in req.jobs} for e in p.evidence)


def test_index_superset_snapshot_explicitly_limited(tmp_path):
    doc,req=synthetic_inputs();idx=DerivedIndex(tmp_path/'idx')
    idx.update(req.jobs,manifest=synthetic_manifest(req.jobs))
    req.jobs=req.jobs[:1]
    res=match_jobs_indexed(req,index=idx)
    p=build_evidence_packet(req,res,doc)
    assert 'index_snapshot_may_include_jobs_outside_request' in p.limitations
    assert all(e.key is None or e.key==res.matching.results[0].key for e in p.evidence)
