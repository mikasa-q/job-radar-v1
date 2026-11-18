"""Optional dense retrieval, with scripted tests and no mandatory ML runtime.

The default engine does not import or call a real model. Local snapshot loading
is explicit, CPU-only and local-files-only. This synchronous adapter does not
claim to survive an OS OOM kill or impose a hard process timeout.
"""
from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
import hashlib
import importlib
import math
from pathlib import Path
import re
from typing import Literal

from .retrieval_models import (
    EmbeddingClient, EngineConfig, PROJECTION_VERSION, RetrievalDocument,
    RetrievalError, RetrievalHit, RetrievalQuery,
)
from .retrieval_text import key_tuple

# Codes are owned by this module; arbitrary client exception text is never used.
DENSE_ERROR_CODES = frozenset({
    'dense_client_missing', 'dense_client_metadata', 'dense_client_changed',
    'dense_client_error', 'dense_encode_timeout', 'dense_resource_error',
    'dense_response_shape', 'dense_vector_dimension', 'dense_vector_nonfinite',
    'dense_zero_vector', 'dense_input_too_long', 'dense_runtime_unavailable',
    'dense_snapshot_missing', 'dense_snapshot_invalid', 'dense_model_unsupported',
    'dense_model_load_failed', 'dense_model_length_unknown',
})

# Candidate model cards are recorded in docs/STAGE2_MATCHING_PLAN.md. A model ID
# is not a local path; callers must provide an already-downloaded snapshot.
_MODEL_SPECS = {
    'intfloat/multilingual-e5-small': (384, 512, 'query: ', 'passage: '),
    'BAAI/bge-small-zh-v1.5': (512, 512, '为这个句子生成表示以用于检索相关文章：', ''),
    'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2': (384, 128, '', ''),
}


def _metadata(client: EmbeddingClient) -> tuple[str, str, int]:
    if client is None:
        raise RetrievalError('dense_client_missing')
    try:
        model_id, revision, dimension = client.model_id, client.revision, client.dimension
        encode = client.encode
    except Exception:
        raise RetrievalError('dense_client_metadata') from None
    if (not isinstance(model_id, str) or not model_id.strip()
            or not isinstance(revision, str) or not revision.strip()
            or type(dimension) is not int or not 1 <= dimension <= 65536
            or not callable(encode)):
        raise RetrievalError('dense_client_metadata')
    return model_id, revision, dimension


def _validate_vectors(response, count: int, dimension: int) -> list[list[float]]:
    """Validate the entire batch before a single vector becomes cacheable."""
    if not isinstance(response, list) or len(response) != count:
        raise RetrievalError('dense_response_shape')
    result = []
    for row in response:
        if not isinstance(row, list) or len(row) != dimension:
            raise RetrievalError('dense_vector_dimension')
        vector = []
        for number in row:
            if type(number) not in {int, float}:
                raise RetrievalError('dense_vector_nonfinite')
            try:
                value = float(number)
            except (OverflowError, ValueError):
                raise RetrievalError('dense_vector_nonfinite') from None
            if not math.isfinite(value):
                raise RetrievalError('dense_vector_nonfinite')
            vector.append(value)
        if not any(vector):
            raise RetrievalError('dense_zero_vector')
        result.append(vector)
    return result


def _encode(client: EmbeddingClient, texts: list[str], *, kind: Literal['query', 'passage']) -> list[list[float]]:
    before = _metadata(client)
    try:
        # The client receives a detached list, not the query/document objects.
        response = client.encode(list(texts), kind=kind)
    except RetrievalError as error:
        code = error.code if error.code in DENSE_ERROR_CODES else 'dense_client_error'
        raise RetrievalError(code) from None
    except TimeoutError:
        raise RetrievalError('dense_encode_timeout') from None
    except MemoryError:
        raise RetrievalError('dense_resource_error') from None
    except Exception:
        raise RetrievalError('dense_client_error') from None
    if _metadata(client) != before:
        raise RetrievalError('dense_client_changed')
    return _validate_vectors(response, len(texts), before[2])


def _unit(vector: list[float]) -> list[float]:
    # Scale before summing squares: finite large numbers must not overflow norm.
    maximum = max(abs(number) for number in vector)
    scaled = [number / maximum for number in vector]
    length = math.sqrt(math.fsum(number * number for number in scaled))
    return [number / length for number in scaled]


