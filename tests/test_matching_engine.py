"""Integrated Stage 2 synthetic tests; no real resume, model or evidence DB."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import pytest
from pydantic import ValidationError
from job_radar.matching_input import build_matching_input,build_matching_request
from job_radar.matching_models import JobCandidate,MatchingRequest,SearchPreferences
from job_radar.resume_models import ResumeBlock,ResumeDocument,ResumePage,ResumeProvenance
from job_radar.resume_profile_rules import build_rule_profile
from job_radar.retrieval_models import EngineConfig,JobKey,MatchingResult,RetrievalError,RetrievalHit,RetrievalQuery
from job_radar.retrieval_text import project_request,tokenize
from job_radar.retrieval_fusion import fuse_rrf
from job_radar.matching_engine import match_jobs
from job_radar.evidence_repository import read_job_candidates


def profile(text='课程项目：使用 Python 和 FastAPI 实现接口。',**prefs):
    doc=ResumeDocument(provenance=ResumeProvenance(source_sha256=hashlib.sha256(text.encode()).hexdigest(),parser_version='synthetic-v1',extractor_version='synthetic-v1'),pages=[ResumePage(page_number=1,width=600.0,height=800.0,blocks=[ResumeBlock(block_id='p001-b001',text=text,bbox=(10.0,10.0,500.0,30.0))])])
    return build_matching_input(build_rule_profile(doc,resume_id='synthetic-engine'),preferences=SearchPreferences(**prefs))


def job(index=1,description='要求 Python 或 Go。FastAPI 加分。',city='上海',**updates):
    from job_radar.import_models import ImportedJob
    record=ImportedJob(source='manual',title='后端开发实习',url=f'https://example.org/jobs/{index}',description=description,city=city,skills=['Python'],**updates)
    canonical=json.dumps(record.model_dump(),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    return JobCandidate(dataset_id='synthetic-v1',job_id=index,job=record,jd_sha256=hashlib.sha256(record.description.encode()).hexdigest(),fact_sha256=hashlib.sha256(canonical.encode()).hexdigest(),review_status='unconfirmed',provenance=[dict(provenance_id=index,source_file_sha256=str(index%10)*64,input_format='txt',parser_version='synthetic-v1',imported_at=datetime(2026,11,11,tzinfo=timezone.utc),import_run_id='synthetic-run')])


def request(jobs=None,p=None):
    return build_matching_request(profile() if p is None else p,[job(),job(2,'岗位要求：Go开发。')] if jobs is None else jobs,purpose='debug')


class VectorClient:
    model_id='scripted-test';revision='v1';dimension=2
    def encode(self,texts,*,kind):
        return [[1.0,0.0] if kind=='query' or 'Python' in x else [0.0,1.0] for x in texts]


def test_default_bm25_rules_complete_result_identity_evidence_and_roundtrip():
    req=request();before=req.model_dump_json();result=match_jobs(req)
    assert result.actual_mode==result.requested_mode=='bm25_rules'
    assert result.results[0].key.job_id==1
    assert result.results[0].bm25.channel=='bm25' and result.results[0].dense is None
    assert result.results[0].fact_sha256==req.jobs[0].fact_sha256
    assert result.results[0].review_status==result.profile_review_status=='unconfirmed'
    assert result.source==req.profile.source
    assert result.channels[1].status=='disabled'
    assert req.model_dump_json()==before
    assert MatchingResult.model_validate_json(result.model_dump_json())==result
    assert 'hire_probability' not in result.model_dump()
    for ranked in result.results:
        original=next(j for j in req.jobs if j.job_id==ranked.key.job_id)
        for check in [*ranked.rules.constraints,*ranked.rules.skills]:
            for ref in check.job_evidence:
                assert getattr(original.job,ref.field)[ref.start:ref.end]==ref.quote


def test_hybrid_and_determinism():
    req=request();config=EngineConfig(mode='hybrid')
    a=match_jobs(req,config=config,embedding_client=VectorClient())
    b=match_jobs(req,config=config,embedding_client=VectorClient())
    assert a.model_dump_json()==b.model_dump_json()
    assert a.actual_mode=='hybrid' and a.channels[1].status=='ok'
    assert a.channels[1].model_id=='scripted-test' and a.results[0].dense is not None


@pytest.mark.parametrize('error',[RetrievalError('embedding_unavailable'),RetrievalError('embedding_timeout')])
def test_explicit_dense_degradation_and_failure(error):
    class Broken(VectorClient):
        def encode(self,*args,**kwargs):raise error
    with pytest.raises(RetrievalError):match_jobs(request(),config=EngineConfig(mode='hybrid'),embedding_client=Broken())
    result=match_jobs(request(),config=EngineConfig(mode='hybrid',allow_dense_fallback=True),embedding_client=Broken())
    assert result.status=='degraded' and result.actual_mode=='bm25_rules'
    assert result.channels[1].status=='degraded' and result.channels[1].reason.startswith(('embedding_','dense_'))
    assert result.results and all(r.dense is None for r in result.results)


def test_missing_dense_client_not_silent():
    with pytest.raises(RetrievalError,match='embedding_client_missing'):match_jobs(request(),config=EngineConfig(mode='hybrid'))
    result=match_jobs(request(),config=EngineConfig(mode='hybrid',allow_dense_fallback=True))
    assert result.channels[1].reason=='embedding_client_missing'


def test_hard_conflict_never_overridden_by_retrieval():
    req=request([job(city='北京'),job(2,city='上海')],profile(cities=['上海']))
    result=match_jobs(req,config=EngineConfig(hard_constraints=['city']))
    assert [r.key.job_id for r in result.results]==[] or all(r.key.job_id!=1 for r in result.results)
    assert [r.key.job_id for r in result.excluded]==[1]


def test_unknown_city_not_excluded():
    req=request([job(city='')],profile(cities=['上海']))
    result=match_jobs(req,config=EngineConfig(hard_constraints=['city']))
    assert result.results and not result.excluded
    assert next(c for c in result.results[0].rules.constraints if c.name=='city').status=='unknown'


@pytest.mark.parametrize('jobs,p,status',[([],None,'no_candidates'),([job()],profile('没有可提取的正向技能。'),'no_query'),([job(description='Rust工程师。')],profile('课程项目：使用 Redis。'),'no_hits')])
def test_empty_boundaries(jobs,p,status):
    assert match_jobs(request(jobs,p)).status==status


def test_empty_query_roundtrip():
    q=RetrievalQuery.empty();assert RetrievalQuery.model_validate_json(q.model_dump_json())==q


@pytest.mark.parametrize('values',[
    {'top_k':0},{'recall_k':0},{'top_k':51},{'k1':0.0},{'k1':float('nan')},
    {'b':1.1},{'title_weight':-1.0},{'rrf_k':0},{'mode':'magic'},
    {'hard_constraints':['city','city']},{'hard_constraints':['salary']},
    {'max_text_chars':0},{'allow_dense_fallback':'yes'},{'top_k':True},
])
def test_config_strict(values):
    with pytest.raises(ValidationError):EngineConfig(**values)


def test_projection_limits_no_silent_truncation():
    with pytest.raises(RetrievalError,match='query_text_too_long'):project_request(request(),EngineConfig(max_text_chars=1))
    with pytest.raises(RetrievalError,match='too_many_jobs'):project_request(request(),EngineConfig(max_jobs=1))
    with pytest.raises(RetrievalError,match='job_text_too_long'):
        project_request(request([job(description='x'*200)],profile('Python')),EngineConfig(max_text_chars=20))


def test_shared_tokens_chinese_aliases_boundaries():
    assert tokenize('Python 3 py.test C++ C# Tool-Calling')==['python','pytest','c++','c#','tool calling']
    assert tokenize('接口开发')==['接口','口开','开发']
    assert 'python' not in tokenize('Pythonista')


def hit(i,rank,channel):return RetrievalHit(key=JobKey(dataset_id='synthetic',job_id=i),channel=channel,score=float(100-rank),rank=rank)


def test_rrf_hand_computation_and_one_channel():
    fused=fuse_rrf([hit(1,1,'bm25'),hit(2,2,'bm25')],[hit(2,1,'dense')],EngineConfig())
    assert fused[0][0].job_id==2 and fused[0][1]==pytest.approx(1/62+1/61)
    assert fused[1][1]==pytest.approx(1/61)
    assert fuse_rrf([],[],EngineConfig())==[]


@pytest.mark.parametrize('hits',[[hit(1,2,'bm25')],[hit(1,1,'dense')],[hit(1,1,'bm25'),hit(1,2,'bm25')]])
def test_rrf_bad_rankings(hits):
    with pytest.raises(RetrievalError,match='invalid_channel_ranking'):fuse_rrf(hits,[],EngineConfig())


def test_formal_gate_revalidated_and_input_detached():
    req=request()
    bad=req.model_copy(update={'purpose':'formal'})
    with pytest.raises(ValidationError):match_jobs(bad)
    req.jobs[0].job.skills.append('invented')
    with pytest.raises(ValidationError):match_jobs(req)


def test_core_no_file_network_database_io(monkeypatch):
    req=request()
    def blocked(*a,**k):raise AssertionError('unexpected IO')
    monkeypatch.setattr(socket,'socket',blocked);monkeypatch.setattr(sqlite3,'connect',blocked)
    monkeypatch.setattr('builtins.open',blocked);monkeypatch.setattr(Path,'read_bytes',blocked)
    assert match_jobs(req).results


def create_db(path):
    from job_radar.dataset_store import _SCHEMA
    record=job();j=record.job
    with sqlite3.connect(path) as conn:
        conn.execute(f'PRAGMA application_id={0x4A525631}');conn.execute('PRAGMA user_version=1')
        for sql in _SCHEMA:conn.execute(sql)
        conn.execute("INSERT INTO import_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('synthetic-run','/synthetic',str(path),'/synthetic/report','a'*64,'apply','t','t','applied',1,1,0,0,2))
        cols='id,source,url,title,company,city,salary,education,experience,attendance,description,description_kind,posted_at,source_tags_json,skills_json,warnings_json,jd_sha256,fact_sha256,review_status,first_imported_at,last_imported_at,created_at,updated_at'
        vals=[1,j.source,j.url,j.title,j.company,j.city,j.salary,j.education,j.experience,j.attendance,j.description,j.description_kind,j.posted_at,json.dumps(j.source_tags),json.dumps(j.skills),json.dumps(j.warnings),record.jd_sha256,record.fact_sha256,'unconfirmed','t','t','t','t']
        conn.execute('INSERT INTO imported_jobs ('+cols+') VALUES ('+','.join('?' for _ in vals)+')',vals)
        for i in [1,2]:conn.execute('INSERT INTO job_provenance VALUES (?,?,?,?,?,?,?,?)',(i,1,'/private/not-exposed',str(i)*64,'txt','synthetic-v1','2026-11-11T00:00:00+00:00','synthetic-run'))


def test_readonly_adapter_multiple_sources_identity_and_hashes(tmp_path):
    path=tmp_path/'evidence.sqlite3';create_db(path);before=path.read_bytes()
    got=read_job_candidates(path,dataset_id='synthetic-db')
    assert len(got)==1 and len(got[0].provenance)==2
    assert got[0].dataset_id=='synthetic-db' and got[0].review_status=='unconfirmed'
    assert '/private' not in got[0].model_dump_json()
    assert path.read_bytes()==before
    assert match_jobs(request(got)).results


@pytest.mark.parametrize('kind',['legacy','wrong_version','missing_sources','corrupt_hash','orphan'])
def test_adapter_rejects_invalid_dataset(tmp_path,kind):
    path=tmp_path/'db';create_db(path)
    with sqlite3.connect(path) as c:
        if kind=='legacy':c.execute('PRAGMA application_id=0')
        elif kind=='wrong_version':c.execute('PRAGMA user_version=2')
        elif kind=='missing_sources':c.execute('DELETE FROM job_provenance')
        elif kind=='corrupt_hash':c.execute("UPDATE imported_jobs SET fact_sha256=?",('f'*64,))
        else:c.execute('UPDATE job_provenance SET job_id=99')
    with pytest.raises(RetrievalError):read_job_candidates(path,dataset_id='test')


def test_adapter_does_not_create_missing_db(tmp_path):
    path=tmp_path/'missing';
    with pytest.raises(FileNotFoundError):read_job_candidates(path,dataset_id='test')
    assert not path.exists()


def test_encoder_projection_drops_recognizable_contacts_and_paths_not_evidence():
    req=request([job(description='必须 Python。联系 dev@example.org https://example.org 13800138000')],profile('课程项目：Python，联系 me@example.org'))
    query,docs=project_request(req,EngineConfig())
    assert 'me@example.org' not in query.text
    assert 'dev@example.org' not in docs[0].text and 'https://' not in docs[0].text and '13800138000' not in docs[0].text
    assert 'dev@example.org' in req.jobs[0].job.description
    assert 'Python' in query.text and 'Python' in docs[0].text


def test_all_excluded_skips_unnecessary_dense_load():
    req=request([job(city='北京')],profile(cities=['上海']))
    result=match_jobs(req,config=EngineConfig(mode='hybrid',hard_constraints=['city']))
    assert result.status=='no_hits' and len(result.excluded)==1
    assert result.channels[1].status=='disabled'


def test_projection_deduplicates_query_text_not_supporting_evidence_paths():
    p=profile();p.skills.append(p.skills[0].model_copy(deep=True))
    req=request([job()],p)
    q,_=project_request(req,EngineConfig())
    assert q.text.count('Python')==1 and len(q.evidence)==3
    assert 'skills[0]' in q.resume_paths and 'skills[2]' in q.resume_paths
