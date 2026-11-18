"""Hand-calculated and synthetic Stage 2 lexical tests; no network or DB."""
import math
from pathlib import Path
import socket
import sqlite3

from pydantic import ValidationError
import pytest

from job_radar.retrieval_bm25 import retrieve_bm25
from job_radar.retrieval_models import (
    EngineConfig, JobKey, RetrievalDocument, RetrievalError, RetrievalQuery,
)
from job_radar.retrieval_text import tokenize


def query(text='Python', *, terms=None):
    return RetrievalQuery(text=text, terms=tokenize(text) if terms is None else terms)


def doc(job_id=1, *, title='岗位', body='Python', dataset='synthetic',
        terms=None, title_terms=None):
    return RetrievalDocument(key=JobKey(dataset_id=dataset, job_id=job_id), title=title,
        text=title+'\n'+body, terms=tokenize(body) if terms is None else terms,
        title_terms=tokenize(title) if title_terms is None else title_terms,
        fact_sha256='a'*64)


def config(**updates):
    return EngineConfig.model_validate({**EngineConfig().model_dump(), **updates})


def test_single_document_positive_idf_hand_calculation():
    result=retrieve_bm25(query(), [doc(title_terms=[])], config())
    assert len(result)==1
    assert result[0].score==pytest.approx(math.log(4/3))
    assert result[0].channel=='bm25' and result[0].rank==1
    assert result[0].matched_terms==['python']


def test_two_documents_tf_length_and_df_hand_calculation():
    docs=[doc(1,title_terms=[],terms=['python','python','sql']),
          doc(2,title_terms=[],terms=['sql'])]
    hit=retrieve_bm25(query(),docs,config())[0]
    # N=2, df=1, dl=3, avgdl=2, tf=2, k1=1.5, b=.75.
    expected=math.log(2) * (2*2.5)/(2 + 1.5*(.25+.75*3/2))
    assert hit.score==pytest.approx(expected)


def test_weighted_title_is_part_of_tf_and_document_length():
    docs=[doc(1,title='Python',body='sql',title_terms=['python'],terms=['sql']),
          doc(2,title='Other',body='Python',title_terms=[],terms=['python'])]
    hits=retrieve_bm25(query(),docs,config(title_weight=2.0))
    # Weighted lengths 3 and 1, avgdl=2, df=2.
    expected=math.log(1.2)*2*2.5/(2+1.5*(.25+.75*3/2))
    title_hit=next(hit for hit in hits if hit.key.job_id==1)
    assert title_hit.score==pytest.approx(expected)
    # Title TF is higher but does not promise rank 1: length normalization
    # still favors the shorter body-only hit in this particular corpus.
    assert [hit.key.job_id for hit in hits]==[2,1]


def test_title_weight_zero_disables_title_hits():
    docs=[doc(1,title='Python',body='Go',title_terms=['python']),
          doc(2,title='Go',body='Python',title_terms=[])]
    hits=retrieve_bm25(query(),docs,config(title_weight=0.0))
    assert [x.key.job_id for x in hits]==[2]


def test_title_weight_changes_relevance_not_query_terms():
    docs=[doc(1,title='Python',body='Other'),doc(2,title='Other',body='Python')]
    low=retrieve_bm25(query(),docs,config(title_weight=0.0))
    high=retrieve_bm25(query(),docs,config(title_weight=3.0))
    assert low[0].key.job_id==2 and high[0].key.job_id==1


def test_query_repetition_contributes_once():
    docs=[doc()]
    once=retrieve_bm25(query(),docs,config())
    repeated=retrieve_bm25(query('Python Python',terms=['python','python']),docs,config())
    assert once==repeated


def test_query_order_is_retained_for_matched_terms():
    hits=retrieve_bm25(query('SQL Python SQL'),[doc(body='Python SQL')],config())
    assert hits[0].matched_terms==['sql','python']


def test_common_terms_still_have_positive_idf():
    hits=retrieve_bm25(query(),[doc(1),doc(2),doc(3)],config())
    assert len(hits)==3 and all(x.score>0 and math.isfinite(x.score) for x in hits)


def test_rare_term_has_larger_idf():
    docs=[doc(1,body='Python SQL'),doc(2,body='SQL Go')]
    rare=retrieve_bm25(query(),docs,config())[0].score
    common=retrieve_bm25(query('SQL'),docs,config())[0].score
    assert rare>common


