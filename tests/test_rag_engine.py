"""Synthetic integration: no fake embeddings, no real data/model dependence."""
import importlib.util
from pathlib import Path
import sys
import pytest
from pydantic import ValidationError
from job_radar.matching_engine import match_jobs
from job_radar.rag_engine import match_jobs_indexed
from job_radar.rag_index import DerivedIndex
from job_radar.rag_models import IndexedMatchingResult, ChunkConfig, IndexManifest
from job_radar.retrieval_models import EngineConfig, RetrievalError

spec = importlib.util.spec_from_file_location('stage3_synthetic', Path(__file__).resolve().parents[1]/'examples/stage3_synthetic.py')
fixtures = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixtures)
job, request, manifest = fixtures.synthetic_job, fixtures.synthetic_request, fixtures.synthetic_manifest


def test_index_memory_equivalent_and_subset(tmp_path):
    jobs = [job(i) for i in range(1, 9)]
    index = DerivedIndex(tmp_path/'derived')
    index.update(jobs, manifest=manifest(jobs))
    for chosen in [jobs, jobs[::2], list(reversed(jobs)), [], jobs[:1]]:
        req = request(chosen)
        for cfg in [EngineConfig(), EngineConfig(k1=0.8,b=0.4,title_weight=1.0,rrf_k=10)]:
            result = match_jobs_indexed(req,index=index,config=cfg)
            assert result.matching == match_jobs(req,config=cfg)
            assert IndexedMatchingResult.model_validate_json(result.model_dump_json()) == result
            assert not result.chunk_hits


def test_hard_filter_candidate_stats_and_formal_gate(tmp_path):
    from job_radar.matching_models import SearchPreferences
    jobs = [job(1,city='北京'), job(2), job(3)]
    req = request(jobs)
    req.profile.preferences = SearchPreferences(cities=['上海'])
    index = DerivedIndex(tmp_path/'derived'); index.update(jobs,manifest=manifest(jobs))
    cfg = EngineConfig(hard_constraints=['city'])
    actual = match_jobs_indexed(req,index=index,config=cfg).matching
    assert actual == match_jobs(req,config=cfg)
    assert [r.key.job_id for r in actual.excluded] == [1]
    req.__dict__['purpose'] = 'formal'
    with pytest.raises(ValidationError): match_jobs_indexed(req,index=index)


def test_explicit_dense_fallback_and_no_runtime_load(tmp_path,monkeypatch):
    import builtins
    original = builtins.__import__
    def guarded(name,*a,**k):
        assert name not in {'torch','transformers','sentence_transformers','numpy'}
        return original(name,*a,**k)
    monkeypatch.setattr(builtins,'__import__',guarded)
    req = request(); idx = DerivedIndex(tmp_path/'derived')
    idx.update(req.jobs,manifest=manifest(req.jobs))
    assert match_jobs_indexed(req,index=idx).matching.channels[1].status == 'disabled'
    with pytest.raises(RetrievalError,match='dense_index_disabled'):
        match_jobs_indexed(req,index=idx,config=EngineConfig(mode='hybrid'))
    result=match_jobs_indexed(req,index=idx,config=EngineConfig(mode='hybrid',allow_dense_fallback=True)).matching
    assert result.status=='degraded' and result.actual_mode=='bm25_rules'
    assert result.channels[1].reason=='dense_index_disabled'
    assert result.results


def test_stale_metadata_does_not_silently_query(tmp_path):
    req = request(); idx=DerivedIndex(tmp_path/'derived');idx.update(req.jobs,manifest=manifest(req.jobs))
    req.jobs[0].review_status='confirmed'
    with pytest.raises(RetrievalError,match='index_'): match_jobs_indexed(req,index=idx)


@pytest.mark.parametrize('text',['没有正向技能。','课程项目：使用 Redis。'])
def test_no_query_and_no_hits(tmp_path,text):
    req=request([job(4)],text=text);idx=DerivedIndex(tmp_path/'derived');idx.update(req.jobs,manifest=manifest(req.jobs))
    assert match_jobs_indexed(req,index=idx).matching==match_jobs(req)


def test_public_limits_not_relaxed(tmp_path):
    req=request();idx=DerivedIndex(tmp_path/'derived');idx.update(req.jobs,manifest=manifest(req.jobs))
    with pytest.raises(RetrievalError,match='too_many_jobs'):
        match_jobs_indexed(req,index=idx,config=EngineConfig(max_jobs=1))
    with pytest.raises(RetrievalError,match='query_text_too_long'):
        match_jobs_indexed(req,index=idx,config=EngineConfig(max_text_chars=2))


def test_engine_binds_single_generation_on_concurrent_switch(tmp_path,monkeypatch):
    from contextlib import contextmanager
    req=request();index=DerivedIndex(tmp_path/'derived');index.update(req.jobs,manifest=manifest(req.jobs))
    original=index.reader
    @contextmanager
    def switched():
        with original() as view:
            newer=[job(9)]
            index.update(newer,manifest=manifest(newer,generation='g2'))
            yield view
    monkeypatch.setattr(index,'reader',switched)
    result=match_jobs_indexed(req,index=index)
    assert result.matching==match_jobs(req)
    assert result.manifest.generation=='synthetic-g1'
