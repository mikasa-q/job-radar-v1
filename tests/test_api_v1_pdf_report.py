"""One public upload with existing PDF limits, queue, report and live approvals."""
from copy import deepcopy
from dataclasses import replace
import json

import httpx2
import pytest
from fastapi.testclient import TestClient

from job_radar.api_v1 import create_app, MAX_MULTIPART, MAX_UPLOAD
from job_radar.api_v1_live import LiveBackend
from job_radar.api_v1_pdf_report import PDF_REPORT_ROUTE
from job_radar.api_v1_provider import MockBackend, build_wire
from job_radar.api_v1_reports import _match_snapshot
from job_radar.rag_models import canonical_hash
from job_radar.report_analysis import validate_analysis
from job_radar.report_core import parse_analysis
from job_radar.report_evidence import build_evidence_packet
from job_radar.report_models import AnalysisConfig
from test_api_v1 import fixtures, pdf, wait
from test_api_v1_billed_integration import approve, planned_report, provider_response


def submit(client, raw, *, key=None, mode=None, mt='application/pdf'):
    return client.post(PDF_REPORT_ROUTE, files={'file': ('resume.pdf', raw, mt)},
                       headers={'Idempotency-Key': key} if key is not None else {},
                       params={'mode': mode} if mode is not None else {})


def test_pdf_only_one_request_returns_markdown_and_replays_after_restart(tmp_path, monkeypatch):
    # With a single shared worker/slot, composing the stages must not deadlock.
    settings = replace(fixtures(tmp_path), max_pending_tasks=1)
    before = settings.evidence_path.read_bytes()
    raw = pdf()
    monkeypatch.setattr('job_radar.api_v1_runtime.OfflineService.billing_backend',
                        lambda _: pytest.fail('provider requested in default offline flow'))
    with TestClient(create_app(settings)) as client:
        response = submit(client, raw)
        assert response.status_code == 201, response.text
        assert '未调用 LLM' in response.text and '待人工复核' in response.text
        assert response.headers['content-type'].startswith('text/markdown')
        assert response.headers['content-disposition'].startswith('attachment;')
        assert response.headers['cache-control'] == 'no-store'
        key, url = response.headers['idempotency-key'], response.headers['location']
        row = client.get(url).json()['data']
        report = row['payload']['report']
        assert row['state'] == 'succeeded' and row['payload']['markdown'] == response.text
        assert (report['status'], report['review_status']) == ('not_run', 'unconfirmed')
        store = client.app.state.offline_service.store
        assert [r['resource']['kind'] for r in store.state['records'].values()] == ['resume', 'profile', 'match', 'report']
        match = store.get(row['payload']['match_id'])['payload']
        assert report['packet']['retrieval'] == match['retrieval']
        assert [job['job_id'] for job in match['request']['jobs']] == [1, 2, 3]
        assert 'billing' not in store.state
        assert submit(client, pdf('SQL'), key=key).json()['error']['code'] == 'idempotency_conflict'
        persisted = deepcopy(store.state)
    assert settings.evidence_path.read_bytes() == before
    settings.evidence_path.unlink()  # replay uses already committed snapshots
    with TestClient(create_app(settings)) as client:
        replay = submit(client, raw, key=key)
        assert replay.status_code == 201 and replay.text == response.text
        assert replay.headers['location'] == url
        assert client.app.state.offline_service.store.state == persisted


