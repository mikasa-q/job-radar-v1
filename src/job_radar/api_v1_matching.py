"""Frozen V1 matching boundary; stages 1D–3 still own projection and ranking.

Only server-configured evidence/index paths are read. Request candidate order is
preserved in the snapshot, while the unchanged indexed engine owns result order.
"""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import Header, Query, Request

from .api_v1_models import (
    Accepted, Envelope, JobCandidate, JobsPage, MatchCreate, MatchPayload, ProfilePayload, Resource,
)
from .api_v1_store import ServiceError
from .evidence_repository import read_job_candidates
from .matching_input import build_matching_input, build_matching_request
from .matching_models import MatchingRequest
from .rag_engine import match_jobs_indexed
from .rag_index import DerivedIndex
from .rag_models import canonical_hash
from .resume_profile_rules import validate_profile_evidence
from .retrieval_models import EngineConfig, RetrievalError


def _typed(value, model):
    # Domain models are strict: their JSON form contains ISO timestamps/arrays.
    return model.model_validate_json(json.dumps(value, ensure_ascii=False))


class MatchingInterface:
    """Mixin using the host's shared settings/store/submit lifecycle."""

    def jobs(self):
        if self.settings.evidence_path is None:
            raise ServiceError('dataset_unavailable')
        try:
            return read_job_candidates(
                self.settings.evidence_path, dataset_id=self.settings.dataset_id)
        except (OSError, ValueError, TypeError):
            raise ServiceError('dataset_unavailable') from None

    def index(self):
        if self.settings.index_root is None:
            raise ServiceError('index_unavailable')
        try:
            return DerivedIndex(self.settings.index_root)
        except (OSError, ValueError, TypeError):
            raise ServiceError('index_unavailable') from None

    def ready(self):
        evidence = index = False
        try:
            jobs = self.jobs()
            evidence = True
            with self.index().reader() as snapshot:
                snapshot.assert_candidates(jobs)
                snapshot.manifest()
            index = True
        except (ServiceError, OSError, ValueError, TypeError):
            pass
        return dict(status='ready' if evidence and index else 'not_ready',
                    evidence=evidence, index=index, real_llm=False, hybrid=False)

    def match(self, body, key):
        # A detached wire copy prevents later mutation from changing a submitted
        # configuration, preferences or candidate scope. No dependency is read.
        value = body.model_dump(mode='json')
        try:
            body = _typed(value, MatchCreate)
        except (ValueError, TypeError):
            raise ServiceError('core_rejected') from None
        if body.retrieval.mode != 'bm25_rules':
            # Explicitly disabled even when allow_dense_fallback is true.
            raise ServiceError('hybrid_not_enabled')

        return self._submit_match('/v1/matches', key, body, value)

    def match_dataset(self, profile_id, key, *, config: EngineConfig):
        """Use all configured jobs, bound at first execution like explicit matches."""
        body = MatchCreate(profile_id=profile_id, candidate_keys=[], retrieval=config)
        value = {**body.model_dump(mode='json'), 'candidate_scope': 'configured_dataset'}
        return self._submit_match('/v1/reports/from-pdf:match', key, body, value, all_candidates=True)

    def _submit_match(self, route, key, body, value, *, all_candidates=False):
        def prepare():
            parent = self.completed(body.profile_id, 'profile')
            try:
                saved = _typed(parent, ProfilePayload)
                document = saved.document
                profile = saved.profile
                if profile.resume_id != saved.resume_id:
                    raise ValueError('profile_resume_binding')
                profile = validate_profile_evidence(profile, document)
            except (ValueError, TypeError, KeyError):
                raise ServiceError('snapshot_corrupt') from None
            available = {(j.dataset_id, j.job_id): j for j in self.jobs()}
            if all_candidates and len(available) > body.retrieval.max_jobs:
                raise ServiceError('core_rejected')
            keys = list(available) if all_candidates else [(k.dataset_id, k.job_id) for k in body.candidate_keys]
            if any(k not in available for k in keys):
                raise ServiceError('resource_not_found')
            jobs = [available[k] for k in keys]
            try:
                projected = build_matching_input(profile, preferences=body.preferences)
                # Use the original request validator for the formal gate; do not
                # map unrelated assembly errors to a review-status failure.
                request = build_matching_request(projected, jobs, purpose=body.purpose)
            except ValueError as error:
                from pydantic import ValidationError
                formal_gate = isinstance(error, ValidationError) and any(
                    item.get('type') == 'value_error'
                    and str(item.get('ctx', {}).get('error')) == 'formal_requires_confirmed'
                    for item in error.errors())
                raise ServiceError('formal_requires_confirmed' if formal_gate
                                   else 'core_rejected') from None
            except TypeError:
                raise ServiceError('core_rejected') from None
            try:
                with self.index().reader() as bound:
                    bound.assert_candidates(jobs)
                    manifest = bound.manifest()
            except (OSError, ValueError, TypeError):
                raise ServiceError('index_unavailable') from None
            return {'document': document.model_dump(mode='json'),
                    'request': request.model_dump(mode='json'),
                    'index_manifest': manifest.model_dump(mode='json'),
                    'engine_config': body.retrieval.model_dump(mode='json')}

        def execute(snapshot):
            try:
                request = _typed(snapshot['request'], MatchingRequest)
                config = _typed(snapshot['engine_config'], EngineConfig)
            except (ValueError, TypeError, KeyError):
                raise ServiceError('snapshot_corrupt') from None
            if config.mode != 'bm25_rules':
                raise ServiceError('hybrid_not_enabled')
            try:
                with self.index().reader() as bound:
                    if bound.manifest().model_dump(mode='json') != snapshot['index_manifest']:
                        raise ServiceError('snapshot_conflict')
                    # Translate core errors *inside* the reader so its broad
                    # corruption guard cannot relabel a core input-limit error.
                    try:
                        result = match_jobs_indexed(request, index=bound, config=config)
                    except RetrievalError as error:
                        raise ServiceError('index_unavailable' if error.code.startswith('index_')
                                           else 'core_rejected') from None
                    except (ValueError, TypeError):
                        raise ServiceError('core_rejected') from None
            except (OSError, ValueError, TypeError):
                raise ServiceError('index_unavailable') from None
            return {**snapshot, 'retrieval': result.model_dump(mode='json'),
                    'snapshot_sha256': canonical_hash(snapshot)}

        return self.submit(route, key, value, 'match', 'bm25_rules', prepare, execute)


