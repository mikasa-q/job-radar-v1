"""Auditable retrieval metrics and split-gated experiments; no model calls.

Rankings contain job identities, not chunk identities. Duplicate returned jobs
are removed before rank cutoffs. Unknown/unjudged labels never become grade 0:
metrics for incomplete candidate universes are null. Human quality and synthetic
correctness are deliberately independent report fields.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
import math

from .matching_models import _plain
from .rag_models import (
    EVAL_VERSION, EvalQuery, EvaluationSet, ExperimentSpec, MetricPolicy,
    canonical_hash,
)
from .retrieval_models import JobKey, RetrievalError

_METRICS = ('Recall@1', 'Recall@5', 'Recall@10', 'MRR@10', 'nDCG@10')


def _error(code: str):
    raise RetrievalError('eval_' + code)


def _identity(key: JobKey) -> tuple[str, int]:
    return key.dataset_id, key.job_id


def _checked(model, value):
    """Recheck mutated/model_copy instances, including their extra fields."""
    return model.model_validate(_plain(value))


def validate_dataset(dataset: EvaluationSet) -> EvaluationSet:
    """Validate references, unique labels and disjoint profile/job groups.

    A judgment with status=unjudged remains a candidate declaration but has no
    grade. Machine-authored expected ranks do not upgrade to human judgments.
    Human labels may annotate synthetic text; text origin remains independent.
    """
    dataset = _checked(EvaluationSet, dataset)
    queries = {q.query_id: q for q in dataset.queries}
    if len(queries) != len(dataset.queries):
        _error('duplicate_query')
    profile_splits = {}
    for query in dataset.queries:
        previous = profile_splits.setdefault(query.profile_group, query.split)
        if previous != query.split:
            _error('profile_group_leakage')
    seen = set()
    group_splits = {}
    identity_groups = {}
    for judgment in dataset.judgments:
        if judgment.query_id not in queries:
            _error('unknown_query_reference')
        key = _identity(judgment.key)
        pair = judgment.query_id, key
        if pair in seen:
            _error('duplicate_judgment')
        seen.add(pair)
        split = queries[judgment.query_id].split
        previous = group_splits.setdefault(judgment.job_group, split)
        if previous != split:
            _error('job_group_leakage')
        group = identity_groups.setdefault(key, judgment.job_group)
        if group != judgment.job_group:
            _error('inconsistent_job_group')
    return dataset


def _split_queries(dataset: EvaluationSet, split: str) -> list[EvalQuery]:
    if split not in {'dev', 'test'}:
        _error('invalid_split')
    queries = [query for query in dataset.queries if query.split == split]
    if not queries:
        _error('empty_split')
    return queries


def _dedup_ranking(values) -> tuple[list[JobKey], int]:
    if not isinstance(values, list):
        _error('invalid_ranking')
    seen = set()
    result = []
    duplicates = 0
    for value in values:
        if not isinstance(value, JobKey):
            _error('invalid_ranking_key')
        key = _checked(JobKey, value)
        identity = _identity(key)
        if identity in seen:
            duplicates += 1
        else:
            seen.add(identity)
            result.append(key)
    return result, duplicates


def _compute_metrics(ranking: list[tuple[str, int]], grades: dict) -> dict:
    relevant = {key for key, grade in grades.items() if grade >= 1}
    if not relevant:
        return dict.fromkeys(_METRICS)
    metrics = {
        f'Recall@{k}': len(set(ranking[:k]) & relevant) / len(relevant)
        for k in (1, 5, 10)
    }
    metrics['MRR@10'] = next((1.0 / rank for rank, key in enumerate(ranking[:10], 1)
                                  if key in relevant), 0.0)
    ideal = sorted(grades.values(), reverse=True)[:10]
    ideal_dcg = math.fsum((2 ** grade - 1) / math.log2(rank + 1)
                          for rank, grade in enumerate(ideal, 1))
    dcg = math.fsum((2 ** grades[key] - 1) / math.log2(rank + 1)
                    for rank, key in enumerate(ranking[:10], 1))
    metrics['nDCG@10'] = dcg / ideal_dcg
    return metrics


def evaluate_rankings(dataset: EvaluationSet, rankings: dict[str, list[JobKey]], *,
                      split: str, experiment: ExperimentSpec | None = None,
                      candidate_keys: list[JobKey] | None = None) -> dict:
    """Evaluate supplied order without peeking into another split.

    All selected queries require an explicit ranking (an empty list is valid).
    No query failure is silently converted to an empty result. Rows are in input
    query order; unique job order is first occurrence order. A ranker must break
    score ties by (dataset_id, job_id), because this API accepts ranks, not scores.
    """
    dataset = validate_dataset(dataset)
    queries = _split_queries(dataset, split)
    query_ids = {query.query_id for query in queries}
    if not isinstance(rankings, dict) or set(rankings) != query_ids:
        _error('ranking_query_mismatch')
    data_hash = canonical_hash(dataset)
    if experiment is not None:
        experiment = _checked(ExperimentSpec, experiment)
        if experiment.dataset_sha256 != data_hash:
            _error('dataset_hash_mismatch')
        if split == 'test' and not experiment.frozen_on_dev:
            _error('test_configuration_not_frozen')
    labels = {query.query_id: {} for query in queries}
    universe = {}
    for judgment in dataset.judgments:
        if judgment.query_id in query_ids:
            key = _identity(judgment.key)
            labels[judgment.query_id][key] = judgment
            universe[key] = judgment.key
    # v1's judgment union is an auditable declared universe, not independent
    # proof that an omitted candidate does not exist in an external corpus.
    explicit_universe = candidate_keys is not None
    if explicit_universe:
        if not isinstance(candidate_keys, list):
            _error('invalid_candidate_universe')
        checked, duplicates = _dedup_ranking(candidate_keys)
        if duplicates:
            _error('duplicate_candidate_universe')
        declared = {_identity(key): key for key in checked}
        if not set(universe) <= set(declared):
            _error('judgment_outside_candidate_universe')
        universe = declared
    universe_keys = set(universe)
    per_query = []
    for query in queries:
        ranked, duplicates = _dedup_ranking(rankings[query.query_id])
        identities = [_identity(key) for key in ranked]
        judged = labels[query.query_id]
        missing = universe_keys - set(judged)
        unknown_returned = set(identities) - universe_keys
        unjudged = {key for key, label in judged.items() if label.grade is None}
        graded = {key: label.grade for key, label in judged.items() if label.grade is not None}
        complete = bool(universe_keys) and not (missing or unjudged or unknown_returned)
        metrics = (_compute_metrics(identities, graded) if complete
                   else dict.fromkeys(_METRICS))
        relevant = {key for key, grade in graded.items() if grade >= 1}
        human = sum(label.status == 'human_judged' for label in judged.values())
        synthetic = sum(label.status == 'synthetic_expected' for label in judged.values())
        errors = []
        if missing:
            errors.append('missing_candidate_judgments')
        if unjudged or unknown_returned:
            errors.append('unjudged_candidates')
        if not universe_keys:
            errors.append('empty_candidate_universe')
        if complete and not relevant:
            errors.append('no_relevant_jobs')
        if complete and relevant:
            if not (set(identities[:10]) & relevant):
                errors.append('relevant_not_in_top10')
            elif metrics['nDCG@10'] < 1.0 - 1e-12:
                errors.append('ranking_error')
        row = {
            'query_id': query.query_id, 'profile_group': query.profile_group,
            'source': query.source, 'metrics': metrics,
            'ranking': [key.model_dump(mode='json') for key in ranked],
            'duplicates_removed': duplicates, 'complete_judgments': complete,
            'candidate_count': len(universe_keys), 'judged_count': len(graded),
            'human_judged_count': human, 'synthetic_expected_count': synthetic,
            'judgment_coverage': len(graded) / len(universe_keys) if universe_keys else None,
            'known_relevant_count': len(relevant),
            'no_relevant_jobs': complete and not relevant,
            'missing_judgments': [universe[key].model_dump(mode='json') for key in sorted(missing)],
            'unjudged_candidates': [judged[key].key.model_dump(mode='json') for key in sorted(unjudged)],
            'unjudged_returned': [key.model_dump(mode='json') for key in ranked
                                  if _identity(key) not in graded],
            'error_categories': errors,
            'judgments': [label.model_dump(mode='json') for _, label in sorted(judged.items())],
        }
        per_query.append(row)
    macro = {}
    support = {}
    for name in _METRICS:
        values = [row['metrics'][name] for row in per_query if row['metrics'][name] is not None]
        macro[name] = math.fsum(values) / len(values) if values else None
        support[name] = len(values)
    total_pairs = len(queries) * len(universe_keys)
    graded_pairs = sum(row['judged_count'] for row in per_query)
    all_complete = all(row['complete_judgments'] for row in per_query)
    all_human = all(row['human_judged_count'] == row['candidate_count'] for row in per_query)
    all_synthetic = all(row['synthetic_expected_count'] == row['candidate_count'] for row in per_query)
    return {
        'schema_version': EVAL_VERSION, 'data_version': dataset.data_version,
        'dataset_sha256': data_hash, 'split': split,
        'policy': {**MetricPolicy().model_dump(),
                   'dedup': 'first_job_occurrence',
                   'tie_order': 'ranker_supplied_dataset_id_job_id',
                   'incomplete_judgments': 'null_metrics'},
        'experiment': experiment.model_dump(mode='json') if experiment else None,
        'candidate_universe': [universe[key].model_dump(mode='json') for key in sorted(universe)],
        'candidate_universe_basis': 'explicit_snapshot' if explicit_universe else 'judgment_union',
        'candidate_universe_verified': explicit_universe,
        'candidate_universe_sha256': canonical_hash([universe[key].model_dump(mode='json') for key in sorted(universe)]),
        'query_count': len(queries), 'candidate_count': len(universe_keys),
        'judgment_coverage': graded_pairs / total_pairs if total_pairs else None,
        'complete_judgments': all_complete,
        'human_labels_complete_for_declared_universe': all_complete and all_human,
        'human_quality_ready': explicit_universe and all_complete and all_human,
        'human_quality_status': ('complete_human_judgments' if explicit_universe and all_complete and all_human else
                                 'candidate_universe_not_independently_declared' if all_complete and all_human else 'human_judgments_pending'),
        'synthetic_correctness_ready': all_complete and all_synthetic,
        'no_relevant_query_ids': [row['query_id'] for row in per_query if row['no_relevant_jobs']],
        'incomplete_query_ids': [row['query_id'] for row in per_query if not row['complete_judgments']],
        'macro': macro, 'metric_query_support': support,
        'error_counts': dict(sorted(Counter(error for row in per_query
                                           for error in row['error_categories']).items())),
        'per_query': per_query,
    }


def run_experiments(dataset: EvaluationSet, experiments: list[ExperimentSpec],
                    ranker: Callable[[EvalQuery, ExperimentSpec], list[JobKey]], *,
                    split: str = 'dev', candidate_keys: list[JobKey] | None = None) -> dict:
    """Run dev comparisons or one previously frozen held-out configuration.

    Ranker owns actual BM25/dense/hybrid execution; this runner does not create
    vectors, infer labels, tune on test, or pick a winner automatically. Failures
    propagate instead of being turned into empty results. Specs/config hashes
    are detached on every callback to prevent ranker mutation from changing an
    experiment midway through execution.
    """
    dataset = validate_dataset(dataset)
    queries = _split_queries(dataset, split)
    if not isinstance(experiments, list) or not experiments:
        _error('empty_experiments')
    specs = [_checked(ExperimentSpec, spec) for spec in experiments]
    if len({spec.experiment_id for spec in specs}) != len(specs):
        _error('duplicate_experiment')
    if split == 'test' and (len(specs) != 1 or not specs[0].frozen_on_dev):
        _error('test_requires_one_frozen_configuration')
    if not callable(ranker):
        _error('ranker_not_callable')
    data_hash = canonical_hash(dataset)
    if any(spec.dataset_sha256 != data_hash for spec in specs):
        _error('dataset_hash_mismatch')
    if any(spec.variant in {'dense','hybrid','no_chunk','chunk_best'} and spec.model_sha256 is None for spec in specs):
        _error('model_hash_required')
    reports = []
    for spec in specs:
        rankings = {}
        for query in queries:
            rankings[query.query_id] = ranker(_checked(EvalQuery, query),
                                             _checked(ExperimentSpec, spec))
        reports.append(evaluate_rankings(dataset, rankings, split=split, experiment=spec, candidate_keys=candidate_keys))
    baseline = reports[0]
    deltas = []
    for report in reports[1:]:
        per_query = []
        for reference, row in zip(baseline['per_query'], report['per_query']):
            changes = {name: (row['metrics'][name] - reference['metrics'][name]
                              if row['metrics'][name] is not None and reference['metrics'][name] is not None
                              else None) for name in _METRICS}
            per_query.append({'query_id': row['query_id'], 'metrics': changes,
                              'ranking_changed': row['ranking'] != reference['ranking'],
                              'error_categories': row['error_categories']})
        deltas.append({'baseline_experiment_id': specs[0].experiment_id,
                       'experiment_id': report['experiment']['experiment_id'],
                       'macro': {name: (report['macro'][name] - baseline['macro'][name]
                                        if report['macro'][name] is not None and baseline['macro'][name] is not None
                                        else None) for name in _METRICS},
                       'per_query': per_query})
    return {'schema_version': 'retrieval-experiments-v1', 'split': split,
            'dataset_sha256': data_hash, 'automatic_selection': False,
            'reports': reports, 'deltas_vs_first': deltas}