@pytest.mark.parametrize('what,status,code', [
    ('wrong mode', 422, 'invalid_request'), ('text part', 415, 'unsupported_media_type'),
    ('no file', 415, 'unsupported_media_type'), ('extra part', 422, 'invalid_request'),
    ('empty', 422, 'pdf_empty_pdf'), ('overlong file', 413, 'payload_too_large'),
    ('oversized body', 413, 'payload_too_large'), ('invalid key', 422, 'idempotency_key_invalid'),
])
def test_rejects_invalid_upload_before_creating_any_resource(tmp_path, what, status, code):
    settings = replace(fixtures(tmp_path), max_pending_tasks=1)
    with TestClient(create_app(settings)) as client:
        if what == 'wrong mode':
            response = submit(client, b'test', mode='hybrid')
        elif what == 'text part':
            response = submit(client, b'test', mt='text/plain')
        elif what == 'no file':
            response = client.post(PDF_REPORT_ROUTE, json={})
        elif what == 'extra part':
            response = client.post(PDF_REPORT_ROUTE, data={'unexpected': 'x'},
                                   files={'file': ('r.pdf', b'test', 'application/pdf')})
        elif what == 'empty':
            response = submit(client, b'')
        elif what == 'overlong file':
            response = submit(client, b'0' * (MAX_UPLOAD + 1))
        elif what == 'oversized body':
            response = client.post(PDF_REPORT_ROUTE, content=b'0' * (MAX_MULTIPART + 1))
        else:
            response = submit(client, b'test', key='bad')
        assert response.status_code == status and response.json()['error']['code'] == code
        assert not client.app.state.offline_service.store.state['records']


def test_bad_pdf_stops_before_profile_and_remains_failed_on_replay(tmp_path):
    settings = fixtures(tmp_path)
    with TestClient(create_app(settings)) as client:
        for _ in range(2):
            response = submit(client, b'not a PDF', key='bad-pdf-same-key')
            assert response.status_code == 422 and response.json()['error']['code'] == 'pdf_invalid_pdf'
        records = list(client.app.state.offline_service.store.state['records'].values())
        assert len(records) == 1 and records[0]['resource']['kind'] == 'resume'


@pytest.mark.parametrize('backend', [None, 'without_report_permission'])
def test_llm_is_explicit_and_rejected_before_extraction_without_report_backend(tmp_path, monkeypatch, backend):
    settings = fixtures(tmp_path)
    actual = None if backend is None else LiveBackend(settings.store_root, api_key='fake-key', allow_report=False)
    monkeypatch.setattr('job_radar.api_v1_resumes.extract_isolated', lambda *a: pytest.fail('unexpected extraction'))
    with TestClient(create_app(settings, live_backend=actual)) as client:
        response = submit(client, b'fake', mode='llm')
        assert response.status_code == 503 and response.json()['error']['code'] == 'llm_disabled'
        assert not client.app.state.offline_service.store.state['records']


