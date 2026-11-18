"""Single-instance, private, atomic resource + idempotency journal (no SQL writes)."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid

from .api_v1_models import Resource, ApiError, RESOURCE_PATTERN
from .api_v1_budget import FROZEN_POLICY
from .rag_models import canonical_hash

OWNER = 'jobradar-api-store-v1\n'
MAX_STORE_BYTES = 256 * 1024 * 1024
MAX_RESOURCES = 256


class ServiceError(Exception):
    def __init__(self, code, fields=None):
        self.code, self.fields = code, fields or []
        super().__init__(code)


def now():
    return datetime.now(timezone.utc).isoformat()


class ResourceStore:
    def __init__(self, root):
        requested = Path(root).expanduser().absolute()
        if requested.is_symlink():
            raise ServiceError('store_unavailable')
        self.root = requested.parent.resolve() / requested.name
        self.mutex = threading.RLock()
        self.lock = None
        self.state = None
        self.write_failed = False

    def open(self):
        if self.lock is not None:
            raise ServiceError('store_unavailable')
        try:
            self.write_failed = False
            current = self.root
            while current != current.parent:
                if current.is_symlink():
                    raise ServiceError('store_unavailable')
                current = current.parent
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.root.stat().st_mode & 0o077:
                raise ServiceError('store_unavailable')
            owner = self.root / 'OWNER'
            owner_existed = owner.exists()
            if not owner_existed:
                if any(self.root.iterdir()):
                    raise ServiceError('store_unavailable')
                with owner.open('x') as out:
                    out.write(OWNER)
                owner.chmod(0o600)
            if owner.is_symlink() or owner.read_text() != OWNER:
                raise ServiceError('store_unavailable')
            if any(p.is_symlink() for p in self.root.iterdir()):
                raise ServiceError('store_unavailable')
            self.lock = (self.root / '.instance.lock').open('a+b')
            os.chmod(self.root / '.instance.lock', 0o600)
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            journal = self.root / 'state.json'
            if journal.exists():
                if journal.stat().st_size > MAX_STORE_BYTES:
                    raise ServiceError('snapshot_corrupt')
                saved = json.loads(journal.read_text())
                if canonical_hash(saved['content']) != saved['sha256']:
                    raise ServiceError('snapshot_corrupt')
                self.state = saved['content']
                if self.state['schema_version'] not in ('api-store-v1','api-store-v1.1') or self.state['policy'] != FROZEN_POLICY:
                    raise ServiceError('snapshot_corrupt')
                self._validate()
                from .api_v1_billing import recover_billing
                recover_billing(self.state)
                for entry in self.state['records'].values():
                    if entry['resource']['state'] in ('queued','running'):
                        entry['resource'].update(state='interrupted', updated_at=now(),
                            error=ApiError(code='task_interrupted').model_dump())
            else:
                if owner_existed:
                    raise ServiceError('snapshot_corrupt')
                self.state = {'schema_version':'api-store-v1','policy':deepcopy(FROZEN_POLICY),
                              'records':{},'idempotency':{}}
            self._save()
        except ServiceError:
            self.close()
            raise
        except (OSError, ValueError, KeyError, TypeError):
            self.close()
            raise ServiceError('store_unavailable') from None

    def _validate(self):
        from .api_v1_billing import validate_billing
        validate_billing(self.state, self.root)
        if (self.state['schema_version'] not in ('api-store-v1','api-store-v1.1')
                or self.state['policy'] != FROZEN_POLICY
                or not isinstance(self.state['records'], dict)
                or len(self.state['records']) > MAX_RESOURCES
                or len(self.state['idempotency']) != len(self.state['records'])):
            raise ServiceError('snapshot_corrupt')
        for rid, entry in self.state['records'].items():
            record = Resource.model_validate(entry['resource'])
            if canonical_hash(entry['snapshot']) != entry['snapshot_sha256']:
                raise ServiceError('snapshot_corrupt')
            if rid != record.resource_id or canonical_hash(entry['input']) != record.input_sha256:
                raise ServiceError('snapshot_corrupt')
            if record.payload is not None and canonical_hash(record.payload) != record.payload_sha256:
                raise ServiceError('snapshot_corrupt')
        bound = set()
        for token, binding in self.state['idempotency'].items():
            if not re.fullmatch(r'[0-9a-f]{64}', token) or binding['resource_id'] in bound:
                raise ServiceError('snapshot_corrupt')
            bound.add(binding['resource_id'])
            record = self.state['records'][binding['resource_id']]
            if binding['input_sha256'] != record['resource']['input_sha256']:
                raise ServiceError('snapshot_corrupt')

    def _save(self):
        self._validate()
        raw = json.dumps({'content':self.state,'sha256':canonical_hash(self.state)},
                         ensure_ascii=False, allow_nan=False, separators=(',',':')).encode()
        if len(raw) > MAX_STORE_BYTES:
            raise ServiceError('store_unavailable')
        fd, temporary = tempfile.mkstemp(prefix='.state-', dir=self.root)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,self.root/'state.json')
            dfd = os.open(self.root,os.O_RDONLY)
            try:os.fsync(dfd)
            finally:os.close(dfd)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)

    def _transaction(self, mutation):
        before = deepcopy(self.state)
        try:
            result = mutation()
            self._save()
            return result
        except Exception as error:
            self.state = before
            # An fsync failure may follow replace: durability is uncertain. Stop
            # serving this instance rather than inventing a committed result.
            if isinstance(error, OSError):
                self.write_failed = True
            raise

    def close(self):
        if self.lock is not None:
            self.lock.close();self.lock=None

    def token(self, route, key):
        if key is None:
            raise ServiceError('idempotency_key_required')
        if not re.fullmatch(r'[A-Za-z0-9._:-]{8,128}',key):
            raise ServiceError('idempotency_key_invalid')
        return canonical_hash([route,key])

    def lookup(self, route, key, value):
        with self.mutex:
            if self.write_failed or self.state is None:
                raise ServiceError('store_unavailable')
            token = self.token(route,key)
            found = self.state['idempotency'].get(token)
            if found:
                if found['input_sha256'] != canonical_hash(value):
                    raise ServiceError('idempotency_conflict')
                return self.get(found['resource_id'])
            return None

    def create(self, route, key, value, kind, mode, *, snapshot=None):
        with self.mutex:
            found = self.lookup(route,key,value)
            if found:return found,False
            if len(self.state['records']) >= MAX_RESOURCES:
                raise ServiceError('store_unavailable')
            prefix = {'resume':'rsm','profile':'prf','match':'mtc','report':'rpt'}[kind]
            rid = prefix+'_'+uuid.uuid4().hex
            stamp = now()
            record = Resource(resource_id=rid,kind=kind,state='queued',execution_mode=mode,
                created_at=stamp,updated_at=stamp,input_sha256=canonical_hash(value)).model_dump(mode='json')
            def write():
                self.state['records'][rid]={'resource':record,'input':deepcopy(value),
                    'snapshot':deepcopy(snapshot),'snapshot_sha256':canonical_hash(snapshot)}
                self.state['idempotency'][self.token(route,key)]={'input_sha256':record['input_sha256'],'resource_id':rid}
            self._transaction(write)
            return deepcopy(record),True

    def get(self, rid):
        with self.mutex:
            if self.write_failed or self.state is None:
                raise ServiceError('store_unavailable')
            if not re.fullmatch(RESOURCE_PATTERN,rid):
                raise ServiceError('resource_not_found')
            item = self.state['records'].get(rid)
            if item is None:raise ServiceError('resource_not_found')
            try:
                r = Resource.model_validate(item['resource'])
                if (rid != r.resource_id
                        or canonical_hash(item['input']) != r.input_sha256
                        or canonical_hash(item['snapshot']) != item['snapshot_sha256']
                        or (r.payload is not None and canonical_hash(r.payload) != r.payload_sha256)):
                    raise ServiceError('snapshot_corrupt')
            except (ValueError, KeyError, TypeError):
                raise ServiceError('snapshot_corrupt') from None
            return deepcopy(item['resource'])

    def transition(self, rid, state, *, payload=None, code=None, billing_finish=None):
        with self.mutex:
            record = self.get(rid)
            allowed = {'queued':{'running','interrupted'},'running':{'succeeded','failed','interrupted'}}
            if state not in allowed.get(record['state'],set()):
                raise ServiceError('snapshot_conflict')
            record.update(state=state,updated_at=now(),payload=deepcopy(payload),
                          payload_sha256=canonical_hash(payload) if payload is not None else None,
                          error=ApiError(code=code).model_dump() if code else None)
            Resource.model_validate(record)
            def commit():
                self.state['records'][rid].update(resource=record)
                if billing_finish is not None:
                    billing_finish(self.state)
            self._transaction(commit)
