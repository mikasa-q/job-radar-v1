"""Stage 3 evaluation tests use authored rankings, never fake embeddings."""
from __future__ import annotations
import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError
from job_radar.rag_models import (
    EvaluationSet, EvalQuery, EvalJudgment, ExperimentSpec, canonical_hash,
)
from job_radar.rag_evaluation import evaluate_rankings, run_experiments, validate_dataset
from job_radar.retrieval_models import JobKey, RetrievalError


def key(i, dataset='synthetic-dev'):
    return JobKey(dataset_id=dataset, job_id=i)


def query(i='q1', split='dev', source='synthetic', profile_group=None):
    return EvalQuery(query_id=i, text='Python 后端工程师', profile_group=profile_group or i,
                     split=split, source=source)


def label(i, grade, *, q='q1', status='synthetic_expected', dataset='synthetic-dev', **updates):
    values = dict(query_id=q, key=key(i, dataset), job_group=f'{dataset}-{i}',
                  grade=grade, status=status, evidence=[f'synthetic-job-{i}:paragraph-1'])
    if status == 'human_judged':
        values['assessor'] = 'reviewer-A'
    values.update(updates)
    return EvalJudgment(**values)


def data(grades=(2, 1, 0), **updates):
    values = dict(data_version='synthetic-v1', queries=[query()],
                  judgments=[label(i, grade) for i, grade in enumerate(grades, 1)])
    values.update(updates)
    return EvaluationSet(**values)


def evaluate(dataset=None, order=(1, 2, 3), **kwargs):
    return evaluate_rankings(data() if dataset is None else dataset,
                              {'q1': [key(i) for i in order]}, split='dev', **kwargs)


def spec(dataset, identifier='base', *, frozen=False, variant='stage2_default', **updates):
    values = dict(experiment_id=identifier, config_sha256='1'*64,
                  dataset_sha256=canonical_hash(dataset), index_sha256='2'*64,
                  variant=variant, frozen_on_dev=frozen)
    values.update(updates)
    return ExperimentSpec(**values)


def test_metrics_hand_computed_with_graded_gain_discount_and_duplicate_removal():
    result = evaluate(order=(3, 2, 2, 1))
    row = result['per_query'][0]
    assert row['duplicates_removed'] == 1
    assert row['metrics']['Recall@1'] == 0.0
    assert row['metrics']['Recall@5'] == row['metrics']['Recall@10'] == 1.0
    assert row['metrics']['MRR@10'] == 0.5
    expected = (1 / math.log2(3) + 3 / math.log2(4)) / (3 + 1 / math.log2(3))
    assert row['metrics']['nDCG@10'] == pytest.approx(expected)
    assert len(row['ranking']) == 3
    assert result['macro'] == row['metrics']
    assert result['metric_query_support'] == dict.fromkeys(row['metrics'], 1)


@pytest.mark.parametrize('grades,order,recall,mrr', [
    ((2, 1, 0), (1, 2, 3), 0.5, 1.0),
    ((0, 2, 0), (3, 2, 1), 0.0, 0.5),
    ((2, 1, 0), (), 0.0, 0.0),
    ((1,), (1, 1, 1), 1.0, 1.0),
])
def test_recall_threshold_and_mrr(grades, order, recall, mrr):
    row = evaluate(data(grades), order)['per_query'][0]
    assert row['metrics']['Recall@1'] == recall
    assert row['metrics']['MRR@10'] == mrr
    assert row['complete_judgments']


def test_cutoffs_apply_after_job_deduplication():
    dataset = data((0,)*10 + (2,))
    order = tuple(range(1, 11)) + (1, 11)
    row = evaluate(dataset, order)['per_query'][0]
    assert row['metrics']['Recall@10'] == row['metrics']['MRR@10'] == row['metrics']['nDCG@10'] == 0.0
    assert 'relevant_not_in_top10' in row['error_categories']


