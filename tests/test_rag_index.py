"""Synthetic structural index acceptance. No model runtimes or fake vectors.

Dense malformed/missing client cases verify error behavior; real vector cache
and cosine quality/resource acceptance is exercised by primary's real-model run.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from job_radar.import_models import ImportedJob
from job_radar.matching_models import JobCandidate
from job_radar.rag_index import DerivedIndex, snapshot_hash
from job_radar.rag_models import ChunkConfig, DenseSpec, EvidenceChunk, IndexManifest, TextMap, canonical_hash
from job_radar.retrieval_bm25 import retrieve_bm25
from job_radar.retrieval_models import EngineConfig, JobKey, JobSpan, RetrievalError, RetrievalQuery
from job_radar.retrieval_text import tokenize


def candidate(number=1, *, description='要求 Python、FastAPI。', title='后端实习', city='上海', review='unconfirmed', dataset='synthetic-index'):
    fact = ImportedJob(source='manual',title=title,url=f'https://example.invalid/{number}',
                       description=description,city=city)
    return JobCandidate(dataset_id=dataset,job_id=number,job=fact,
        jd_sha256=hashlib.sha256(description.encode()).hexdigest(),
        fact_sha256=canonical_hash(fact),review_status=review,
        provenance=[dict(provenance_id=number,source_file_sha256=str(number%10)*64,
            input_format='txt',parser_version='synthetic-v1',
            imported_at=datetime(2026,11,12,tzinfo=timezone.utc),import_run_id='synthetic-run')])


def manifest(jobs, generation='logical-g1', **kwargs):
    return IndexManifest(generation=generation,snapshot_sha256=snapshot_hash(jobs),**kwargs)


def key(number=1, dataset='synthetic-index'):
    return JobKey(dataset_id=dataset,job_id=number)


def chunk(job, ordinal=0):
    encoding = job.job.title+'\n'+job.job.description
    return EvidenceChunk(key=key(job.job_id,job.dataset_id),jd_sha256=job.jd_sha256,ordinal=ordinal,
        span=JobSpan(field='description',start=0,end=len(job.job.description),quote=job.job.description),
        encoding_text=encoding,encoding_sha256=hashlib.sha256(encoding.encode()).hexdigest(),
        token_count=20,mapping=[
            TextMap(field='title',source_start=0,source_end=len(job.job.title),encoded_start=0,encoded_end=len(job.job.title)),
            TextMap(field='description',source_start=0,source_end=len(job.job.description),encoded_start=len(job.job.title)+1,encoded_end=len(encoding))])


def dense_spec():
    return DenseSpec(model_id='BAAI/bge-small-zh-v1.5',revision='a'*40,dimension=512,
        tokenizer_id='BAAI/bge-small-zh-v1.5',tokenizer_revision='a'*40,
        pooling='cls',runtime_sha256='b'*64)


def current_file(index):
    return index.root/json.loads((index.root/'CURRENT').read_text())['file']


def repoint_after_tamper(index):
    pointer=json.loads((index.root/'CURRENT').read_text())
    pointer['sha256']=hashlib.sha256((index.root/pointer['file']).read_bytes()).hexdigest()
    (index.root/'CURRENT').write_text(json.dumps(pointer))


def test_build_restart_roundtrip_order_and_inputs_untouched(tmp_path):
    jobs=[candidate(2),candidate(1)]
    before=[job.model_dump_json() for job in jobs]
    index=DerivedIndex(tmp_path/'derived')
    update=index.update(jobs,manifest=manifest(jobs))
    assert update.added==2 and update.rebuilt and update.encoded_chunks==0
    restarted=DerivedIndex(index.root)
    assert restarted.manifest()==manifest(jobs)
    assert [d.key.job_id for d in restarted.documents([key(2),key(1)])]==[2,1]
    assert restarted.chunks([key(1)])==[]
    assert [job.model_dump_json() for job in jobs]==before
    assert restarted.assert_candidates(jobs) is None


def test_snapshot_order_independent_metadata_sensitive(tmp_path):
    jobs=[candidate(2),candidate(1)]
    assert snapshot_hash(jobs)==snapshot_hash(list(reversed(jobs)))
    assert snapshot_hash([candidate(review='confirmed')])!=snapshot_hash([candidate()])


def test_increment_add_change_delete_noop_and_metadata_only(tmp_path):
    index=DerivedIndex(tmp_path/'derived')
    first=[candidate(1),candidate(2)]
    index.update(first,manifest=manifest(first))
    pointer=(index.root/'CURRENT').read_bytes()
    noop=index.update(list(reversed(first)),manifest=manifest(first,'logical-ignored'))
    assert noop.unchanged==2 and noop.generation=='logical-g1' and not noop.rebuilt
    assert (index.root/'CURRENT').read_bytes()==pointer
    second=[candidate(1,description='Rust only'),candidate(3)]
    delta=index.update(second,manifest=manifest(second,'g2'))
    assert (delta.added,delta.changed,delta.deleted,delta.metadata_only,delta.unchanged)==(1,1,1,0,0)
    assert not delta.rebuilt
    with pytest.raises(RetrievalError,match='index_candidate_missing'):
        index.documents([key(2)])
    third=[candidate(1,description='Rust only',review='confirmed'),candidate(3)]
    delta=index.update(third,manifest=manifest(third,'g3'))
    assert (delta.changed,delta.metadata_only,delta.unchanged,delta.encoded_chunks)==(0,1,1,0)
    index.assert_candidates(third)
    with pytest.raises(RetrievalError,match='index_candidate_stale'):
        index.assert_candidates(second)


def test_metadata_only_keeps_chunks_without_encoding(tmp_path):
    job=candidate(); index=DerivedIndex(tmp_path/'derived')
    index.update([job],manifest=manifest([job]),chunks=[chunk(job)])
    new=candidate(review='confirmed')
    update=index.update([new],manifest=manifest([new],'g2'))
    assert update.metadata_only==1 and update.encoded_chunks==0
    assert index.chunks([key()])==[chunk(job)]


def test_full_rebuild_equals_incremental_documents_stats_and_chunks(tmp_path):
    original=[candidate(),candidate(2)]
    final=[candidate(1,description='Go Python'),candidate(3,description='Redis')]
    inc=DerivedIndex(tmp_path/'incremental'); rebuilt=DerivedIndex(tmp_path/'rebuild')
    inc.update(original,manifest=manifest(original),chunks=[chunk(j) for j in original])
    inc.update(final,manifest=manifest(final,'final'),chunks=[chunk(j) for j in final])
    rebuilt.update(final,manifest=manifest(final,'final'),chunks=[chunk(j) for j in final])
    keys=[key(1),key(3)]
    assert inc.documents(keys)==rebuilt.documents(keys)
    assert inc.chunks(keys)==rebuilt.chunks(keys)
    assert inc.lexical_statistics()==rebuilt.lexical_statistics()


def test_corpus_statistics_updated_but_candidate_scoring_uses_subset(tmp_path):
    jobs=[candidate(1,description='Python Python'),candidate(2,description='Go'),candidate(3,description='Go Redis Python')]
    index=DerivedIndex(tmp_path/'derived'); index.update(jobs,manifest=manifest(jobs))
    stats=index.lexical_statistics()
    assert stats['documents']==3 and stats['df']['python']==2 and stats['df']['go']==2
    subset=index.documents([key(1),key(2)])
    query=RetrievalQuery(text='Python',terms=['python'])
    scores=retrieve_bm25(query,subset,EngineConfig())
    isolated=DerivedIndex(tmp_path/'isolated'); isolated.update(jobs[:2],manifest=manifest(jobs[:2]))
    assert scores==retrieve_bm25(query,isolated.documents([key(1),key(2)]),EngineConfig())
    index.update(jobs[:1],manifest=manifest(jobs[:1],'g2'))
    assert index.lexical_statistics()['df']['python']==1
    assert 'go' not in index.lexical_statistics()['df']


def test_empty_index_and_delete_all(tmp_path):
    index=DerivedIndex(tmp_path/'derived')
    assert index.update([],manifest=manifest([])).rebuilt
    assert index.documents([])==[] and index.chunks([])==[]
    assert index.lexical_statistics()['documents']==0
    one=[candidate()]; index.update(one,manifest=manifest(one,'g2'))
    assert index.update([],manifest=manifest([],'g3')).deleted==1
    with pytest.raises(RetrievalError,match='index_candidate_missing'):
        index.assert_candidates(one)


@pytest.mark.parametrize('failpoint',['during_build','before_switch'])
def test_interruption_keeps_old_generation_and_restart(tmp_path,failpoint):
    index=DerivedIndex(tmp_path/'derived'); old=[candidate()]
    index.update(old,manifest=manifest(old)); pointer=(index.root/'CURRENT').read_bytes()
    new=[candidate(2)]
    with pytest.raises(RetrievalError,match='index_build_interrupted'):
        index.update(new,manifest=manifest(new,'g2'),failpoint=failpoint)
    assert (index.root/'CURRENT').read_bytes()==pointer
    restarted=DerivedIndex(index.root)
    assert restarted.documents([key()])[0].text.endswith(old[0].job.description)
    restarted.update(new,manifest=manifest(new,'g2'))
    assert restarted.documents([key(2)])[0].key.job_id==2
    with pytest.raises(RetrievalError,match='index_candidate_missing'):
        restarted.documents([key()])


@pytest.mark.parametrize('failpoint',['during_build','before_switch'])
def test_first_build_interruption_does_not_publish(tmp_path,failpoint):
    index=DerivedIndex(tmp_path/'derived'); jobs=[candidate()]
    with pytest.raises(RetrievalError,match='index_build_interrupted'):
        index.update(jobs,manifest=manifest(jobs),failpoint=failpoint)
    with pytest.raises(RetrievalError,match='index_not_built'):
        index.manifest()
    index.update(jobs,manifest=manifest(jobs))
    assert index.documents([key()])


def test_incompatible_chunk_config_rebuild_and_expected_gate(tmp_path):
    jobs=[candidate()]; original=manifest(jobs)
    index=DerivedIndex(tmp_path/'derived'); index.update(jobs,manifest=original)
    new=manifest(jobs,'g2',chunk_config=ChunkConfig(max_tokens=256,overlap_tokens=16))
    with pytest.raises(RetrievalError,match='index_incompatible_version'):
        DerivedIndex(index.root,expected=new).manifest()
    changed=index.update(jobs,manifest=new)
    assert changed.rebuilt and changed.unchanged==1
    assert DerivedIndex(index.root,expected=new).manifest()==new
    with pytest.raises(RetrievalError,match='index_incompatible_version'):
        DerivedIndex(index.root,expected=original).documents([key()])


@pytest.mark.parametrize('operation',['manifest','documents','chunks','assert_candidates','lexical_statistics'])
def test_checksum_corruption_detected_all_query_apis(tmp_path,operation):
    jobs=[candidate()]; index=DerivedIndex(tmp_path/'derived'); index.update(jobs,manifest=manifest(jobs))
    with current_file(index).open('ab') as stream: stream.write(b'corrupt')
    args={'documents':([key()],),'chunks':([key()],),'assert_candidates':(jobs,)}.get(operation,())
    with pytest.raises(RetrievalError,match='index_generation_corrupt'):
        getattr(index,operation)(*args)


@pytest.mark.parametrize('pointer',['not json','{}','{"file":"../evidence.sqlite3","sha256":"0","manifest_sha256":"0"}'])
def test_pointer_corruption_rejected(tmp_path,pointer):
    jobs=[candidate()]; index=DerivedIndex(tmp_path/'derived'); index.update(jobs,manifest=manifest(jobs))
    (index.root/'CURRENT').write_text(pointer)
    with pytest.raises(RetrievalError,match='index_'):
        index.manifest()


def test_schema_incompatibility_not_silent(tmp_path):
    jobs=[candidate()]; index=DerivedIndex(tmp_path/'derived'); index.update(jobs,manifest=manifest(jobs))
    with sqlite3.connect(current_file(index)) as db:
        record=json.loads(db.execute("SELECT value FROM info WHERE name='manifest'").fetchone()[0])
        record['schema_version']='future-index-v99'
        db.execute("UPDATE info SET value=? WHERE name='manifest'",(json.dumps(record),))
    repoint_after_tamper(index)
    with pytest.raises(RetrievalError,match='index_schema_incompatible'):
        index.manifest()


def test_manifest_hash_corruption_not_silent(tmp_path):
    jobs=[candidate()]; index=DerivedIndex(tmp_path/'derived'); index.update(jobs,manifest=manifest(jobs))
    with sqlite3.connect(current_file(index)) as db:
        record=json.loads(db.execute("SELECT value FROM info WHERE name='manifest'").fetchone()[0])
        record['generation']='wrong-generation'
        db.execute("UPDATE info SET value=? WHERE name='manifest'",(json.dumps(record),))
    repoint_after_tamper(index)
    with pytest.raises(RetrievalError,match='index_manifest_corrupt'):
        index.manifest()


def test_source_databases_and_unmanaged_directories_untouched(tmp_path):
    source=tmp_path/'evidence.sqlite3'; source.write_bytes(b'synthetic evidence sentinel')
    before=source.read_bytes()
    with pytest.raises(RetrievalError,match='index_independent_directory_required'):
        DerivedIndex(source)
    assert source.read_bytes()==before
    with pytest.raises(RetrievalError,match='index_unmanaged_directory'):
        DerivedIndex(tmp_path)
    assert not (tmp_path/'INDEX_OWNER').exists()


def test_directory_and_generation_symlinks_rejected(tmp_path):
    actual=tmp_path/'actual'; actual.mkdir(); linked=tmp_path/'link'; linked.symlink_to(actual)
    with pytest.raises(RetrievalError,match='index_root_symlink'):
        DerivedIndex(linked)
    jobs=[candidate()]; index=DerivedIndex(actual); index.update(jobs,manifest=manifest(jobs))
    external=tmp_path/'outside'; external.write_text('sentinel')
    (actual/'g-00000000000000000000000000000000.sqlite3').symlink_to(external)
    with pytest.raises(RetrievalError,match='index_unmanaged_directory'):
        index.manifest()
    assert external.read_text()=='sentinel'


def test_relative_root_rejected():
    with pytest.raises(RetrievalError,match='index_absolute_root_required'):
        DerivedIndex('relative')


@pytest.mark.parametrize('operation',['documents','chunks'])
def test_keys_scope_missing_duplicate_and_sql_binding(tmp_path,operation):
    jobs=[candidate(1),candidate(2)]; index=DerivedIndex(tmp_path/'derived'); index.update(jobs,manifest=manifest(jobs))
    result=getattr(index,operation)([key(2)])
    assert operation=='chunks' or len(result)==1 and result[0].key.job_id==2
    with pytest.raises(RetrievalError,match='index_duplicate_keys'):
        getattr(index,operation)([key(),key()])
    with pytest.raises(RetrievalError,match='index_candidate_missing'):
        getattr(index,operation)([key(99)])
    with pytest.raises(RetrievalError,match='index_candidate_missing'):
        getattr(index,operation)([key(1,"' OR 1=1 --")])


def test_unbuilt_read_does_not_create_root(tmp_path):
    root=tmp_path/'not-created'; index=DerivedIndex(root)
    with pytest.raises(RetrievalError,match='index_not_built'): index.documents([])
    assert not root.exists()


@pytest.mark.parametrize('jobs',[None,(),[object()]])
def test_invalid_candidates_fail_before_writes(tmp_path,jobs):
    index=DerivedIndex(tmp_path/'derived')
    with pytest.raises(RetrievalError,match='index_invalid_candidates'):
        index.update(jobs,manifest=manifest([]))
    assert not index.root.exists()


def test_mutated_candidate_extra_fields_and_duplicate_rejected(tmp_path):
    job=candidate(); job.job.__dict__['extra_bad']='x'
    index=DerivedIndex(tmp_path/'derived')
    with pytest.raises(RetrievalError,match='index_invalid_candidates'):
        index.update([job],manifest=manifest([]))
    good=candidate()
    with pytest.raises(RetrievalError,match='index_duplicate_identity'):
        snapshot_hash([good,good])
    assert not index.root.exists()


def test_snapshot_hash_wrong_and_unsupported_version_fail_before_write(tmp_path):
    jobs=[candidate()]; index=DerivedIndex(tmp_path/'derived')
    with pytest.raises(RetrievalError,match='index_snapshot_mismatch'):
        index.update(jobs,manifest=IndexManifest(generation='g',snapshot_sha256='0'*64))
    with pytest.raises(RetrievalError,match='index_incompatible_version'):
        index.update(jobs,manifest=manifest(jobs,lexical_version='future'))
    assert not index.root.exists()


@pytest.mark.parametrize('change,code',[
    (lambda c:setattr(c.span,'quote','x'*len(c.span.quote)),'index_chunk_source_mismatch'),
    (lambda c:setattr(c,'jd_sha256','0'*64),'index_chunk_identity_mismatch'),
    (lambda c:setattr(c.key,'job_id',99),'index_chunk_identity_mismatch'),
    (lambda c:setattr(c,'token_count',513),'index_chunk_budget_mismatch'),
    (lambda c:setattr(c,'ordinal',2),'index_chunk_ordinals_not_contiguous'),
    (lambda c:setattr(c,'mapping',[]),'index_chunk_map_missing'),
    (lambda c:setattr(c.mapping[0],'source_start',1),'index_invalid_chunks'),
])
def test_invalid_chunks_rejected_before_generation(tmp_path,change,code):
    job=candidate(); piece=chunk(job)
    # Model validators may reject at assignment; mutate nested raw field to test boundary revalidation.
    try: change(piece)
    except ValueError: pass
    index=DerivedIndex(tmp_path/'derived')
    with pytest.raises(RetrievalError,match=code):
        index.update([job],manifest=manifest([job]),chunks=[piece])
    assert not (index.root/'CURRENT').exists()


def test_chunk_mapping_and_ordinals_persist_exactly(tmp_path):
    job=candidate(); index=DerivedIndex(tmp_path/'derived'); piece=chunk(job)
    index.update([job],manifest=manifest([job]),chunks=[piece])
    got=index.chunks([key()])[0]
    assert got==piece
    for mapping in got.mapping:
        assert getattr(job.job,mapping.field)[mapping.source_start:mapping.source_end]==got.encoding_text[mapping.encoded_start:mapping.encoded_end]


def test_duplicate_chunks_and_noop_conflict_rejected(tmp_path):
    job=candidate(); piece=chunk(job); index=DerivedIndex(tmp_path/'derived')
    with pytest.raises(RetrievalError,match='index_duplicate_chunk'):
        index.update([job],manifest=manifest([job]),chunks=[piece,piece])
    index.update([job],manifest=manifest([job]),chunks=[piece])
    with pytest.raises(RetrievalError,match='index_chunk_snapshot_conflict'):
        index.update([job],manifest=manifest([job],'g2'),chunks=[])


def test_dense_build_never_fabricates_vectors_or_silently_skips(tmp_path):
    job=candidate(); index=DerivedIndex(tmp_path/'derived'); config=manifest([job],dense=dense_spec())
    with pytest.raises(RetrievalError,match='index_dense_chunks_required'):
        index.update([job],manifest=config)
    assert not (index.root/'CURRENT').exists()
    with pytest.raises(RetrievalError,match='dense_client_missing'):
        index.update([job],manifest=config,chunks=[chunk(job)])
    assert not (index.root/'CURRENT').exists()


def test_dense_query_on_lexical_index_reports_disabled(tmp_path):
    job=candidate(); index=DerivedIndex(tmp_path/'derived'); index.update([job],manifest=manifest([job]))
    with pytest.raises(RetrievalError,match='dense_index_disabled'):
        index.dense_hits(RetrievalQuery(text='Python',terms=['python']),[key()],config=EngineConfig(mode='hybrid'),embedding_client=None)


def test_writer_lock_released_and_does_not_accept_concurrent_writer(tmp_path):
    index=DerivedIndex(tmp_path/'derived'); job=candidate()
    with index._writer():
        with pytest.raises(RetrievalError,match='index_writer_busy'):
            index.update([job],manifest=manifest([job]))
    assert index.update([job],manifest=manifest([job])).added==1


def test_held_reader_keeps_immutable_old_generation_across_switch(tmp_path):
    index=DerivedIndex(tmp_path/'derived'); one=[candidate()]; two=[candidate(2)]
    index.update(one,manifest=manifest(one))
    with index._read() as (reader,old):
        index.update(two,manifest=manifest(two,'g2'))
        assert reader.execute('SELECT job_id FROM jobs').fetchall()==[(1,)]
        assert old.generation=='logical-g1'
    assert index.documents([key(2)])[0].key.job_id==2


def test_sqlite_vectors_table_uses_blob_schema_no_python_matrix(tmp_path):
    index=DerivedIndex(tmp_path/'derived'); jobs=[candidate()]; index.update(jobs,manifest=manifest(jobs))
    with index._read() as (db,_):
        columns={row[1]:row[2] for row in db.execute('PRAGMA table_info(vectors)')}
        assert columns['vector']=='BLOB'
        assert db.execute('SELECT COUNT(*) FROM vectors').fetchone()==(0,)


def test_system_temporary_directory_parent_alias_is_canonicalized():
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as parent:
        index=DerivedIndex(Path(parent)/'derived')
        jobs=[candidate()]
        index.update(jobs,manifest=manifest(jobs))
        assert index.documents([key()])
        assert index.root==index.root.resolve()


def test_multi_dataset_identity_and_stable_request_order(tmp_path):
    jobs=[candidate(1,dataset='dataset-A'),candidate(1,dataset='dataset-B')]
    index=DerivedIndex(tmp_path/'derived'); index.update(jobs,manifest=manifest(jobs))
    assert [d.key.dataset_id for d in index.documents([key(1,'dataset-B'),key(1,'dataset-A')])]==['dataset-B','dataset-A']
    index.assert_candidates(jobs[:1])


def test_deleted_active_generation_clears_associations_and_queries(tmp_path):
    jobs=[candidate(1),candidate(2)]; index=DerivedIndex(tmp_path/'derived')
    index.update(jobs,manifest=manifest(jobs),chunks=[chunk(j) for j in jobs])
    index.update(jobs[1:],manifest=manifest(jobs[1:],'g2'),chunks=[chunk(jobs[1])])
    with index._read() as (db,_):
        assert db.execute('SELECT job_id FROM jobs').fetchall()==[(2,)]
        assert db.execute('SELECT job_id FROM chunks').fetchall()==[(2,)]
        assert not list(db.execute('PRAGMA foreign_key_check'))
    with pytest.raises(RetrievalError,match='index_candidate_missing'):
        index.chunks([key(1)])


def test_metadata_provenance_only_updates_snapshot_not_document(tmp_path):
    job=candidate(); index=DerivedIndex(tmp_path/'derived'); index.update([job],manifest=manifest([job]),chunks=[chunk(job)])
    before=index.documents([key()])
    copied=JobCandidate.model_validate_json(job.model_dump_json())
    copied.provenance[0].import_run_id='synthetic-second-run'
    result=index.update([copied],manifest=manifest([copied],'g2'))
    assert result.metadata_only==1 and result.changed==0 and result.encoded_chunks==0
    assert index.documents([key()])==before
    index.assert_candidates([copied])
    with pytest.raises(RetrievalError,match='index_candidate_stale'):
        index.assert_candidates([job])


def test_metadata_review_change_is_not_content_hash_change(tmp_path):
    old=candidate(); updated=candidate(review='rejected')
    assert old.fact_sha256==updated.fact_sha256 and old.jd_sha256==updated.jd_sha256
    index=DerivedIndex(tmp_path/'derived'); index.update([old],manifest=manifest([old]))
    result=index.update([updated],manifest=manifest([updated],'g2'))
    assert result.metadata_only==1 and result.encoded_chunks==0
    index.assert_candidates([updated])


def test_unrecognized_index_files_are_not_removed(tmp_path):
    root=tmp_path/'derived'; root.mkdir(); unrelated=root/'evidence.db'; unrelated.write_bytes(b'sentinel')
    with pytest.raises(RetrievalError,match='index_unmanaged_directory'):
        DerivedIndex(root)
    assert unrelated.read_bytes()==b'sentinel'
    assert sorted(p.name for p in root.iterdir())==['evidence.db']


def test_index_owner_marker_mismatch(tmp_path):
    root=tmp_path/'derived'; root.mkdir(); (root/'INDEX_OWNER').write_text('wrong owner')
    with pytest.raises(RetrievalError,match='index_owner_mismatch'):
        DerivedIndex(root)


def test_missing_generation_is_not_silently_rebuilt_on_update(tmp_path):
    jobs=[candidate()]; index=DerivedIndex(tmp_path/'derived'); index.update(jobs,manifest=manifest(jobs))
    current_file(index).unlink()
    with pytest.raises(RetrievalError,match='index_generation_corrupt'):
        index.update(jobs,manifest=manifest(jobs,'g2'))


def test_large_document_explicit_build_limit_rolls_back(tmp_path):
    small=[candidate()]; index=DerivedIndex(tmp_path/'derived'); index.update(small,manifest=manifest(small))
    big=[candidate(description='x'*128001)]
    pointer=(index.root/'CURRENT').read_bytes()
    with pytest.raises(RetrievalError,match='index_job_text_too_long'):
        index.update(big,manifest=manifest(big,'g2'))
    assert (index.root/'CURRENT').read_bytes()==pointer
    index.assert_candidates(small)


def test_unmapped_nonwhitespace_encoder_text_is_rejected(tmp_path):
    job=candidate(); piece=chunk(job)
    # Hash is internally valid, but appended words have no source mapping.
    data=piece.model_dump()
    data['encoding_text']+=' invented extra requirement'
    data['encoding_sha256']=hashlib.sha256(data['encoding_text'].encode()).hexdigest()
    modified=EvidenceChunk.model_validate(data)
    index=DerivedIndex(tmp_path/'derived')
    with pytest.raises(RetrievalError,match='index_chunk_map_incomplete'):
        index.update([job],manifest=manifest([job]),chunks=[modified])
    assert not index.root.exists()


def test_b_chunker_output_persists_contacts_offsets_overlap_and_tail(tmp_path):
    from job_radar.rag_chunks import chunk_job
    from job_radar.retrieval_text import retrieval_text
    class SyntheticCharacterBudget:
        tokenizer_id='synthetic-character-offsets-not-real-model'
        revision='synthetic-v1'
        max_tokens=64
        def count(self,text,*,kind):return len(text)+2
        def offsets(self,text):return [(i,i+1) for i in range(len(text)) if not text[i].isspace()]
    job=candidate(description='请联系 test@example.invalid。'+('要求 Python 中文 API。'*8)+'\n最后必须会 Redis。')
    config=ChunkConfig(max_tokens=64,overlap_tokens=5)
    pieces=chunk_job(job,budget=SyntheticCharacterBudget(),config=config)
    index=DerivedIndex(tmp_path/'derived')
    index.update([job],manifest=manifest([job],chunk_config=config),chunks=pieces)
    restored=index.chunks([key()])
    assert restored==pieces and len(restored)>1
    assert restored[-1].span.quote.endswith('最后必须会 Redis。')
    assert all('test@example.invalid' not in piece.encoding_text for piece in restored)


def test_managed_root_replaced_with_symlink_is_rejected(tmp_path):
    index=DerivedIndex(tmp_path/'derived')
    target=tmp_path/'target'; target.mkdir()
    index.root.symlink_to(target)
    with pytest.raises(RetrievalError,match='index_root_symlink'):
        index.manifest()
    assert list(target.iterdir())==[]


def test_metadata_and_unaffected_jobs_reuse_projection(tmp_path,monkeypatch):
    import job_radar.rag_index as module
    first=[candidate(),candidate(2)]
    index=DerivedIndex(tmp_path/'derived'); index.update(first,manifest=manifest(first))
    real=module._document; projected=[]
    def tracked(job):
        projected.append(job.job_id)
        return real(job)
    monkeypatch.setattr(module,'_document',tracked)
    metadata=[candidate(review='confirmed'),candidate(2)]
    index.update(metadata,manifest=manifest(metadata,'metadata'))
    assert projected==[]
    changed=[candidate(review='confirmed'),candidate(2,description='new Rust contents')]
    index.update(changed,manifest=manifest(changed,'content'))
    assert projected==[2]


def test_pinned_reader_stays_on_one_generation_during_switch(tmp_path):
    index=DerivedIndex(tmp_path/'derived');old=[candidate(1)]
    index.update(old,manifest=manifest(old,'g1'))
    with index.reader() as view:
        before=view.manifest()
        newer=[candidate(2)]
        index.update(newer,manifest=manifest(newer,'g2'))
        assert view.manifest()==before
        view.assert_candidates(old)
        assert view.documents([key(1)])[0].key==key(1)
        with pytest.raises(RetrievalError,match='index_reader_read_only'):view.update(old,manifest=manifest(old))
    with pytest.raises(RetrievalError,match='index_reader_closed'):view.manifest()
    index.assert_candidates(newer)


def test_incremental_projection_recomputes_only_changed_or_added(tmp_path,monkeypatch):
    import job_radar.rag_index as module
    index=DerivedIndex(tmp_path/'derived');jobs=[candidate(1),candidate(2)]
    index.update(jobs,manifest=manifest(jobs))
    original=module._document;calls=[]
    def measured(job):calls.append(job.job_id);return original(job)
    monkeypatch.setattr(module,'_document',measured)
    revised=[candidate(1,review='confirmed'),candidate(2),candidate(3)]
    index.update(revised,manifest=manifest(revised,'g2'))
    assert calls==[3]
    calls.clear();revised[1]=candidate(2,description='Rust only')
    index.update(revised,manifest=manifest(revised,'g3'))
    assert calls==[2]


def test_dense_client_metadata_mismatch_is_explicit_channel_error():
    from job_radar.rag_index import _client_metadata
    class MetadataOnly:
        model_id='other';revision='b'*40;dimension=512
        def encode(self,*args,**kwargs):raise AssertionError('must not encode')
    with pytest.raises(RetrievalError,match='^dense_index_metadata_mismatch$'):
        _client_metadata(MetadataOnly(),dense_spec())
