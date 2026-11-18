# Stage 3 RAG retrieval engineering

## Entry points and compatibility

- Stage 2 `match_jobs` stays the in-memory baseline with unchanged contracts.
- New `rag_engine.match_jobs_indexed(request, index=..., config=..., embedding_client=...)` returns versioned `IndexedMatchingResult`: `matching` is the existing MatchingResult; `manifest` and `chunk_hits` are explicit additions.
- `DerivedIndex` uses an independent empty directory. Never pass an evidence/legacy database or a directory containing unrelated files. Original candidates are inputs; no source databases are opened. A full update snapshot defines deletions, while query requests may select subsets. Identity, content, provenance and review metadata must match index snapshot.
- Default path persists projections/terms but still runs existing BM25 over the hard-filtered eligible subset. Whole-index df is audit-only. It never widens requested candidates, changes human review status or converts chunk hits into rule proof.

```python
from job_radar.rag_index import DerivedIndex, snapshot_hash
from job_radar.rag_models import IndexManifest
from job_radar.rag_engine import match_jobs_indexed

index = DerivedIndex('/absolute/new-derived-directory')
manifest = IndexManifest(generation='build-001', snapshot_sha256=snapshot_hash(request.jobs))
update = index.update(request.jobs, manifest=manifest)
result = match_jobs_indexed(request, index=index)
assert result.matching.actual_mode == 'bm25_rules'
```

## Chunks and dense indexing

`chunk_job` requires TokenBudget; production `LocalTokenBudget` uses the actual local fast tokenizer. Title, query/passage prefix and special tokens count against the limit. Paragraph/sentence boundaries precede offset splitting; no model truncation. Evidence quote offsets address original JD, while sanitized encoding mapping is separate. Every chunk binds job/JD/encoded hashes. Best chunk score creates one job position before existing RRF; long documents do not gain by summed chunk count.

Pass all chunks and a DenseSpec-bound client to `update` for dense builds. Float32 BLOB vectors are streamed for requested keys, not converted wholesale into Python floats. Cache keys bind exact encoded text, passage/query role, model revision, tokenizer, pooling, normalization, runtime, projection and chunk config. Content changes update affected projections/chunks/vectors; review/provenance-only changes reuse content. Query ranking parameters live in experiment config, not dense compatibility. Incompatible model/chunk/schema cannot silently reuse vectors.

Every build stages a new immutable SQLite generation, checks hashes/integrity and atomically switches CURRENT. Interrupted build keeps old pointer; incompatible expected manifest rejects reads. Generations remain for recovery, so disk figures include retained generations. Corrupt current generations fail explicitly, never silently select stale ones.

## Evaluation

Labels: 0 unrelated, 1 partial, 2 highly relevant. Labels and source are separate: synthetic text can eventually be human-judged, but machine expected labels are always `synthetic_expected`. Missing judgments stay unjudged, never zero. Queries/profile groups and near-duplicate job groups stay disjoint across dev/test. Declare a complete candidate universe per split for human quality acceptance; judgment-union default alone is not independent completeness evidence.

Metrics deduplicate jobs by first ranked occurrence, Recall@1/5/10 uses grade>=1, MRR@10 uses first relevant job, nDCG@10 uses (2**grade-1)/log2(rank+1). No relevant jobs gives null metrics and a separate query list. Incomplete labels give null metrics with coverage/error categories. Ranker breaks score ties by dataset_id/job_id. Dev permits multiple configs; held-out experiment execution requires one frozen config, no automatic test-set selection. Raw hand-authored lists can be checked independently of real model quality.

A runnable synthetic baseline/tuning example uses existing BM25 k1/b/title_weight and outputs explicit parameters/hashes/reports. The experiment callback supports real Dense/Hybrid/no-chunk/chunk-best ablations; those count as observed only when run using real models and complete data. Human quality is pending until human labeling/hold-out evaluation completes.

## Verification and benchmarks

- `python -B -m pytest -q tests/test_rag_*.py` runs deterministic synthetic structural tests, authored-rank metric tests and process-supervision failures, without a model dependency. Synthetic tokenizer offsets test mechanics only.
- `python -B examples/stage3_evaluate.py --output /absolute/new-evaluation-dir` runs three lexical configs over synthetic intents; not human retrieval quality.
- `python -B examples/stage3_benchmark.py --output /absolute/new-benchmark-dir` freezes repetitions/warmup/time/RSS/disk/p95 budgets before scales 100→1000→10000. Builder limit is explicit 10k; public request limit stays default1000/max10000 and this benchmark queries at most1000. No index size is misreported as query candidate count. Synthetic scale measures resources, not relevance.
- Opt-in real runtime/length/pooling/model files and supervised local-only inference: see STAGE3_RUNTIME.md. Per-model processes are serial, CPU/batch1, native and common128-token chunk budgets measured separately.

No FastAPI changes, LLM answer/gap analysis, reranker, paid API, model fine-tuning, review-status changes, expanded scraping, Qdrant service or ANN approximation. Current exact-search results decide whether another vector service experiment is justified later.

A single indexed match uses `index.reader()` so concurrent atomic updates do not mix old candidate validation with new manifest/documents/vectors. Evaluation accepts `candidate_keys` as the explicit split candidate universe, with a separately reported canonical hash; complete human judgments then enable human-quality readiness. Declared source/group/assessor correctness still belongs to the human dataset review.
