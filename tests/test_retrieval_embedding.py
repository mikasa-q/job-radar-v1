"""Dense retrieval tests use only synthetic vectors and stub local runtimes."""
from copy import deepcopy
import math
from pathlib import Path
import subprocess
import sys

from pydantic import ValidationError
import pytest

from job_radar.retrieval_embedding import (
    CachedEmbeddingClient, DENSE_ERROR_CODES, LocalSnapshotEmbeddingClient,
    ScriptedEmbeddingClient, retrieve_dense,
)
from job_radar.retrieval_models import (
    EngineConfig, JobKey, RetrievalDocument, RetrievalError, RetrievalQuery,
)


def query(text='Python API project'):
    return RetrievalQuery(text=text, terms=['python', 'api', 'project'])


def documents(count=3):
    return [RetrievalDocument(key=JobKey(dataset_id='synthetic', job_id=i),
        title=f'岗位{i}', text=f'合成岗位{i}要求Python。', title_terms=[],
        terms=['python'], fact_sha256=f'{i:064x}') for i in range(1, count + 1)]


def config(**updates):
    return EngineConfig.model_validate({'mode': 'hybrid', **updates})


def fake(q=None, rows=None, **metadata):
    return ScriptedEmbeddingClient([[[1.0, 0.0]] if q is None else q,
        [[0.0, 1.0], [1.0, 1.0], [1.0, 0.0]] if rows is None else rows], **metadata)


def code(expected, call):
    with pytest.raises(RetrievalError) as caught:
        call()
    assert caught.value.code == expected
    assert str(caught.value) == expected
    assert expected in DENSE_ERROR_CODES or expected in {'duplicate_job_identity', 'too_many_jobs'}


def test_cosine_hand_calculation_and_role_separation():
    client = fake()
    docs = documents()
    before = [doc.model_dump_json() for doc in docs]
    hits = retrieve_dense(query(), docs, config(), client=client)
    assert [hit.key.job_id for hit in hits] == [3, 2, 1]
    assert [hit.rank for hit in hits] == [1, 2, 3]
    assert [hit.score for hit in hits] == pytest.approx([1.0, 1 / math.sqrt(2), 0.0])
    assert all(hit.channel == 'dense' and hit.matched_terms == [] for hit in hits)
    assert client.requests == [('query', [query().text]), ('passage', [doc.text for doc in docs])]
    assert [doc.model_dump_json() for doc in docs] == before


def test_negative_cosine_is_not_capability_or_threshold_assertion():
    hits = retrieve_dense(query(), documents(2), config(), client=fake(rows=[[-1.0, 0.0], [0.0, 1.0]]))
    assert [hit.score for hit in hits] == [0.0, -1.0]
    assert [hit.key.job_id for hit in hits] == [2, 1]


def test_ties_stable_across_input_order_and_dataset_ids():
    docs = documents(3)[::-1]
    docs[0].key.dataset_id = 'z'
    hits = retrieve_dense(query(), docs, config(), client=fake(rows=[[1.0, 0.0]] * 3))
    assert [(h.key.dataset_id, h.key.job_id) for h in hits] == [('synthetic', 1), ('synthetic', 2), ('z', 3)]


def test_large_and_tiny_finite_values_do_not_overflow_cosine():
    for magnitude in [1e308, 1e-308]:
        hits = retrieve_dense(query(), documents(1), config(),
            client=fake(q=[[magnitude, magnitude]], rows=[[magnitude, magnitude]]))
        assert math.isfinite(hits[0].score) and hits[0].score == pytest.approx(1.0)


def test_recall_limit_and_detached_hit_identity():
    docs = documents()
    hits = retrieve_dense(query(), docs, config(top_k=1, recall_k=2), client=fake())
    assert len(hits) == 2
    hits[0].key.job_id = 50
    assert docs[-1].key.job_id == 3


@pytest.mark.parametrize('empty', ['query', 'documents', 'both'])
def test_empty_inputs_do_not_access_client(empty):
    q = RetrievalQuery.empty() if empty != 'documents' else query()
    docs = [] if empty != 'query' else documents()
    assert retrieve_dense(q, docs, config(), client=None) == []


