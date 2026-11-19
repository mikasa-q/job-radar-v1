"""Offline administration for the loopback S1/J1 service check; never loads a key."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.api_v1 import create_app
from job_radar.api_v1_billing import (
    AllowedCall, Approval, BillingJournal, SERVICE_SCOPE, SERVICE_SEED_SHA256, initialize,
)
from job_radar.api_v1_models import MatchCreate
from job_radar.api_v1_provider import RESERVATIONS, build_wire, input_allocation
from job_radar.api_v1_reports import _match_snapshot
from job_radar.api_v1_runtime import ServiceSettings
from job_radar.api_v1_store import ResourceStore, ServiceError
from job_radar.rag_models import canonical_hash
from job_radar.report_analysis import build_analysis_request
from job_radar.report_evidence import build_evidence_packet
from job_radar.report_models import AnalysisConfig
from job_radar.resume_models import ResumeDocument
from job_radar.resume_structure import build_structure_request

from stage5_j1_checkpoint import DATASET, ACCEPTED_CANDIDATES
from stage5_s1_checkpoint import TEXT, digest, write


def load(path):
    return json.loads(path.read_text())


def allow(kind, value, snapshot, wire):
    return AllowedCall(kind=kind, context_sha256=canonical_hash({
        'kind': kind, 'input': value, 'snapshot': snapshot}),
        payload_sha256=canonical_hash(wire), input_limit={'profile': 12000, 'report': 20000}[kind])


def prepare(root, previous):
    root = root.resolve()
    previous = previous.resolve()
    manifest = load(previous.parent / 'MANIFEST.json')
    pdf = previous.parent / 'S1.pdf'
    predecessor = previous / 'state.json'
    jobs_path = previous.parent / 'original-jobs.json'
    if (manifest['source_pdf_sha256'] != digest(pdf)
            or manifest['artifact_sha256']['original-jobs.json'] != digest(jobs_path)):
        raise ServiceError('snapshot_conflict')
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    with TestClient(create_app(ServiceSettings(store_root=root / 'api-store'))) as client:
        response = client.post('/v1/resumes', files={'file': ('S1.pdf', pdf.read_bytes(), 'application/pdf')},
                               headers={'Idempotency-Key': 'service-check-s1-upload'})
        if response.status_code != 201:
            raise ServiceError('core_rejected')
        row = response.json()['data']
        snapshot = row['payload']
        doc = ResumeDocument.model_validate_json(json.dumps(snapshot, ensure_ascii=False))
        if (doc.provenance.source_sha256 != manifest['source_pdf_sha256']
                or [b.text for p in doc.pages for b in p.blocks] != [TEXT + '\n']):
            raise ServiceError('snapshot_conflict')
        value = {'mode': 'llm'}
        wire = build_wire('profile', build_structure_request(doc, timeout_seconds=90))
        call = allow('profile', value, snapshot, wire)
        store = client.app.state.offline_service.store
        approval = Approval(root=str(store.root), scope=SERVICE_SCOPE, seed_sha256=SERVICE_SEED_SHA256,
                            max_calls=2, expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                            seed_refs={str(predecessor): digest(predecessor)}, allowed=[call])
        totals = initialize(store, approval).status()
        summary = {'scope': SERVICE_SCOPE, 'store_root': str(store.root),
                   'profile_path': f"/v1/resumes/{row['resource_id']}/profiles",
                   'profile_idempotency_key': 'service-check-s1-profile', 'profile_body': value,
                   'allowed': call.model_dump(mode='json'), 'input_allocation': input_allocation(wire),
                   'source_pdf_sha256': doc.provenance.source_sha256,
                   'job_facts': [job_fact(job) for job in load(jobs_path)],
                   'model': approval.model, 'reasoning': approval.reasoning, 'retries': 0,
                   'expires_at': totals['approval']['expires_at'],
                   **{k: totals[k] for k in ('cap_cny', 'actual_cny', 'retained_reserve_cny', 'new_attempts')},
                   'first_send_exposure_cny': str(sent_exposure(totals, 'profile'))}
        write(root / 'S1_PLAN.json', summary)
    return summary


def sent_exposure(totals, kind):
    return sum(Decimal(totals[k]) for k in ('actual_cny', 'retained_reserve_cny',
                    'pending_usage_ceiling_cny')) + 2 * Decimal(RESERVATIONS[kind])


def job_fact(job):
    return {name: job[name] for name in ('dataset_id', 'job_id', 'fact_sha256', 'jd_sha256', 'review_status')}


def report_plan(service, match_id, approved_jobs):
    ledger = BillingJournal(service.store)
    totals = ledger.status()
    attempts = list(totals['attempts'].values())
    last_operation = next((e['type'] for e in reversed(totals['events']) if e['type'] != 'raise_service_cap'), None)
    if (totals['approval']['scope'] != SERVICE_SCOPE or totals['halted'] or totals['inflight']
            or len(attempts) != 1 or attempts[0]['kind'] != 'profile' or attempts[0]['state'] != 'completed'
            or last_operation != 'complete'):
        raise ServiceError('snapshot_conflict')
    snapshot = service.completed(match_id, 'match')
    # Require a match made from the new S1 profile, not a prior scope or a rules profile.
    record = service.store.state['records'][match_id]
    body = MatchCreate.model_validate_json(json.dumps(record['input']))
    if body.profile_id != attempts[0]['resource_id'] or body.retrieval.mode != 'bm25_rules' or body.purpose != 'debug':
        raise ServiceError('snapshot_conflict')
    saved = _match_snapshot(snapshot)
    candidates = [job_fact(job.model_dump(mode='json')) for job in saved.request.jobs]
    if ([(j['dataset_id'], j['job_id']) for j in candidates] != [(DATASET, i) for i in ACCEPTED_CANDIDATES]
            or candidates != approved_jobs):
        raise ServiceError('snapshot_conflict')
    config = AnalysisConfig(top_n=1)
    packet = build_evidence_packet(saved.request, saved.retrieval, saved.document, config=config)
    if [key.model_dump(mode='json') for key in packet.selected_keys] != [{'dataset_id': DATASET, 'job_id': 1}]:
        raise ServiceError('snapshot_conflict')
    request = build_analysis_request(packet, packet.selected_keys[0], config=config)
    value = {'match_id': match_id, 'top_n': 1, 'mode': 'llm'}
    wire = build_wire('report', request)
    projection = sent_exposure(totals, 'report')
    if projection > Decimal(totals['cap_cny']):
        raise ServiceError('budget_exhausted')
    return {'scope': SERVICE_SCOPE, 'store_root': str(service.store.root),
            'report_body': value, 'report_idempotency_key': 'service-check-j1-report',
            'profile_id': body.profile_id, 'ranking': [row.key.model_dump(mode='json')
                  for row in saved.retrieval.matching.results],
            'allowed': allow('report', value, snapshot, wire).model_dump(mode='json'),
            'input_allocation': input_allocation(wire), 'prompt_version': request.prompt_version,
            'first_send_exposure_cny': str(projection), 'cap_cny': totals['cap_cny']}


def raise_budget_cap(root):
    store = ResourceStore(root / 'api-store')
    if not (store.root / 'state.json').is_file():
        raise ServiceError('llm_disabled')
    store.open()
    try:
        totals = BillingJournal(store).raise_service_cap()
        keys = ('cap_cny', 'actual_cny', 'retained_reserve_cny', 'pending_usage_ceiling_cny', 'new_attempts')
        return {'scope': SERVICE_SCOPE, **{k: totals[k] for k in keys},
                'max_calls': totals['approval']['max_calls']}
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('run_root', type=Path)
    p.add_argument('previous_store', type=Path)
    p = sub.add_parser('raise-budget-cap', help='apply the one approved ¥1.10 → ¥2.00 change')
    p.add_argument('run_root', type=Path)
    for action in ('plan-report', 'authorize-report'):
        p = sub.add_parser(action)
        p.add_argument('run_root', type=Path)
        p.add_argument('match_id')
        if action == 'authorize-report':
            p.add_argument('--accept-plan-sha256', required=True)
    a = parser.parse_args()
    try:
        if a.action == 'prepare':
            plan = prepare(a.run_root, a.previous_store)
        elif a.action == 'raise-budget-cap':
            plan = raise_budget_cap(a.run_root)
        else:
            root = a.run_root.resolve()
            if not (root / 'api-store' / 'state.json').is_file():
                raise ServiceError('llm_disabled')
            with TestClient(create_app(ServiceSettings(store_root=root / 'api-store'))) as client:
                service = client.app.state.offline_service
                plan = report_plan(service, a.match_id, load(root / 'S1_PLAN.json')['job_facts'])
                plan['plan_sha256'] = canonical_hash(plan)
                if a.action == 'authorize-report':
                    if a.accept_plan_sha256 != plan['plan_sha256']:
                        raise ServiceError('snapshot_conflict')
                    BillingJournal(service.store).authorize([plan['allowed']])
                else:
                    write(root / 'J1_PLAN.json', plan)
    except ServiceError as error:
        parser.exit(1, error.code + '\n')
    print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
