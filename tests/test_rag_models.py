"""Shared Stage 3 contracts reject malformed and mutated values."""
import hashlib
import pytest
from pydantic import ValidationError
from job_radar.rag_models import *
from job_radar.retrieval_models import JobKey,JobSpan


def test_hash_canonical_order():
    assert canonical_hash({'a':1,'b':2})==canonical_hash({'b':2,'a':1})


@pytest.mark.parametrize('kwargs',[{'max_tokens':0},{'overlap_tokens':512},{'max_chunks_per_job':0},{'max_tokens':True},{'version':'v2'},{'typo':1}])
def test_chunk_config_strict(kwargs):
    with pytest.raises(ValidationError):ChunkConfig(**kwargs)


def test_manifest_changes_only_relevant_compatibility():
    m=IndexManifest(generation='g1',snapshot_sha256='a'*64)
    assert m.compatibility_hash()==m.model_copy(update={'generation':'g2','snapshot_sha256':'b'*64}).compatibility_hash()
    assert m.compatibility_hash()!=m.model_copy(update={'chunk_config':ChunkConfig(max_tokens=128,overlap_tokens=16)}).compatibility_hash()


@pytest.mark.parametrize('update',[{'grade':1,'status':'unjudged'},{'grade':None,'status':'synthetic_expected'},{'grade':3},{'grade':True},{'status':'human_judged'}])
def test_judgment_invalid(update):
    args=dict(query_id='q',key=JobKey(dataset_id='s',job_id=1),job_group='g',grade=1,status='synthetic_expected');args.update(update)
    with pytest.raises(ValidationError):EvalJudgment(**args)


def test_textmap_offset_lengths():
    with pytest.raises(ValidationError):TextMap(field='description',source_start=0,source_end=5,encoded_start=0,encoded_end=4)


def test_chunk_hash_rejects():
    with pytest.raises(ValidationError):EvidenceChunk(key=JobKey(dataset_id='s',job_id=1),jd_sha256='a'*64,ordinal=0,
        span=JobSpan(field='description',start=0,end=1,quote='a'),encoding_text='a',encoding_sha256='0'*64,token_count=1)