def test_whitespace_query_no_call():
    assert retrieve_dense(query('   '), documents(), config(), client=None) == []


@pytest.mark.parametrize('client', [None, object()])
def test_missing_or_invalid_client(client):
    code('dense_client_missing' if client is None else 'dense_client_metadata',
         lambda: retrieve_dense(query(), documents(), config(), client=client))


@pytest.mark.parametrize('metadata', [
    {'model_id': ''}, {'model_id': ' '}, {'revision': ''}, {'revision': None},
    {'dimension': 0}, {'dimension': True}, {'dimension': '2'}, {'dimension': 65537},
])
def test_explicit_metadata_required(metadata):
    code('dense_client_metadata', lambda: fake(**metadata))


@pytest.mark.parametrize('response,expected', [
    ([], 'dense_response_shape'),
    ([[1.0, 0.0], [0.0, 1.0]], 'dense_response_shape'),
    (([1.0, 0.0],), 'dense_response_shape'),
    ([[]], 'dense_vector_dimension'),
    ([[1.0]], 'dense_vector_dimension'),
    ([[1.0, 0.0, 0.0]], 'dense_vector_dimension'),
    ([('x', 0.0)], 'dense_vector_dimension'),
    ([[0.0, 0.0]], 'dense_zero_vector'),
    ([[float('nan'), 1.0]], 'dense_vector_nonfinite'),
    ([[float('inf'), 1.0]], 'dense_vector_nonfinite'),
    ([[True, 1.0]], 'dense_vector_nonfinite'),
    ([['1.0', 0.0]], 'dense_vector_nonfinite'),
    ([[10**500, 0.0]], 'dense_vector_nonfinite'),
])
def test_query_response_validated_before_document_call(response, expected):
    client = ScriptedEmbeddingClient([response])
    code(expected, lambda: retrieve_dense(query(), documents(), config(), client=client))
    assert len(client.requests) == 1


@pytest.mark.parametrize('response,expected', [
    ([[1.0, 0.0]], 'dense_response_shape'),
    ([[1.0, 0.0], [0.0, 0.0], [0.0, 1.0]], 'dense_zero_vector'),
    ([[1.0, 0.0], [1.0], [0.0, 1.0]], 'dense_vector_dimension'),
    ([[1.0, 0.0], [float('-inf'), 1.0], [0.0, 1.0]], 'dense_vector_nonfinite'),
])
def test_entire_document_batch_validated(response, expected):
    code(expected, lambda: retrieve_dense(query(), documents(), config(), client=fake(rows=response)))


@pytest.mark.parametrize('error,expected', [
    (TimeoutError('private raw text'), 'dense_encode_timeout'),
    (MemoryError('private raw text'), 'dense_resource_error'),
    (ValueError('private raw text'), 'dense_client_error'),
    (RetrievalError('private arbitrary code'), 'dense_client_error'),
    (RetrievalError('dense_runtime_unavailable'), 'dense_runtime_unavailable'),
])
def test_client_exception_sanitized(error, expected):
    client = ScriptedEmbeddingClient([error])
    code(expected, lambda: retrieve_dense(query(), documents(), config(), client=client))


def test_client_metadata_mutation_during_encode_rejected():
    client = fake()
    original = client.encode
    def changed(texts, *, kind):
        result = original(texts, kind=kind)
        client.revision = 'changed'
        return result
    client.encode = changed
    code('dense_client_changed', lambda: retrieve_dense(query(), documents(), config(), client=client))


def test_duplicate_job_identity_and_limits_checked_before_client():
    docs = documents()
    code('duplicate_job_identity', lambda: retrieve_dense(query(), [docs[0], docs[0]], config(), client=None))
    code('too_many_jobs', lambda: retrieve_dense(query(), docs, config(max_jobs=1), client=None))
    code('dense_input_too_long', lambda: retrieve_dense(query(), docs, config(max_text_chars=1), client=None))