def test_length_normalization_favors_concise_equal_tf():
    docs=[doc(1,title_terms=[],body='Python '+ 'other '*30),
          doc(2,title_terms=[],body='Python')]
    assert retrieve_bm25(query(),docs,config())[0].key.job_id==2


def test_b_zero_ignores_document_length():
    docs=[doc(2,title_terms=[],body='Python '+ 'other '*30),
          doc(1,title_terms=[],body='Python')]
    hits=retrieve_bm25(query(),docs,config(b=0.0))
    assert hits[0].score==hits[1].score
    assert [x.key.job_id for x in hits]==[1,2]


def test_tf_saturation_is_sublinear():
    cfg=config(b=0.0)
    docs=[doc(1,title_terms=[],terms=['python']),doc(2,title_terms=[],terms=['python']*4)]
    hits=retrieve_bm25(query(),docs,cfg)
    assert hits[0].score>hits[1].score
    assert hits[0].score/hits[1].score<4


@pytest.mark.parametrize('text,expected',[
    ('Python3', ['python']), ('Python 3',['python']), ('py.test',['pytest']),
    ('Tool-Calling',['tool calling']), ('C++',['c++']), ('C#',['c#']),
    ('FastAPI',['fastapi']), ('检索引擎',['检索','索引','引擎']),
])
def test_shared_tokenizer_technical_and_chinese_terms(text,expected):
    assert tokenize(text)==expected
    hits=retrieve_bm25(query(text),[doc(body=text)],config())
    assert hits and hits[0].matched_terms==expected


def test_aliases_match_between_query_and_document():
    hits=retrieve_bm25(query('Python 3 py.test Tool-Calling'),
        [doc(body='Python3 pytest Tool Calling')],config())
    assert hits[0].matched_terms==['python','pytest','tool calling']


def test_exact_token_not_substring():
    assert retrieve_bm25(query('Python'),[doc(body='Pythonista')],config())==[]


def test_chinese_bigram_retrieval_with_english_skill():
    hits=retrieve_bm25(query('接口测试 Python'),
        [doc(1,body='Python 自动化接口测试'),doc(2,body='销售岗位')],config())
    assert [h.key.job_id for h in hits]==[1]
    assert '接口' in hits[0].matched_terms and 'python' in hits[0].matched_terms


@pytest.mark.parametrize('q',[RetrievalQuery.empty(),query('Python',terms=[]),query('',terms=['python']),query(' ',terms=['python'])])
def test_empty_query_returns_no_hits(q):
    assert retrieve_bm25(q,[doc()],config())==[]


def test_empty_corpus_and_empty_lexical_documents():
    assert retrieve_bm25(query(),[],config())==[]
    assert retrieve_bm25(query(),[doc(terms=[],title_terms=[])],config())==[]


def test_no_hits_does_not_pad_results_to_recall_k():
    assert retrieve_bm25(query('Rust'),[doc()],config())==[]
    hits=retrieve_bm25(query(),[doc(1),doc(2,body='Rust')],config())
    assert [h.key.job_id for h in hits]==[1]


def test_empty_lexical_doc_is_in_corpus_average_without_zero_division():
    hits=retrieve_bm25(query(),[doc(1,title_terms=[]),doc(2,title_terms=[],terms=[])],config())
    assert len(hits)==1 and math.isfinite(hits[0].score)


def test_deterministic_ties_ignore_input_order():
    docs=[doc(4,dataset='b'),doc(2,dataset='a'),doc(1,dataset='a')]
    left=retrieve_bm25(query(),docs,config())
    right=retrieve_bm25(query(),list(reversed(docs)),config())
    assert left==right
    assert [(x.key.dataset_id,x.key.job_id) for x in left]==[('a',1),('a',2),('b',4)]


def test_recall_k_not_top_k_controls_channel_size():
    hits=retrieve_bm25(query(),[doc(i) for i in range(1,5)],config(top_k=1,recall_k=2))
    assert len(hits)==2 and [h.rank for h in hits]==[1,2]


def test_same_job_id_in_different_datasets_is_allowed():
    hits=retrieve_bm25(query(),[doc(dataset='a'),doc(dataset='b')],config())
    assert len(hits)==2


def test_duplicate_identity_is_rejected_even_for_empty_query():
    with pytest.raises(RetrievalError,match='^duplicate_document_identity$'):
        retrieve_bm25(RetrievalQuery.empty(),[doc(),doc()],config())