def retrieve_dense(
    query: RetrievalQuery, documents: list[RetrievalDocument], config: EngineConfig,
    *, client: EmbeddingClient,
) -> list[RetrievalHit]:
    """Cosine ranking over supplied documents, not a capability/probability score.

    No arbitrary semantic threshold is claimed: all valid cosine scores,
    including zero and negative values, may rank in the returned recall set.
    The engine can fuse ranks; it must not interpret a dense hit as rule proof.
    """
    query = RetrievalQuery.model_validate(query)
    config = EngineConfig.model_validate(config)
    if not isinstance(documents, list):
        raise TypeError('expected_retrieval_documents')
    checked = [RetrievalDocument.model_validate(item) for item in documents]
    identities = [key_tuple(item.key) for item in checked]
    if len(identities) != len(set(identities)):
        raise RetrievalError('duplicate_job_identity')
    if len(checked) > config.max_jobs:
        raise RetrievalError('too_many_jobs')
    if len(query.text) > config.max_text_chars or any(len(item.text) > config.max_text_chars for item in checked):
        raise RetrievalError('dense_input_too_long')
    if not query.text.strip() or not checked:
        return []
    metadata = _metadata(client)
    query_vector = _encode(client, [query.text], kind='query')[0]
    document_vectors = _encode(client, [item.text for item in checked], kind='passage')
    if _metadata(client) != metadata:
        raise RetrievalError('dense_client_changed')
    unit_query = _unit(query_vector)
    scores = []
    for document, vector in zip(checked, document_vectors):
        score = math.fsum(left * right for left, right in zip(unit_query, _unit(vector)))
        # Rounding can yield 1.0000000000000002 for identical finite vectors.
        score = max(-1.0, min(1.0, score))
        scores.append((document, score))
    scores.sort(key=lambda row: (-row[1], key_tuple(row[0].key)))
    return [RetrievalHit(key=item.key, channel='dense', score=score, rank=rank)
            for rank, (item, score) in enumerate(scores[:config.recall_k], 1)]


class ScriptedEmbeddingClient:
    """Explicit fake: returns hand-authored batches; performs no text encoding."""

    def __init__(self, responses: Sequence[list[list[float]] | Exception], *,
                 model_id: str = 'scripted-fake', revision: str = 'synthetic-v1', dimension: int = 2):
        self.model_id = model_id
        self.revision = revision
        self.dimension = dimension
        self._responses = deepcopy(list(responses))
        self.requests: list[tuple[Literal['query', 'passage'], list[str]]] = []
        _metadata(self)

    def encode(self, texts: list[str], *, kind: Literal['query', 'passage']) -> list[list[float]]:
        self.requests.append((kind, list(texts)))
        index = len(self.requests) - 1
        if index >= len(self._responses):
            raise RetrievalError('dense_client_error')
        response = self._responses[index]
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)


class CachedEmbeddingClient:
    """Process-local bounded cache; no persistence or covert network/model loads.

    Keys bind model/revision/dimension, query/passage role, projection version,
    and exact UTF-8 text hash. Complete returned batches validate before writes.
    """

    def __init__(self, client: EmbeddingClient, *, projection_version: str = PROJECTION_VERSION,
                 max_entries: int = 4096):
        _metadata(client)
        if not isinstance(projection_version, str) or not projection_version.strip():
            raise ValueError('invalid_projection_version')
        if type(max_entries) is not int or max_entries < 1:
            raise ValueError('invalid_cache_limit')
        self._client = client
        self.projection_version = projection_version
        self.max_entries = max_entries
        self._cache: dict[tuple, list[float]] = {}

    @property
    def model_id(self):
        return self._client.model_id

    @property
    def revision(self):
        return self._client.revision

    @property
    def dimension(self):
        return self._client.dimension

    def encode(self, texts: list[str], *, kind: Literal['query', 'passage']) -> list[list[float]]:
        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            raise RetrievalError('dense_response_shape')
        if kind not in {'query', 'passage'}:
            raise RetrievalError('dense_client_metadata')
        model_id, revision, dimension = _metadata(self._client)
        keys = [(model_id, revision, dimension, self.projection_version, kind,
                 hashlib.sha256(text.encode('utf-8')).hexdigest()) for text in texts]
        missing = []
        missing_keys = []
        seen = set()
        for key, text in zip(keys, texts):
            if key not in self._cache and key not in seen:
                missing.append(text)
                missing_keys.append(key)
                seen.add(key)
        generated = _encode(self._client, missing, kind=kind) if missing else []
        if _metadata(self._client) != (model_id, revision, dimension):
            raise RetrievalError('dense_client_changed')
        fresh = dict(zip(missing_keys, generated))
        # Copy values before eviction so a batch larger than capacity still works.
        result = [list(self._cache[key] if key in self._cache else fresh[key]) for key in keys]
        for key, vector in fresh.items():
            if len(self._cache) >= self.max_entries:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = list(vector)
        return result

    def clear(self) -> None:
        self._cache.clear()


