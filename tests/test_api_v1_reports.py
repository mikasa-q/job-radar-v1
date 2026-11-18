"""C adapter coverage: synthetic snapshots and scripted clients, no live LLM."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from job_radar.api_v1_models import (
    Accepted, ApiError, Envelope, ERROR_HTTP, ReportCreate, ReportPayload, Resource)
from job_radar.api_v1_reports import ReportInterface, register_routes
from job_radar.api_v1_store import ResourceStore, ServiceError
from job_radar.rag_engine import match_jobs_indexed
from job_radar.rag_index import DerivedIndex
from job_radar.rag_models import canonical_hash
from job_radar.report_demo_client import SyntheticReportClient
from job_radar.report_engine import generate_match_report
from job_radar.report_models import AnalysisConfig, MatchReport, ReportError
from job_radar.report_render import render_match_report, validate_report
from job_radar.report_synthetic import synthetic_inputs, synthetic_manifest


def snapshot_case(root, **kwargs):
    document, request = synthetic_inputs(**kwargs)
    index = DerivedIndex(root / 'index')
    index.update(request.jobs, manifest=synthetic_manifest(request.jobs))
    result = match_jobs_indexed(request, index=index)
    preimage = {
        'document': document.model_dump(mode='json'),
        'request': request.model_dump(mode='json'),
        'index_manifest': result.manifest.model_dump(mode='json'),
        'engine_config': result.matching.config.model_dump(mode='json'),
    }
    return {**preimage, 'retrieval': result.model_dump(mode='json'),
            'snapshot_sha256': canonical_hash(preimage)}


def rehash(snapshot):
    snapshot['snapshot_sha256'] = canonical_hash({name: snapshot[name] for name in (
        'document', 'request', 'index_manifest', 'engine_config')})


class ReportHarness(ReportInterface):
    """Only a task driver; production ResourceStore retains its real invariants."""

    def __init__(self, root):
        self.store = ResourceStore(root / 'store')
        self.store.open()
        self.jobs = []
        self.parent_reads = 0
        self.live_attempts = 0

    def seed_match(self, payload, *, state='succeeded'):
        row, _ = self.store.create('/fixture/matches', 'fixture-' + uuid.uuid4().hex,
                                   {}, 'match', 'bm25_rules')
        if state != 'queued':
            self.store.transition(row['resource_id'], 'running')
            if state == 'succeeded':
                self.store.transition(row['resource_id'], state, payload=payload)
            elif state == 'failed':
                self.store.transition(row['resource_id'], state, code='core_rejected')
        return row['resource_id']

    def completed(self, rid, kind):
        self.parent_reads += 1
        row = self.store.get(rid)
        if row['kind'] != kind:
            raise ServiceError('resource_not_found')
        if row['state'] != 'succeeded':
            raise ServiceError('snapshot_conflict')
        return row['payload']

    def require_live(self):
        self.live_attempts += 1
        raise ServiceError('llm_disabled')

    def submit(self, route, key, value, kind, mode, prepare, execute):
        found = self.store.lookup(route, key, value)
        if found:
            return found
        snapshot = prepare()
        row, _ = self.store.create(route, key, value, kind, mode, snapshot=snapshot)
        self.jobs.append((row['resource_id'], deepcopy(snapshot), execute))
        return row

    def run(self):
        rid, snapshot, execute = self.jobs.pop(0)
        self.store.transition(rid, 'running')
        try:
            self.store.transition(rid, 'succeeded', payload=execute(snapshot))
        except ServiceError as error:
            self.store.transition(rid, 'failed', code=error.code)
        except ValueError:
            self.store.transition(rid, 'failed', code='core_rejected')
        return self.store.get(rid)


@pytest.fixture
def case(tmp_path):
    service = ReportHarness(tmp_path)
    saved = snapshot_case(tmp_path)
    match_id = service.seed_match(saved)
    try:
        yield service, saved, match_id
    finally:
        service.store.close()


def http_client(service):
    app = FastAPI()

    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'req_' + 'a' * 32
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers['Cache-Control'] = 'no-store'
        return response

    def wrap(request, data=None, error=None):
        return Envelope(request_id=request.state.request_id, data=data,
                        error=error).model_dump(mode='json')

    @app.exception_handler(ServiceError)
    async def failure(request: Request, error: ServiceError):
        return JSONResponse(status_code=ERROR_HTTP[error.code],
                            content=wrap(request, error=ApiError(code=error.code)))

    def typed(value, model):
        return model.model_validate_json(json.dumps(value, ensure_ascii=False))

    def accepted(row):
        return Accepted(resource_id=row['resource_id'], state=row['state'],
                        status_url='/v1/reports/' + row['resource_id'])

    register_routes(app, service, wrap=wrap, typed=typed, accepted=accepted, responses={})
    return TestClient(app)


def test_report_routes_preserve_openapi_identity(case):
    service, _, _ = case
    with http_client(service) as client:
        actual = client.get('/openapi.json').json()
    frozen = json.loads((Path(__file__).parents[1] / 'docs/STAGE5_OPENAPI.json').read_text())
    for path, method in [('/v1/reports', 'post'), ('/v1/reports/{report_id}', 'get')]:
        assert actual['paths'][path][method]['operationId'] == frozen['paths'][path][method]['operationId']
        assert actual['paths'][path][method]['parameters'] == frozen['paths'][path][method]['parameters']
    assert actual['paths']['/v1/reports']['post']['requestBody'] == frozen['paths']['/v1/reports']['post']['requestBody']


def test_offline_report_preserves_snapshot_and_no_provider(case, monkeypatch):
    service, snapshot, match_id = case
    import job_radar.report_apinebula as provider

    def forbidden(*args, **kwargs):
        raise AssertionError('unexpected_provider_or_socket')

    monkeypatch.setattr(provider, 'APINebulaAnalysisClient', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    before = deepcopy(snapshot)
    accepted = service.report(ReportCreate(match_id=match_id, top_n=2), 'report-0001')
    assert accepted['kind'] == 'report' and accepted['execution_mode'] == 'offline'
    assert accepted['state'] == 'queued' and accepted['payload'] is None
    row = service.run()
    assert row['state'] == 'succeeded'
    payload = ReportPayload.model_validate_json(json.dumps(row['payload'], ensure_ascii=False))
    report = payload.report
    assert report.status == 'not_run' and report.review_status == 'unconfirmed'
    assert all(a.status == 'not_analyzed' and a.draft is None and a.audit is None
               for a in report.analyses)
    assert report.packet.retrieval.model_dump(mode='json') == snapshot['retrieval']
    assert len(report.packet.selected_keys) == 2
    assert payload.snapshot_sha256 == snapshot['snapshot_sha256']
    assert payload.match_id == match_id
    assert snapshot == before
    assert '本报告未调用 LLM；分析状态为 not_run。' in payload.markdown
    assert '预设响应只验证工程链路' not in payload.markdown
    assert service.live_attempts == 0


def test_get_json_and_markdown_do_not_regenerate(case, monkeypatch):
    service, _, match_id = case
    with http_client(service) as client:
        response = client.post('/v1/reports', json={'match_id': match_id},
                               headers={'Idempotency-Key': 'report-0001'})
        assert response.status_code == 202
        url = response.json()['data']['status_url']
        row = service.run()

        def forbidden(*args, **kwargs):
            raise AssertionError('get_must_not_regenerate')

        import job_radar.api_v1_reports as adapter
        monkeypatch.setattr(adapter, 'generate_match_report', forbidden)
        monkeypatch.setattr(service, 'completed', forbidden)
        fetched = client.get(url)
        assert fetched.status_code == 200
        expected = Resource[ReportPayload].model_validate_json(json.dumps(row, ensure_ascii=False))
        assert fetched.json()['data'] == expected.model_dump(mode='json')
        for _ in range(2):
            markdown = client.get(url + '?format=markdown')
            assert markdown.status_code == 200
            assert markdown.text == row['payload']['markdown']
            assert markdown.headers['content-type'].startswith('text/markdown')
            assert markdown.headers['content-disposition'] == f'attachment; filename="{row["resource_id"]}.md"'
            assert markdown.headers['cache-control'] == 'no-store'
        assert not service.jobs


@pytest.mark.parametrize('parent_state', ['queued', 'running', 'failed'])
def test_incomplete_parent_rejected_without_new_resource(case, parent_state):
    service, snapshot, _ = case
    match_id = service.seed_match(snapshot, state=parent_state)
    count = len(service.store.state['records'])
    with pytest.raises(ServiceError, match='^snapshot_conflict$'):
        service.report(ReportCreate(match_id=match_id), 'pending-parent')
    assert len(service.store.state['records']) == count and not service.jobs


@pytest.mark.parametrize('state', ['queued', 'running', 'failed', 'interrupted'])
def test_markdown_requires_succeeded_report(case, state):
    service, _, match_id = case
    row = service.report(ReportCreate(match_id=match_id), 'pending-markdown')
    rid = row['resource_id']
    if state in ('running', 'failed'):
        service.store.transition(rid, 'running')
    if state == 'failed':
        service.store.transition(rid, 'failed', code='core_rejected')
    if state == 'interrupted':
        service.store.transition(rid, 'interrupted', code='task_interrupted')
    with http_client(service) as client:
        response = client.get('/v1/reports/' + rid + '?format=markdown')
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'snapshot_conflict'
        assert client.get('/v1/reports/' + rid).json()['data']['state'] == state


def test_unknown_or_wrong_kind_is_not_a_report(case):
    service, _, match_id = case
    with http_client(service) as client:
        for rid in [match_id, 'rpt_' + 'f' * 32, 'not-a-resource']:
            response = client.get('/v1/reports/' + rid)
            assert response.status_code == 404
            assert response.json()['error']['code'] == 'resource_not_found'
        assert client.get('/v1/reports/' + match_id + '?format=html').status_code == 422


@pytest.mark.parametrize('key,code', [(None, 'idempotency_key_required'),
                                     ('tiny', 'idempotency_key_invalid')])
def test_report_key_validation_precedes_parent(case, key, code):
    service, _, match_id = case
    with pytest.raises(ServiceError, match='^' + code + '$'):
        service.report(ReportCreate(match_id=match_id), key)
    assert service.parent_reads == 0 and not service.jobs


def test_idempotent_report_replay_ignores_missing_parent(case, monkeypatch):
    service, _, match_id = case
    body = ReportCreate(match_id=match_id)
    initial = service.report(body, 'report-replay')
    result = service.run()
    reads = service.parent_reads

    def missing_parent(*args, **kwargs):
        raise AssertionError('replay_must_not_read_parent')

    monkeypatch.setattr(service, 'completed', missing_parent)
    repeated = service.report(body, 'report-replay')
    assert repeated == result and repeated['resource_id'] == initial['resource_id']
    assert service.parent_reads == reads and not service.jobs
    with pytest.raises(ServiceError, match='^idempotency_conflict$'):
        service.report(ReportCreate(match_id=match_id, top_n=1), 'report-replay')


def test_failed_report_replays_and_never_publishes_partial_payload(case, monkeypatch):
    service, _, match_id = case
    import job_radar.api_v1_reports as adapter

    def rejected(*args, **kwargs):
        raise ValueError('private input and hidden exception body')

    monkeypatch.setattr(adapter, 'generate_match_report', rejected)
    body = ReportCreate(match_id=match_id)
    service.report(body, 'failed-report')
    failed = service.run()
    assert failed['state'] == 'failed'
    assert failed['payload'] is None and failed['payload_sha256'] is None
    assert failed['error']['code'] == 'core_rejected'
    assert 'private input' not in json.dumps(failed)
    assert service.report(body, 'failed-report') == failed
    assert not service.jobs


@pytest.mark.parametrize('change', [
    'hash', 'document', 'manifest', 'config', 'retrieval_manifest', 'retrieval_config',
    'missing', 'invalid', 'nonfinite'])
def test_snapshot_binding_tampering_rejected_before_generation(case, change, monkeypatch):
    service, saved, _ = case
    changed = deepcopy(saved)
    if change == 'hash':
        changed['snapshot_sha256'] = '0' * 64
    elif change == 'document':
        changed['document']['pages'][0]['blocks'][0]['text'] = 'Different private evidence'
    elif change == 'manifest':
        changed['index_manifest']['generation'] = 'different-generation'
        rehash(changed)
    elif change == 'config':
        changed['engine_config']['top_k'] = 1
        rehash(changed)
    elif change == 'retrieval_manifest':
        changed['retrieval']['manifest']['generation'] = 'different-generation'
    elif change == 'retrieval_config':
        changed['retrieval']['matching']['config']['top_k'] = 1
    elif change == 'missing':
        del changed['document']
    elif change == 'invalid':
        changed['request']['purpose'] = 'private-invalid-mode'
    else:
        changed['retrieval']['matching']['results'][0]['fusion_score'] = float('nan')
    # Exercise prepare independent of storage's additional journal integrity gate.
    monkeypatch.setattr(service, 'completed', lambda *args: changed)
    count = len(service.store.state['records'])
    with pytest.raises(ServiceError, match='^snapshot_conflict$'):
        service.report(ReportCreate(match_id='mtc_' + 'a' * 32), 'tampered-report')
    assert len(service.store.state['records']) == count and not service.jobs


def test_queued_request_and_parent_mutation_do_not_change_report(case):
    service, snapshot, match_id = case
    body = ReportCreate(match_id=match_id, top_n=1)
    service.report(body, 'frozen-report')
    body.top_n = 3
    body.match_id = 'mtc_' + 'f' * 32
    snapshot['document']['pages'][0]['blocks'][0]['text'] = 'later mutation'
    row = service.run()
    assert row['state'] == 'succeeded'
    assert row['payload']['match_id'] == match_id
    assert len(row['payload']['report']['packet']['selected_keys']) == 1


def test_execution_rechecks_snapshot_binding(case):
    service, _, match_id = case
    service.report(ReportCreate(match_id=match_id), 'execute-corrupt')
    service.jobs[0][1]['snapshot_sha256'] = '0' * 64
    row = service.run()
    assert row['state'] == 'failed' and row['error']['code'] == 'snapshot_conflict'
    assert row['payload'] is None


def test_original_core_rejects_changed_retrieval_rank_without_partial_success(case, monkeypatch):
    service, snapshot, match_id = case
    corrupted = deepcopy(snapshot)
    corrupted['retrieval']['matching']['results'][0]['rank'] = 99
    monkeypatch.setattr(service, 'completed', lambda *args: corrupted)
    service.report(ReportCreate(match_id=match_id), 'invalid-ranking')
    row = service.run()
    assert row['state'] == 'failed' and row['error']['code'] == 'core_rejected'
    assert row['payload'] is None and row['payload_sha256'] is None


@pytest.mark.parametrize('field,value', [('top_n', 21), ('mode', 'scripted'),
                                        ('match_id', 'not-a-match')])
def test_mutated_direct_request_is_revalidated(case, field, value):
    service, _, match_id = case
    body = ReportCreate(match_id=match_id)
    body.__dict__[field] = value
    with pytest.raises(ServiceError, match='^core_rejected$'):
        service.report(body, 'invalid-direct')
    assert service.parent_reads == 0 and not service.jobs


def test_llm_mode_stays_closed_before_parent_and_keys(case, monkeypatch):
    service, _, match_id = case
    monkeypatch.setenv('JOBRADAR_LLM_ENABLED', 'true')
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-not-real')
    with http_client(service) as client:
        response = client.post('/v1/reports', json={'match_id': match_id, 'mode': 'llm'})
        assert response.status_code == 503 and response.json()['error']['code'] == 'llm_disabled'
    assert service.live_attempts == 1
    assert service.parent_reads == 0 and not service.jobs


def test_report_mode_never_opens_after_miswired_gate(case, monkeypatch):
    service, _, match_id = case
    monkeypatch.setattr(service, 'require_live', lambda: None)
    with pytest.raises(ServiceError, match='^llm_disabled$'):
        service.report(ReportCreate(match_id=match_id, mode='llm'), 'still-disabled')
    assert service.parent_reads == 0 and not service.jobs


@pytest.mark.parametrize('kwargs,expected', [
    ({'text': '没有相关技能信息。'}, 'no_query'),
    ({'descriptions': []}, 'no_candidates'),
    ({'descriptions': ['要求使用 Rust。']}, 'no_hits')])
def test_empty_paths_remain_offline_business_results(tmp_path, kwargs, expected):
    service = ReportHarness(tmp_path)
    try:
        snapshot = snapshot_case(tmp_path, **kwargs)
        match_id = service.seed_match(snapshot)
        service.report(ReportCreate(match_id=match_id), 'empty-report')
        row = service.run()
        assert row['state'] == 'succeeded'
        report = row['payload']['report']
        assert report['packet']['retrieval']['matching']['status'] == expected
        assert report['status'] == 'not_run' and report['analyses'] == []
        assert service.live_attempts == 0
    finally:
        service.store.close()


def test_scripted_partial_failure_preserves_rankings_and_stops(case):
    _, snapshot, _ = case
    from job_radar.api_v1_reports import _match_snapshot
    saved = _match_snapshot(snapshot)

    class SecondFails(SyntheticReportClient):
        halted = None

        def complete(self, request):
            if self.calls == 1:
                self.calls += 1
                raise TimeoutError('private synthetic error text')
            return super().complete(request)

        def halt(self, code):
            self.halted = code

    client = SecondFails()
    report = generate_match_report(saved.request, saved.retrieval, saved.document,
        report_id='synthetic-partial', generated_at=datetime.now(timezone.utc), client=client)
    assert report.status == 'partial' and client.calls == 2
    assert client.halted == 'analysis_validation_failed'
    assert [a.status for a in report.analyses] == ['completed', 'failed', 'not_analyzed']
    assert report.analyses[1].draft is None
    assert report.packet.retrieval.model_dump(mode='json') == snapshot['retrieval']
    assert 'private synthetic' not in report.model_dump_json()


@pytest.mark.parametrize('version', ['match-analysis-prompt-v1', 'match-analysis-prompt-v2'])
def test_historical_scripted_report_keeps_version_and_rejects_bad_citations(case, version):
    _, snapshot, _ = case
    from job_radar.api_v1_reports import _match_snapshot
    saved = _match_snapshot(snapshot)
    report = generate_match_report(saved.request, saved.retrieval, saved.document,
        report_id='synthetic-history', generated_at=datetime.now(timezone.utc),
        client=SyntheticReportClient(), config=AnalysisConfig(top_n=1))
    value = report.model_dump(mode='json')
    value['prompt_version'] = version
    historical = MatchReport.model_validate_json(json.dumps(value, ensure_ascii=False))
    before = historical.model_dump_json()
    assert version in render_match_report(historical)
    assert '历史只读，不代表已通过v3' in render_match_report(historical)
    assert historical.model_dump_json() == before
    with pytest.raises(ReportError, match='^analysis_validation_policy_mismatch$'):
        validate_report(historical, require_current_policy=True)
    historical.analyses[0].draft.claims[0].evidence_ids = ['e' + 'f' * 24]
    with pytest.raises(ReportError, match='^analysis_evidence_scope$'):
        render_match_report(historical)


@pytest.mark.parametrize('mode', ['valid', 'invalid_json', 'timeout', 'unknown_usage'])
def test_mock_transport_never_calls_network_and_halts_failures(case, monkeypatch, mode):
    import httpx2
    from job_radar.api_v1_reports import _match_snapshot
    from job_radar.report_analysis import SYSTEM_PROMPT
    from job_radar.report_apinebula import APINebulaAnalysisClient, MODEL
    from job_radar.report_models import AnalysisRequest, MatchAnalysisDraft, PROMPT_VERSION
    from job_radar.resume_structure_apinebula import RateCard
    _, snapshot, _ = case
    saved = _match_snapshot(snapshot)
    calls = []
    scripted = SyntheticReportClient()

    def handle(request):
        calls.append(request)
        if mode == 'timeout':
            raise httpx2.ReadTimeout('private timeout body', request=request)
        wire = json.loads(request.content)
        analysis_request = AnalysisRequest(system_prompt=SYSTEM_PROMPT, packet_json=wire['input'],
            output_schema_json=json.dumps(MatchAnalysisDraft.model_json_schema()),
            timeout_seconds=90, max_output_bytes=128 * 1024, prompt_version=PROMPT_VERSION)
        text = scripted.complete(analysis_request).text if mode == 'valid' else '{invalid'
        body = {'model': MODEL, 'status': 'completed', 'output': [{
            'type': 'message', 'role': 'assistant', 'status': 'completed',
            'content': [{'type': 'output_text', 'text': text}]}]}
        if mode != 'unknown_usage':
            body['usage'] = {'input_tokens': 1000, 'output_tokens': 500, 'total_tokens': 1500}
        return httpx2.Response(200, json=body)

    def network_forbidden(*args, **kwargs):
        raise AssertionError('mock_transport_must_not_open_socket')

    monkeypatch.setattr(socket, 'socket', network_forbidden)
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-key-not-real')
    client = APINebulaAnalysisClient(
        RateCard('synthetic', 'CNY', '0.78', '3.90', '1', '0.975'),
        budget_usd='1', max_input_tokens=65536, max_output_tokens=8192,
        _transport=httpx2.MockTransport(handle))
    try:
        report = generate_match_report(saved.request, saved.retrieval, saved.document,
            report_id='synthetic-sdk', generated_at=datetime.now(timezone.utc), client=client)
        if mode == 'valid':
            assert report.status == 'completed' and len(calls) == 3
            assert all(a.audit is not None and a.audit.total_tokens == 1500
                       for a in report.analyses)
        else:
            assert report.status == 'failed' and len(calls) == 1
            assert report.analyses[0].audit.reserved_units is not None
            assert client.audit['halted'] is not None
            assert all(a.status == 'not_analyzed' for a in report.analyses[1:])
        assert report.packet.retrieval.model_dump(mode='json') == snapshot['retrieval']
        assert 'private timeout body' not in report.model_dump_json()
        assert 'synthetic-test-key' not in report.model_dump_json()
    finally:
        client.close()
