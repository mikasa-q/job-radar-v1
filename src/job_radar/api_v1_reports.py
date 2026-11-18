"""Frozen report HTTP adapter; reports consume only a saved match snapshot."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Annotated, Literal
import uuid

from fastapi import Header, Request
from fastapi.responses import PlainTextResponse

from .api_v1_models import Accepted, Envelope, MatchPayload, ReportCreate, ReportPayload, Resource
from .api_v1_store import ServiceError
from .rag_models import canonical_hash
from .report_engine import generate_match_report
from .report_models import AnalysisConfig


def _match_snapshot(value) -> MatchPayload:
    """Rehydrate JSON strictly and check bindings before any report generation."""
    try:
        payload = MatchPayload.model_validate_json(json.dumps(value, ensure_ascii=False, allow_nan=False))
        preimage = {name: value[name] for name in (
            'document', 'request', 'index_manifest', 'engine_config')}
        if (canonical_hash(preimage) != payload.snapshot_sha256
                or payload.retrieval.manifest != payload.index_manifest
                or payload.retrieval.matching.config != payload.engine_config):
            raise ValueError('match_snapshot_binding')
        return payload
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise ServiceError('snapshot_conflict') from None


class ReportInterface:
    """Shared service owns persistence, replay, task states and the live gate."""

    def report_llm(self,body,key):
        from .report_evidence import build_evidence_packet
        from .report_analysis import build_analysis_request
        if body.top_n!=1:raise ServiceError('core_rejected')
        value=body.model_dump(mode='json')
        def prepare():
            snapshot=deepcopy(self.completed(body.match_id,'match'))
            _match_snapshot(snapshot)
            return snapshot
        from .api_v1_live import LiveBackend
        live = type(getattr(self, 'live_backend', None)) is LiveBackend
        config=AnalysisConfig(top_n=body.top_n)
        def requests(snapshot):
            saved=_match_snapshot(snapshot)
            packet=build_evidence_packet(saved.request,saved.retrieval,saved.document,config=config)
            return [build_analysis_request(packet,k,config=config) for k in packet.selected_keys]
        def execute(snapshot,client):
            saved=_match_snapshot(snapshot)
            report=generate_match_report(saved.request,saved.retrieval,saved.document,
                report_id=('live-' if live else 'mock-')+uuid.uuid4().hex,generated_at=datetime.now(timezone.utc),
                client=client,config=config)
            if report.status!='completed':raise ServiceError('upstream_error')
            from .api_v1_presentation import render_api_report
            return {'match_id':body.match_id,'snapshot_sha256':saved.snapshot_sha256,
                    'report':report.model_dump(mode='json'),
                    'markdown':render_api_report(report,mode='live' if live else 'scripted')}
        return self.submit_llm('/v1/reports',key,value,'report',prepare,execute,requests)

    def report(self, body: ReportCreate, key):
        # Capture request scalars now: queued execution never observes later mutation.
        value = body.model_dump(mode='json')
        try:
            frozen = ReportCreate.model_validate_json(json.dumps(value, ensure_ascii=False, allow_nan=False))
        except (ValueError, TypeError):
            raise ServiceError('core_rejected') from None
        match_id, top_n, mode = frozen.match_id, frozen.top_n, frozen.mode
        if mode == 'llm':
            if getattr(self,'mock_backend',None) is not None:
                return self.report_llm(frozen,key)
            from .api_v1_live import LiveBackend
            backend = getattr(self, 'live_backend', None)
            if type(backend) is LiveBackend and backend.allow_report:
                return self.report_llm(frozen,key)
            self.require_live()
            raise ServiceError('llm_disabled')

        def prepare():
            snapshot = deepcopy(self.completed(match_id, 'match'))
            _match_snapshot(snapshot)
            return snapshot

        def execute(snapshot):
            saved = _match_snapshot(snapshot)
            report = generate_match_report(
                saved.request, saved.retrieval, saved.document,
                report_id='offline-' + uuid.uuid4().hex,
                generated_at=datetime.now(timezone.utc),
                client=None, config=AnalysisConfig(top_n=top_n))
            # Presentation provenance is explicit; core output is never rewritten.
            from .api_v1_presentation import render_api_report
            return {
                'match_id': match_id,
                'snapshot_sha256': saved.snapshot_sha256,
                'report': report.model_dump(mode='json'),
                'markdown': render_api_report(report, mode='offline'),
            }

        return self.submit('/v1/reports', key, value, 'report', 'offline', prepare, execute)


def register_routes(app, service, *, wrap, typed, accepted, responses):
    """Keep v1 endpoint names and wire schemas stable during module extraction."""

    @app.post('/v1/reports', status_code=202, response_model=Envelope[Accepted], responses=responses)
    def report(request: Request, body: ReportCreate,
               idempotency_key: Annotated[str | None, Header()] = None):
        return wrap(request, accepted(service.report(body, idempotency_key)))

    @app.get('/v1/reports/{report_id}', response_model=Envelope[Resource[ReportPayload]], responses=responses)
    def get_report(request: Request, report_id: str,
                   format: Literal['json', 'markdown'] = 'json'):
        row = service.store.get(report_id)
        if row['kind'] != 'report':
            raise ServiceError('resource_not_found')
        if format == 'markdown':
            if row['state'] != 'succeeded':
                raise ServiceError('snapshot_conflict')
            return PlainTextResponse(row['payload']['markdown'], media_type='text/markdown',
                headers={'Content-Disposition': f'attachment; filename="{report_id}.md"'})
        return Envelope(request_id=request.state.request_id, data=typed(row, Resource[ReportPayload]))