def _load_runtime():
    """Lazy import seam, also used to test absence without optional packages."""
    try:
        module = importlib.import_module('sentence_transformers')
        return module.SentenceTransformer
    except Exception:
        raise RetrievalError('dense_runtime_unavailable') from None


class LocalSnapshotEmbeddingClient:
    """Explicit CPU adapter for an existing pinned Hugging Face snapshot folder.

    `revision` must be a full 40-character commit hash and `snapshot_path` must
    name that snapshot directory. No model download, package install or API call
    occurs here. Snapshot origin/integrity remains the caller's responsibility.
    """

    def __init__(self, snapshot_path: str | Path, *, model_id: str, revision: str,
                 batch_size: int = 1):
        if model_id not in _MODEL_SPECS:
            raise RetrievalError('dense_model_unsupported')
        if not isinstance(revision, str) or re.fullmatch(r'[0-9a-f]{40}', revision) is None:
            raise RetrievalError('dense_snapshot_invalid')
        path = Path(snapshot_path).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise RetrievalError('dense_snapshot_missing')
        if path.name != revision or not (path / 'config.json').is_file():
            raise RetrievalError('dense_snapshot_invalid')
        if type(batch_size) is not int or not 1 <= batch_size <= 8:
            raise RetrievalError('dense_client_metadata')
        self.model_id = model_id
        self.revision = revision
        self.dimension, self.max_tokens, self._query_prefix, self._passage_prefix = _MODEL_SPECS[model_id]
        self._path = path
        self._batch_size = batch_size
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        factory = _load_runtime()
        try:
            # Official SentenceTransformer constructor; never a hub model ID.
            # The candidate cards show model.encode(..., normalize_embeddings=True).
            model = factory(str(self._path), device='cpu', local_files_only=True,
                            trust_remote_code=False)
            if model.get_sentence_embedding_dimension() != self.dimension:
                raise RetrievalError('dense_vector_dimension')
            native_length = model.max_seq_length
            if type(native_length) is not int or not 1 <= native_length <= self.max_tokens:
                raise RetrievalError('dense_model_length_unknown')
            self.max_tokens = native_length
            self._model = model
            return model
        except RetrievalError:
            raise
        except MemoryError:
            raise RetrievalError('dense_resource_error') from None
        except Exception:
            raise RetrievalError('dense_model_load_failed') from None

    def encode(self, texts: list[str], *, kind: Literal['query', 'passage']) -> list[list[float]]:
        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            raise RetrievalError('dense_response_shape')
        if kind not in {'query', 'passage'}:
            raise RetrievalError('dense_client_metadata')
        if not texts:
            return []
        prefix = self._query_prefix if kind == 'query' else self._passage_prefix
        prepared = [prefix + text for text in texts]
        model = self._load()
        try:
            # Count with the model tokenizer BEFORE encode can silently truncate.
            # Tokenizer API supports truncation=False and add_special_tokens=True.
            for text in prepared:
                tokens = model.tokenizer(text, truncation=False, add_special_tokens=True)['input_ids']
                if not isinstance(tokens, list) or any(type(x) is not int for x in tokens):
                    raise RetrievalError('dense_model_length_unknown')
                if len(tokens) > self.max_tokens:
                    raise RetrievalError('dense_input_too_long')
            encoded = model.encode(prepared, batch_size=self._batch_size,
                                   normalize_embeddings=True, show_progress_bar=False,
                                   convert_to_numpy=True)
            response = encoded.tolist()
        except RetrievalError:
            raise
        except TimeoutError:
            raise RetrievalError('dense_encode_timeout') from None
        except MemoryError:
            raise RetrievalError('dense_resource_error') from None
        except Exception:
            raise RetrievalError('dense_client_error') from None
        return _validate_vectors(response, len(texts), self.dimension)
