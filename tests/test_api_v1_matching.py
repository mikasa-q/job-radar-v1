"""Matching-interface acceptance with independent, synthetic offline fixtures."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import time

from fastapi.testclient import TestClient
import pytest

from job_radar.api_v1 import create_app
from job_radar.api_v1_matching import MatchingInterface
from job_radar.api_v1_models import MatchCreate, Resource, MatchPayload
from job_radar.api_v1_runtime import OfflineService, ServiceSettings
from job_radar.api_v1_store import ServiceError
from job_radar.dataset_store import apply_records, dataset_snapshot
from job_radar.evidence_repository import read_job_candidates
from job_radar.matching_models import MatchingRequest
from job_radar.rag_engine import match_jobs_indexed
from job_radar.rag_index import DerivedIndex, snapshot_hash
from job_radar.rag_models import IndexManifest, canonical_hash
from job_radar.report_synthetic import synthetic_inputs
from job_radar.resume_profile_rules import build_rule_profile
from job_radar.retrieval_models import EngineConfig


DATASET = 'synthetic-stage5-b'


def prepare_settings(root):
    db = root / 'evidence.sqlite3'
    records = []
    for i, description in enumerate([
        '要求掌握 Python。Docker 加分。', '要求掌握 FastAPI。', '要求掌握 SQL。Python 加分。',
    ], 1):
        records.append({'job': {'source': 'manual', 'title': f'合成接口岗位{i}',
            'url': f'https://example.org/stage5-b/{i}', 'description': description},
            'provenance': [{'source_file': '/synthetic/b.txt', 'source_file_sha256': 'a' * 64,
                            'input_format': 'txt', 'parser_version': 'synthetic-stage5-b'}]})
    apply_records(db, records, {'run_id': 'synthetic-stage5-b', 'input_dir': '/synthetic',
        'report_path': '/synthetic/report.json', 'plan_sha256': 'b' * 64, 'failed_count': 0},
        dataset_snapshot(db)['state_sha256'])
    jobs = read_job_candidates(db, dataset_id=DATASET)
    index_root = (root / 'index').resolve()
    DerivedIndex(index_root).update(jobs, manifest=IndexManifest(
        generation='synthetic-b-g1', snapshot_sha256=snapshot_hash(jobs)))
    return ServiceSettings(store_root=root / 'store', evidence_path=db,
                           dataset_id=DATASET, index_root=index_root)


@pytest.fixture
def settings(tmp_path):
    return prepare_settings(tmp_path)


@pytest.fixture
def service(settings):
    service = OfflineService(settings)
    service.start()
    try:
        yield service
    finally:
        service.close()


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings,
                    legacy_database_path=settings.store_root.parent / 'absent-legacy.db')) as client:
        yield client


def seed_profile(service, *, text='课程项目：使用 Python 和 FastAPI 开发接口。',
                 review_status='unconfirmed', mutate=None):
    document, _ = synthetic_inputs(text=text)
    document.provenance.processed_at = datetime(2026, 11, 16, tzinfo=timezone.utc)
    resume_id = 'rsm_' + 'e' * 32
    profile = build_rule_profile(document, resume_id=resume_id)
    profile.review_status = review_status
    payload = {'resume_id': resume_id, 'document': document.model_dump(mode='json'),
               'profile': profile.model_dump(mode='json'), 'comparison': None}
    if mutate:
        mutate(payload)
    record, _ = service.store.create('/synthetic/profile', 'profile-' + str(len(service.store.state['records'])),
                                     {'synthetic': len(service.store.state['records'])}, 'profile', 'rules')
    service.store.transition(record['resource_id'], 'running')
    service.store.transition(record['resource_id'], 'succeeded', payload=payload)
    return record['resource_id']


def body(profile_id, ids=(1, 2, 3), **changes):
    value = {'profile_id': profile_id,
             'candidate_keys': [{'dataset_id': DATASET, 'job_id': i} for i in ids]}
    value.update(changes)
    return MatchCreate.model_validate(value)


def wait(service, rid):
    for _ in range(400):
        result = service.store.get(rid)
        if result['state'] not in ('queued', 'running'):
            return result
        time.sleep(.005)
    raise AssertionError('matching_task_timeout')


def wait_http(client, url):
    for _ in range(400):
        response = client.get(url)
        assert response.status_code == 200, response.text
        result = response.json()['data']
        if result['state'] not in ('queued', 'running'):
            return result
        time.sleep(.005)
    raise AssertionError('matching_http_timeout')


def capture_execution(service, monkeypatch):
    captured = {}
    def submit(route, key, value, kind, mode, prepare, execute):
        captured.update(snapshot=prepare(), execute=execute, value=value,
                        route=route, kind=kind, mode=mode)
        return captured
    monkeypatch.setattr(service, 'submit', submit)
    return captured


def test_matching_is_shared_runtime_mixin(service):
    assert isinstance(service, MatchingInterface)
    assert service.ready() == {'status': 'ready', 'evidence': True, 'index': True,
                               'real_llm': False, 'hybrid': False}


def test_scoped_order_config_hash_and_core_equivalence(service, settings):
    before = settings.evidence_path.read_bytes()
    profile_id = seed_profile(service)
    request = body(profile_id, ids=(3, 1), retrieval={'top_k': 1, 'k1': 2.0,
                                                  'title_weight': 0.5, 'rrf_k': 40})
    accepted = service.match(request, 'match-core-equivalent')
    result = wait(service, accepted['resource_id'])
    assert result['state'] == 'succeeded', result
    payload = result['payload']
    Resource[MatchPayload].model_validate_json(json.dumps(result, ensure_ascii=False))
    assert [j['job_id'] for j in payload['request']['jobs']] == [3, 1]
    assert payload['engine_config'] == request.retrieval.model_dump(mode='json')
    direct = match_jobs_indexed(
        MatchingRequest.model_validate_json(json.dumps(payload['request'], ensure_ascii=False)),
        index=DerivedIndex(settings.index_root), config=request.retrieval)
    assert direct.model_dump(mode='json') == payload['retrieval']
    assert all(row['key']['job_id'] in (3, 1)
               for row in payload['retrieval']['matching']['results'])
    assert payload['snapshot_sha256'] == canonical_hash({k: payload[k] for k in (
        'document', 'request', 'index_manifest', 'engine_config')})
    assert payload['request']['profile']['review_status'] == 'unconfirmed'
    assert all(j['review_status'] == 'unconfirmed' for j in payload['request']['jobs'])
    assert settings.evidence_path.read_bytes() == before


def test_http_listing_detail_matching_and_frozen_operation_ids(client, settings):
    service = client.app.state.offline_service
    profile_id = seed_profile(service)
    before = settings.evidence_path.read_bytes()
    page = client.get('/v1/jobs?limit=1&offset=1')
    assert page.status_code == 200
    assert page.json()['data']['total'] == 3
    assert [j['job_id'] for j in page.json()['data']['items']] == [2]
    assert page.headers['cache-control'] == 'no-store'
    assert page.headers['x-request-id'] == page.json()['request_id']
    detail = client.get(f'/v1/jobs/{DATASET}/2')
    assert detail.status_code == 200 and detail.json()['data'] == page.json()['data']['items'][0]
    assert client.get('/v1/jobs/another-dataset/2').status_code == 404
    assert client.get('/v1/jobs?limit=0').status_code == 422
    assert client.get('/v1/jobs?limit=101').status_code == 422
    assert client.get('/v1/jobs?offset=-1').status_code == 422
    response = client.post('/v1/matches', json=body(profile_id, ids=(2,)).model_dump(mode='json'),
                           headers={'Idempotency-Key': 'http-match-key'})
    assert response.status_code == 202, response.text
    row = wait_http(client, response.json()['data']['status_url'])
    assert row['state'] == 'succeeded'
    spec = client.get('/openapi.json').json()['paths']
    assert spec['/v1/jobs']['get']['operationId'] == 'jobs_v1_jobs_get'
    assert spec['/v1/jobs/{dataset_id}/{job_id}']['get']['operationId'] == 'job_v1_jobs__dataset_id___job_id__get'
    assert spec['/v1/matches']['post']['operationId'] == 'match_v1_matches_post'
    assert spec['/v1/matches/{match_id}']['get']['operationId'] == 'get_match_v1_matches__match_id__get'
    assert settings.evidence_path.read_bytes() == before
    assert not (settings.store_root.parent / 'absent-legacy.db').exists()


def test_get_match_rejects_other_kind_resource(client):
    profile_id = seed_profile(client.app.state.offline_service)
    response = client.get('/v1/matches/' + profile_id)
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'resource_not_found'


@pytest.mark.parametrize('ids,text,expected', [
    ((), '课程项目：使用 Python。', 'no_candidates'),
    ((1,), '教育经历：某大学本科。', 'no_query'),
    ((1,), '课程项目：使用 Redis。', 'no_hits'),
])
def test_empty_business_states_are_successful(service, ids, text, expected):
    profile_id = seed_profile(service, text=text)
    accepted = service.match(body(profile_id, ids=ids), 'empty-state-key')
    row = wait(service, accepted['resource_id'])
    assert row['state'] == 'succeeded', row
    assert row['payload']['retrieval']['matching']['status'] == expected
    assert row['payload']['retrieval']['matching']['results'] == []


def test_unknown_candidate_does_not_create_task(service):
    profile_id = seed_profile(service)
    count = len(service.store.state['records'])
    with pytest.raises(ServiceError, match='resource_not_found'):
        service.match(body(profile_id, ids=(999,)), 'unknown-candidate')
    assert len(service.store.state['records']) == count
    assert service.store.lookup('/v1/matches', 'unknown-candidate',
                                body(profile_id, ids=(999,)).model_dump(mode='json')) is None


def test_composite_candidate_identity_is_required(service):
    profile_id = seed_profile(service)
    value = body(profile_id).model_dump(mode='json')
    value['candidate_keys'][0]['dataset_id'] = 'another-dataset'
    with pytest.raises(ServiceError, match='resource_not_found'):
        service.match(MatchCreate.model_validate(value), 'composite-key-test')


@pytest.mark.parametrize('profile_status,ids', [('unconfirmed', ()), ('confirmed', (1,))])
def test_original_formal_gate_remains_409(service, profile_status, ids):
    profile_id = seed_profile(service, review_status=profile_status)
    with pytest.raises(ServiceError, match='formal_requires_confirmed'):
        service.match(body(profile_id, ids=ids, purpose='formal'), 'formal-gate-test')


def test_confirmed_formal_input_is_preserved_not_promoted(service, monkeypatch):
    profile_id = seed_profile(service, review_status='confirmed')
    jobs = service.jobs()
    for candidate in jobs:
        candidate.review_status = 'confirmed'
    DerivedIndex(service.settings.index_root).update(jobs, manifest=IndexManifest(
        generation='synthetic-confirmed', snapshot_sha256=snapshot_hash(jobs)))
    monkeypatch.setattr(service, 'jobs', lambda: jobs)
    row = wait(service, service.match(body(profile_id, purpose='formal'),
                                      'formal-confirmed')['resource_id'])
    assert row['state'] == 'succeeded', row
    assert row['payload']['request']['purpose'] == 'formal'
    assert row['payload']['request']['profile']['review_status'] == 'confirmed'
    assert all(j['review_status'] == 'confirmed' for j in row['payload']['request']['jobs'])


@pytest.mark.parametrize('mutation', [
    lambda p: p['profile']['source'].update(source_sha256='f' * 64),
    lambda p: p['profile'].update(resume_id='rsm_' + 'a' * 32),
    lambda p: p['document']['pages'][0]['blocks'][0].update(text='其他原文。'),
    lambda p: p['document'].update(pages=[]),
    lambda p: p['profile']['skills'][0]['mention']['evidence'][0].update(block_id='p001-b009'),
])
def test_profile_evidence_boundary_rejects_corrupt_snapshot(service, mutation):
    profile_id = seed_profile(service, mutate=mutation)
    with pytest.raises(ServiceError, match='snapshot_corrupt'):
        service.match(body(profile_id), 'bad-profile-snapshot')


def test_replay_precedes_parent_dataset_index_reads_and_survives_restart(service, settings, monkeypatch):
    profile_id = seed_profile(service)
    request = body(profile_id, ids=(3, 1))
    first = wait(service, service.match(request, 'replay-matching-key')['resource_id'])
    settings.evidence_path.unlink()
    (settings.index_root / 'CURRENT').unlink()
    service.close()
    service.start()
    def forbidden(*args, **kwargs):
        raise AssertionError('replay_read_dependency')
    monkeypatch.setattr(service, 'completed', forbidden)
    monkeypatch.setattr(service, 'jobs', forbidden)
    monkeypatch.setattr(service, 'index', forbidden)
    assert service.match(request, 'replay-matching-key') == first
    changed = body(profile_id, ids=(1,))
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        service.match(changed, 'replay-matching-key')


@pytest.mark.parametrize('mode_fallback', [False, True])
def test_hybrid_never_falls_back_or_reads_dependencies(service, monkeypatch, mode_fallback):
    def forbidden(*args, **kwargs):
        raise AssertionError('hybrid_dependency_read')
    monkeypatch.setattr(service, 'submit', forbidden)
    request = body('prf_' + 'a' * 32, ids=(), retrieval={
        'mode': 'hybrid', 'allow_dense_fallback': mode_fallback})
    with pytest.raises(ServiceError, match='hybrid_not_enabled'):
        service.match(request, 'hybrid-disabled-key')


@pytest.mark.parametrize('dependency,expected', [('dataset', 'dataset_unavailable'),
                                               ('index', 'index_unavailable')])
def test_dependency_corruption_has_fixed_error_and_not_ready(service, settings, dependency, expected):
    profile_id = seed_profile(service)
    if dependency == 'dataset':
        settings.evidence_path.write_bytes(b'synthetic corrupt database')
    else:
        (settings.index_root / 'CURRENT').write_text('not-json')
    assert service.ready()['status'] == 'not_ready'
    with pytest.raises(ServiceError, match=expected):
        service.match(body(profile_id), 'corrupt-dependency-key')


def test_unconfigured_paths_use_fixed_errors(service):
    service.settings = replace(service.settings, evidence_path=None, index_root=None)
    with pytest.raises(ServiceError, match='dataset_unavailable'):
        service.jobs()
    with pytest.raises(ServiceError, match='index_unavailable'):
        service.index()
    assert service.ready()['status'] == 'not_ready'


def test_generation_drift_between_accept_and_execute_is_conflict(service, monkeypatch):
    profile_id = seed_profile(service)
    captured = capture_execution(service, monkeypatch)
    service.match(body(profile_id), 'generation-drift-key')
    old = deepcopy(captured['snapshot'])
    jobs = service.jobs()
    # Incremental update intentionally reuses a byte-identical generation.
    # Build an independent generation, then switch the configured directory.
    replacement = service.settings.index_root.parent / 'replacement-index'
    DerivedIndex(replacement).update(jobs, manifest=IndexManifest(
        generation='synthetic-b-g2', snapshot_sha256=snapshot_hash(jobs)))
    service.settings.index_root.rename(service.settings.index_root.parent / 'old-index')
    replacement.rename(service.settings.index_root)
    with pytest.raises(ServiceError, match='snapshot_conflict'):
        captured['execute'](captured['snapshot'])
    assert captured['snapshot'] == old


def test_request_mutation_after_submission_cannot_change_snapshot(service, monkeypatch):
    profile_id = seed_profile(service)
    captured = capture_execution(service, monkeypatch)
    request = body(profile_id, ids=(3, 1), retrieval={'top_k': 1})
    service.match(request, 'immutable-request-key')
    original = deepcopy(captured['snapshot'])
    request.retrieval.top_k = 3
    request.candidate_keys.clear()
    request.preferences.cities = ['深圳']
    payload = captured['execute'](captured['snapshot'])
    assert captured['snapshot'] == original
    assert [j['job_id'] for j in payload['request']['jobs']] == [3, 1]
    assert payload['engine_config']['top_k'] == 1
    assert payload['request']['profile']['preferences']['cities'] is None


def test_core_limit_failure_is_task_error_not_index_fallback(service):
    profile_id = seed_profile(service)
    accepted = service.match(body(profile_id, retrieval={'max_jobs': 1}), 'core-limit-failure')
    row = wait(service, accepted['resource_id'])
    assert row['state'] == 'failed', row
    assert row['error']['code'] == 'core_rejected'
    assert row['payload'] is None
    assert service.match(body(profile_id, retrieval={'max_jobs': 1}),
                         'core-limit-failure')['resource_id'] == row['resource_id']


def test_index_changed_after_prepare_is_not_accepted_as_success(service, monkeypatch):
    profile_id = seed_profile(service)
    captured = capture_execution(service, monkeypatch)
    service.match(body(profile_id), 'index-corrupt-execution')
    (service.settings.index_root / 'CURRENT').write_text('{}')
    with pytest.raises(ServiceError, match='index_unavailable'):
        captured['execute'](captured['snapshot'])


def test_invalid_keys_duplicates_and_untrusted_snapshot_fields_are_http_errors(client):
    profile_id = seed_profile(client.app.state.offline_service)
    value = body(profile_id).model_dump(mode='json')
    value['candidate_keys'].append(value['candidate_keys'][0])
    assert client.post('/v1/matches', json=value,
                       headers={'Idempotency-Key': 'duplicates-key'}).status_code == 422
    for extra in ('jobs', 'index_root', 'retrieval_result', 'document'):
        value = body(profile_id).model_dump(mode='json')
        value[extra] = '/synthetic/not-opened'
        response = client.post('/v1/matches', json=value,
                               headers={'Idempotency-Key': 'untrusted-fields-key'})
        assert response.status_code == 422
        assert '/synthetic/not-opened' not in response.text
        assert extra not in response.json()['error']['fields']
    response = client.post('/v1/matches', json=body(profile_id).model_dump(mode='json'))
    assert response.json()['error']['code'] == 'idempotency_key_required'


def test_missing_parent_or_pending_parent_never_launches_matching(service):
    with pytest.raises(ServiceError, match='resource_not_found'):
        service.match(body('prf_' + 'a' * 32), 'missing-parent-key')
    record, _ = service.store.create('/synthetic/pending', 'pending-profile', {}, 'profile', 'rules')
    with pytest.raises(ServiceError, match='snapshot_conflict'):
        service.match(body(record['resource_id']), 'pending-parent-key')


def test_unrelated_formal_assembly_error_is_not_mislabeled(service, monkeypatch):
    profile_id = seed_profile(service, review_status='confirmed')
    def reject(*args, **kwargs):
        raise ValueError('synthetic_core_validation')
    monkeypatch.setattr('job_radar.api_v1_matching.build_matching_input', reject)
    with pytest.raises(ServiceError, match='core_rejected'):
        service.match(body(profile_id, ids=(), purpose='formal'), 'formal-other-failure')


def test_stale_candidate_evidence_is_not_silently_retrieved(service, monkeypatch):
    profile_id = seed_profile(service)
    jobs = service.jobs()
    jobs[0].review_status = 'rejected'
    monkeypatch.setattr(service, 'jobs', lambda: jobs)
    with pytest.raises(ServiceError, match='index_unavailable'):
        service.match(body(profile_id, ids=(1,)), 'stale-candidate-key')
