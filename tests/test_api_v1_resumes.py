"""A-module contract tests; all documents are synthetic, with no providers."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import asyncio
import hashlib
import io
import json
import multiprocessing
import threading
import time

import anyio
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pymupdf
import pytest
from starlette.datastructures import FormData, UploadFile

from job_radar.api_v1 import BodyLimit
from job_radar import api_v1_resumes as module
from job_radar.api_v1_budget import BudgetRejected, require_live_authorization
from job_radar.api_v1_models import Accepted, ApiError, Envelope, ERROR_HTTP, ProfileCreate
from job_radar.api_v1_runtime import OfflineService, ServiceSettings
from job_radar.api_v1_store import ServiceError
from job_radar.rag_models import canonical_hash
from job_radar.resume_pdf import extract_resume_pdf
from job_radar.resume_profile_rules import build_rule_profile


class AService(OfflineService):
    """Bind only the module under test, independent of application composition."""
    upload = module.ResumeInterface.upload
    profile = module.ResumeInterface.profile

    def require_live(self):
        try:
            require_live_authorization()
        except BudgetRejected as error:
            raise ServiceError(str(error)) from None


def pdf(text='Course project: Python and FastAPI API development.', pages=1):
    with pymupdf.open() as document:
        for _ in range(pages):
            page = document.new_page()
            if text:
                page.insert_text((40, 60), text)
        return document.tobytes()


def wire_document(raw=None):
    return extract_resume_pdf(pdf() if raw is None else raw,
                              processed_at=datetime(2026, 11, 17, tzinfo=timezone.utc)).model_dump(mode='json')


@pytest.fixture
def service(tmp_path):
    value = AService(ServiceSettings(store_root=tmp_path / 'store', max_pending_tasks=2))
    value.start()
    try:
        yield value
    finally:
        value.close()


def test_app(service):
    app = FastAPI()
    app.add_middleware(BodyLimit)

    def wrap(request, data=None, error=None):
        return Envelope(request_id=request.state.request_id, data=data, error=error).model_dump(mode='json')

    def typed(value, model):
        return model.model_validate_json(json.dumps(value, ensure_ascii=False))

    def accepted(record):
        return Accepted(resource_id=record['resource_id'], state=record['state'],
                        status_url='/v1/profiles/' + record['resource_id']).model_dump(mode='json')

    @app.exception_handler(ServiceError)
    async def error(request, exc):
        return JSONResponse(status_code=ERROR_HTTP[exc.code], content=wrap(request, error=ApiError(code=exc.code)))

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse(status_code=422, content=wrap(request, error=ApiError(code='invalid_request')))

    module.register_routes(app, service, wrap=wrap, typed=typed, accepted=accepted,
                           responses={str(code): {'model': Envelope[dict]} for code in set(ERROR_HTTP.values())})
    return app


# This helper is not a pytest case.
test_app.__test__ = False


@pytest.fixture
def client(service):
    with TestClient(test_app(service)) as client:
        yield client


def post_pdf(client, raw, key='resume-upload', filename='resume.pdf', mime='application/pdf'):
    return client.post('/v1/resumes', files={'file': (filename, raw, mime)},
                       headers={'Idempotency-Key': key})


def terminal(client, url):
    for _ in range(400):
        response = client.get(url)
        assert response.status_code == 200, response.text
        row = response.json()['data']
        if row['state'] not in ('queued', 'running'):
            return row
        time.sleep(.005)
    raise AssertionError('task_not_finished')


def test_real_pdf_and_rule_profile_preserve_domain_evidence(client, service):
    raw = pdf()
    uploaded = post_pdf(client, raw)
    assert uploaded.status_code == 201, uploaded.text
    row = uploaded.json()['data']
    rid = row['resource_id']
    document = row['payload']
    assert document['provenance']['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert document['provenance']['processed_at'].endswith('Z')
    assert row['payload_sha256'] == canonical_hash(document)
    assert client.get('/v1/resumes/' + rid).json()['data'] == row
    response = client.post('/v1/resumes/' + rid + '/profiles', json={'mode': 'rules'},
                           headers={'Idempotency-Key': 'profile-rules'})
    assert response.status_code == 202, response.text
    profile = terminal(client, response.json()['data']['status_url'])
    assert profile['state'] == 'succeeded', profile
    payload = profile['payload']
    parsed = module.ResumeDocument.model_validate_json(json.dumps(document))
    assert payload['profile'] == build_rule_profile(parsed, resume_id=rid).model_dump(mode='json')
    assert payload['document'] == document and payload['comparison'] is None
    assert payload['profile']['review_status'] == 'unconfirmed'
    assert payload['profile']['warnings'][0] == 'rules_skills_only'
    assert [s['name'] for s in payload['profile']['skills']] == ['Python', 'FastAPI']
    stored = service.store.state['records'][rid]
    assert stored['input'] == {'source_sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    assert stored['snapshot'] is None
    assert 'resume.pdf' not in json.dumps(service.store.state)


def test_upload_idempotency_uses_actual_bytes_and_does_not_reextract(client, service, monkeypatch):
    raw = pdf()
    document = wire_document(raw)
    calls = []
    monkeypatch.setattr(module, 'extract_isolated', lambda *args: calls.append(args) or deepcopy(document))
    first = post_pdf(client, raw).json()['data']
    second = post_pdf(client, raw, filename='renamed.pdf', mime='application/octet-stream')
    assert second.status_code == 201 and second.json()['data'] == first
    assert len(calls) == 1
    changed = post_pdf(client, raw + b'\n')
    assert changed.status_code == 409 and changed.json()['error']['code'] == 'idempotency_conflict'
    assert len(calls) == len(service.store.state['records']) == 1


def test_failed_extraction_is_persisted_and_replayed_without_retry(client, service, monkeypatch):
    calls = []

    def fail(*args):
        calls.append(args)
        raise ServiceError('pdf_invalid_pdf')

    monkeypatch.setattr(module, 'extract_isolated', fail)
    for _ in range(2):
        response = post_pdf(client, b'not a PDF')
        assert response.status_code == 422 and response.json()['error']['code'] == 'pdf_invalid_pdf'
    assert len(calls) == len(service.store.state['records']) == 1
    resource = next(iter(service.store.state['records'].values()))['resource']
    assert resource['state'] == 'failed' and resource['payload'] is None
    assert resource['error']['code'] == 'pdf_invalid_pdf'
    assert service.slots.acquire(blocking=False)
    service.slots.release()


def test_unexpected_parser_error_is_redacted_and_releases_queue(client, service, monkeypatch):
    def fail(*args):
        raise RuntimeError('synthetic private parser detail')

    monkeypatch.setattr(module, 'extract_isolated', fail)
    response = post_pdf(client, b'PDF')
    assert response.status_code == 500 and response.json()['error']['code'] == 'internal_error'
    assert 'private parser' not in response.text
    row = next(iter(service.store.state['records'].values()))['resource']
    assert row['state'] == 'failed' and row['error']['code'] == 'internal_error'
    assert service.slots.acquire(blocking=False)
    service.slots.release()


@pytest.mark.parametrize('key,code', [(None, 'idempotency_key_required'), ('short', 'idempotency_key_invalid'),
                                    ('invalid key!', 'idempotency_key_invalid'), ('a' * 129, 'idempotency_key_invalid')])
def test_invalid_key_never_starts_extraction(client, service, monkeypatch, key, code):
    monkeypatch.setattr(module, 'extract_isolated', lambda *args: pytest.fail('unexpected extraction'))
    headers = {} if key is None else {'Idempotency-Key': key}
    response = client.post('/v1/resumes', files={'file': ('x.pdf', b'PDF', 'application/pdf')}, headers=headers)
    assert response.status_code == 422 and response.json()['error']['code'] == code
    assert service.store.state['records'] == {}


@pytest.mark.parametrize('raw,code', [(b'', 'pdf_empty_pdf'), (b'x' * (module.MAX_UPLOAD + 1), 'payload_too_large')])
def test_actual_file_limits_before_extraction(client, service, monkeypatch, raw, code):
    monkeypatch.setattr(module, 'extract_isolated', lambda *args: pytest.fail('unexpected extraction'))
    response = post_pdf(client, raw)
    assert response.json()['error']['code'] == code
    assert service.store.state['records'] == {}


@pytest.mark.parametrize('files,data', [
    ({'other': ('x.pdf', b'PDF', 'application/pdf')}, None),
    ([('file', ('a.pdf', b'PDF', 'application/pdf')), ('file', ('b.pdf', b'PDF', 'application/pdf'))], None),
    ({'file': ('x.pdf', b'PDF', 'application/pdf')}, {'hidden': 'do not echo'}),
    ({'file': (None, 'not a file')}, None),
])
def test_multipart_rejects_extra_wrong_or_duplicate_parts(client, service, monkeypatch, files, data):
    monkeypatch.setattr(module, 'extract_isolated', lambda *args: pytest.fail('unexpected extraction'))
    response = client.post('/v1/resumes', files=files, data=data, headers={'Idempotency-Key': 'multipart-key'})
    assert response.status_code == 422 and response.json()['error']['code'] == 'invalid_request'
    assert 'do not echo' not in response.text and service.store.state['records'] == {}


@pytest.mark.parametrize('content_type,body', [
    ('multipart/form-data', b'garbage'),
    ('multipart/form-data; boundary=bad', b'invalid framing'),
    ('multipart/form-data; boundary=bad', b'--bad\r\nContent-Disposition: form-data; name="file"; filename="x.pdf"\r\n\r\nincomplete'),
])
def test_malformed_multipart_is_fixed_error(client, content_type, body):
    response = client.post('/v1/resumes', content=body,
                           headers={'Content-Type': content_type, 'Idempotency-Key': 'malformed-key'})
    assert response.status_code == 422, response.text
    assert response.json()['error']['code'] == 'invalid_request'


@pytest.mark.parametrize('mime', ['text/plain', 'application/json', 'image/png'])
def test_unsupported_part_type_is_rejected(client, service, mime):
    response = post_pdf(client, b'PDF', mime=mime)
    assert response.status_code == 415 and response.json()['error']['code'] == 'unsupported_media_type'
    assert service.store.state['records'] == {}


def test_nonmultipart_content_is_rejected(client):
    response = client.post('/v1/resumes', content=b'PDF', headers={'Content-Type': 'application/pdf'})
    assert response.status_code == 415 and response.json()['error']['code'] == 'unsupported_media_type'


@pytest.mark.parametrize('error', [False, True])
def test_multipart_files_close_on_success_and_service_error(client, monkeypatch, error):
    import starlette.formparsers as formparsers
    created = []
    factory = formparsers.SpooledTemporaryFile

    def tracked(*args, **kwargs):
        result = factory(*args, **kwargs)
        created.append(result)
        return result

    monkeypatch.setattr(formparsers, 'SpooledTemporaryFile', tracked)

    def parse(*args):
        if error:
            raise ServiceError('pdf_invalid_pdf')
        return wire_document()

    monkeypatch.setattr(module, 'extract_isolated', parse)
    response = post_pdf(client, b'PDF')
    assert response.status_code == (422 if error else 201)
    assert created and all(file.closed for file in created)


def test_multipart_files_close_on_parse_error(client, monkeypatch):
    import starlette.formparsers as formparsers
    created = []
    factory = formparsers.SpooledTemporaryFile

    def tracked(*args, **kwargs):
        result = factory(*args, **kwargs)
        created.append(result)
        return result

    monkeypatch.setattr(formparsers, 'SpooledTemporaryFile', tracked)
    response = client.post('/v1/resumes', files=[('file', ('a.pdf', b'a', 'application/pdf')),
                                                ('file', ('b.pdf', b'b', 'application/pdf'))])
    assert response.status_code == 422
    assert created and all(file.closed for file in created)


def test_cancelled_file_read_closes_form_and_never_submits(service, monkeypatch):
    app = test_app(service)
    endpoint = next(route.endpoint for route in app.routes
                    if getattr(route, 'path', None) == '/v1/resumes')
    storage = io.BytesIO(b'PDF')

    class CancelledUpload(UploadFile):
        async def read(self, size=-1):
            raise asyncio.CancelledError()

    upload = CancelledUpload(storage, filename='cancelled.pdf',
                              headers={'content-type': 'application/pdf'})
    form = FormData([('file', upload)])

    class FormContext:
        async def __aenter__(self):
            return form

        async def __aexit__(self, *args):
            await form.close()

    class CancelledRequest:
        headers = {'content-type': 'multipart/form-data; boundary=test'}

        def form(self, **kwargs):
            return FormContext()

    monkeypatch.setattr(service, 'upload', lambda *args: pytest.fail('cancelled upload submitted'))

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await endpoint(CancelledRequest(), 'cancelled-key')

    anyio.run(run)
    assert storage.closed
    assert service.store.state['records'] == {}


def test_parallel_same_key_parses_once_and_queue_does_not_bind_rejected_key(service, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    raw = pdf()
    document = wire_document(raw)
    calls = []

    def parse(*args):
        calls.append(args)
        entered.set()
        assert release.wait(5)
        return deepcopy(document)

    monkeypatch.setattr(module, 'extract_isolated', parse)
    with ThreadPoolExecutor(max_workers=1) as executor:
        original = executor.submit(service.upload, raw, 'concurrent-key')
        try:
            assert entered.wait(2)
            with pytest.raises(ServiceError, match='snapshot_conflict'):
                service.upload(raw, 'concurrent-key')
            # Occupy the only remaining slot without creating an idempotency record.
            assert service.slots.acquire(blocking=False)
            try:
                with pytest.raises(ServiceError, match='queue_full'):
                    service.upload(raw, 'rejected-key')
                assert service.store.lookup('/v1/resumes', 'rejected-key', {
                    'source_sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}) is None
            finally:
                service.slots.release()
        finally:
            release.set()
        first = original.result(timeout=3)
    assert service.upload(raw, 'concurrent-key') == first
    assert len(calls) == 1


def test_profile_replay_precedes_parent_read_and_does_not_promote_confirmation(service, monkeypatch):
    raw = pdf()
    monkeypatch.setattr(module, 'extract_isolated', lambda *args: wire_document(raw))
    parent = service.upload(raw, 'seed-profile-parent')
    rid = parent['resource_id']
    body = ProfileCreate(mode='rules')
    first = service.profile(rid, body, 'profile-replay-key')
    for _ in range(400):
        row = service.store.get(first['resource_id'])
        if row['state'] not in ('queued', 'running'):
            break
        time.sleep(.005)
    assert row['state'] == 'succeeded'
    monkeypatch.setattr(service, 'completed', lambda *args: pytest.fail('parent reread'))
    assert service.profile(rid, body, 'profile-replay-key')['resource_id'] == first['resource_id']
    assert row['payload']['profile']['review_status'] == 'unconfirmed'


def test_llm_mode_calls_shared_gate_before_parent_or_submit(service, monkeypatch):
    calls = []

    def disabled():
        calls.append('gate')
        raise ServiceError('llm_disabled')

    monkeypatch.setattr(service, 'require_live', disabled)
    monkeypatch.setattr(service, 'completed', lambda *args: pytest.fail('read parent'))
    monkeypatch.setattr(service, 'submit', lambda *args: pytest.fail('submitted LLM'))
    with pytest.raises(ServiceError, match='llm_disabled'):
        service.profile('rsm_' + 'a' * 32, ProfileCreate(mode='llm'), 'llm-mode-key')
    assert calls == ['gate'] and service.store.state['records'] == {}


def test_llm_mode_never_falls_through_to_rules_if_gate_returns(service, monkeypatch):
    monkeypatch.setattr(service, 'require_live', lambda: None)
    monkeypatch.setattr(service, 'submit', lambda *args: pytest.fail('LLM fell through to rules'))
    with pytest.raises(ServiceError, match='llm_disabled'):
        service.profile('rsm_' + 'a' * 32, ProfileCreate(mode='llm'), 'llm-mode-key')


def test_llm_route_stays_disabled_with_environment_flags(client, service, monkeypatch):
    monkeypatch.setenv('JOBRADAR_LLM_ENABLED', 'true')
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-not-a-credential')
    response = client.post('/v1/resumes/rsm_' + 'a' * 32 + '/profiles', json={'mode': 'llm'},
                           headers={'Idempotency-Key': 'llm-mode-key'})
    assert response.status_code == 503 and response.json()['error']['code'] == 'llm_disabled'
    assert service.store.state['records'] == {}


def test_get_routes_hide_wrong_resource_kinds(client, service, monkeypatch):
    monkeypatch.setattr(module, 'extract_isolated', lambda *args: wire_document())
    row = service.upload(b'PDF', 'kind-test-key')
    response = client.get('/v1/profiles/' + row['resource_id'])
    assert response.status_code == 404 and response.json()['error']['code'] == 'resource_not_found'
    assert client.get('/v1/resumes/not-an-id').status_code == 404


@pytest.mark.parametrize('raw,code', [(b'not a PDF', 'pdf_invalid_pdf'),
                                    (pdf(''), 'pdf_no_extractable_text'),
                                    (pdf(pages=11), 'pdf_page_limit')])
def test_real_parser_errors_have_fixed_codes_and_reap_process(raw, code):
    before = {process.pid for process in multiprocessing.active_children()}
    with pytest.raises(ServiceError, match=code):
        module.extract_isolated(raw, 15)
    assert {process.pid for process in multiprocessing.active_children()} == before


def test_real_worker_timeout_reaps_process():
    before = {process.pid for process in multiprocessing.active_children()}
    with pytest.raises(ServiceError, match='execution_timeout'):
        module.extract_isolated(pdf(), .000001)
    assert {process.pid for process in multiprocessing.active_children()} == before


def fake_spawn(monkeypatch, *, start_error=False, recv_error=False, alive=True, rss='1', ok=False):
    """Control worker failures without external processes or unbounded waits."""
    calls = []

    class Pipe:
        def __init__(self, receiver):
            self.receiver = receiver
            self.closed = False

        def poll(self, timeout):
            return recv_error or ok

        def recv(self):
            if recv_error:
                raise EOFError()
            return 'ok', wire_document()

        def close(self):
            self.closed = True

    receiver, sender = Pipe(True), Pipe(False)

    class Process:
        pid = 42
        running = alive

        def start(self):
            calls.append('start')
            if start_error:
                raise OSError('synthetic spawn failure')

        def join(self, timeout=None):
            calls.append(('join', timeout))

        def is_alive(self):
            return self.running

        def terminate(self):
            calls.append('terminate')
            # Stay alive to require the final kill branch.

        def kill(self):
            calls.append('kill')
            self.running = False

        def close(self):
            calls.append('close')

    process = Process()

    class Context:
        def Pipe(self, duplex):
            assert duplex is False
            return receiver, sender

        def Process(self, **kwargs):
            assert kwargs['daemon'] is True
            return process

    def context(method):
        assert method == 'spawn'
        return Context()

    class Sample:
        stdout = rss

    monkeypatch.setattr(module.multiprocessing, 'get_context', context)
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: Sample())
    return receiver, sender, calls


def test_spawn_start_failure_closes_both_pipe_ends_and_process(monkeypatch):
    receiver, sender, calls = fake_spawn(monkeypatch, start_error=True)
    with pytest.raises(ServiceError, match='pdf_extract_failed'):
        module.extract_isolated(b'PDF', 1)
    assert receiver.closed and sender.closed and calls == ['start', 'close']


def test_worker_pipe_eof_reaps_and_kills_unresponsive_worker(monkeypatch):
    receiver, sender, calls = fake_spawn(monkeypatch, recv_error=True)
    with pytest.raises(ServiceError, match='pdf_extract_failed'):
        module.extract_isolated(b'PDF', 1)
    assert receiver.closed and sender.closed
    assert 'terminate' in calls and 'kill' in calls and calls[-1] == 'close'


def test_sampled_rss_limit_reaps_worker(monkeypatch):
    receiver, sender, calls = fake_spawn(monkeypatch, rss=str(module.MAX_RSS_KIB + 1))
    with pytest.raises(ServiceError, match='payload_too_large'):
        module.extract_isolated(b'PDF', 1)
    assert receiver.closed and sender.closed
    assert 'terminate' in calls and 'kill' in calls and calls[-1] == 'close'


def test_worker_unexpected_exit_is_fixed_parse_error(monkeypatch):
    receiver, sender, calls = fake_spawn(monkeypatch, alive=False)
    with pytest.raises(ServiceError, match='pdf_extract_failed'):
        module.extract_isolated(b'PDF', 1)
    assert receiver.closed and sender.closed
    assert 'terminate' not in calls and calls[-1] == 'close'


def test_openapi_has_only_a_routes_and_existing_operation_names(client):
    schema = client.get('/openapi.json').json()
    paths = schema['paths']
    assert set(paths) == {'/v1/resumes', '/v1/resumes/{resume_id}',
                          '/v1/resumes/{resume_id}/profiles', '/v1/profiles/{profile_id}'}
    assert paths['/v1/resumes']['post']['operationId'].startswith('upload_')
    assert paths['/v1/resumes/{resume_id}/profiles']['post']['operationId'].startswith('profile_')
    upload_schema = paths['/v1/resumes']['post']['requestBody']['content']['multipart/form-data']['schema']
    assert upload_schema['required'] == ['file'] and upload_schema['additionalProperties'] is False