@pytest.mark.parametrize('binding', ['exact', 'different_wire', 'different_context', 'timeout'])
def test_llm_sends_only_one_approved_top1_report_and_replay_never_resends(tmp_path, binding):
    settings = replace(fixtures(tmp_path), max_pending_tasks=1)
    calls = []
    responses = []

    def handler(request):
        calls.append(json.loads(request.content))
        if binding == 'timeout':
            raise httpx2.ReadTimeout('synthetic timeout')
        return httpx2.Response(200, json=provider_response(responses[0]))

    raw, key = pdf('Course project: used FastAPI to build an API.'), 'approved-pdf-job-report'
    backend = MockBackend(httpx2.MockTransport(handler))
    with TestClient(create_app(settings, mock_backend=backend)) as client:
        offline = submit(client, raw, key=key)
        assert offline.status_code == 201, offline.text
        saved = client.get(offline.headers['location']).json()['data']['payload']
        service = client.app.state.offline_service
        missing = submit(client, raw, key=key, mode='llm')
        assert missing.json()['error']['code'] == 'llm_disabled' and not calls
        matches = [x['resource'] for x in service.store.state['records'].values() if x['resource']['kind'] == 'match']
        assert len(matches) == 2
        match = next(row for row in matches if row['resource_id'] != saved['match_id'])
        match_id = match['resource_id']
        snapshot = match['payload']
        assert snapshot['retrieval']['matching']['results'] == saved['report']['packet']['retrieval']['matching']['results'][:1]
        _, request, allowed = planned_report(match_id, snapshot)
        if binding == 'different_wire':
            allowed['payload_sha256'] = '0' * 64
        if binding == 'different_context':
            allowed['context_sha256'] = '0' * 64
        ledger = approve(service, [allowed])
        saved_match = _match_snapshot(snapshot)
        packet = build_evidence_packet(saved_match.request, saved_match.retrieval, saved_match.document,
                                       config=AnalysisConfig(top_n=1))
        selected = packet.selected_keys[0]
        rule = next(s for s in packet.retrieval.matching.results[0].rules.skills
                    if s.kind == 'required' and 'FastAPI' in s.matched)
        references = [next(e.evidence_id for e in packet.evidence if e.key == selected and e.job_span in rule.job_evidence),
                      next(e.evidence_id for e in packet.evidence if e.resume_ref in rule.resume_evidence),
                      next(e.evidence_id for e in packet.evidence if e.key == selected and e.kind == 'rule'
                           and json.loads(e.value_json) == rule.model_dump(mode='json'))]
        text = json.dumps({'key': selected.model_dump(), 'claims': [{'claim_id': 'c1', 'kind': 'strength',
            'subject': 'FastAPI', 'status': 'supported', 'text': '课程项目使用 FastAPI 开发接口。',
            'requirement_kind': 'required', 'evidence_ids': references}]}, ensure_ascii=False)
        validate_analysis(parse_analysis(text), packet, selected)
        responses.append(text)
        result = submit(client, raw, key=key, mode='llm')
        if binding in ('different_wire', 'different_context'):
            assert result.status_code == 422 and result.json()['error']['code'] == 'core_rejected', result.text
            assert ledger.status()['new_attempts'] == 0 and not calls
            assert len(service.store.state['records']) == 5
            return
        assert result.status_code == 202, result.text
        accepted = result.json()['data']
        assert accepted['status_url'] == result.headers['location']
        final = wait(client, accepted['status_url'])
        if binding == 'timeout':
            assert final['state'] == 'failed' and final['payload'] is None
            confirmed = ledger.status()
            assert confirmed['halted'] and confirmed['new_attempts'] == 1
            again = submit(client, raw, key=key, mode='llm')
            assert again.status_code == 202 and again.json()['data']['state'] == 'failed'
            assert ledger.status() == confirmed and len(calls) == 1
            return
        assert final['state'] == 'succeeded' and final['payload']['match_id'] == match_id, final
        assert final['payload']['report']['status'] == 'completed'
        assert '本报告使用模拟响应' in final['payload']['markdown']
        assert len(final['payload']['report']['packet']['selected_keys']) == 1
        assert calls == [build_wire('report', request)]
        assert canonical_hash(calls[0]) == allowed['payload_sha256']
        after = ledger.status()
        assert after['new_attempts'] == 1 and after['inflight'] is None
        assert all(attempt['kind'] == 'report' for attempt in after['attempts'].values())
        again = submit(client, raw, key=key, mode='llm')
        assert again.status_code == 202 and again.json()['data']['resource_id'] == final['resource_id']
        assert ledger.status() == after and len(calls) == 1 and len(service.store.state['records']) == 6


def test_missing_approval_never_falls_back_from_explicit_llm(tmp_path):
    settings = fixtures(tmp_path)
    backend = MockBackend(httpx2.MockTransport(lambda _: pytest.fail('outbound send without approval')))
    with TestClient(create_app(settings, mock_backend=backend)) as client:
        response = submit(client, pdf(), mode='llm')
        assert response.status_code == 503 and response.json()['error']['code'] == 'llm_disabled'
        state = client.app.state.offline_service.store.state
        assert 'billing' not in state and all(e['resource']['kind'] != 'report' for e in state['records'].values())


def test_openapi_shows_a_single_pdf_and_both_response_modes(tmp_path):
    spec = create_app(fixtures(tmp_path)).openapi()['paths'][PDF_REPORT_ROUTE]['post']
    body = spec['requestBody']['content']['multipart/form-data']['schema']
    assert body['required'] == ['file'] and body['properties']['file']['format'] == 'binary'
    assert 'text/markdown' in spec['responses']['201']['content']
    assert 'application/json' in spec['responses']['202']['content']
    assert all(not parameter['required'] for parameter in spec['parameters'])
