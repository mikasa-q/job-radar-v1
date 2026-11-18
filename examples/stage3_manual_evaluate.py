#!/usr/bin/env python3
"""One fixed local Hybrid evaluation; no tuning, label edits, or source writes.

Use the Stage 3 real-ablation query adapter: existing lexical projection,
DerivedIndex.dense_hits (best chunk per job), and existing fuse_rrf. There is no
resume profile to project and no rule-based filtering/reranking of text queries.
The input is projected by split without altering labels/groups. Shared job groups
are explicitly reported, so this is not a job-group-independent held-out claim.
An existing output or nonempty index directory is rejected, never overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from job_radar.evidence_repository import read_job_candidates
from job_radar.rag_chunks import chunk_job
from job_radar.rag_evaluation import evaluate_rankings, validate_dataset
from job_radar.rag_index import DerivedIndex, snapshot_hash
from job_radar.rag_models import (ChunkConfig, DenseSpec, EvaluationSet,
                                 ExperimentSpec, IndexManifest, canonical_hash)
from job_radar.retrieval_bm25 import retrieve_bm25
from job_radar.retrieval_fusion import fuse_rrf
from job_radar.retrieval_models import EngineConfig, JobKey, RetrievalError, RetrievalQuery
from job_radar.retrieval_text import retrieval_text, tokenize

MODEL_ID = 'BAAI/bge-small-zh-v1.5'
REVISION = '7999e1d3359715c523056ef9478215996d62a620'
METRICS = ('Recall@1', 'Recall@5', 'Recall@10', 'MRR@10', 'nDCG@10')
EXPECTED_SPLITS = {'dev': ['dev-python', 'dev-data'], 'test': ['test-mobile']}
PRIOR = (Path.home() / '.codex/visualizations/2026/11/12/'
         '01a1d05b-33ee-7331-9a41-172588d232af/jobradar-stage3')
DEFAULT_SNAPSHOT = PRIOR / 'models/BAAI--bge-small-zh-v1.5' / REVISION


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def identity(key):
    return key.dataset_id, key.job_id


def split_view(dataset, split):
    queries = [q for q in dataset.queries if q.split == split]
    ids = {q.query_id for q in queries}
    view = EvaluationSet(schema_version=dataset.schema_version,
                         data_version=dataset.data_version, queries=queries,
                         judgments=[j for j in dataset.judgments if j.query_id in ids])
    return validate_dataset(view)


def check_inputs(dataset, keys):
    if dataset.data_version != 'manual-v1' or len(keys) != 15 or len(set(map(identity, keys))) != 15:
        raise ValueError('expected_manual_v1_and_15_unique_jobs')
    if len(dataset.queries) != 3 or len(dataset.judgments) != 45:
        raise ValueError('expected_3_queries_45_judgments')
    for split, expected in EXPECTED_SPLITS.items():
        if [q.query_id for q in dataset.queries if q.split == split] != expected:
            raise ValueError('unexpected_query_split_or_order')
    universe = set(map(identity, keys))
    for query in dataset.queries:
        labels = [j for j in dataset.judgments if j.query_id == query.query_id]
        if query.source != 'human' or len(labels) != 15 or {identity(j.key) for j in labels} != universe:
            raise ValueError('incomplete_query_candidate_labels')
        if any(j.status != 'human_judged' or type(j.grade) is not int for j in labels):
            raise ValueError('expected_complete_human_judgments')
    # Full validation is always attempted and its actual error is preserved.
    full_errors = []
    try:
        validate_dataset(dataset)
    except RetrievalError as error:
        if error.code != 'eval_job_group_leakage':
            raise
        full_errors.append(error.code)
    views = {split: split_view(dataset, split) for split in EXPECTED_SPLITS}
    groups = defaultdict(set)
    by_query = {q.query_id: q.split for q in dataset.queries}
    identity_groups = {}
    for label in dataset.judgments:
        key = identity(label.key)
        if identity_groups.setdefault(key, label.job_group) != label.job_group:
            raise ValueError('inconsistent_original_job_group')
        groups[label.job_group].add(by_query[label.query_id])
    profile_splits = defaultdict(set)
    for query in dataset.queries:
        profile_splits[query.profile_group].add(query.split)
    if any(len(splits) > 1 for splits in profile_splits.values()):
        raise ValueError('eval_profile_group_leakage')
    shared = sorted(group for group, splits in groups.items() if len(splits) > 1)
    return views, {
        'full_dataset_validation_errors': full_errors,
        'evaluation_scope': 'separate_split_views_of_original_shared_candidate_corpus',
        'original_fields_modified': False,
        'shared_job_groups': shared,
        'job_group_independent_holdout': not bool(shared),
        'interpretation': ('Split metrics are descriptive on the same 15 jobs; '
                           'not an independent job-group held-out acceptance.' if shared else
                           'Job groups are disjoint across splits.'),
    }


def macro_metrics(rows):
    means, support = {}, {}
    for name in METRICS:
        values = [row['metrics'][name] for row in rows if row['metrics'][name] is not None]
        means[name] = math.fsum(values) / len(values) if values else None
        support[name] = len(values)
    return means, support


def verify_snapshot(snapshot, manifest_path):
    manifest = json.loads(manifest_path.read_text())
    if manifest['model_id'] != MODEL_ID or manifest['revision'] != REVISION:
        raise ValueError('pinned_model_manifest_mismatch')
    if snapshot.name != REVISION or snapshot.is_symlink():
        raise ValueError('pinned_snapshot_mismatch')
    for item in manifest['files']:
        name = Path(item['name'])
        if name.is_absolute() or '..' in name.parts:
            raise ValueError('invalid_model_manifest_path')
        path = snapshot / name
        if path.is_symlink() or path.stat().st_size != item['bytes'] or sha256(path) != item['sha256']:
            raise ValueError('model_file_hash_mismatch:' + str(name))
    return {'model_id': MODEL_ID, 'revision': REVISION, 'snapshot': str(snapshot),
            'manifest_sha256': sha256(manifest_path), 'verified_file_count': len(manifest['files'])}


def run(args):
    start = time.perf_counter()
    annotation = args.annotations.expanduser().resolve(strict=True)
    database = args.database.expanduser().resolve(strict=True)
    output = args.output.expanduser().absolute()
    index_path = args.index.expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError('report_exists:' + str(output))
    if index_path.exists() and (not index_path.is_dir() or any(index_path.iterdir())):
        raise FileExistsError('index_must_be_new_or_empty:' + str(index_path))
    if index_path.is_symlink():
        raise ValueError('index_symlink')
    before = {str(p): sha256(p) for p in (annotation, database)}
    dataset = EvaluationSet.model_validate_json(annotation.read_bytes())
    jobs = read_job_candidates(database, dataset_id='jobradar-v1')
    keys = [JobKey(dataset_id=j.dataset_id, job_id=j.job_id) for j in jobs]
    views, validation = check_inputs(dataset, keys)
    # Freeze a single configuration before loading a model or running queries.
    config = EngineConfig(mode='hybrid', top_k=10)
    chunk_config = ChunkConfig()
    config_hash = canonical_hash({'engine': config.model_dump(mode='json'),
                                  'chunk': chunk_config.model_dump(mode='json'),
                                  'model_id': MODEL_ID, 'revision': REVISION,
                                  'query_adapter': 'stage3_real_ablation_exact_text'})
    snapshot = args.snapshot.expanduser().resolve(strict=True)
    model_info = verify_snapshot(snapshot, ROOT / 'config/stage3-bge-model-manifest.json')
    runtime_info = {'packages': {name: version(name) for name in
                               ('torch', 'sentence-transformers', 'transformers', 'tokenizers', 'numpy')},
                    'lock_sha256': sha256(ROOT / 'config/requirements.embedding.macos-x86_64.lock.txt')}
    for name, value in {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                        'TOKENIZERS_PARALLELISM': 'false', 'OMP_NUM_THREADS': '1',
                        'MKL_NUM_THREADS': '1', 'PYTHONDONTWRITEBYTECODE': '1'}.items():
        os.environ[name] = value
    import torch
    from job_radar.rag_runtime import LocalTokenBudget
    from job_radar.retrieval_embedding import LocalSnapshotEmbeddingClient
    torch.set_num_threads(1)
    budget = LocalTokenBudget(snapshot, model_id=MODEL_ID, revision=REVISION)
    client = LocalSnapshotEmbeddingClient(snapshot, model_id=MODEL_ID, revision=REVISION, batch_size=1)
    dense = DenseSpec(model_id=MODEL_ID, revision=REVISION, dimension=client.dimension,
                      tokenizer_id=budget.tokenizer_id, tokenizer_revision=budget.revision,
                      pooling='cls', query_prefix='为这个句子生成表示以用于检索相关文章：',
                      runtime_sha256=canonical_hash(runtime_info))
    manifest = IndexManifest(generation='manual-v1-bge-fixed', snapshot_sha256=snapshot_hash(jobs),
                             chunk_config=chunk_config, dense=dense)
    chunks = [chunk for job in jobs for chunk in chunk_job(job, budget=budget, config=chunk_config)]
    index = DerivedIndex(index_path)
    update = index.update(jobs, manifest=manifest, chunks=chunks, embedding_client=client)
    config_audit = {'engine': config.model_dump(mode='json'), 'chunk': chunk_config.model_dump(mode='json'),
                    'config_sha256': config_hash, 'frozen_before_any_ranking': True,
                    'parameter_search_performed': False, 'configuration_count': 1,
                    'query_adapter': 'stage3_real_ablation_exact_text',
                    'rule_filtering_or_reranking': False, 'allow_dense_fallback': False}
    by_key = {identity(k): job for k, job in zip(keys, jobs)}
    retrieval = {}
    ranking_by_split = {split: {} for split in EXPECTED_SPLITS}
    with index.reader() as reader:
        reader.assert_candidates(jobs)
        docs = reader.documents(keys)
        for query_spec in dataset.queries:
            now = time.perf_counter()
            text = retrieval_text(query_spec.text)
            query = RetrievalQuery(text=text, terms=tokenize(text))
            lexical = retrieve_bm25(query, docs, config)
            semantic = reader.dense_hits(query, keys, config=config, embedding_client=client)
            if len(semantic.hits) != len(keys):
                raise ValueError('dense_channel_missing_candidate')
            fused = fuse_rrf(lexical, semantic.hits, config)[:config.top_k]
            ranking_by_split[query_spec.split][query_spec.query_id] = [key for key, _ in fused]
            bmap = {identity(h.key): h for h in lexical}
            dmap = {identity(h.key): h for h in semantic.hits}
            top10 = []
            for rank, (key, score) in enumerate(fused, 1):
                job = by_key[identity(key)]
                top10.append({'rank': rank, 'key': key.model_dump(mode='json'),
                              'title': job.job.title, 'company': job.job.company,
                              'fusion_score': score,
                              'bm25': bmap[identity(key)].model_dump(mode='json') if identity(key) in bmap else None,
                              'dense': dmap[identity(key)].model_dump(mode='json')})
            retrieval[query_spec.query_id] = {'text': query_spec.text, 'retrieval_text': text,
                'actual_mode': 'hybrid', 'channels': {'bm25': 'ok', 'dense': 'ok'},
                'retrieval_calls': 1, 'seconds': time.perf_counter()-now, 'top10': top10,
                'bm25_channel': [h.model_dump(mode='json') for h in lexical],
                'dense_channel': [h.model_dump(mode='json') for h in semantic.hits]}
            print('RETRIEVED ' + query_spec.query_id + ': hybrid top10=' +
                  ','.join(str(key.job_id) for key, _ in fused), flush=True)
    # Evaluation reads labels only after all ranks are fixed; no ranker sees them.
    reports, rows = {}, []
    for split in EXPECTED_SPLITS:
        view = views[split]
        experiment = ExperimentSpec(experiment_id='manual-v1-bge-fixed', config_sha256=config_hash,
            dataset_sha256=canonical_hash(view), index_sha256=canonical_hash(manifest),
            model_sha256=model_info['manifest_sha256'], variant='hybrid', frozen_on_dev=True)
        report = evaluate_rankings(view, ranking_by_split[split], split=split,
                                   experiment=experiment, candidate_keys=keys)
        for row in report['per_query']:
            labels = {identity(j.key): j.grade for j in view.judgments if j.query_id == row['query_id']}
            details = retrieval[row['query_id']]
            for hit in details['top10']:
                hit['grade'] = labels[(hit['key']['dataset_id'], hit['key']['job_id'])]
            row.update(details)
            row['split'] = split
            rows.append(row)
        reports[split] = report
    macro, support = macro_metrics(rows)
    after = {str(p): sha256(p) for p in (annotation, database)}
    if after != before:
        raise ValueError('input_changed_during_run')
    result = {'schema_version': 'manual-hybrid-report-v1', 'data_version': dataset.data_version,
        'input_sha256': before, 'inputs_unchanged': True,
        'dataset_validation': validation, 'configuration': config_audit,
        'model': model_info, 'runtime': runtime_info, 'manifest': manifest.model_dump(mode='json'),
        'index_path': str(index_path), 'index_update': update.model_dump(mode='json'),
        'index_chunk_count': len(chunks), 'retrieval_calls': 3,
        'candidate_count': len(keys), 'judgment_count': len(dataset.judgments),
        'candidate_keys': [key.model_dump(mode='json') for key in keys],
        'splits': reports, 'per_query': rows, 'macro': macro, 'metric_query_support': support,
        'macro_policy': 'unweighted_query_mean_excluding_null; zero-relevant query not counted as zero',
        'error_categories': dict(sorted(Counter(e for row in rows for e in row['error_categories']).items())),
        'job_group_independent_holdout': validation['job_group_independent_holdout'],
        'elapsed_seconds': time.perf_counter()-start}
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
    with output.open('x', encoding='utf-8') as stream:
        stream.write(payload)
    reopened = json.loads(output.read_text())
    if reopened != result or any(sha256(p) != digest for p, digest in before.items()):
        raise ValueError('post_write_verification_failed')
    print(json.dumps({'status': 'completed', 'report': str(output), 'queries': 3,
                      'retrieval_calls': 3, 'macro': macro, 'metric_query_support': support,
                      'error_categories': result['error_categories'], 'inputs_unchanged': True},
                     ensure_ascii=False, allow_nan=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path.home() / 'Desktop/jobradar-v1-data'
    parser.add_argument('--annotations', type=Path, default=base/'evaluation/manual-v1.json')
    parser.add_argument('--database', type=Path, default=base/'evidence.sqlite3')
    parser.add_argument('--output', type=Path, default=base/'evaluation/manual-v1-report.json')
    parser.add_argument('--snapshot', type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument('--index', type=Path, required=True, help='new independent derived index directory')
    run(parser.parse_args())


if __name__ == '__main__':
    main()
