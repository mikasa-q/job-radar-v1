"""Résumé transport and offline adapters for the frozen Stage 5 v1 contract.

Domain PDF extraction and rule-profile construction remain unchanged. No
provider is constructed here; the shared live gate owns every LLM request.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import subprocess
import time
from typing import Annotated

import anyio
from fastapi import Header, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from .api_v1_models import (
    Accepted, Envelope, ERROR_HTTP, ProfileCreate, ProfilePayload,
    Resource, ResumeDocument,
)
from .api_v1_store import ServiceError
from .resume_pdf import DEFAULT_MAX_BYTES

MAX_UPLOAD = DEFAULT_MAX_BYTES
MAX_EXTRACTED_JSON = 8 * 1024 * 1024
MAX_RSS_KIB = 256 * 1024


def _pdf_worker(pipe, raw):
    """Run only the existing parser in an independently supervised process."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        from .resume_pdf import extract_resume_pdf
        document = extract_resume_pdf(raw, processed_at=datetime.now(timezone.utc))
        if len(document.model_dump_json().encode()) > MAX_EXTRACTED_JSON:
            pipe.send(('error', 'payload_too_large'))
        else:
            pipe.send(('ok', document.model_dump(mode='json')))
    except Exception as error:
        code = 'pdf_' + getattr(error, 'code', 'extract_failed')
        pipe.send(('error', code if code in ERROR_HTTP else 'pdf_extract_failed'))
    finally:
        pipe.close()


def extract_isolated(raw, timeout):
    """Enforce wall time and sampled RSS, then reap the worker on every exit.

macOS does not support the needed RLIMIT_AS semantics. CPU is limited in the
worker; RSS is sampled by the parent and is deliberately not a hard OS cap.
"""
    context = multiprocessing.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_pdf_worker, args=(sender, raw), daemon=True)
    started = False
    try:
        process.start()
        started = True
        sender.close()
        deadline = time.monotonic() + timeout
        while not receiver.poll(.05):
            if time.monotonic() >= deadline:
                raise ServiceError('execution_timeout')
            if not process.is_alive():
                raise ServiceError('pdf_extract_failed')
            try:
                sample = subprocess.run(
                    ['/bin/ps', '-o', 'rss=', '-p', str(process.pid)],
                    capture_output=True, text=True, timeout=1, check=True,
                )
                if int(sample.stdout.strip()) > MAX_RSS_KIB:
                    raise ServiceError('payload_too_large')
            except (subprocess.SubprocessError, ValueError, OSError):
                if process.is_alive():
                    raise ServiceError('pdf_extract_failed') from None
        try:
            state, value = receiver.recv()
        except (EOFError, OSError, TypeError, ValueError):
            raise ServiceError('pdf_extract_failed') from None
        if state != 'ok':
            code = value if isinstance(value, str) and value in ERROR_HTTP else 'pdf_extract_failed'
            raise ServiceError(code)
        # Revalidate the process boundary before persisting a success payload.
        try:
            return ResumeDocument.model_validate_json(
                json.dumps(value, ensure_ascii=False)
            ).model_dump(mode='json')
        except (TypeError, ValueError):
            raise ServiceError('pdf_extract_failed') from None
    except ServiceError:
        raise
    except Exception:
        raise ServiceError('pdf_extract_failed') from None
    finally:
        sender.close()
        receiver.close()
        if started:
            process.join(.5)
            if process.is_alive():
                process.terminate()
                process.join(2)
            if process.is_alive():
                process.kill()
                process.join()
        process.close()


