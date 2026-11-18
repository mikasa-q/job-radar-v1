"""Atomic, retained-reserve billing facts in the resource journal.

No credentials, sockets, SDK construction or automatic initialization belong
here. A successful durable send-intent is the only permission to send once.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .api_v1_models import ERROR_HTTP
from .api_v1_store import ServiceError
from .rag_models import canonical_hash

SCHEMA = 'api-billing-v1'
MAX_RAW_BYTES = 1024 * 1024
SHA = r'^[0-9a-f]{64}$'
SEED = {'cap_cny': '1.00', 'historical_actual_cny': '0.05320458',
        'historical_reserved_cny': '0.6829056',
        'historical_exposure_cny': '0.73611018'}
SEED_SHA256 = canonical_hash(SEED)
RATES = {'input_cny_per_million': '0.78',
         'cache_create_cny_per_million': '0.975',
         'output_cny_per_million': '3.90'}
LIMITS = {'profile': 12000, 'report': 20000}


class AllowedCall(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['profile', 'report']
    context_sha256: str = Field(pattern=SHA)
    payload_sha256: str = Field(pattern=SHA)
    input_limit: int
    output_limit: Literal[8192] = 8192

    @model_validator(mode='after')
    def limits(self):
        if type(self.input_limit) is not int or self.input_limit != LIMITS[self.kind]:
            raise ValueError('frozen_input_limit')
        return self


class Approval(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    root: str
    scope: str = Field(min_length=1, max_length=128)
    seed_sha256: Literal[SEED_SHA256] = SEED_SHA256
    seed_refs: dict[str, str]
    expires_at: datetime
    max_calls: int = Field(ge=1, le=2)
    allowed: list[AllowedCall] = Field(min_length=1, max_length=2)
    model: Literal['gpt-6.1-sol'] = 'gpt-6.1-sol'
    reasoning: Literal['low'] = 'low'
    report_prompt: Literal['match-analysis-prompt-v3'] = 'match-analysis-prompt-v3'

    @model_validator(mode='after')
    def binding(self):
        if (self.root != str(Path(self.root).resolve()) or not self.seed_refs
                or self.expires_at.utcoffset() is None
                or self.expires_at.utcoffset().total_seconds() != 0
                or type(self.max_calls) is not int):
            raise ValueError('invalid_approval')
        for path, digest in self.seed_refs.items():
            if not Path(path).is_absolute() or not re.fullmatch(SHA, digest):
                raise ValueError('invalid_seed_ref')
        keys = [(x.kind, x.context_sha256, x.payload_sha256) for x in self.allowed]
        if len(set(keys)) != len(keys):
            raise ValueError('duplicate_allowed_call')
        return self


def _approval(value):
    # JSON is the persisted wire representation (strict datetime accepts its
    # ISO string only through the JSON validator, never arbitrary coercions).
    return Approval.model_validate_json(json.dumps(value, allow_nan=False))


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _money(value):
    if not isinstance(value, str):
        raise ValueError('decimal_string_required')
    d = Decimal(value)
    if not d.is_finite() or d < 0:
        raise ValueError('invalid_money')
    return d


def _cost(inp, out):
    return (Decimal(inp) * (Decimal('0.78') + Decimal('0.975'))
            + Decimal(out) * Decimal('3.90')) / Decimal(1000000)


def _reserve_amount(call):
    return _cost(call.input_limit, call.output_limit)


def _fits(actual, reserved, pending, amount):
    return actual + reserved + pending + 2 * amount <= Decimal('1.00')


def _usage(value):
    if not isinstance(value, dict):
        return None
    parts = [value.get(k) for k in ('input_tokens', 'output_tokens', 'total_tokens')]
    if (any(type(x) is not int or x < 0 for x in parts)
            or parts[0] < 1 or parts[0] + parts[1] != parts[2]):
        return None
    return dict(zip(('input_tokens', 'output_tokens', 'total_tokens'), parts))


def _allocation(payload):
    return len(json.dumps(payload, ensure_ascii=False, allow_nan=False,
                          separators=(',', ':')).encode('utf-8')) + 1024


def _context(state, rid, kind):
    item = state['records'][rid]
    if (item['resource']['kind'] != kind
            or item['resource']['execution_mode'] != 'llm'):
        raise ValueError('billing_resource_mismatch')
    return canonical_hash({'kind': kind, 'input': item['input'],
                           'snapshot': item['snapshot']})


def _genesis(b):
    return canonical_hash({'schema_version': SCHEMA, 'seed': b['seed'],
                           'approval': b['approval'], 'rates': RATES})


def _append(state, typ, **data):
    b = state['billing']
    event = {'seq': len(b['events']) + 1, 'type': typ, 'at': _stamp(),
             'prev_sha256': b['events'][-1]['sha256'] if b['events'] else _genesis(b),
             **deepcopy(data)}
    event['sha256'] = canonical_hash(event)
    b['events'].append(event)
    b['totals'] = _derive(state)


def _derive(state):
    b = state['billing']; approval = _approval(b['approval'])
    actual = _money(SEED['historical_actual_cny'])
    reserved = _money(SEED['historical_reserved_cny']); pending = Decimal(0)
    attempts = {}; used = set(); halted = False; halt_code = None; inflight = None
    previous = _genesis(b)
    allowed = list(approval.allowed)
    if not isinstance(b['events'], list) or len(b['events']) > 100:
        raise ValueError('invalid_events')
    for seq, event in enumerate(b['events'], 1):
        if not isinstance(event, dict):raise ValueError('invalid_event')
        content = {k: v for k, v in event.items() if k != 'sha256'}
        if (type(event['seq']) is not int or event['seq'] != seq
                or event['prev_sha256'] != previous
                or canonical_hash(content) != event['sha256']):
            raise ValueError('event_chain')
        stamp = datetime.fromisoformat(event['at'])
        if stamp.utcoffset() is None:raise ValueError('event_time')
        previous = event['sha256']; typ = event['type']
        common = {'seq', 'type', 'at', 'prev_sha256', 'sha256'}
        if typ == 'authorize':
            if (set(event) != common | {'allowed'} or halted or inflight is not None
                    or stamp >= approval.expires_at or not attempts
                    or any(a['state'] != 'completed' for a in attempts.values())):
                raise ValueError('invalid_authorization_checkpoint')
            additions = [AllowedCall.model_validate_json(json.dumps(c)) for c in event['allowed']]
            if not additions or len(allowed) + len(additions) > approval.max_calls:
                raise ValueError('authorization_limit')
            keys = {(c.kind, c.context_sha256, c.payload_sha256) for c in allowed}
            for call in additions:
                key = (call.kind, call.context_sha256, call.payload_sha256)
                if call.kind != 'report' or key in keys:
                    raise ValueError('authorization_duplicate')
                keys.add(key)
            allowed.extend(additions)
        elif typ == 'send_intent':
            expected = common | {'attempt_id', 'resource_id', 'kind', 'context_sha256',
                'payload_sha256', 'allowed_index', 'local_input_allocation', 'reservation_cny'}
            index = event['allowed_index']
            if type(index) is not int or index < 0 or index >= len(allowed):
                raise ValueError('allowed_index')
            call = allowed[index]; amount = _reserve_amount(call)
            if (set(event) != expected or halted or inflight is not None
                    or index in used or len(attempts) >= min(9, approval.max_calls)
                    or not re.fullmatch(r'att_[0-9a-f]{32}', event['attempt_id'])
                    or event['attempt_id'] in attempts
                    or event['kind'] != call.kind
                    or event['context_sha256'] != call.context_sha256
                    or event['payload_sha256'] != call.payload_sha256
                    or _context(state, event['resource_id'], call.kind) != call.context_sha256
                    or type(event['local_input_allocation']) is not int
                    or not 1024 <= event['local_input_allocation'] <= call.input_limit
                    or _money(event['reservation_cny']) != amount
                    or not _fits(actual, reserved, pending, amount)
                    or stamp >= approval.expires_at):
                raise ValueError('invalid_send_intent')
            inflight = event['attempt_id']; used.add(index)
            reserved += amount; pending += amount
            attempts[inflight] = {**deepcopy(event), 'state': 'send_intent',
                                  'usage': None, 'actual_cny': None}
        elif typ == 'settled':
            expected = common | {'attempt_id', 'usage', 'actual_cny', 'raw',
                                  'raw_sha256', 'raw_valid', 'usage_within_limit'}
            aid = event['attempt_id']; usage = _usage(event['usage'])
            if (set(event) != expected or aid != inflight or halted
                    or attempts[aid]['state'] != 'send_intent'
                    or canonical_hash(event['raw']) != event['raw_sha256']
                    or len(json.dumps(event['raw'], ensure_ascii=False,
                         allow_nan=False).encode('utf-8')) > MAX_RAW_BYTES
                    or type(event['raw_valid']) is not bool
                    or (event['raw_valid'] and not isinstance(event['raw'], (dict, str)))
                    or canonical_hash(event['usage']) != canonical_hash(usage)):
                raise ValueError('invalid_settlement')
            attempt = attempts[aid]; call = allowed[attempt['allowed_index']]
            within = usage is not None and usage['input_tokens'] <= call.input_limit and usage['output_tokens'] <= call.output_limit
            if event['usage_within_limit'] is not within:
                raise ValueError('invalid_usage_limit')
            if usage is None:
                if event['actual_cny'] is not None:raise ValueError('unknown_usage_cost')
            else:
                amount = _cost(usage['input_tokens'], usage['output_tokens'])
                if _money(event['actual_cny']) != amount:raise ValueError('usage_cost_mismatch')
                actual += amount; pending -= _reserve_amount(call)
            attempt.update(state='settled', usage=usage, actual_cny=event['actual_cny'],
                           usage_within_limit=within, raw_valid=event['raw_valid'])
        elif typ == 'complete':
            aid = event['attempt_id']; attempt = attempts.get(aid)
            if (set(event) != common | {'attempt_id'} or halted or aid != inflight
                    or not attempt or attempt['state'] != 'settled'
                    or not attempt['usage_within_limit'] or not attempt['raw_valid']):
                raise ValueError('invalid_completion')
            attempt['state'] = 'completed'; inflight = None
        elif typ == 'halt':
            if set(event) != common | {'code'} or event['code'] not in ERROR_HTTP:
                raise ValueError('invalid_halt')
            halted = True
            if halt_code is None:halt_code = event['code']
        else:raise ValueError('unknown_event')
    return {'actual_cny': str(actual), 'retained_reserve_cny': str(reserved),
            'pending_usage_ceiling_cny': str(pending), 'new_attempts': len(attempts),
            'halted': halted, 'halt_code': halt_code, 'inflight': inflight,
            'attempts': attempts}


def validate_billing(state, root):
    """Validate from facts, not trusted cached counters; old offline stores work."""
    try:
        if 'billing' not in state:
            if state.get('schema_version') == 'api-store-v1.1':raise ValueError('missing_billing')
            return
        b = state['billing']
        if (state['schema_version'] != 'api-store-v1.1'
                or set(b) != {'schema_version', 'seed', 'approval', 'events', 'totals'}
                or b['schema_version'] != SCHEMA or b['seed'] != SEED):
            raise ValueError('billing_schema')
        approval = _approval(b['approval'])
        if approval.root != str(Path(root).resolve()):raise ValueError('billing_root')
        totals = _derive(state)
        if canonical_hash(totals) != canonical_hash(b['totals']):raise ValueError('billing_totals')
        if (any(a['state'] == 'settled' and
                (not a['usage_within_limit'] or not a['raw_valid'])
                for a in totals['attempts'].values()) and not totals['halted']):
            raise ValueError('missing_failure_halt')
    except ServiceError:raise
    except Exception:
        raise ServiceError('snapshot_corrupt') from None


def recover_billing(state):
    """Startup is an uncertainty boundary: retain all facts and never resend."""
    if 'billing' in state and state['billing']['totals']['inflight'] is not None:
        if not state['billing']['totals']['halted']:
            _append(state, 'halt', code='task_interrupted')


def complete_in_state(state, attempt_id):
    """For ResourceStore.transition's single result+completion transaction."""
    try:_append(state, 'complete', attempt_id=attempt_id)
    except Exception:raise ServiceError('budget_halted') from None