def test_mutated_model_revalidation_and_strict_input_container():
    docs = documents()
    docs[0] = docs[0].model_copy(update={'fact_sha256': 'bad'})
    with pytest.raises(ValidationError):
        retrieve_dense(query(), docs, config(), client=None)
    with pytest.raises(TypeError, match='expected_retrieval_documents'):
        retrieve_dense(query(), tuple(documents()), config(), client=None)


def test_scripted_client_is_copied_and_explicit_fake():
    batches = [[[1.0, 0.0]]]
    client = ScriptedEmbeddingClient(batches)
    batches[0][0][0] = 99.0
    got = client.encode(['text'], kind='query')
    assert got == [[1.0, 0.0]] and client.model_id == 'scripted-fake'
    got[0][0] = 50.0
    code('dense_client_error', lambda: client.encode(['text'], kind='query'))


def test_cache_deduplicates_text_and_copies_results():
    client = ScriptedEmbeddingClient([[[1.0, 0.0], [0.0, 1.0]]])
    cache = CachedEmbeddingClient(client)
    got = cache.encode(['a', 'b', 'a'], kind='query')
    assert client.requests == [('query', ['a', 'b'])]
    got[0][0] = 99.0
    assert cache.encode(['a'], kind='query') == [[1.0, 0.0]]
    assert len(client.requests) == 1


def test_cache_key_separates_kind_model_revision_dimension_projection_and_exact_text():
    client = ScriptedEmbeddingClient([[[1.0, 0.0]]] * 7)
    cache = CachedEmbeddingClient(client)
    cache.encode(['text'], kind='query')
    cache.encode(['text'], kind='passage')
    client.revision = 'v2'; cache.encode(['text'], kind='query')
    client.model_id = 'other'; cache.encode(['text'], kind='query')
    cache.projection_version = 'retrieval-text-v2'; cache.encode(['text'], kind='query')
    cache.encode(['text '], kind='query')
    client.dimension = 1
    client._responses[6] = [[1.0]]
    cache.encode(['text'], kind='query')
    assert len(client.requests) == 7


def test_invalid_batch_does_not_write_partial_cache():
    client = ScriptedEmbeddingClient([[[1.0, 0.0], [0.0, 0.0]], [[0.0, 1.0]]])
    cache = CachedEmbeddingClient(client)
    code('dense_zero_vector', lambda: cache.encode(['a', 'b'], kind='query'))
    assert cache.encode(['a'], kind='query') == [[0.0, 1.0]]
    assert len(client.requests) == 2


def test_cache_bound_and_clear():
    client = ScriptedEmbeddingClient([[[1.0, 0.0], [0.0, 1.0]], [[1.0, 1.0]], [[1.0, 0.0]]])
    cache = CachedEmbeddingClient(client, max_entries=1)
    assert cache.encode(['a', 'b'], kind='query') == [[1.0, 0.0], [0.0, 1.0]]
    cache.encode(['a'], kind='query')
    cache.clear()
    cache.encode(['a'], kind='query')
    assert len(client.requests) == 3 and len(cache._cache) == 1


@pytest.mark.parametrize('updates', [{'max_entries': 0}, {'max_entries': True}, {'projection_version': ''}])
def test_cache_options_strict(updates):
    with pytest.raises(ValueError): CachedEmbeddingClient(fake(), **updates)


def snapshot(tmp_path, revision='a'*40):
    path = tmp_path / revision
    path.mkdir()
    (path / 'config.json').write_text('{}')
    return path


def test_local_constructor_lazy_no_runtime_load(tmp_path, monkeypatch):
    import job_radar.retrieval_embedding as module
    def blocked(): raise AssertionError('runtime loaded during constructor')
    monkeypatch.setattr(module, '_load_runtime', blocked)
    client = LocalSnapshotEmbeddingClient(snapshot(tmp_path), model_id='intfloat/multilingual-e5-small', revision='a'*40)
    assert client.dimension == 384 and client._model is None


