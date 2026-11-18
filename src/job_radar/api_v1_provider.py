"""Stage 5 billing boundary: pinned Responses SDK with explicit transport ownership.

The default constructor remains MockTransport-only; the explicit live backend
uses the same one-shot implementation without environment credential lookup. The original stages 1–4 payload builders and domain response
models remain authoritative; the journal owns every reservation and settlement.
"""
from __future__ import annotations

from copy import deepcopy
import json
import threading
import time

import httpx2
import openai

from .api_v1_store import ServiceError
from .report_apinebula import APINebulaAnalysisClient, BASE_URL, MODEL, SDK_VERSION
from .report_models import AnalysisResponse, CallAudit, strict_json
from .resume_structure import ClientResponse
from .resume_structure_apinebula import APINebulaStructureClient

INPUT_LIMITS = {'profile': 12000, 'report': 20000}
RESERVATIONS = {'profile': '0.0530088', 'report': '0.0670488'}
MAX_OUTPUT_TOKENS = 8192
MAX_RESPONSE_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 90.0
SYNTHETIC_API_KEY = 'synthetic-stage5-no-live'


def input_allocation(wire: dict) -> int:
    """Same conservative local unit as stage 4, not billable-token counting."""
    try:
        return len(json.dumps(wire, ensure_ascii=False, separators=(',', ':'),
                              allow_nan=False).encode('utf-8')) + 1024
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        raise ServiceError('core_rejected') from None


def build_wire(kind: str, request) -> dict:
    """Reuse pure old builders without constructing their env-reading clients."""
    if kind not in INPUT_LIMITS:
        raise ServiceError('core_rejected')
    try:
        if kind == 'report':
            # _payload only needs these two configuration scalars. No SDK/key or
            # old in-memory budget is constructed by this pure builder call.
            planner = object.__new__(APINebulaAnalysisClient)
            planner._max_output = MAX_OUTPUT_TOKENS
            planner._timeout = TIMEOUT_SECONDS
            wire = APINebulaAnalysisClient._payload(planner, request)
        else:
            planner = object.__new__(APINebulaStructureClient)
            wire = APINebulaStructureClient._payload(planner, request)
            wire.update(max_output_tokens=MAX_OUTPUT_TOKENS, store=False,
                        tools=[], stream=False)
        if input_allocation(wire) > INPUT_LIMITS[kind]:
            raise ServiceError('core_rejected')
        return deepcopy(wire)
    except ServiceError:
        raise
    except Exception:
        raise ServiceError('core_rejected') from None


def _usage(value, kind):
    if not isinstance(value, dict):
        raise ServiceError('upstream_error')
    usage = {name: value.get(name) for name in
             ('input_tokens', 'output_tokens', 'total_tokens')}
    inp, out, total = usage.values()
    if (not all(type(v) is int and v >= 0 for v in (inp, out, total))
            or inp < 1 or inp + out != total
            or inp > INPUT_LIMITS[kind] or out > MAX_OUTPUT_TOKENS):
        raise ServiceError('upstream_error')
    return usage


def _output(result, maximum):
    if result.get('model') != MODEL or result.get('status') != 'completed':
        raise ServiceError('upstream_error')
    items = result.get('output')
    if not isinstance(items, list):
        raise ServiceError('upstream_error')
    texts = []
    for item in items:
        if not isinstance(item, dict):
            raise ServiceError('upstream_error')
        if item.get('type') == 'reasoning':
            continue
        if (item.get('type') != 'message' or item.get('role') != 'assistant'
                or item.get('status') != 'completed'
                or not isinstance(item.get('content'), list)):
            raise ServiceError('upstream_error')
        for part in item['content']:
            if not isinstance(part, dict) or part.get('type') != 'output_text' or not isinstance(part.get('text'), str):
                raise ServiceError('upstream_error')
            texts.append(part['text'])
    if len(texts) != 1:
        raise ServiceError('upstream_error')
    try:
        if len(texts[0].encode('utf-8')) > maximum:
            raise ServiceError('upstream_error')
    except UnicodeError:
        raise ServiceError('upstream_error') from None
    return texts[0]