def test_rank_ten_contributes_to_mrr_ndcg():
    dataset = data((0,)*9 + (2,))
    row = evaluate(dataset, tuple(range(1, 11)))['per_query'][0]
    assert row['metrics']['Recall@10'] == 1.0
    assert row['metrics']['MRR@10'] == 0.1
    assert row['metrics']['nDCG@10'] == pytest.approx(1 / math.log2(11))


def test_empty_relevant_queries_are_null_and_separately_reported():
    dataset = data((0, 0, 0))
    result = evaluate(dataset)
    assert result['no_relevant_query_ids'] == ['q1']
    assert result['macro'] == dict.fromkeys(result['macro'])
    assert result['metric_query_support'] == dict.fromkeys(result['macro'], 0)
    assert result['per_query'][0]['no_relevant_jobs'] is True
    assert result['complete_judgments'] is True
    assert result['error_counts'] == {'no_relevant_jobs': 1}


def test_unjudged_not_negative_even_when_ranked_first():
    dataset = data(judgments=[label(1, 2), label(2, None, status='unjudged')])
    result = evaluate(dataset, (2, 1))
    assert result['judgment_coverage'] == 0.5
    assert result['per_query'][0]['metrics'] == dict.fromkeys(result['macro'])
    assert result['per_query'][0]['unjudged_returned'] == [key(2).model_dump()]
    assert result['incomplete_query_ids'] == ['q1']
    assert result['no_relevant_query_ids'] == []
    assert result['human_quality_ready'] is False
    assert result['synthetic_correctness_ready'] is False


def test_missing_candidate_judgment_in_matrix_is_reported_and_null():
    dataset = data(queries=[query(), query('q2')], judgments=[label(1, 2), label(2, 0), label(1, 2, q='q2')])
    result = evaluate_rankings(dataset, {'q1': [key(1), key(2)], 'q2': [key(1)]}, split='dev')
    assert result['per_query'][0]['metrics']['Recall@1'] == 1.0
    assert result['per_query'][1]['metrics']['Recall@1'] is None
    assert result['per_query'][1]['missing_judgments'] == [key(2).model_dump()]
    assert result['judgment_coverage'] == 0.75
    assert result['metric_query_support']['Recall@1'] == 1
    assert result['complete_judgments'] is False


def test_undeclared_returned_job_cannot_be_negative():
    result = evaluate(order=(99, 1, 2, 3))
    assert result['complete_judgments'] is False
    assert result['per_query'][0]['unjudged_returned'] == [key(99).model_dump()]
    assert all(value is None for value in result['macro'].values())


def test_empty_judgment_universe_is_not_empty_relevant_success():
    result = evaluate(data(judgments=[]), ())
    assert result['candidate_count'] == 0
    assert result['judgment_coverage'] is None
    assert result['complete_judgments'] is False
    assert result['synthetic_correctness_ready'] is False
    assert result['no_relevant_query_ids'] == []
    assert result['error_counts'] == {'empty_candidate_universe': 1}


def test_synthetic_sample_never_claims_human_quality():
    result = evaluate()
    assert result['synthetic_correctness_ready'] is True
    assert result['human_quality_ready'] is False
    assert result['human_quality_status'] == 'human_judgments_pending'
    assert result['per_query'][0]['source'] == 'synthetic'


def test_human_labels_on_synthetic_text_preserve_origin_and_audit_record():
    dataset = data(judgments=[label(1, 2, status='human_judged', review_note='reviewed grade 1→2'),
                             label(2, 1, status='human_judged'), label(3, 0, status='human_judged')])
    result = evaluate(dataset)
    assert result['human_labels_complete_for_declared_universe']
    assert result['human_quality_ready'] is False  # no independently declared candidate list in frozen v1
    assert result['human_quality_status'] == 'candidate_universe_not_independently_declared'
    row = result['per_query'][0]
    assert row['source'] == 'synthetic'
    assert row['judgments'][0]['review_note'] == 'reviewed grade 1→2'
    assert row['judgments'][0]['assessor'] == 'reviewer-A'
    assert row['judgments'][0]['evidence'] == ['synthetic-job-1:paragraph-1']