@pytest.mark.parametrize('updates,expected', [
    ({'model_id': 'unknown'}, 'dense_model_unsupported'),
    ({'revision': 'main'}, 'dense_snapshot_invalid'),
    ({'revision': 'b'*40}, 'dense_snapshot_invalid'),
    ({'batch_size': 9}, 'dense_client_metadata'),
    ({'batch_size': True}, 'dense_client_metadata'),
])
def test_local_constructor_fixed_snapshot_rules(tmp_path, updates, expected):
    args = dict(model_id='intfloat/multilingual-e5-small', revision='a'*40)
    args.update(updates)
    code(expected, lambda: LocalSnapshotEmbeddingClient(snapshot(tmp_path), **args))


def test_missing_local_snapshot_rejected_before_runtime(tmp_path):
    code('dense_snapshot_missing', lambda: LocalSnapshotEmbeddingClient(tmp_path/'missing',
        model_id='intfloat/multilingual-e5-small', revision='a'*40))


def test_missing_optional_runtime_is_explicit_and_no_package_install(tmp_path, monkeypatch):
    import job_radar.retrieval_embedding as module
    def absent(name): raise ModuleNotFoundError('private path')
    monkeypatch.setattr(module.importlib, 'import_module', absent)
    client = LocalSnapshotEmbeddingClient(snapshot(tmp_path), model_id='intfloat/multilingual-e5-small', revision='a'*40)
    code('dense_runtime_unavailable', lambda: client.encode(['合成文本'], kind='query'))


class ArrayStub:
    def __init__(self, rows): self.rows = rows
    def tolist(self): return deepcopy(self.rows)


class ModelStub:
    def __init__(self, dimension=384, max_length=512):
        self.dimension = dimension
        self.max_seq_length = max_length
        self.calls = []
        self.token_calls = []
    def get_sentence_embedding_dimension(self): return self.dimension
    def tokenizer(self, text, **kwargs):
        self.token_calls.append((text, kwargs))
        return {'input_ids': list(range(len(text.split()) + 2))}
    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        return ArrayStub([[1.0] + [0.0] * (self.dimension - 1) for text in texts])


def install_stub(monkeypatch, model, captured):
    import job_radar.retrieval_embedding as module
    def factory(path, **kwargs):
        captured.append((path, kwargs))
        return model
    monkeypatch.setattr(module, '_load_runtime', lambda: factory)


@pytest.mark.parametrize('model_id,dimension,query_prefix,passage_prefix,max_length', [
    ('intfloat/multilingual-e5-small', 384, 'query: ', 'passage: ', 512),
    ('BAAI/bge-small-zh-v1.5', 512, '为这个句子生成表示以用于检索相关文章：', '', 512),
    ('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', 384, '', '', 128),
])
def test_local_stub_cpu_only_prefixes_normalization_and_token_counting(tmp_path, monkeypatch, model_id, dimension, query_prefix, passage_prefix, max_length):
    captured = []
    model = ModelStub(dimension, max_length)
    install_stub(monkeypatch, model, captured)
    path = snapshot(tmp_path)
    client = LocalSnapshotEmbeddingClient(path, model_id=model_id, revision='a'*40)
    query_vectors = client.encode(['合成查询'], kind='query')
    passage_vectors = client.encode(['合成岗位'], kind='passage')
    assert len(query_vectors[0]) == len(passage_vectors[0]) == dimension
    assert captured == [(str(path), {'device': 'cpu', 'local_files_only': True, 'trust_remote_code': False})]
    assert model.calls[0][0] == [query_prefix + '合成查询']
    assert model.calls[1][0] == [passage_prefix + '合成岗位']
    assert model.calls[0][1] == {'batch_size': 1, 'normalize_embeddings': True, 'show_progress_bar': False, 'convert_to_numpy': True}
    assert all(options == {'truncation': False, 'add_special_tokens': True} for _, options in model.token_calls)


def test_local_stub_rejects_long_tokens_before_silent_encode(tmp_path, monkeypatch):
    captured = []
    model = ModelStub(384, 128)
    install_stub(monkeypatch, model, captured)
    client = LocalSnapshotEmbeddingClient(snapshot(tmp_path),
        model_id='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', revision='a'*40)
    code('dense_input_too_long', lambda: client.encode(['word ' * 127], kind='query'))
    assert model.calls == []


