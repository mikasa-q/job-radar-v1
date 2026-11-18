"""Deterministic runner/metric checks. No embedding model or fake vectors."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_radar.rag_evaluation import evaluate_rankings
from job_radar.rag_models import EvaluationSet, canonical_hash
from job_radar.retrieval_models import JobKey, RetrievalError

spec = importlib.util.spec_from_file_location('manual_evaluate',
    Path(__file__).resolve().parents[1] / 'examples/stage3_manual_evaluate.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def data():
    queries = [{'query_id': qid, 'text': text, 'profile_group': qid,
                'source': 'human', 'split': split}
               for split, qid, text in [('dev', 'dev-python', 'Python 后端接口开发'),
                                        ('dev', 'dev-data', 'SQL 数据分析'),
                                        ('test', 'test-mobile', 'Swift iOS 界面开发')]]
    labels = [{'query_id': q['query_id'], 'key': {'dataset_id': 'jobradar-v1', 'job_id': i},
               'job_group': 'job-'+str(i),
               'grade': (2 if i == 1 else 1 if i == 2 else 0) if q['split'] == 'dev' else 0,
               'status': 'human_judged', 'assessor': 'unit-test'}
              for q in queries for i in range(1, 16)]
    return EvaluationSet(data_version='manual-v1', queries=queries, judgments=labels)


@pytest.fixture
def keys():
    return [JobKey(dataset_id='jobradar-v1', job_id=i) for i in range(1, 16)]


def test_split_views_do_not_mutate_original(data, keys):
    before = canonical_hash(data)
    views, audit = runner.check_inputs(data, keys)
    assert len(views['dev'].judgments) == 30
    assert len(views['test'].judgments) == 15
    assert canonical_hash(data) == before
    assert audit['full_dataset_validation_errors'] == ['eval_job_group_leakage']
    assert len(audit['shared_job_groups']) == 15
    assert not audit['job_group_independent_holdout']
    assert not audit['original_fields_modified']


def test_null_no_relevant_and_no_zero_imputation(data, keys):
    views, _ = runner.check_inputs(data, keys)
    test = evaluate_rankings(views['test'], {'test-mobile': keys[:10]},
                             split='test', candidate_keys=keys)
    row = test['per_query'][0]
    assert row['metrics'] == dict.fromkeys(runner.METRICS)
    assert row['error_categories'] == ['no_relevant_jobs']
    assert row['no_relevant_jobs'] and row['complete_judgments']
    assert test['macro'] == dict.fromkeys(runner.METRICS)
    assert test['metric_query_support'] == dict.fromkeys(runner.METRICS, 0)
    assert test['candidate_universe_verified']


def test_ranking_error_preserved(data, keys):
    view = runner.split_view(data, 'dev')
    # Positive jobs are found but lower grades precede grade 2.
    order = [keys[1], keys[0], *keys[2:10]]
    report = evaluate_rankings(view, {qid: order for qid in runner.EXPECTED_SPLITS['dev']},
                               split='dev', candidate_keys=keys)
    for row in report['per_query']:
        assert row['error_categories'] == ['ranking_error']
        assert row['metrics']['nDCG@10'] < 1.0
        assert row['metrics']['MRR@10'] == 1.0
        assert row['metrics']['Recall@10'] == 1.0


def test_macro_excludes_null_not_zero():
    rows = [{'metrics': dict.fromkeys(runner.METRICS, v)} for v in (0.5, 1.0, None)]
    macro, support = runner.macro_metrics(rows)
    assert macro == dict.fromkeys(runner.METRICS, 0.75)
    assert support == dict.fromkeys(runner.METRICS, 2)
    assert runner.macro_metrics(rows[2:]) == (dict.fromkeys(runner.METRICS),
                                           dict.fromkeys(runner.METRICS, 0))


@pytest.mark.parametrize('mutation', ['unjudged', 'missing', 'wrong_split', 'duplicate_candidate',
                                    'inconsistent_group', 'profile_leakage'])
def test_malformed_input_rejected(data, keys, mutation):
    if mutation == 'unjudged':
        data.judgments[0] = data.judgments[0].model_copy(update={'grade': None, 'status': 'unjudged'})
    elif mutation == 'missing':
        data.judgments.pop()
    elif mutation == 'wrong_split':
        data.queries[0].split = 'test'
    elif mutation == 'duplicate_candidate':
        keys[-1] = keys[0]
    elif mutation == 'inconsistent_group':
        data.judgments[-1].job_group = 'wrong-group'
    elif mutation == 'profile_leakage':
        data.queries[-1].profile_group = data.queries[0].profile_group
    with pytest.raises((ValueError, RetrievalError)):
        runner.check_inputs(data, keys)


def test_no_overwrite_existing_report(tmp_path):
    annotations = tmp_path/'annotations.json'
    db = tmp_path/'evidence.sqlite3'
    output = tmp_path/'report.json'
    for path in (annotations, db, output):
        path.write_text('preserve')
    args = SimpleNamespace(annotations=annotations, database=db, output=output, index=tmp_path/'index')
    with pytest.raises(FileExistsError, match='report_exists'):
        runner.run(args)
    assert all(path.read_text() == 'preserve' for path in (annotations, db, output))
    assert not args.index.exists()


def test_no_reuse_nonempty_index(tmp_path):
    annotations, db = tmp_path/'annotations.json', tmp_path/'evidence.sqlite3'
    for path in (annotations, db):
        path.write_text('preserve')
    index = tmp_path/'index'; index.mkdir(); marker = index/'unrelated.txt';marker.write_text('preserve')
    args = SimpleNamespace(annotations=annotations, database=db, output=tmp_path/'report.json', index=index)
    with pytest.raises(FileExistsError, match='index_must_be_new_or_empty'):
        runner.run(args)
    assert marker.read_text() == 'preserve' and not args.output.exists()


def test_snapshot_hash_validation(tmp_path):
    snapshot = tmp_path/runner.REVISION;snapshot.mkdir()
    payload = snapshot/'config.json';payload.write_text('{}')
    manifest = {'model_id': runner.MODEL_ID, 'revision': runner.REVISION,
                'files': [{'name': 'config.json', 'bytes': 2, 'sha256': runner.sha256(payload)}]}
    path = tmp_path/'manifest.json';path.write_text(json.dumps(manifest))
    assert runner.verify_snapshot(snapshot, path)['verified_file_count'] == 1
    payload.write_text('[]')
    with pytest.raises(ValueError, match='model_file_hash_mismatch'):
        runner.verify_snapshot(snapshot, path)