@pytest.mark.parametrize('mutate',[
    lambda q,docs,cfg: q.model_copy(update={'version':'v2'}),
    lambda q,docs,cfg: q.model_copy(update={'terms':[2]}),
    lambda q,docs,cfg: q.model_copy(update={'extra':'invented'}),
])
def test_query_model_copy_bypass_revalidated(mutate):
    q=query();docs=[doc()];cfg=config()
    with pytest.raises(ValidationError):retrieve_bm25(mutate(q,docs,cfg),docs,cfg)


@pytest.mark.parametrize('updates',[
    {'version':'v2'},{'fact_sha256':'bad'},{'key':{'dataset_id':'d','job_id':0}},
    {'terms':[3]},{'title_terms':[True]},{'unexpected':'x'},
])
def test_document_bypass_revalidated(updates):
    with pytest.raises(ValidationError):retrieve_bm25(query(),[doc().model_copy(update=updates)],config())


@pytest.mark.parametrize('updates',[
    {'k1':0.0},{'k1':float('nan')},{'b':-0.1},{'b':float('inf')},
    {'title_weight':-1.0},{'title_weight':float('inf')},{'recall_k':True},
    {'top_k':51},{'extra':1},
])
def test_config_bypass_revalidated(updates):
    with pytest.raises(ValidationError):retrieve_bm25(query(),[doc()],config().model_copy(update=updates))


@pytest.mark.parametrize('side',['query','body','title'])
@pytest.mark.parametrize('term',['','  '])
def test_blank_terms_rejected(side,term):
    q=query();d=doc()
    if side=='query': q.terms.append(term)
    elif side=='body':d.terms.append(term)
    else:d.title_terms.append(term)
    with pytest.raises(RetrievalError,match='^invalid_lexical_term$'):
        retrieve_bm25(q,[d],config())


@pytest.mark.parametrize('side',['query','doc'])
def test_text_resource_limit_rejected_not_truncated(side):
    cfg=config(max_text_chars=3)
    q=query('Py',terms=['python']);d=doc(title='P',body='P')
    if side=='query':q=query('Python')
    else:d=doc()
    with pytest.raises(RetrievalError,match='^'+('query_text_too_long' if side=='query' else 'job_text_too_long')+'$'):
        retrieve_bm25(q,[d],cfg)


def test_max_jobs_rejected():
    with pytest.raises(RetrievalError,match='^too_many_jobs$'):
        retrieve_bm25(query(),[doc(1),doc(2)],config(max_jobs=1))


@pytest.mark.parametrize('q,docs,cfg',[
    ({},[],EngineConfig()),(RetrievalQuery.empty(),(),EngineConfig()),
    (RetrievalQuery.empty(),[{}],EngineConfig()),(RetrievalQuery.empty(),[],{}),
])
def test_public_types_are_explicit(q,docs,cfg):
    with pytest.raises(TypeError):retrieve_bm25(q,docs,cfg)


def test_inputs_are_unchanged_and_results_detached():
    q=query('SQL Python');docs=[doc(body='Python SQL')];cfg=config()
    before=[q.model_dump_json(),docs[0].model_dump_json(),cfg.model_dump_json()]
    hits=retrieve_bm25(q,docs,cfg)
    assert [q.model_dump_json(),docs[0].model_dump_json(),cfg.model_dump_json()]==before
    hits[0].key.dataset_id='changed';hits[0].matched_terms.append('invented')
    assert docs[0].key.dataset_id=='synthetic' and q.terms==['sql','python']


def test_scoring_does_not_call_file_network_database(monkeypatch):
    q=query();docs=[doc()];cfg=config()
    def blocked(*args,**kwargs):raise AssertionError('I/O called')
    monkeypatch.setattr('builtins.open',blocked)
    monkeypatch.setattr(Path,'read_bytes',blocked)
    monkeypatch.setattr(Path,'read_text',blocked)
    monkeypatch.setattr(socket,'socket',blocked)
    monkeypatch.setattr(sqlite3,'connect',blocked)
    assert len(retrieve_bm25(q,docs,cfg))==1


def test_scores_remain_finite_and_ranked_in_varied_corpus():
    docs=[doc(i,title='Python' if i%3 else 'SQL',body='Python '*i+'SQL '*3)
          for i in range(1,41)]
    hits=retrieve_bm25(query('Python SQL'),docs,config())
    assert len(hits)==40
    assert all(math.isfinite(h.score) and h.score>0 for h in hits)
    assert [h.score for h in hits]==sorted([h.score for h in hits],reverse=True)
