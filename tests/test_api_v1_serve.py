"""Synthetic credential file, prior journal and provider; no upstream network."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys

from fastapi.testclient import TestClient
import httpx2
import pytest
from pydantic import ValidationError

from job_radar import api_v1_serve as serve
from job_radar.api_v1 import create_app
from job_radar.api_v1_billing import (
    AllowedCall, Approval, BillingJournal, PREVIOUS_SERVICE_SCOPE, SERVICE_SCOPE, SERVICE_SEED_SHA256,
    initialize, validate_billing,
)
from job_radar.api_v1_provider import build_wire
from job_radar.api_v1_runtime import ServiceSettings
from job_radar.api_v1_store import ResourceStore, ServiceError
from job_radar.rag_models import canonical_hash
from test_api_v1 import create_match, create_profile, fixtures, wait
from test_api_v1_billed_integration import (
    planned_profile, planned_report, profile_submit, provider_response, scripted_report,
    seed_resume,
)
from test_api_v1_billing import approval, reserve, resource, usage
from test_resume_structure import empty, skill

KEY = 'synthetic-local-launch-token-4911'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / 'home'
    dotdir = home / '.claude-mem'
    dotdir.mkdir(parents=True)
    monkeypatch.setattr(serve.Path, 'home', lambda: home)
    monkeypatch.delenv('OPENAI_CUSTOM_HEADERS', raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-env-decoy')
    monkeypatch.setenv('OPENAI_COMPAT_API_KEY', 'synthetic-env-decoy')
    monkeypatch.setattr(socket.socket, 'connect', lambda *_: pytest.fail('unexpected network'))
    monkeypatch.setattr(socket, 'create_connection', lambda *_: pytest.fail('unexpected network'))
    return dotdir / '.env'


def make_previous(root):
    store = ResourceStore(root / 'previous')
    store.open()
    try:
        plans = [(kind, *resource(store, kind)) for kind in ('profile', 'report')]
        ledger = initialize(store, approval(store, plans, scope=PREVIOUS_SERVICE_SCOPE))
        for plan, sample in zip(plans, (usage(2060, 492), usage(3920, 314))):
            aid = reserve(ledger, plan)
            ledger.settle(aid, sample, {})
            ledger.complete(aid)
    finally:
        store.close()
    return store.root / 'state.json'


def make_service(settings, previous, *, expiry=None):
    with TestClient(create_app(settings)) as client:
        service = client.app.state.offline_service
        rid, doc = seed_resume(service)
        allowed = [planned_profile(rid, doc)]
        signed = Approval(root=str(service.store.root), scope=SERVICE_SCOPE,
                          seed_sha256=SERVICE_SEED_SHA256, seed_refs={str(previous): digest(previous)},
                          expires_at=expiry or datetime.now(timezone.utc) + timedelta(hours=1),
                          max_calls=2, allowed=allowed)
        ledger = initialize(service.store, signed)
        assert ledger.status()['approval']['max_calls'] == 2
        return rid, ledger.status()


def transport(monkeypatch, handler):
    def factory(**options):
        assert options == {'retries': 0, 'trust_env': False}
        return httpx2.MockTransport(handler)
    monkeypatch.setattr('job_radar.api_v1_live.httpx2.HTTPTransport', factory)


@pytest.mark.parametrize('value', [KEY, f'"{KEY}" # ignored', f"'{KEY}'"])
def test_file_only_key_parser(isolated, value):
    isolated.write_text(f'OTHER=ignored\nexport OPENAI_COMPAT_API_KEY = {value}\n')
    assert serve._read_key() == KEY
    assert os.environ['OPENAI_API_KEY'] == 'synthetic-env-decoy'
    assert os.environ['OPENAI_COMPAT_API_KEY'] == 'synthetic-env-decoy'


@pytest.mark.parametrize('file', [None, 'OTHER=a', 'OPENAI_COMPAT_API_KEY=',
    'OPENAI_COMPAT_API_KEY="bad value"', 'OPENAI_COMPAT_API_KEY="unterminated',
    'OPENAI_COMPAT_API_KEY=${SHELL_VARIABLE}',
    'OPENAI_COMPAT_API_KEY=x\nOPENAI_COMPAT_API_KEY=y', 'X' * (256 * 1024 + 1)])
def test_key_parser_fails_closed_without_environment_fallback(isolated, file):
    if file is not None:
        isolated.write_text(file)
    with pytest.raises(ServiceError, match='^llm_disabled$'):
        serve._read_key()


def test_live_missing_ledger_doesnt_create_a_store_or_read_key(tmp_path, monkeypatch):
    settings = ServiceSettings(tmp_path / 'store')
    monkeypatch.setattr(serve, '_read_key', lambda: pytest.fail('credential read'))
    with pytest.raises(ServiceError, match='llm_disabled'):
        with TestClient(serve.create_live_app(settings)):
            pass
    assert not settings.store_root.exists()


def test_wrong_scope_cannot_start_live_or_read_credentials(tmp_path, monkeypatch):
    previous = make_previous(tmp_path)
    monkeypatch.setattr(serve, '_read_key', lambda: pytest.fail('credential read'))
    with pytest.raises(ServiceError, match='llm_disabled'):
        with TestClient(serve.create_live_app(ServiceSettings(previous.parent))):
            pass


def test_live_key_missing_releases_lock_without_falling_back(tmp_path):
    settings = ServiceSettings(tmp_path / 'run')
    make_service(settings, make_previous(tmp_path))
    with pytest.raises(ServiceError, match='llm_disabled'):
        with TestClient(serve.create_live_app(settings)):
            pass
    with TestClient(create_app(settings)) as client:
        assert client.get('/health').status_code == 200


def test_ambient_sdk_header_override_fails_before_reading_key(tmp_path, monkeypatch):
    settings = ServiceSettings(tmp_path / 'run')
    make_service(settings, make_previous(tmp_path))
    monkeypatch.setenv('OPENAI_CUSTOM_HEADERS', 'Authorization: Bearer synthetic-ambient-override')
    monkeypatch.setattr(serve, '_read_key', lambda: pytest.fail('credential read'))
    with pytest.raises(ServiceError, match='llm_disabled'):
        with TestClient(serve.create_live_app(settings)):
            pass


def test_seed_is_tied_to_new_scope_and_old_journal_must_match(tmp_path):
    previous = make_previous(tmp_path)
    settings = ServiceSettings(tmp_path / 'run')
    fake = tmp_path / 'wrong.json'
    fake.write_bytes(previous.read_bytes())  # canonical root no longer matches
    with pytest.raises(ServiceError, match='snapshot_conflict'):
        make_service(settings, fake)
    with TestClient(create_app(settings)) as client:
        assert 'billing' not in client.app.state.offline_service.store.state
    with pytest.raises(ValidationError):
        with TestClient(create_app(settings)) as client:
            store = client.app.state.offline_service.store
            plan = ('profile', *resource(store, 'profile'))
            approval(store, [plan], scope=SERVICE_SCOPE)  # refuses a reset to old seed


def test_cli_is_loopback_single_worker_and_does_not_read_at_construction(tmp_path, monkeypatch):
    import uvicorn
    monkeypatch.setattr(serve, '_read_key', lambda: pytest.fail('credential read'))
    called = []
    for name in ('openai', 'httpx', 'httpx2', 'httpcore', 'httpcore2'):
        logger = logging.getLogger(name)
        monkeypatch.setattr(logger, 'level', logger.level)
    monkeypatch.setattr(uvicorn, 'run', lambda app, **options: called.append(options))
    serve.main(['--store-root', str(tmp_path / 'irrelevant')])
    assert called == [dict(host='127.0.0.1', port=8000, workers=1, reload=False,
                          proxy_headers=False, lifespan='on', access_log=False, ws='none')]


def test_only_the_effective_cap_changes_the_result_at_the_budget_boundary(tmp_path):
    settings = ServiceSettings(tmp_path / 'run')
    rid, _ = make_service(settings, make_previous(tmp_path))
    with TestClient(create_app(settings)) as client:
        service = client.app.state.offline_service
        ledger = BillingJournal(service.store)
        allowed = ledger.status()['approval']['allowed'][0]
        document = service.completed(rid, 'resume')
        from job_radar.resume_models import ResumeDocument
        from job_radar.resume_structure import build_structure_request
        draft_request = build_structure_request(ResumeDocument.model_validate_json(json.dumps(document)),
                                                timeout_seconds=90)
        task, _ = service.store.create('/synthetic/profile', 'cap-boundary-s1', {'mode': 'llm'},
                                      'profile', 'llm', snapshot=document)
        aid = ledger.reserve(task['resource_id'], 'profile', allowed['context_sha256'],
                             build_wire('profile', draft_request))
        ledger.settle(aid, usage(12000, 8192), {})
        ledger.complete(aid)
        report_plan = ('report', *resource(service.store, 'report'))
        _, _, report_context, report_wire = report_plan
        ledger.authorize([AllowedCall(kind='report', context_sha256=report_context,
            payload_sha256=canonical_hash(report_wire), input_limit=20000)])
        with pytest.raises(ServiceError, match='budget_exhausted'):
            ledger.require_ready()
        before = ledger.status()
        after = ledger.raise_service_cap()
        assert {k: v for k, v in after.items() if k not in ('events', 'cap_cny')} == {
            k: v for k, v in before.items() if k not in ('events', 'cap_cny')}
        assert after['events'][:-1] == before['events']
        assert ledger.ready()
        aid = reserve(ledger, report_plan)
        ledger.settle(aid, usage(), {})
        ledger.complete(aid)
        with pytest.raises(ServiceError, match='attempt_limit'):
            ledger.require_ready()
        assert ledger.status()['new_attempts'] == 2


def test_default_offline_even_after_new_seed(tmp_path, monkeypatch):
    settings = fixtures(tmp_path)
    rid, initial = make_service(settings, make_previous(tmp_path))
    monkeypatch.setattr(serve, '_read_key', lambda: pytest.fail('credential read'))
    with TestClient(create_app(settings)) as client:
        assert profile_submit(client, rid).json()['error']['code'] == 'llm_disabled'
        _, profile = create_profile(client)
        match, _ = create_match(client, profile['resource_id'])
        response = client.post('/v1/reports', json={'match_id': match['resource_id']},
                               headers={'Idempotency-Key': 'service-offline-report'})
        assert wait(client, response.json()['data']['status_url'])['payload']['report']['status'] == 'not_run'
        assert BillingJournal(client.app.state.offline_service.store).status() == initial


@pytest.mark.parametrize('failure', [None, 'credential_echo', 'timeout'])
def test_service_two_call_path_and_fail_stop(tmp_path, monkeypatch, isolated, caplog, failure):
    settings = replace(fixtures(tmp_path), max_pending_tasks=1)
    previous = make_previous(tmp_path)
    previous_hash = digest(previous)
    rid, initial = make_service(settings, previous)
    assert initial['new_attempts'] == 0 and initial['actual_cny'] == '0.06684288'
    assert initial['retained_reserve_cny'] == '0.8029632' and initial['cap_cny'] == '1.10'
    isolated.write_text(f'OPENAI_COMPAT_API_KEY={KEY}\n')
    responses = [json.dumps(empty(skills=[skill('Python', quote='Course project: Python and FastAPI API development.')]))]
    calls = []

    def handler(request):
        assert request.headers['authorization'] == 'Bearer ' + KEY
        saved = json.loads((settings.store_root / 'state.json').read_text())['content']['billing']['totals']
        assert saved['inflight'] and saved['new_attempts'] == len(calls) + 1
        calls.append(json.loads(request.content))
        if failure == 'timeout':
            raise httpx2.ReadTimeout('synthetic timeout')
        value = provider_response(responses[len(calls) - 1])
        if failure == 'credential_echo':
            value['output'][0]['content'][0]['text'] = KEY
        return httpx2.Response(200, json=value)

    transport(monkeypatch, handler)
    with TestClient(serve.create_live_app(settings)) as client:
        service = client.app.state.offline_service
        backend = service.live_backend
        assert backend.allow_report and backend.scope == SERVICE_SCOPE and not calls
        response = profile_submit(client, rid)
        assert response.status_code == 202, response.text
        row = wait(client, response.json()['data']['status_url'])
        ledger = BillingJournal(service.store)
        after = ledger.status()
        assert len(calls) == 1 and after['new_attempts'] == 1
        assert after['retained_reserve_cny'] == '0.8559720'
        if failure:
            assert row['state'] == 'failed' and after['halted']
            assert profile_submit(client, rid, key='different-profile-key').json()['error']['code'] == 'budget_halted'
            assert len(calls) == 1
            if failure == 'credential_echo':
                assert after['actual_cny'] == '0.06898788'
                assert after['events'][1]['raw']['reason'] == 'credential_echo'
            else:
                assert Decimal(after['pending_usage_ceiling_cny']) > 0
        else:
            assert row['state'] == 'succeeded', row
            assert after['actual_cny'] == '0.06898788'
            match, _ = create_match(client, row['resource_id'])
            value, request, allowed = planned_report(match['resource_id'], match['payload'])
            responses.append(scripted_report(request))
            ledger.authorize([allowed])
            bad = client.post('/v1/reports', json={**value, 'top_n': 2},
                              headers={'Idempotency-Key': 'service-wrong-topn'})
            assert bad.json()['error']['code'] == 'core_rejected' and len(calls) == 1
            response = client.post('/v1/reports', json=value,
                                   headers={'Idempotency-Key': 'service-report-once'})
            assert response.status_code == 202, response.text
            report = wait(client, response.json()['data']['status_url'])
            assert report['state'] == 'succeeded', report
            assert report['payload']['report']['review_status'] == 'unconfirmed'
            assert len(calls) == 2 and calls[1] == build_wire('report', request)
            final = ledger.status()
            assert final['actual_cny'] == '0.07113288' and final['retained_reserve_cny'] == '0.9230208'
            assert final['new_attempts'] == 2 and not final['halted'] and final['inflight'] is None
            replay = client.post('/v1/reports', json=value,
                                 headers={'Idempotency-Key': 'service-report-once'})
            assert replay.json()['data']['resource_id'] == report['resource_id'] and len(calls) == 2
    assert backend._closed and backend._api_key is None and service.live_backend is None
    assert KEY not in (settings.store_root / 'state.json').read_text() and KEY not in caplog.text
    assert previous_hash == digest(previous)


def test_cap_upgrade_persists_without_changing_scope_history_or_call_authority(tmp_path):
    previous = make_previous(tmp_path)
    previous_hash = digest(previous)
    settings = ServiceSettings(tmp_path / 'run' / 'api-store')
    _, before = make_service(settings, previous)
    script = Path(__file__).resolve().parents[1] / 'examples/stage5_service_checkpoint.py'
    cmd = [sys.executable, '-B', str(script), 'raise-budget-cap', str(settings.store_root.parent)]
    summary = json.loads(subprocess.check_output(cmd, text=True, timeout=15))
    assert summary == {'scope': SERVICE_SCOPE, 'cap_cny': '2.00',
                       'actual_cny': before['actual_cny'], 'retained_reserve_cny': before['retained_reserve_cny'],
                       'pending_usage_ceiling_cny': before['pending_usage_ceiling_cny'],
                       'new_attempts': 0, 'max_calls': 2}
    assert json.loads(subprocess.check_output(cmd, text=True, timeout=15)) == summary
    assert digest(previous) == previous_hash
    with TestClient(create_app(settings)) as client:
        ledger = BillingJournal(client.app.state.offline_service.store)
        after = ledger.status()
        event = after.pop('events')[-1]
        assert event['type'] == 'raise_service_cap' and event['previous_cap_cny'] == '1.10'
        assert event['cap_cny'] == '2.00'
        assert after == {**{k: v for k, v in before.items() if k != 'events'}, 'cap_cny': '2.00'}


@pytest.mark.parametrize('halt', [False, True])
def test_cap_upgrade_cannot_clear_blocked_ledger(tmp_path, halt):
    settings = ServiceSettings(tmp_path / 'run')
    rid, _ = make_service(settings, make_previous(tmp_path))
    with TestClient(create_app(settings)) as client:
        service = client.app.state.offline_service
        ledger = BillingJournal(service.store)
        if halt:
            ledger.halt()
        else:
            from job_radar.resume_models import ResumeDocument
            from job_radar.resume_structure import build_structure_request
            document = service.completed(rid, 'resume')
            request = build_structure_request(ResumeDocument.model_validate_json(json.dumps(document)),
                                              timeout_seconds=90)
            task, _ = service.store.create('/synthetic/profile', 'in-flight-cap-test', {'mode': 'llm'},
                                         'profile', 'llm', snapshot=document)
            context = ledger.status()['approval']['allowed'][0]['context_sha256']
            ledger.reserve(task['resource_id'], 'profile', context, build_wire('profile', request))
        before = ledger.status()
        with pytest.raises(ServiceError, match='budget_halted'):
            ledger.raise_service_cap()
        assert ledger.status() == before and before['cap_cny'] == '1.10'


@pytest.mark.parametrize('change', ['duplicate', 'oversize', 'wrong_predecessor'])
def test_cap_event_is_validated_even_with_recomputed_hash(tmp_path, change):
    settings = ServiceSettings(tmp_path / 'run')
    make_service(settings, make_previous(tmp_path))
    with TestClient(create_app(settings)) as client:
        store = client.app.state.offline_service.store
        BillingJournal(store).raise_service_cap()
        state = deepcopy(store.state)
        events = state['billing']['events']
        target = events[-1]
        if change == 'duplicate':
            target = {**target, 'seq': target['seq'] + 1, 'prev_sha256': target['sha256']}
            events.append(target)
        else:
            target.update({'cap_cny': '20.00'} if change == 'oversize' else {'previous_cap_cny': '1.00'})
        target['sha256'] = canonical_hash({k: v for k, v in target.items() if k != 'sha256'})
        with pytest.raises(ServiceError, match='snapshot_corrupt'):
            validate_billing(state, store.root)