def test_multiple_queries_macro_denominator_and_no_relevant_separate():
    dataset = data(queries=[query(), query('q2'), query('q3')], judgments=[
        label(1, 2), label(2, 0), label(1, 0, q='q2'), label(2, 2, q='q2'),
        label(1, 0, q='q3'), label(2, 0, q='q3')])
    result = evaluate_rankings(dataset, {'q1': [key(1), key(2)], 'q2': [key(1), key(2)],
                                         'q3': []}, split='dev')
    assert result['macro']['Recall@1'] == 0.5
    assert result['macro']['MRR@10'] == 0.75
    assert result['no_relevant_query_ids'] == ['q3']
    assert result['metric_query_support']['MRR@10'] == 2


def test_ranking_order_preserved_with_stable_tie_contract():
    result = evaluate(order=(2, 1, 3))
    assert result['per_query'][0]['ranking'] == [key(2).model_dump(), key(1).model_dump(), key(3).model_dump()]
    assert result['policy']['tie_order'] == 'ranker_supplied_dataset_id_job_id'


@pytest.mark.parametrize('dataset,code', [
    (lambda: data(queries=[query(), query()]), 'duplicate_query'),
    (lambda: data(judgments=[label(1, 1), label(1, 2)]), 'duplicate_judgment'),
    (lambda: data(judgments=[label(1, 1, q='unknown')]), 'unknown_query_reference'),
    (lambda: data(queries=[query(), query('q2', 'test', profile_group='q1')]), 'profile_group_leakage'),
    (lambda: data(queries=[query(), query('q2', 'test')], judgments=[label(1, 2), label(1, 0, q='q2')]), 'job_group_leakage'),
    (lambda: data(queries=[query(), query('q2')], judgments=[label(1, 2), label(1, 0, q='q2', job_group='other')]), 'inconsistent_job_group'),
])
def test_dataset_validation_rejects_ambiguity_leakage(dataset, code):
    with pytest.raises(RetrievalError, match='eval_' + code):
        validate_dataset(dataset())


def test_near_duplicate_job_groups_cannot_cross_split_even_distinct_ids():
    dataset = data(queries=[query(), query('q2', 'test')], judgments=[
        label(1, 1, job_group='near-duplicate'),
        label(9, 1, q='q2', dataset='synthetic-test', job_group='near-duplicate')])
    with pytest.raises(RetrievalError, match='eval_job_group_leakage'):
        validate_dataset(dataset)


def test_distinct_groups_and_keys_allowed_across_splits():
    dataset = data(queries=[query(), query('heldout', 'test')], judgments=[label(1, 2),
            label(2, 1, q='heldout', dataset='synthetic-test')])
    assert validate_dataset(dataset) == dataset
    result = evaluate_rankings(dataset, {'heldout': [key(2, 'synthetic-test')]}, split='test')
    assert result['candidate_count'] == 1
    assert result['per_query'][0]['metrics']['Recall@1'] == 1.0


@pytest.mark.parametrize('updates', [
    dict(grade=None, status='synthetic_expected'), dict(grade=1, status='unjudged'),
    dict(grade=3), dict(grade=-1), dict(grade=True), dict(grade='1'),
    dict(status='human_judged', assessor=None), dict(status='human_judged', assessor=' '),
])
def test_judgment_strict_schema(updates):
    with pytest.raises(ValidationError):
        label(1, 2, **updates) if 'grade' not in updates else EvalJudgment(**{
            **label(1, 2).model_dump(), **updates})


def test_dataset_and_rankings_are_detached_and_mutations_revalidated():
    dataset = data()
    snapshot = dataset.model_dump_json()
    ranked = [key(1), key(2), key(3)]
    result = evaluate_rankings(dataset, {'q1': ranked}, split='dev')
    result['per_query'][0]['judgments'][0]['evidence'].append('changed')
    result['per_query'][0]['ranking'][0]['job_id'] = 90
    assert dataset.model_dump_json() == snapshot
    assert ranked[0].job_id == 1
    tampered = dataset.model_copy(update={'extra': 'bad'})
    with pytest.raises(ValidationError):
        validate_dataset(tampered)
    tampered = key(1).model_copy(update={'job_id': 0})
    with pytest.raises(ValidationError):
        evaluate_rankings(dataset, {'q1': [tampered]}, split='dev')