class BillingClient:
    """One authorized generation, persistent billing, then domain validation.

    ``complete`` does not mark the journal attempt completed: the application
    must first run its unchanged domain validators and only then call complete
    on the journal. Response arrival/usage settling is not semantic success.
    """
    def __init__(self, kind, ledger, rid, context_sha256, transport, *,
                 api_key=SYNTHETIC_API_KEY):
        if (kind not in INPUT_LIMITS or type(transport) is not httpx2.MockTransport
                or api_key != SYNTHETIC_API_KEY):
            raise ServiceError('llm_disabled')
        self._initialize(kind, ledger, rid, context_sha256, transport, api_key=api_key)

    def _initialize(self, kind, ledger, rid, context_sha256, transport, *, api_key):
        # Shared SDK construction; only the opt-in live boundary calls this with
        # a real HTTPTransport. Constructors do not issue HTTP requests.
        if openai.__version__ != SDK_VERSION:
            raise ServiceError('dependency_not_ready')
        self.kind, self.ledger, self.rid = kind, ledger, rid
        self.context_sha256 = context_sha256
        self.attempt_id = None
        self.failure_code = None
        self._last_audit = None
        self._lock = threading.RLock()
        self._closed = self._halted = False
        self._called = False
        http = httpx2.Client(transport=transport, trust_env=False,
                            follow_redirects=False, timeout=TIMEOUT_SECONDS)
        try:
            self._sdk = openai.OpenAI(api_key=api_key, base_url=BASE_URL,
                organization='', project='', max_retries=0, http_client=http,
                timeout=TIMEOUT_SECONDS)
        except Exception:
            http.close()
            raise ServiceError('dependency_not_ready') from None

    @property
    def last_call_audit(self):
        return None if self._last_audit is None else self._last_audit.model_copy(deep=True)

    @property
    def attempt_ids(self):
        """Immutable compatibility view; this client permits one generation."""
        return () if self.attempt_id is None else (self.attempt_id,)

    @property
    def audit(self):
        return {'generation_attempts': int(self.attempt_id is not None),
                'attempt_id': self.attempt_id, 'halted': self.failure_code,
                'count_api_requests': 0, 'mode': 'mock', 'provider': 'apinebula'}

    def halt(self, code='analysis_validation_failed'):
        # The core's semantic-halt code is not a new public error; classify it
        # using the existing API error vocabulary, without model body or path.
        if self._halted and self.failure_code is not None:
            return
        self._halted = True
        self.failure_code = code if code in {'execution_timeout', 'store_unavailable',
            'budget_halted', 'budget_exhausted', 'attempt_limit', 'snapshot_conflict',
            'snapshot_corrupt', 'llm_disabled'} else 'upstream_error'
        self.ledger.halt(self.failure_code)

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._sdk.close()

    def _remaining(self, deadline):
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('execution_timeout')
        return min(value, TIMEOUT_SECONDS)

    def _read(self, response, deadline):
        body = bytearray()
        for block in response.iter_bytes(chunk_size=4096):
            self._remaining(deadline)
            if len(body) + len(block) > MAX_RESPONSE_BYTES:
                raise ServiceError('upstream_error')
            body.extend(block)
        self._remaining(deadline)
        try:
            result = strict_json(body.decode('utf-8'))
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, UnicodeError, RecursionError):
            # Preserve a bounded undecodable response as plain diagnostic text;
            # unknown usage keeps the full pending ceiling and globally halts.
            self.ledger.settle(self.attempt_id, None, body.decode('utf-8', errors='replace'))
            raise ServiceError('upstream_error') from None

    def complete(self, request):
        with self._lock:
            if self._closed or self._halted or self._called:
                raise ServiceError('budget_halted')
            # Validation failures before this line do not consume a generation.
            wire = build_wire(self.kind, request)
            self._called = True
            started = time.monotonic()
            deadline = started + min(TIMEOUT_SECONDS, request.timeout_seconds)
            try:
                self.attempt_id = self.ledger.reserve(
                    self.rid, self.kind, self.context_sha256, wire)
                self._last_audit = CallAudit(provider='apinebula', model=MODEL,
                    attempts=1, reserved_units=RESERVATIONS[self.kind], billing_unit='CNY')
                with self._sdk.responses.with_streaming_response.create(
                        **wire, timeout=self._remaining(deadline)) as response:
                    result = self._read(response, deadline)
                # Preserve the original bounded response before interpreting
                # model/finish status. Known fees remain charged on rejection.
                self.ledger.settle(self.attempt_id, result.get('usage'), result)
                usage = _usage(result.get('usage'), self.kind)
                self._last_audit = CallAudit(provider='apinebula', model=MODEL,
                    attempts=1, reserved_units=RESERVATIONS[self.kind], billing_unit='CNY',
                    **usage, elapsed_seconds=max(0.0, time.monotonic() - started))
                text = _output(result, request.max_output_bytes)
                if self.kind == 'profile':
                    return ClientResponse(status='completed', text=text)
                return AnalysisResponse(status='completed', text=text, audit=self.last_call_audit)
            except (TimeoutError, openai.APITimeoutError):
                code = 'execution_timeout'
            except ServiceError as error:
                code = error.code
            except Exception:
                code = 'upstream_error'
            if self.attempt_id is None:
                self.failure_code = code
                raise ServiceError(code) from None
            try:
                self.halt(code)
            except Exception:
                self._halted = True
                self.failure_code = 'store_unavailable'
                raise ServiceError('store_unavailable') from None
            raise ServiceError(code) from None


class MockBackend:
    """Explicit programmatic injection for offline HTTP tests; never an env flag."""
    def __init__(self, transport):
        if type(transport) is not httpx2.MockTransport:
            raise ServiceError('llm_disabled')
        self.transport = transport

    def client(self, kind, ledger, rid, context_sha256):
        return BillingClient(kind, ledger, rid, context_sha256, self.transport)


# Both names designate the exact same type, preserving strict injection checks.
MockProvider = MockBackend
