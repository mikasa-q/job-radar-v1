"""Independent, derived SQLite generations; original evidence is never opened.

Readers bind one immutable generation through a hashed CURRENT pointer. A writer
builds a new SQLite file, validates and fsyncs it, then atomically replaces that
pointer. Supplied keys, not the full index, define the query candidate set. BM25
statistics for scoring remain the existing scorer's candidate-local statistics.
Dense passage vectors are normalized float32 BLOBs and streamed one row at a time.
No optional ML runtime is imported or model loaded by a lexical-only index.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
from urllib.parse import quote
import uuid

from .matching_models import JobCandidate, _plain
from .rag_models import (
    AggregatedChunks, ChunkHit, EvidenceChunk, INDEX_SCHEMA, IndexManifest,
    IndexUpdate, canonical_hash,
)
from .retrieval_models import (
    EmbeddingClient, EngineConfig, JobKey, RetrievalDocument, RetrievalError,
    RetrievalHit, RetrievalQuery, PROJECTION_VERSION,
)
from .retrieval_text import key_tuple, retrieval_text, tokenize

_OWNER = 'job-radar-independent-derived-index-v1\n'
_GENERATION = re.compile(r'g-[0-9a-f]{32}\.sqlite3\Z')
_ALLOWED = re.compile(r'(?:INDEX_OWNER|CURRENT|\.writer\.lock|\.CURRENT-[0-9a-f]{32}|(?:g-|\.build-)[0-9a-f]{32}\.sqlite3(?:-journal)?)\Z')
_MAX_BUILD_JOBS = 10000
_MAX_BUILD_CHARS = 128000
_SCHEMA = '''
CREATE TABLE info (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE jobs (dataset_id TEXT NOT NULL, job_id INTEGER NOT NULL,
 candidate TEXT NOT NULL, document TEXT NOT NULL,
 PRIMARY KEY(dataset_id,job_id));
CREATE TABLE chunks (dataset_id TEXT NOT NULL, job_id INTEGER NOT NULL,
 ordinal INTEGER NOT NULL, chunk TEXT NOT NULL, cache_key TEXT NOT NULL,
 PRIMARY KEY(dataset_id,job_id,ordinal),
 FOREIGN KEY(dataset_id,job_id) REFERENCES jobs(dataset_id,job_id) ON DELETE CASCADE);
CREATE TABLE vectors (cache_key TEXT PRIMARY KEY, dimension INTEGER NOT NULL,
 vector BLOB NOT NULL);
CREATE TABLE lexical_df (term TEXT PRIMARY KEY, document_frequency INTEGER NOT NULL);
'''


def _json(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _error(code):
    return RetrievalError('index_' + code)


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _candidate_list(jobs):
    if not isinstance(jobs, list):
        raise _error('invalid_candidates')
    if len(jobs) > _MAX_BUILD_JOBS:
        raise _error('too_many_jobs')
    try:
        checked = [JobCandidate.model_validate(_plain(job)) for job in jobs]
    except Exception:
        raise _error('invalid_candidates') from None
    ids = [(job.dataset_id, job.job_id) for job in checked]
    urls = [(job.dataset_id, job.job.source, job.job.url) for job in checked]
    if len(set(ids)) != len(ids) or len(set(urls)) != len(urls):
        raise _error('duplicate_identity')
    return sorted(checked, key=lambda job: (job.dataset_id, job.job_id))


def snapshot_hash(jobs: list[JobCandidate]) -> str:
    """Canonical complete snapshot, including provenance and review metadata."""
    return canonical_hash([job.model_dump(mode='json') for job in _candidate_list(jobs)])


def _document(job):
    title = retrieval_text(job.job.title)
    body = retrieval_text(job.job.description)
    text = title + '\n' + body
    if not text.strip():
        raise _error('empty_document')
    if len(text) > _MAX_BUILD_CHARS:
        raise _error('job_text_too_long')
    return RetrievalDocument(key=JobKey(dataset_id=job.dataset_id, job_id=job.job_id),
        title=job.job.title, text=text, title_terms=tokenize(title), terms=tokenize(body),
        fact_sha256=job.fact_sha256)


def _keys(keys):
    if not isinstance(keys, list):
        raise _error('invalid_keys')
    try:
        checked = [JobKey.model_validate(_plain(key)) for key in keys]
    except Exception:
        raise _error('invalid_keys') from None
    identities = [key_tuple(key) for key in checked]
    if len(set(identities)) != len(identities):
        raise _error('duplicate_keys')
    return checked


def _check_manifest(manifest):
    try:
        manifest = IndexManifest.model_validate(_plain(manifest))
    except Exception:
        raise _error('invalid_manifest') from None
    if manifest.projection_version != PROJECTION_VERSION or manifest.lexical_version != 'char-bigram-alias-v1' or manifest.build_version != 'rag-v1':
        raise _error('incompatible_version')
    return manifest


def _chunk_cache_key(chunk, manifest):
    return canonical_hash({'role': 'passage', 'text_sha256': chunk.encoding_sha256,
        'dense': manifest.dense.model_dump(mode='json') if manifest.dense else None,
        'projection': manifest.projection_version,
        'chunk_config': manifest.chunk_config.model_dump(mode='json')})


def _validate_chunks(chunks, jobs, manifest):
    if not isinstance(chunks, list):
        raise _error('invalid_chunks')
    by_id = {(job.dataset_id, job.job_id): job for job in jobs}
    checked = []
    ordinals = {}
    try:
        for raw in chunks:
            chunk = EvidenceChunk.model_validate(_plain(raw))
            identity = key_tuple(chunk.key)
            job = by_id.get(identity)
            if job is None or chunk.jd_sha256 != job.jd_sha256:
                raise _error('chunk_identity_mismatch')
            if chunk.span.field != 'description' or job.job.description[chunk.span.start:chunk.span.end] != chunk.span.quote:
                raise _error('chunk_source_mismatch')
            if chunk.span.end > len(job.job.description):
                raise _error('chunk_source_mismatch')
            if chunk.token_count > manifest.chunk_config.max_tokens:
                raise _error('chunk_budget_mismatch')
            seen = ordinals.setdefault(identity, set())
            if chunk.ordinal in seen:
                raise _error('duplicate_chunk')
            seen.add(chunk.ordinal)
            if len(seen) > manifest.chunk_config.max_chunks_per_job:
                raise _error('too_many_chunks')
            last_encoded = 0
            for mapping in chunk.mapping:
                source = getattr(job.job, mapping.field)
                if (mapping.source_end > len(source) or mapping.encoded_end > len(chunk.encoding_text)
                        or mapping.encoded_start < last_encoded
                        or source[mapping.source_start:mapping.source_end] != chunk.encoding_text[mapping.encoded_start:mapping.encoded_end]
                        or (mapping.field == 'description' and (mapping.source_start < chunk.span.start or mapping.source_end > chunk.span.end))):
                    raise _error('chunk_map_mismatch')
                if chunk.encoding_text[last_encoded:mapping.encoded_start].strip():
                    raise _error('chunk_map_incomplete')
                last_encoded = mapping.encoded_end
            if not chunk.mapping:
                raise _error('chunk_map_missing')
            if chunk.encoding_text[last_encoded:].strip():
                raise _error('chunk_map_incomplete')
            checked.append(chunk)
        for values in ordinals.values():
            if values != set(range(len(values))):
                raise _error('chunk_ordinals_not_contiguous')
    except RetrievalError:
        raise
    except Exception:
        raise _error('invalid_chunks') from None
    return sorted(checked, key=lambda item: (*key_tuple(item.key), item.ordinal))


def _client_metadata(client, dense):
    from .retrieval_embedding import _metadata
    metadata = _metadata(client)
    if dense is None or metadata != (dense.model_id, dense.revision, dense.dimension):
        raise RetrievalError('dense_index_metadata_mismatch')
    return metadata


def _pack_vector(vector):
    from .retrieval_embedding import _unit
    normalized = _unit(vector)
    return struct.pack('<' + 'f' * len(normalized), *normalized)


def _unpack_vector(blob, dimension):
    if not isinstance(blob, bytes) or len(blob) != dimension * 4:
        raise _error('vector_corrupt')
    vector = struct.unpack('<' + 'f' * dimension, blob)
    if not all(math.isfinite(value) for value in vector) or not any(vector):
        raise _error('vector_corrupt')
    return vector


class DerivedIndex:
    """An explicit independent directory, not a legacy/evidence SQLite path.

    A managed directory can contain immutable generations and staging files.
    Unrecognized files and all symlinks are rejected before any database opens.
    `expected` pins channel compatibility (snapshot and generation may differ).
    """
    def __init__(self, root: str | Path, *, expected: IndexManifest | None = None):
        path = Path(root).expanduser()
        if not path.is_absolute():
            raise _error('absolute_root_required')
        if path.is_symlink():
            raise _error('root_symlink')
        self.root = path.resolve()
        self.expected = None if expected is None else _check_manifest(expected)
        self._query_cache = OrderedDict()
        self._check_root()

    def _check_root(self):
        if self.root.is_symlink():
            raise _error('root_symlink')
        if not self.root.exists():
            return
        if not self.root.is_dir():
            raise _error('independent_directory_required')
        children = list(self.root.iterdir())
        if any(path.is_symlink() or not path.is_file() or not _ALLOWED.fullmatch(path.name) for path in children):
            raise _error('unmanaged_directory')
        marker = self.root / 'INDEX_OWNER'
        if children and not marker.is_file():
            raise _error('unmanaged_directory')
        if marker.exists() and marker.read_text() != _OWNER:
            raise _error('owner_mismatch')

    @contextmanager
    def _writer(self):
        self._check_root()
        self.root.mkdir(parents=True, exist_ok=True)
        # flock releases automatically after process termination; no stale PID lock.
        with (self.root / '.writer.lock').open('a+b') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise _error('writer_busy') from None
            try:
                marker = self.root / 'INDEX_OWNER'
                if not marker.exists():
                    marker.write_text(_OWNER)
                    with marker.open('rb') as stream:
                        os.fsync(stream.fileno())
                    _sync_dir(self.root)
                self._check_root()
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @contextmanager
    def _read(self, *, ignore_expected=False):
        self._check_root()
        pointer = self.root / 'CURRENT'
        if not pointer.is_file():
            raise _error('not_built')
        connection = None
        try:
            data = json.loads(pointer.read_text())
            if set(data) != {'file', 'sha256', 'manifest_sha256'} or not _GENERATION.fullmatch(data['file']):
                raise _error('pointer_corrupt')
            path = self.root / data['file']
            if not path.is_file() or _file_hash(path) != data['sha256']:
                raise _error('generation_corrupt')
            connection = sqlite3.connect('file:' + quote(str(path), safe='/') + '?mode=ro', uri=True)
            connection.execute('PRAGMA query_only=ON')
            if connection.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise _error('generation_corrupt')
            raw_manifest = connection.execute("SELECT value FROM info WHERE name='manifest'").fetchone()
            if raw_manifest is None:
                raise _error('manifest_missing')
            raw = json.loads(raw_manifest[0])
            if raw.get('schema_version') != INDEX_SCHEMA:
                raise _error('schema_incompatible')
            manifest = _check_manifest(IndexManifest.model_validate_json(raw_manifest[0]))
            if canonical_hash(manifest) != data['manifest_sha256']:
                raise _error('manifest_corrupt')
            if not ignore_expected and self.expected is not None and manifest.compatibility_hash() != self.expected.compatibility_hash():
                raise _error('incompatible_version')
            yield connection, manifest
        except RetrievalError:
            raise
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            raise _error('generation_corrupt') from None
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def reader(self):
        """Bind all query API calls to one validated immutable generation."""
        with self._read() as (connection, manifest):
            view = object.__new__(_PinnedIndex)
            view.root, view.expected = self.root, self.expected
            view._query_cache = self._query_cache
            view._connection, view._manifest = connection, manifest
            try:
                yield view
            finally:
                view._connection = None

    def manifest(self) -> IndexManifest:
        with self._read() as (_, manifest):
            return manifest

    def documents(self, keys: list[JobKey]) -> list[RetrievalDocument]:
        checked = _keys(keys)
        with self._read() as (connection, _):
            documents = []
            for key in checked:
                row = connection.execute('SELECT document FROM jobs WHERE dataset_id=? AND job_id=?', key_tuple(key)).fetchone()
                if row is None:
                    raise _error('candidate_missing')
                documents.append(RetrievalDocument.model_validate_json(row[0]))
            return documents

    def chunks(self, keys: list[JobKey]) -> list[EvidenceChunk]:
        checked = _keys(keys)
        with self._read() as (connection, _):
            chunks = []
            for key in checked:
                if not connection.execute('SELECT 1 FROM jobs WHERE dataset_id=? AND job_id=?', key_tuple(key)).fetchone():
                    raise _error('candidate_missing')
                for row in connection.execute('SELECT chunk FROM chunks WHERE dataset_id=? AND job_id=? ORDER BY ordinal', key_tuple(key)):
                    chunks.append(EvidenceChunk.model_validate_json(row[0]))
            return chunks

    def assert_candidates(self, jobs: list[JobCandidate]) -> None:
        checked = _candidate_list(jobs)
        with self._read() as (connection, _):
            for job in checked:
                row = connection.execute('SELECT candidate FROM jobs WHERE dataset_id=? AND job_id=?', (job.dataset_id, job.job_id)).fetchone()
                if row is None:
                    raise _error('candidate_missing')
                if row[0] != _json(job):
                    raise _error('candidate_stale')

    def lexical_statistics(self) -> dict:
        """Audit corpus statistics only; never used as candidate-local BM25 df."""
        with self._read() as (connection, _):
            stats = json.loads(connection.execute("SELECT value FROM info WHERE name='lexical_statistics'").fetchone()[0])
            stats['df'] = dict(connection.execute('SELECT term,document_frequency FROM lexical_df ORDER BY term'))
            return stats

    def update(self, jobs: list[JobCandidate], *, manifest: IndexManifest,
               chunks: list[EvidenceChunk] | None = None,
               embedding_client: EmbeddingClient | None = None,
               failpoint: str | None = None) -> IndexUpdate:
        jobs = _candidate_list(jobs)
        manifest = _check_manifest(manifest)
        if snapshot_hash(jobs) != manifest.snapshot_sha256:
            raise _error('snapshot_mismatch')
        if self.expected is not None and self.expected.compatibility_hash() != manifest.compatibility_hash():
            raise _error('incompatible_version')
        if failpoint not in {None, 'during_build', 'before_switch'}:
            raise _error('invalid_failpoint')
        checked_chunks = None if chunks is None else _validate_chunks(chunks, jobs, manifest)
        if manifest.dense is not None and embedding_client is not None:
            _client_metadata(embedding_client, manifest.dense)
        with self._writer():
            old_connection = None
            old_context = None
            staging = None
            pointer_temp = None
            connection = None
            try:
                old_manifest = None
                old_jobs = {}
                if (self.root / 'CURRENT').exists():
                    old_context = self._read(ignore_expected=True)
                    old_connection, old_manifest = old_context.__enter__()
                    old_jobs = {(row[0], row[1]): row[2] for row in old_connection.execute('SELECT dataset_id,job_id,candidate FROM jobs')}
                compatible = old_manifest is not None and old_manifest.compatibility_hash() == manifest.compatibility_hash()
                incoming = {(job.dataset_id, job.job_id): job for job in jobs}
                added = len(incoming.keys() - old_jobs.keys())
                deleted = len(old_jobs.keys() - incoming.keys())
                changed = metadata_only = unchanged = 0
                content_unchanged = set()
                for identity, job in incoming.items():
                    previous = old_jobs.get(identity)
                    if previous is None:
                        continue
                    if previous == _json(job):
                        unchanged += 1; content_unchanged.add(identity)
                    elif json.loads(previous)['fact_sha256'] == job.fact_sha256:
                        metadata_only += 1; content_unchanged.add(identity)
                    else:
                        changed += 1
                if compatible and old_manifest.snapshot_sha256 == manifest.snapshot_sha256 and not (added or deleted or changed or metadata_only):
                    # Explicit new chunks are still validated; reject conflicting content.
                    if checked_chunks is not None:
                        stored = [EvidenceChunk.model_validate_json(row[0]) for row in old_connection.execute('SELECT chunk FROM chunks ORDER BY dataset_id,job_id,ordinal')]
                        if stored != checked_chunks:
                            raise _error('chunk_snapshot_conflict')
                    return IndexUpdate(generation=old_manifest.generation, unchanged=unchanged)
                if checked_chunks is None:
                    checked_chunks = []
                    if compatible:
                        for identity in content_unchanged:
                            checked_chunks.extend(EvidenceChunk.model_validate_json(row[0]) for row in old_connection.execute('SELECT chunk FROM chunks WHERE dataset_id=? AND job_id=? ORDER BY ordinal', identity))
                    if manifest.dense is not None:
                        missing = set(incoming) - content_unchanged if compatible else set(incoming)
                        if missing:
                            raise _error('dense_chunks_required')
                grouped = Counter(key_tuple(chunk.key) for chunk in checked_chunks)
                if manifest.dense is not None and any(identity not in grouped and retrieval_text(job.job.description) for identity, job in incoming.items()):
                    raise _error('dense_chunks_required')
                identifier = uuid.uuid4().hex
                staging = self.root / ('.build-' + identifier + '.sqlite3')
                final = self.root / ('g-' + identifier + '.sqlite3')
                connection = sqlite3.connect(staging)
                connection.execute('PRAGMA foreign_keys=ON')
                connection.executescript(_SCHEMA)
                connection.execute('INSERT INTO info VALUES (?,?)', ('manifest', _json(manifest)))
                df = Counter(); body_length = title_length = 0
                for offset, job in enumerate(jobs):
                    identity = (job.dataset_id, job.job_id)
                    if compatible and identity in content_unchanged:
                        row = old_connection.execute('SELECT document FROM jobs WHERE dataset_id=? AND job_id=?', identity).fetchone()
                        document = RetrievalDocument.model_validate_json(row[0])
                    else:
                        identity = (job.dataset_id, job.job_id)
                    if compatible and identity in content_unchanged:
                        previous_doc = old_connection.execute('SELECT document FROM jobs WHERE dataset_id=? AND job_id=?', identity).fetchone()
                        if previous_doc is None:
                            raise _error('generation_corrupt')
                        document = RetrievalDocument.model_validate_json(previous_doc[0])
                    else:
                        document = _document(job)
                    connection.execute('INSERT INTO jobs VALUES (?,?,?,?)', (job.dataset_id,job.job_id,_json(job),_json(document)))
                    df.update(set(document.terms) | set(document.title_terms))
                    body_length += len(document.terms); title_length += len(document.title_terms)
                    if failpoint == 'during_build' and offset == len(jobs)//2:
                        raise _error('build_interrupted')
                if failpoint == 'during_build' and not jobs:
                    raise _error('build_interrupted')
                connection.executemany('INSERT INTO lexical_df VALUES (?,?)', sorted(df.items()))
                statistics = {'documents': len(jobs), 'body_terms': body_length,
                              'title_terms': title_length,
                              'average_body_length': body_length/len(jobs) if jobs else 0.0,
                              'average_title_length': title_length/len(jobs) if jobs else 0.0}
                connection.execute('INSERT INTO info VALUES (?,?)', ('lexical_statistics', _json(statistics)))
                encoded = 0
                for chunk in checked_chunks:
                    cache_key = _chunk_cache_key(chunk, manifest)
                    connection.execute('INSERT INTO chunks VALUES (?,?,?,?,?)', (*key_tuple(chunk.key), chunk.ordinal, _json(chunk), cache_key))
                    if manifest.dense is None or connection.execute('SELECT 1 FROM vectors WHERE cache_key=?', (cache_key,)).fetchone():
                        continue
                    old_vector = None
                    if compatible:
                        old_vector = old_connection.execute('SELECT dimension,vector FROM vectors WHERE cache_key=?', (cache_key,)).fetchone()
                    if old_vector is not None:
                        dimension, blob = old_vector
                        if dimension != manifest.dense.dimension:
                            raise _error('vector_corrupt')
                        _unpack_vector(blob, dimension)
                    else:
                        from .retrieval_embedding import _encode
                        _client_metadata(embedding_client, manifest.dense)
                        vector = _encode(embedding_client, [chunk.encoding_text], kind='passage')[0]
                        _client_metadata(embedding_client, manifest.dense)
                        dimension = manifest.dense.dimension
                        blob = _pack_vector(vector)
                        encoded += 1
                    connection.execute('INSERT INTO vectors VALUES (?,?,?)', (cache_key, dimension, blob))
                connection.commit()
                if connection.execute('PRAGMA integrity_check').fetchone() != ('ok',) or list(connection.execute('PRAGMA foreign_key_check')):
                    raise _error('generation_corrupt')
                connection.close(); connection = None
                with staging.open('rb') as stream:
                    os.fsync(stream.fileno())
                os.replace(staging, final); staging = None
                _sync_dir(self.root)
                if failpoint == 'before_switch':
                    raise _error('build_interrupted')
                pointer = {'file': final.name, 'sha256': _file_hash(final), 'manifest_sha256': canonical_hash(manifest)}
                pointer_temp = self.root / ('.CURRENT-' + identifier)
                with pointer_temp.open('w') as stream:
                    stream.write(_json(pointer)); stream.flush(); os.fsync(stream.fileno())
                os.replace(pointer_temp, self.root / 'CURRENT'); pointer_temp = None
                _sync_dir(self.root)
                self._query_cache.clear()
                return IndexUpdate(generation=manifest.generation, added=added, changed=changed,
                    deleted=deleted, metadata_only=metadata_only, unchanged=unchanged,
                    encoded_chunks=encoded, rebuilt=old_manifest is None or not compatible)
            except RetrievalError:
                raise
            except (OSError, ValueError, TypeError, sqlite3.Error):
                raise _error('build_failed') from None
            finally:
                if connection is not None:
                    connection.close()
                if old_context is not None:
                    old_context.__exit__(None, None, None)
                if staging is not None:
                    staging.unlink(missing_ok=True)
                    Path(str(staging)+'-journal').unlink(missing_ok=True)
                if pointer_temp is not None:
                    pointer_temp.unlink(missing_ok=True)

    def dense_hits(self, query: RetrievalQuery, keys: list[JobKey], *,
                   config: EngineConfig, embedding_client: EmbeddingClient) -> AggregatedChunks:
        from .retrieval_embedding import _encode, _unit
        query = RetrievalQuery.model_validate(_plain(query))
        config = EngineConfig.model_validate(_plain(config))
        checked = _keys(keys)
        if len(checked) > config.max_jobs:
            raise _error('too_many_jobs')
        if len(query.text) > config.max_text_chars:
            raise RetrievalError('dense_input_too_long')
        with self._read() as (connection, manifest):
            if manifest.dense is None:
                raise RetrievalError('dense_index_disabled')
            metadata = _client_metadata(embedding_client, manifest.dense)
            for key in checked:
                if not connection.execute('SELECT 1 FROM jobs WHERE dataset_id=? AND job_id=?', key_tuple(key)).fetchone():
                    raise _error('candidate_missing')
            if not checked or not query.text.strip():
                return AggregatedChunks()
            query_key = canonical_hash({'role': 'query', 'text_sha256': hashlib.sha256(query.text.encode()).hexdigest(),
                'dense': manifest.dense.model_dump(mode='json'), 'projection': manifest.projection_version})
            cached = self._query_cache.get(query_key)
            if cached is None:
                query_vector = _unit(_encode(embedding_client, [query.text], kind='query')[0])
                if _client_metadata(embedding_client, manifest.dense) != metadata:
                    raise RetrievalError('dense_index_metadata_mismatch')
                self._query_cache[query_key] = query_vector
                if len(self._query_cache) > 128:
                    self._query_cache.popitem(last=False)
            else:
                self._query_cache.move_to_end(query_key)
                query_vector = cached
            best_hits = []
            for key in checked:
                best = None
                sql = '''SELECT c.chunk,v.dimension,v.vector FROM chunks c
                    JOIN vectors v ON c.cache_key=v.cache_key
                    WHERE c.dataset_id=? AND c.job_id=? ORDER BY c.ordinal'''
                chunk_count = connection.execute('SELECT COUNT(*) FROM chunks WHERE dataset_id=? AND job_id=?', key_tuple(key)).fetchone()[0]
                seen = 0
                for row in connection.execute(sql, key_tuple(key)):
                    seen += 1
                    if row[1] != manifest.dense.dimension:
                        raise _error('vector_corrupt')
                    vector = _unpack_vector(row[2], row[1])
                    # Stored float32 rows remain streamed; no full Python matrix.
                    norm = math.sqrt(math.fsum(value*value for value in vector))
                    score = max(-1.0, min(1.0, math.fsum(a*b for a,b in zip(query_vector, vector))/norm))
                    chunk = EvidenceChunk.model_validate_json(row[0])
                    if best is None or score > best[0]:
                        best = (score, chunk)
                if seen != chunk_count:
                    raise _error('vector_missing')
                if best is not None:
                    best_hits.append(best)
            best_hits.sort(key=lambda row: (-row[0], key_tuple(row[1].key), row[1].ordinal))
            selected = best_hits[:config.recall_k]
            return AggregatedChunks(
                hits=[RetrievalHit(key=chunk.key, channel='dense', score=score, rank=rank)
                      for rank,(score,chunk) in enumerate(selected,1)],
                evidence=[ChunkHit(chunk=chunk,score=score,rank=rank)
                          for rank,(score,chunk) in enumerate(selected,1)])


class _PinnedIndex(DerivedIndex):
    """Read-only API view whose lifetime is the parent's reader() context."""
    @contextmanager
    def _read(self, *, ignore_expected=False):
        if self._connection is None:
            raise _error('reader_closed')
        yield self._connection, IndexManifest.model_validate(self._manifest)

    def update(self, *args, **kwargs):
        raise _error('reader_read_only')