@pytest.mark.parametrize('dimension,max_length,expected', [
    (512, 512, 'dense_vector_dimension'), (384, 1024, 'dense_model_length_unknown'),
    (384, 0, 'dense_model_length_unknown'),
])
def test_local_snapshot_runtime_metadata_rechecked(tmp_path, monkeypatch, dimension, max_length, expected):
    install_stub(monkeypatch, ModelStub(dimension, max_length), [])
    client = LocalSnapshotEmbeddingClient(snapshot(tmp_path), model_id='intfloat/multilingual-e5-small', revision='a'*40)
    code(expected, lambda: client.encode(['synthetic'], kind='query'))


def test_local_runtime_load_errors_sanitized(tmp_path, monkeypatch):
    import job_radar.retrieval_embedding as module
    def factory(*args, **kwargs): raise RuntimeError('raw private location')
    monkeypatch.setattr(module, '_load_runtime', lambda: factory)
    client = LocalSnapshotEmbeddingClient(snapshot(tmp_path), model_id='intfloat/multilingual-e5-small', revision='a'*40)
    code('dense_model_load_failed', lambda: client.encode(['synthetic'], kind='query'))


def test_dense_module_import_does_not_load_optional_runtime():
    script = "import sys;import job_radar.retrieval_embedding;assert not any(x in sys.modules for x in ['torch','numpy','sentence_transformers','transformers']);print('OPTIONAL_RUNTIME_NOT_LOADED')"
    result = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True, check=True)
    assert result.stdout == 'OPTIONAL_RUNTIME_NOT_LOADED\n'


def test_retrieve_dense_cache_reuses_query_and_passages_across_calls():
    underlying = fake()
    cached = CachedEmbeddingClient(underlying)
    left = retrieve_dense(query(), documents(), config(), client=cached)
    right = retrieve_dense(query(), documents(), config(), client=cached)
    assert left == right and len(underlying.requests) == 2


def test_cache_mutating_metadata_is_never_written():
    underlying = fake()
    original = underlying.encode
    def changed(texts, *, kind):
        result = original(texts, kind=kind)
        underlying.revision = 'different-revision'
        return result
    underlying.encode = changed
    cache = CachedEmbeddingClient(underlying)
    code('dense_client_changed', lambda: cache.encode(['a'], kind='query'))
    assert cache._cache == {}


@pytest.mark.parametrize('kind,texts', [('other', ['a']), ('query', 'a'), ('query', [1])])
def test_direct_client_bad_shape_rejected(tmp_path,kind,texts):
    cache=CachedEmbeddingClient(fake())
    code('dense_client_metadata' if kind=='other' else 'dense_response_shape',
         lambda:cache.encode(texts,kind=kind))
    local=LocalSnapshotEmbeddingClient(snapshot(tmp_path),model_id='intfloat/multilingual-e5-small',revision='a'*40)
    code('dense_client_metadata' if kind=='other' else 'dense_response_shape',
         lambda:local.encode(texts,kind=kind))


def test_local_empty_batch_does_not_load_runtime(tmp_path,monkeypatch):
    import job_radar.retrieval_embedding as module
    def blocked():raise AssertionError('runtime called for no inputs')
    monkeypatch.setattr(module,'_load_runtime',blocked)
    local=LocalSnapshotEmbeddingClient(snapshot(tmp_path),model_id='intfloat/multilingual-e5-small',revision='a'*40)
    assert local.encode([],kind='query')==[]


def test_runtime_oom_maps_to_explicit_resource_code(tmp_path,monkeypatch):
    import job_radar.retrieval_embedding as module
    def factory(*args,**kwargs):raise MemoryError('raw memory text')
    monkeypatch.setattr(module,'_load_runtime',lambda:factory)
    local=LocalSnapshotEmbeddingClient(snapshot(tmp_path),model_id='intfloat/multilingual-e5-small',revision='a'*40)
    code('dense_resource_error',lambda:local.encode(['a'],kind='query'))