def register_routes(app, service, *, wrap, typed, accepted, responses):
    """Register matching routes without changing frozen operation identifiers."""

    @app.get('/v1/jobs', response_model=Envelope[JobsPage], responses=responses)
    def jobs(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 20,
             offset: Annotated[int, Query(ge=0)] = 0):
        rows = service.jobs()
        return Envelope(request_id=request.state.request_id, data=typed({
            'total': len(rows), 'limit': limit, 'offset': offset,
            'items': [j.model_dump(mode='json') for j in rows[offset:offset + limit]],
        }, JobsPage))

    @app.get('/v1/jobs/{dataset_id}/{job_id}', response_model=Envelope[JobCandidate], responses=responses)
    def job(request: Request, dataset_id: str, job_id: int):
        for row in service.jobs():
            if (row.dataset_id, row.job_id) == (dataset_id, job_id):
                return Envelope(request_id=request.state.request_id, data=row)
        raise ServiceError('resource_not_found')

    @app.post('/v1/matches', status_code=202, response_model=Envelope[Accepted], responses=responses)
    def match(request: Request, body: MatchCreate,
              idempotency_key: Annotated[str | None, Header()] = None):
        return wrap(request, accepted(service.match(body, idempotency_key)))

    @app.get('/v1/matches/{match_id}', response_model=Envelope[Resource[MatchPayload]], responses=responses)
    def get_match(request: Request, match_id: str):
        row = service.store.get(match_id)
        if row['kind'] != 'match':
            raise ServiceError('resource_not_found')
        return Envelope(request_id=request.state.request_id, data=typed(row, Resource[MatchPayload]))