@pytest.mark.parametrize('rankings', [[], {'unknown': []}, {}, {'q1': [], 'q2': []}])
def test_ranking_query_set_exact(rankings):
    with pytest.raises(RetrievalError, match='eval_ranking_query_mismatch'):
        evaluate_rankings(data(), rankings, split='dev')


@pytest.mark.parametrize('order', [None, (key(1),), [dict(dataset_id='x', job_id=1)], [1], ['x']])
def test_bad_ranking_shape(order):
    with pytest.raises(RetrievalError, match='eval_invalid_ranking'):
        evaluate_rankings(data(), {'q1': order}, split='dev')


@pytest.mark.parametrize('split,code', [('train', 'invalid_split'), ('test', 'empty_split')])
def test_split_errors(split, code):
    with pytest.raises(RetrievalError, match='eval_' + code):
        evaluate_rankings(data(), {'q1': []}, split=split)


def test_experiment_hash_bound_to_exact_dataset():
    dataset = data()
    good = spec(dataset)
    result = evaluate(experiment=good)
    assert result['experiment']['config_sha256'] == '1'*64
    assert result['dataset_sha256'] == canonical_hash(dataset)
    bad = good.model_copy(update={'dataset_sha256': '9'*64})
    with pytest.raises(RetrievalError, match='eval_dataset_hash_mismatch'):
        evaluate(experiment=bad)


def test_experiment_runner_compares_authored_rankings_no_automatic_selection():
    dataset = data()
    specs = [spec(dataset), spec(dataset, 'tuned', variant='tuned_bm25')]
    def ranker(query, experiment):
        assert query.query_id == 'q1'
        return [key(i) for i in ((3, 2, 1) if experiment.experiment_id == 'base' else (1, 2, 3))]
    result = run_experiments(dataset, specs, ranker)
    assert len(result['reports']) == 2
    assert result['automatic_selection'] is False
    delta = result['deltas_vs_first'][0]
    assert delta['macro']['Recall@1'] == 0.5
    assert delta['macro']['MRR@10'] == 0.5
    assert delta['per_query'][0]['ranking_changed'] is True
    assert delta['per_query'][0]['error_categories'] == []


def test_runner_detaches_query_and_spec_each_call():
    dataset = data(queries=[query(), query('q2')], judgments=[label(1, 2), label(1, 2, q='q2')])
    experiment = spec(dataset)
    before = dataset.model_dump_json(), experiment.model_dump_json()
    def ranker(q, s):
        q.text = 'changed'
        s.config_sha256 = '9'*64
        return [key(1)]
    result = run_experiments(dataset, [experiment], ranker)
    assert (dataset.model_dump_json(), experiment.model_dump_json()) == before
    assert result['reports'][0]['experiment']['config_sha256'] == '1'*64


def test_ranker_failures_are_not_fabricated_empty_results():
    dataset = data()
    def ranker(*args):
        raise RuntimeError('controlled failure')
    with pytest.raises(RuntimeError, match='controlled failure'):
        run_experiments(dataset, [spec(dataset)], ranker)


@pytest.mark.parametrize('make_specs,code', [
    (lambda d: [], 'empty_experiments'),
    (lambda d: [spec(d), spec(d)], 'duplicate_experiment'),
    (lambda d: [spec(d, dataset_sha256='9'*64)], 'dataset_hash_mismatch'),
])
def test_runner_preflight_before_ranker(make_specs, code):
    calls = []
    with pytest.raises(RetrievalError, match='eval_' + code):
        run_experiments(data(), make_specs(data()), lambda *args: calls.append(args))
    assert calls == []