def _effective_allowed(b):
    calls = list(_approval(b['approval']).allowed)
    for event in b['events']:
        if event['type'] == 'authorize':
            calls.extend(AllowedCall.model_validate_json(json.dumps(c)) for c in event['allowed'])
    return calls


def initialize(store, approval):
    """Explicit administrative action, not an implicit zero-balance fallback."""
    if not isinstance(approval, Approval):raise ServiceError('llm_disabled')
    with store.mutex:
        if store.write_failed or store.state is None:raise ServiceError('store_unavailable')
        if 'billing' in store.state:raise ServiceError('snapshot_conflict')
        if approval.root != str(store.root.resolve()) or approval.expires_at <= datetime.now(timezone.utc):
            raise ServiceError('llm_disabled')
        # Only explicit administration reads ordinary provenance artifacts.
        # Runtime/recovery does not depend on external seed files remaining.
        try:
            for name, digest in approval.seed_refs.items():
                path = Path(name)
                if (path.name in {'.env', 'auth.json'} or path.is_symlink()
                        or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024
                        or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
                    raise ValueError('seed_source_mismatch')
        except Exception:
            raise ServiceError('snapshot_conflict') from None
        def commit():
            store.state['schema_version'] = 'api-store-v1.1'
            store.state['billing'] = {'schema_version': SCHEMA, 'seed': deepcopy(SEED),
                'approval': approval.model_dump(mode='json'), 'events': [], 'totals': {}}
            store.state['billing']['totals'] = _derive(store.state)
        _transaction(store, commit)
    return BillingJournal(store)


def _transaction(store, mutation):
    try:return store._transaction(mutation)
    except OSError:
        store.write_failed = True
        raise ServiceError('store_unavailable') from None


class BillingJournal:
    def __init__(self, store):self.store = store

    def _state(self):
        if self.store.write_failed or self.store.state is None:
            raise ServiceError('store_unavailable')
        validate_billing(self.store.state, self.store.root)
        if 'billing' not in self.store.state:raise ServiceError('llm_disabled')
        return self.store.state['billing']

    def status(self):
        with self.store.mutex:
            b = self._state()
            return deepcopy({**b['totals'], 'events': b['events'], 'approval': b['approval'],
                             'cap_cny': SEED['cap_cny']})

    def require_ready(self):
        with self.store.mutex:
            b = self._state(); a = _approval(b['approval']); t = b['totals']
            if t['halted'] or t['inflight'] is not None:raise ServiceError('budget_halted')
            if datetime.now(timezone.utc) >= a.expires_at:raise ServiceError('llm_disabled')
            used = {x['allowed_index'] for x in t['attempts'].values()}
            remaining = [c for i, c in enumerate(_effective_allowed(b)) if i not in used]
            if t['new_attempts'] >= min(9, a.max_calls) or not remaining:
                raise ServiceError('attempt_limit')
            amounts = tuple(_money(t[k]) for k in ('actual_cny', 'retained_reserve_cny',
                                                  'pending_usage_ceiling_cny'))
            if not any(_fits(*amounts, _reserve_amount(c)) for c in remaining):
                raise ServiceError('budget_exhausted')

    def ready(self):
        try:
            self.require_ready()
            return True
        except Exception:return False

    def _check(self, kind, context_sha256, payload):
        self.require_ready()
        b = self._state(); a = _approval(b['approval']); t = b['totals']
        try:
            digest = canonical_hash(payload); allocated = _allocation(payload)
            call_index = next(i for i, c in enumerate(_effective_allowed(b))
                if c.kind == kind and c.context_sha256 == context_sha256 and c.payload_sha256 == digest)
            call = _effective_allowed(b)[call_index]
            if not isinstance(payload, dict) or allocated > call.input_limit:
                raise ValueError('allocation')
            fmt = payload['text']['format']
            if (payload.get('model') != 'gpt-6.1-sol'
                    or payload.get('reasoning') != {'effort': 'low'}
                    or type(payload.get('max_output_tokens')) is not int
                    or payload.get('max_output_tokens') != 8192
                    or payload.get('truncation') != 'disabled'
                    or payload.get('store') is not False
                    or payload.get('stream') is not False
                    or payload.get('tools') != [] or fmt.get('strict') is not True
                    or fmt.get('type') != 'json_schema'
                    or not isinstance(fmt.get('schema'), dict)):
                raise ValueError('wire_config')
        except Exception:raise ServiceError('core_rejected') from None
        if any(v['allowed_index'] == call_index for v in t['attempts'].values()):
            raise ServiceError('attempt_limit')
        amount = _reserve_amount(call)
        if not _fits(*(_money(t[k]) for k in ('actual_cny', 'retained_reserve_cny',
             'pending_usage_ceiling_cny')), amount):
            raise ServiceError('budget_exhausted')
        return call_index, call, allocated

    def check(self, kind, context_sha256, payload):
        with self.store.mutex:self._check(kind, context_sha256, payload)

    def reserve(self, resource_id, kind, context_sha256, payload):
        with self.store.mutex:
            index, call, allocated = self._check(kind, context_sha256, payload)
            try:
                if (_context(self.store.state, resource_id, kind) != context_sha256
                        or self.store.state['records'][resource_id]['resource']['state'] not in ('queued', 'running')):
                    raise ValueError('resource_binding')
            except Exception:raise ServiceError('snapshot_conflict') from None
            aid = 'att_' + uuid.uuid4().hex
            _transaction(self.store, lambda: _append(self.store.state, 'send_intent',
                attempt_id=aid, resource_id=resource_id, kind=kind,
                context_sha256=context_sha256, payload_sha256=call.payload_sha256,
                allowed_index=index, local_input_allocation=allocated,
                reservation_cny=str(_reserve_amount(call))))
            return aid

    def settle(self, attempt_id, usage, raw):
        with self.store.mutex:
            b = self._state(); t = b['totals']; attempt = t['attempts'].get(attempt_id)
            if t['halted'] or attempt_id != t['inflight'] or not attempt or attempt['state'] != 'send_intent':
                raise ServiceError('budget_halted')
            call = _effective_allowed(b)[attempt['allowed_index']]
            normalized = _usage(usage)
            within = (normalized is not None and normalized['input_tokens'] <= call.input_limit
                      and normalized['output_tokens'] <= call.output_limit)
            raw_valid = isinstance(raw, (dict, str))
            try:
                encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False).encode('utf-8')
                if len(encoded) > MAX_RAW_BYTES:raw_valid = False
            except Exception:
                encoded = b''; raw_valid = False
            diagnostic = deepcopy(raw) if raw_valid else {
                'diagnostic_omitted': True, 'bytes': len(encoded),
                'sha256': hashlib.sha256(encoded).hexdigest()}
            cost = str(_cost(normalized['input_tokens'], normalized['output_tokens'])) if normalized else None
            def commit():
                _append(self.store.state, 'settled', attempt_id=attempt_id,
                    usage=normalized, actual_cny=cost, raw=diagnostic,
                    raw_sha256=canonical_hash(diagnostic), raw_valid=raw_valid,
                    usage_within_limit=within)
                if not within or not raw_valid:
                    _append(self.store.state, 'halt', code='upstream_error')
            _transaction(self.store, commit)
            if not within or not raw_valid:raise ServiceError('upstream_error')

    def authorize(self, allowed):
        """Explicit local report checkpoint, never changes genesis/expiry/cap."""
        with self.store.mutex:
            b = self._state()
            try:
                additions = [c if isinstance(c, AllowedCall) else
                    AllowedCall.model_validate_json(json.dumps(c)) for c in allowed]
                def commit():
                    _append(self.store.state, 'authorize',
                            allowed=[c.model_dump(mode='json') for c in additions])
                _transaction(self.store, commit)
            except ServiceError:
                raise
            except Exception:
                raise ServiceError('snapshot_conflict') from None

    def finish_in_state(self, state, attempt_ids):
        """Called only inside the resource-success transaction; no nested write."""
        if state is not self.store.state or len(attempt_ids) != 1:
            raise ServiceError('snapshot_conflict')
        for aid in attempt_ids:
            attempt = state['billing']['totals']['attempts'].get(aid)
            if (not attempt or state['records'][attempt['resource_id']]['resource']['state'] != 'succeeded'):
                raise ServiceError('snapshot_conflict')
            complete_in_state(state, aid)

    def complete(self, attempt_id):
        with self.store.mutex:
            self._state()
            _transaction(self.store, lambda: complete_in_state(self.store.state, attempt_id))

    def halt(self, code='upstream_error'):
        if code not in ERROR_HTTP:code = 'upstream_error'
        with self.store.mutex:
            b = self._state()
            if not b['totals']['halted']:
                _transaction(self.store, lambda: _append(self.store.state, 'halt', code=code))
