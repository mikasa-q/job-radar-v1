"""V1.1 response-only schema widening; no providers, credentials or real data."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from job_radar.api_v1 import create_app
from job_radar.api_v1_models import (
    API_VERSION, ERROR_HTTP, Envelope, ProfilePayload, Ready, Resource,
)
from job_radar.api_v1_runtime import ServiceSettings
from job_radar.resume_profile_rules import build_rule_profile
from job_radar.resume_structure import ClientResponse, SkillComparison, structure_resume
from job_radar.resume_structure_fake import FakeStructureClient
from job_radar.report_synthetic import synthetic_inputs
from job_radar.rag_models import canonical_hash

ROOT = Path(__file__).resolve().parents[1]


def schema():
    return create_app().openapi()


def row(kind, mode, *, payload=None):
    stamp = datetime(2026, 11, 17, tzinfo=timezone.utc)
    prefix = {'resume': 'rsm', 'profile': 'prf', 'match': 'mtc', 'report': 'rpt'}[kind]
    return dict(resource_id=prefix+'_'+'a'*32, kind=kind,
        state='queued' if payload is None else 'succeeded', execution_mode=mode,
        created_at=stamp, updated_at=stamp, input_sha256='b'*64,
        payload=payload, payload_sha256=None if payload is None else canonical_hash(payload))


def test_new_schema_has_only_approved_response_widening_and_info():
    old = json.loads((ROOT/'docs/STAGE5_OPENAPI.json').read_text())
    new = schema()
    expected = deepcopy(old)
    assert new['info']['version'] == '1.1.0'
    assert new['info']['title'] == 'JobRadar V1 整合 API（真实适配：离线验收）'
    expected['info']['version'] = new['info']['version']
    expected['info']['title'] = new['info']['title']
    before = expected['components']['schemas']
    after = new['components']['schemas']
    assert set(after)-set(before) == {'SkillComparison'}
    assert not set(before)-set(after)
    resource_schemas = {
        name for name, value in before.items()
        if 'execution_mode' in value.get('properties', {})
    }
    assert resource_schemas == {
        'Resource_ResumeDocument_', 'Resource_ProfilePayload_',
        'Resource_MatchPayload_', 'Resource_ReportPayload_',
    }
    for name in resource_schemas:
        prop = before[name]['properties']['execution_mode']
        assert prop['enum'] == ['pdf', 'rules', 'bm25_rules', 'offline']
        prop['enum'].append('llm')
    del before['Ready']['properties']['real_llm']['const']
    before['ProfilePayload']['properties']['comparison'] = {
        'anyOf': [{'$ref': '#/components/schemas/SkillComparison'}, {'type': 'null'}],
    }
    before['SkillComparison'] = {
        'properties': {
            'rule_only_skills': {'items': {'type': 'string'}, 'type': 'array', 'title': 'Rule Only Skills'},
            'model_only_skills': {'items': {'type': 'string'}, 'type': 'array', 'title': 'Model Only Skills'},
        },
        'additionalProperties': False, 'type': 'object', 'title': 'SkillComparison',
    }
    assert set(new['paths'].pop('/v1/reports/from-pdf')) == {'post'}
    assert new == expected
    assert API_VERSION == 'jobradar-api-v1'


def test_no_new_http_control_or_request_fields_on_existing_routes():
    old = json.loads((ROOT/'docs/STAGE5_OPENAPI.json').read_text())
    new = schema()
    assert {path: methods for path, methods in new['paths'].items() if path != '/v1/reports/from-pdf'} == old['paths']
    for name in ('ProfileCreate', 'ReportCreate', 'MatchCreate', 'ApiError', 'Accepted'):
        assert new['components']['schemas'][name] == old['components']['schemas'][name]
    assert ERROR_HTTP['llm_disabled'] == ERROR_HTTP['hybrid_not_enabled'] == 503


def test_v1_1_openapi_snapshot_exactly_matches_application():
    frozen = json.loads((ROOT/'docs/STAGE5_OPENAPI_V1_1.json').read_text())
    current = schema()
    assert set(current['paths'].pop('/v1/reports/from-pdf')) == {'post'}
    assert current == frozen


@pytest.mark.parametrize('kind,mode', [
    ('resume', 'pdf'), ('profile', 'rules'), ('match', 'bm25_rules'),
    ('report', 'offline'), ('profile', 'llm'), ('report', 'llm'),
])
def test_allowed_kind_modes_retain_resource_state_and_ids(kind, mode):
    parsed = Resource.model_validate(row(kind, mode))
    assert parsed.kind == kind and parsed.execution_mode == mode and parsed.state == 'queued'
    assert parsed.payload is None and parsed.error is None


@pytest.mark.parametrize('kind,mode', [
    ('resume', 'llm'), ('resume', 'rules'), ('profile', 'pdf'), ('profile', 'offline'),
    ('match', 'llm'), ('match', 'offline'), ('report', 'rules'), ('report', 'pdf'),
    ('resume', 'bm25_rules'), ('resume', 'offline'), ('profile', 'bm25_rules'),
    ('match', 'pdf'), ('match', 'rules'), ('report', 'bm25_rules'),
])
def test_invalid_kind_mode_pairs_are_rejected(kind, mode):
    with pytest.raises(ValidationError):
        Resource.model_validate(row(kind, mode))


def test_ready_boolean_is_not_a_paid_probe_and_hybrid_remains_false():
    assert Ready(status='ready', evidence=True, index=True).real_llm is False
    assert Ready(status='ready', evidence=True, index=True, real_llm=True).real_llm is True
    with pytest.raises(ValidationError):
        Ready(status='ready', evidence=True, index=True, hybrid=True)


def test_rules_profile_keeps_null_comparison_and_unconfirmed():
    document, _ = synthetic_inputs()
    rid = 'rsm_'+'a'*32
    profile = build_rule_profile(document, resume_id=rid)
    payload = ProfilePayload(resume_id=rid, document=document, profile=profile)
    assert payload.comparison is None and payload.profile.review_status == 'unconfirmed'
    value = payload.model_dump(mode='json')
    parsed = ProfilePayload.model_validate_json(json.dumps(value, ensure_ascii=False))
    assert parsed.model_dump(mode='json') == value


def test_structured_profile_returns_original_comparison_not_skill_union():
    document, _ = synthetic_inputs()
    rid = 'rsm_'+'a'*32
    empty = dict(education=[], skills=[], projects=[], work_experience=[], summary=None, warnings=[])
    result = structure_resume(document, resume_id=rid,
        client=FakeStructureClient([ClientResponse(status='completed', text=json.dumps(empty))]))
    before = result.model_dump_json()
    payload = ProfilePayload(resume_id=rid, document=document,
        profile=result.profile, comparison=result.comparison)
    parsed = ProfilePayload.model_validate_json(payload.model_dump_json())
    assert isinstance(parsed.comparison, SkillComparison)
    assert parsed.comparison == result.comparison
    assert parsed.comparison.rule_only_skills == ['FastAPI', 'Python']
    assert parsed.profile.skills == [] and parsed.profile.review_status == 'unconfirmed'
    assert result.model_dump_json() == before
    wire = row('profile', 'llm', payload=payload.model_dump(mode='json'))
    wire['created_at'] = wire['created_at'].isoformat()
    wire['updated_at'] = wire['updated_at'].isoformat()
    record = Resource[ProfilePayload].model_validate_json(json.dumps(wire, ensure_ascii=False))
    assert record.execution_mode == 'llm' and record.payload.profile.review_status == 'unconfirmed'
    envelope = Envelope(request_id='req_'+'a'*32, data=record)
    assert envelope.schema_version == 'jobradar-api-v1'


def test_default_runtime_keeps_live_and_hybrid_disabled_before_dependencies(tmp_path, monkeypatch):
    monkeypatch.setenv('JOBRADAR_LLM_ENABLED', 'true')
    # A synthetic env value is not read as authorization or used to construct SDKs.
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-not-real')
    settings = ServiceSettings(store_root=tmp_path/'store')
    with TestClient(create_app(settings)) as client:
        bodies = [('/v1/resumes/rsm_'+'a'*32+'/profiles', {'mode': 'llm'}),
            ('/v1/reports', {'match_id': 'mtc_'+'a'*32, 'mode': 'llm'})]
        for url, body in bodies:
            response = client.post(url, json=body, headers={'Idempotency-Key': 'disabled-contract'})
            assert response.status_code == 503 and response.json()['error']['code'] == 'llm_disabled'
        response = client.post('/v1/matches', json={
            'profile_id': 'prf_'+'a'*32, 'candidate_keys': [],
            'retrieval': {'mode': 'hybrid', 'allow_dense_fallback': True}},
            headers={'Idempotency-Key': 'hybrid-contract'})
        assert response.status_code == 503 and response.json()['error']['code'] == 'hybrid_not_enabled'
        assert client.app.state.offline_service.store.state['records'] == {}
        assert client.get('/v1/ready').json()['data']['real_llm'] is False