class ResumeInterface:
    """Composed by the shared runtime, which owns store, queue and live gate."""

    def upload(self, raw, key):
        if not raw:
            raise ServiceError('pdf_empty_pdf')
        if len(raw) > MAX_UPLOAD:
            raise ServiceError('payload_too_large')
        value = {'source_sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        with self.gate:
            found = self.store.lookup('/v1/resumes', key, value)
            if found:
                if found['error']:
                    raise ServiceError(found['error']['code'])
                if found['state'] != 'succeeded':
                    raise ServiceError('snapshot_conflict')
                return found
            if not self.slots.acquire(blocking=False):
                raise ServiceError('queue_full')
            try:
                record, _ = self.store.create('/v1/resumes', key, value, 'resume', 'pdf')
            except Exception:
                self.slots.release()
                raise
        rid = record['resource_id']
        try:
            self.store.transition(rid, 'running')
            document = extract_isolated(raw, self.settings.pdf_timeout_seconds)
            self.store.transition(rid, 'succeeded', payload=document)
        except ServiceError as error:
            self._fail_task(rid, error.code)
            if self.store.write_failed:
                raise ServiceError('store_unavailable') from None
            raise
        except OSError:
            self._fail_task(rid, 'store_unavailable')
            raise ServiceError('store_unavailable') from None
        except Exception:
            self._fail_task(rid, 'internal_error')
            if self.store.write_failed:
                raise ServiceError('store_unavailable') from None
            raise ServiceError('internal_error') from None
        finally:
            self.slots.release()
        return self.store.get(rid)

    def profile_llm(self,rid,body,key):
        from .resume_structure import build_structure_request,structure_resume
        value=body.model_dump(mode='json')
        def prepare():return self.completed(rid,'resume')
        def parse(snapshot):return ResumeDocument.model_validate_json(json.dumps(snapshot,ensure_ascii=False))
        def requests(snapshot):return [build_structure_request(parse(snapshot),timeout_seconds=90)]
        def execute(snapshot,client):
            result=structure_resume(parse(snapshot),client=client,resume_id=rid,timeout_seconds=90)
            return {'resume_id':rid,'document':snapshot,'profile':result.profile.model_dump(mode='json'),
                    'comparison':result.comparison.model_dump(mode='json')}
        return self.submit_llm('/v1/resumes/'+rid+'/profiles',key,value,'profile',prepare,execute,requests)

    def profile(self, rid, body, key):
        if body.mode == 'llm':
            if getattr(self,'mock_backend',None) is not None or getattr(self,'live_backend',None) is not None:
                return self.profile_llm(rid,body,key)
            self.require_live()
            raise ServiceError('llm_disabled')

        def prepare():
            return self.completed(rid, 'resume')

        def execute(document):
            from .resume_profile_rules import build_rule_profile
            parsed = ResumeDocument.model_validate_json(json.dumps(document, ensure_ascii=False))
            profile = build_rule_profile(parsed, resume_id=rid)
            return {
                'resume_id': rid, 'document': document,
                'profile': profile.model_dump(mode='json'), 'comparison': None,
            }

        return self.submit('/v1/resumes/' + rid + '/profiles', key, body.model_dump(mode='json'),
                           'profile', 'rules', prepare, execute)


def register_routes(app, service, *, wrap, typed, accepted, responses):
    """Register only A's four paths, preserving operation names and wire types."""

    @app.post('/v1/resumes', status_code=201,
              response_model=Envelope[Resource[ResumeDocument]], responses=responses,
              openapi_extra={'requestBody': {'required': True, 'content': {
                  'multipart/form-data': {'schema': {
                      'type': 'object', 'required': ['file'], 'additionalProperties': False,
                      'properties': {'file': {'type': 'string', 'format': 'binary'}},
                  }}}}})
    async def upload(request: Request, idempotency_key: Annotated[str | None, Header()] = None):
        media_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
        if media_type != 'multipart/form-data':
            raise ServiceError('unsupported_media_type')
        try:
            async with request.form(max_files=1, max_fields=0, max_part_size=MAX_UPLOAD) as form:
                try:
                    values = form.multi_items()
                    if len(values) != 1 or values[0][0] != 'file' or not isinstance(values[0][1], UploadFile):
                        raise ServiceError('invalid_request')
                    file = values[0][1]
                    if file.content_type not in ('application/pdf', 'application/octet-stream'):
                        raise ServiceError('unsupported_media_type')
                    raw = await file.read(MAX_UPLOAD + 1)
                    record = await run_in_threadpool(service.upload, raw, idempotency_key)
                    return Envelope(request_id=request.state.request_id,
                                    data=typed(record, Resource[ResumeDocument]))
                finally:
                    # Starlette closes successful forms on exit; shield this first
                    # close so cancellation also releases disk-spooled PDF files.
                    with anyio.CancelScope(shield=True):
                        await form.close()
        except HTTPException as error:
            if error.status_code != 400:
                raise
            raise ServiceError('invalid_request') from None

    @app.get('/v1/resumes/{resume_id}', response_model=Envelope[Resource[ResumeDocument]],
             responses=responses)
    def get_resume(request: Request, resume_id: str):
        row = service.store.get(resume_id)
        if row['kind'] != 'resume':
            raise ServiceError('resource_not_found')
        return Envelope(request_id=request.state.request_id, data=typed(row, Resource[ResumeDocument]))

    @app.post('/v1/resumes/{resume_id}/profiles', status_code=202,
              response_model=Envelope[Accepted], responses=responses)
    def profile(request: Request, resume_id: str, body: ProfileCreate,
                idempotency_key: Annotated[str | None, Header()] = None):
        return wrap(request, accepted(service.profile(resume_id, body, idempotency_key)))

    @app.get('/v1/profiles/{profile_id}', response_model=Envelope[Resource[ProfilePayload]],
             responses=responses)
    def get_profile(request: Request, profile_id: str):
        row = service.store.get(profile_id)
        if row['kind'] != 'profile':
            raise ServiceError('resource_not_found')
        return Envelope(request_id=request.state.request_id, data=typed(row, Resource[ProfilePayload]))
