"""Explicit local launcher for offline V1 or the approved Stage 5 service scope.

The default ``job_radar.api_v1:app`` has no credential discovery or live backend.
This launcher never initializes a ledger or submits an LLM request.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, asynccontextmanager
import logging
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from .api_v1_billing import BillingJournal, SERVICE_SCOPE, SERVICE_SEED_SHA256
from .api_v1_runtime import ServiceSettings
from .api_v1_store import ServiceError

_KEY_FIELD = 'OPENAI_COMPAT_API_KEY'
_KEY_LINE = re.compile(rf'^\s*(?:export\s+)?{_KEY_FIELD}\s*=\s*(.*)$')
_KEY_VALUE = re.compile(r'''(?:"([^"\s]+)"|'([^'\s]+)'|([^\s"'#]+))(?:\s+#.*)?''')
_BEARER = re.compile(r'[A-Za-z0-9._~+/-]+=*')
_MAX_DOTENV_BYTES = 256 * 1024


def _read_key() -> str:
    """Read one literal assignment, never source a shell file or consult env."""
    try:
        with (Path.home() / '.claude-mem' / '.env').open('rb') as stream:
            raw = stream.read(_MAX_DOTENV_BYTES + 1)
        if len(raw) > _MAX_DOTENV_BYTES:
            raise ValueError
        values = []
        for line in raw.decode('utf-8-sig').splitlines():
            match = _KEY_LINE.fullmatch(line)
            if match:
                literal = _KEY_VALUE.fullmatch(match[1].rstrip())
                if not literal:
                    raise ValueError
                values.append(next(value for value in literal.groups() if value is not None))
        if len(values) != 1 or not _BEARER.fullmatch(values[0]):
            raise ValueError
        return values[0]
    except (OSError, ValueError):
        raise ServiceError('llm_disabled') from None


def create_live_app(settings: ServiceSettings):
    """Attach the backend only in lifespan, under the service's store lock."""
    from .api_v1 import create_app
    from .api_v1_live import LiveBackend

    app = create_app(settings)
    offline_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        backend = None
        service = None
        try:
            # Do not create a new resource store because of a missing ledger.
            root = settings.store_root.expanduser().absolute()
            if (root.is_symlink() or root != root.resolve()
                    or (root / 'state.json').is_symlink() or not (root / 'state.json').is_file()):
                raise ServiceError('llm_disabled')
            async with offline_lifespan(app):
                service = app.state.offline_service
                ledger = BillingJournal(service.store)
                approval = ledger.status()['approval']
                if approval['scope'] != SERVICE_SCOPE or approval['seed_sha256'] != SERVICE_SEED_SHA256:
                    raise ServiceError('llm_disabled')
                ledger.require_ready()
                # The pinned SDK can merge ambient custom headers, including
                # Authorization, even when its api_key argument is explicit.
                if 'OPENAI_CUSTOM_HEADERS' in os.environ:
                    raise ServiceError('llm_disabled')
                backend = LiveBackend(service.store.root, api_key=_read_key(),
                                      scope=SERVICE_SCOPE, allow_report=True)
                service.live_backend = backend
                yield
            # offline_lifespan drains accepted work before we drop the key.
        finally:
            if backend is not None:
                backend.close()
                service.live_backend = None

    app.router.lifespan_context = lifespan
    return app


def main(argv=None):
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store-root', type=Path, required=True)
    parser.add_argument('--evidence-db', type=Path)
    parser.add_argument('--dataset-id', default='v1-evidence')
    index = parser.add_mutually_exclusive_group()
    index.add_argument('--index-root', type=Path)
    index.add_argument('--temporary-lexical-index', action='store_true',
                       help='build a disposable BM25 index (offline only)')
    parser.add_argument('--offline', action='store_true', help='do not attach a live backend or read a key')
    parser.add_argument('--host', default='127.0.0.1', choices=['127.0.0.1', '0.0.0.0'],
                        help='use 0.0.0.0 only inside a container published to host loopback')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port must be in 1..65535')
    if args.temporary_lexical_index and (not args.offline or args.evidence_db is None):
        parser.error('--temporary-lexical-index requires --offline and --evidence-db')
    # Do not let OPENAI_LOG or HTTP transport verbosity log provider details.
    for name in ('openai', 'httpx', 'httpx2', 'httpcore', 'httpcore2'):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)
    with ExitStack() as stack:
        index_root = args.index_root
        if args.temporary_lexical_index:
            from .evidence_repository import read_job_candidates
            from .rag_index import DerivedIndex, snapshot_hash
            from .rag_models import IndexManifest

            index_root = Path(stack.enter_context(TemporaryDirectory(prefix='jobradar-lexical-')))
            try:
                jobs = read_job_candidates(args.evidence_db, dataset_id=args.dataset_id)
                snapshot = snapshot_hash(jobs)
                DerivedIndex(index_root).update(jobs, manifest=IndexManifest(
                    generation='offline-' + snapshot[:16], snapshot_sha256=snapshot))
            except (OSError, ValueError, TypeError):
                parser.exit(1, '无法从指定的只读证据库构建离线索引。\n')
        settings = ServiceSettings(store_root=args.store_root, evidence_path=args.evidence_db,
                                   dataset_id=args.dataset_id, index_root=index_root,
                                   max_pending_tasks=1)
        if args.offline:
            from .api_v1 import create_app
            app = create_app(settings)
        else:
            app = create_live_app(settings)
        uvicorn.run(app, host=args.host, port=args.port, workers=1, reload=False,
                    proxy_headers=False, lifespan='on', access_log=False, ws='none')


if __name__ == '__main__':
    main()
