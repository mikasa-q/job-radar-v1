"""Explicit Stage 5 approved live boundary; credentials are injected, never discovered.

The approved PLAN Phase 5 requires human review after the profile.
Report access additionally requires explicit allow_report=True and its own
full-wire approval in the same persistent ledger; the default is profile-only.
Its client reuses the tested complete-wire/reserve/send/settle implementation.
"""
from __future__ import annotations

from pathlib import Path
import threading

import httpx2

from .api_v1_provider import BillingClient, SYNTHETIC_API_KEY
from .api_v1_store import ServiceError

DEFAULT_SCOPE = 'stage5-s1-j1-live-20261118'


def _transport():
    # Uses the installed httpx2.HTTPTransport signature. No proxy/env settings,
    # redirects, connection retries or connection are performed by construction.
    return httpx2.HTTPTransport(retries=0, trust_env=False)


class LiveBackend:
    """Programmatic opt-in bound to one canonical journal and approval scope.

    No __repr__ includes credentials. The caller owns the short-lived execution
    process and may close this object to drop its credential reference.
    """
    __slots__ = ('root', 'scope', 'allow_report', '_api_key', '_closed', '_lock')

    def __init__(self, canonical_root, *, api_key, scope=DEFAULT_SCOPE, allow_report=False):
        requested = Path(canonical_root).expanduser().absolute()
        if (type(allow_report) is not bool
                or requested.is_symlink() or str(requested) != str(requested.resolve())
                or not isinstance(scope, str) or not 1 <= len(scope) <= 128
                or not isinstance(api_key, str) or not api_key.strip()
                or api_key != api_key.strip() or api_key == SYNTHETIC_API_KEY
                or any(c.isspace() for c in api_key)):
            raise ServiceError('llm_disabled')
        self.root, self.scope = str(requested), scope
        self.allow_report = allow_report
        self._api_key = api_key
        self._closed = False
        self._lock = threading.RLock()

    def _check(self, kind, ledger):
        if self._closed or kind not in ('profile', 'report') or (kind == 'report' and not self.allow_report):
            raise ServiceError('llm_disabled')
        status = ledger.status()
        approval = status['approval']
        if (str(ledger.store.root.resolve()) != self.root
                or approval['root'] != self.root or approval['scope'] != self.scope):
            raise ServiceError('llm_disabled')
        ledger.require_ready()

    def client(self, kind, ledger, rid, context_sha256):
        with self._lock:
            self._check(kind, ledger)
            return LiveBillingClient(self, ledger, rid, context_sha256, kind=kind)

    def close(self):
        with self._lock:
            self._closed = True
            self._api_key = None


class LiveBillingClient(BillingClient):
    """Approved kind: fixed endpoint/low model, zero retries, no env lookup."""
    def __init__(self, backend, ledger, rid, context_sha256, *, kind='profile'):
        if type(backend) is not LiveBackend:
            raise ServiceError('llm_disabled')
        backend._check(kind, ledger)
        self._backend = backend
        self._initialize(kind, ledger, rid, context_sha256, _transport(),
                         api_key=backend._api_key)

    @property
    def audit(self):
        return {**super().audit, 'mode': 'live'}

    def complete(self, request):
        # Recheck root/scope/expiry immediately before the inherited durable
        # reserve. The inherited ledger.reserve binds the full wire/context.
        with self._lock:
            self._backend._check(self.kind, self.ledger)
            return super().complete(request)