def test_heldout_requires_one_frozen_configuration_and_never_tunes():
    dataset = data(queries=[query('q1', 'test')])
    def ranker(*args):
        return [key(1), key(2), key(3)]
    with pytest.raises(RetrievalError, match='eval_test_requires_one_frozen_configuration'):
        run_experiments(dataset, [spec(dataset)], ranker, split='test')
    with pytest.raises(RetrievalError, match='eval_test_requires_one_frozen_configuration'):
        run_experiments(dataset, [spec(dataset, frozen=True), spec(dataset, 'other', frozen=True)],
                        ranker, split='test')
    result = run_experiments(dataset, [spec(dataset, frozen=True)], ranker, split='test')
    assert len(result['reports']) == 1
    assert result['deltas_vs_first'] == []
    assert result['automatic_selection'] is False
    with pytest.raises(RetrievalError, match='eval_test_configuration_not_frozen'):
        evaluate_rankings(dataset, {'q1': ranker()}, split='test', experiment=spec(dataset))


def test_non_callable_ranker_rejected():
    dataset = data()
    with pytest.raises(RetrievalError, match='eval_ranker_not_callable'):
        run_experiments(dataset, [spec(dataset)], None)


def test_synthetic_fixture_roundtrip_and_each_split_metrics():
    path = Path(__file__).resolve().parents[1] / 'examples/stage3_evaluation.synthetic.json'
    dataset = EvaluationSet.model_validate_json(path.read_text())
    validate_dataset(dataset)
    assert all(query.source == 'synthetic' for query in dataset.queries)
    assert all(j.status == 'synthetic_expected' for j in dataset.judgments)
    assert dataset == EvaluationSet.model_validate_json(dataset.model_dump_json())
    for split in ('dev', 'test'):
        rankings = {}
        for q in dataset.queries:
            if q.split == split:
                labels = [j for j in dataset.judgments if j.query_id == q.query_id]
                rankings[q.query_id] = [j.key for j in sorted(labels,
                                    key=lambda j: (-j.grade, j.key.dataset_id, j.key.job_id))]
        result = evaluate_rankings(dataset, rankings, split=split)
        assert result['synthetic_correctness_ready']
        assert not result['human_quality_ready']
        assert result['macro']['nDCG@10'] == 1.0
        json.dumps(result, allow_nan=False)


def test_explicit_candidate_universe_enables_complete_human_quality():
    dataset=data(queries=[query(source='human')],judgments=[label(i,g,status='human_judged') for i,g in enumerate([2,1,0],1)])
    report=evaluate_rankings(dataset,{'q1':[key(1),key(2),key(3)]},split='dev',candidate_keys=[key(1),key(2),key(3)])
    assert report['human_quality_ready'] and report['candidate_universe_verified']
    assert len(report['candidate_universe_sha256'])==64
    missing=evaluate_rankings(dataset,{'q1':[key(1)]},split='dev',candidate_keys=[key(1),key(2),key(3),key(4)])
    assert not missing['human_quality_ready'] and missing['macro']['Recall@1'] is None


def test_explicit_universe_duplicates_and_outside_rejected():
    with pytest.raises(RetrievalError,match='duplicate_candidate_universe'):
        evaluate_rankings(data(),{'q1':[key(1)]},split='dev',candidate_keys=[key(1),key(1)])
    with pytest.raises(RetrievalError,match='judgment_outside_candidate_universe'):
        evaluate_rankings(data(),{'q1':[key(1)]},split='dev',candidate_keys=[key(1)])


def test_dense_experiments_require_model_hash():
    dataset=data()
    with pytest.raises(RetrievalError,match='model_hash_required'):
        run_experiments(dataset,[spec(dataset,variant='dense')],lambda q,s:[key(1)])


def test_experiment_runner_forwards_explicit_universe():
    dataset=data(queries=[query(source='human')],judgments=[label(i,g,status='human_judged') for i,g in enumerate([2,1,0],1)])
    result=run_experiments(dataset,[spec(dataset)],lambda q,s:[key(1)],candidate_keys=[key(1),key(2),key(3)])
    assert result['reports'][0]['human_quality_ready']
