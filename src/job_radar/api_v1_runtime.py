"""Single shared lifecycle, serialized execution, and hard-disabled live gate."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import threading
from copy import deepcopy

from .api_v1_store import ResourceStore, ServiceError
from .api_v1_budget import require_live_authorization, BudgetRejected
from .retrieval_models import RetrievalError
from .api_v1_resumes import ResumeInterface, extract_isolated
from .api_v1_matching import MatchingInterface
from .api_v1_reports import ReportInterface

@dataclass(frozen=True)
class ServiceSettings:
    store_root: Path
    evidence_path: Path | None = None
    dataset_id: str = 'v1-evidence'
    index_root: Path | None = None
    max_pending_tasks: int = 4
    pdf_timeout_seconds: float = 15.0

    def __post_init__(self):
        if not 1 <= self.max_pending_tasks <= 16 or not 0 < self.pdf_timeout_seconds <= 30:
            raise ValueError('invalid_runtime_limits')


class OfflineService(ResumeInterface, MatchingInterface, ReportInterface):
    def __init__(self, settings, *, mock_backend=None, live_backend=None):
        self.settings=settings
        if mock_backend is not None and live_backend is not None:
            raise ServiceError('llm_disabled')
        self.mock_backend=mock_backend
        self.live_backend=live_backend
        self.store=ResourceStore(settings.store_root)
        self.gate=threading.RLock()
        self.slots=threading.BoundedSemaphore(settings.max_pending_tasks)
        self.executor=None

    def start(self):
        with self.gate:
            if self.executor is not None:
                raise ServiceError('dependency_not_ready')
            self.store.open()
            try:
                self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='jobradar-offline')
            except Exception:
                self.store.close()
                raise ServiceError('dependency_not_ready') from None

    def close(self):
        with self.gate:
            if self.executor is not None:
                self.executor.shutdown(wait=True,cancel_futures=False)
                self.executor=None
            self.store.close()

    def completed(self,rid,kind):
        resource=self.store.get(rid)
        if resource['kind']!=kind:raise ServiceError('resource_not_found')
        if resource['state']!='succeeded':raise ServiceError('snapshot_conflict')
        return resource['payload']

    def submit(self,route,key,value,kind,mode,prepare,execute):
        # Idempotency lookup precedes database/parent reads: replay uses original snapshot.
        with self.gate:
            found=self.store.lookup(route,key,value)
            if found:return found
            if self.executor is None:raise ServiceError('dependency_not_ready')
            if not self.slots.acquire(blocking=False):raise ServiceError('queue_full')
            try:
                snapshot=deepcopy(prepare())
                record,_=self.store.create(route,key,value,kind,mode,snapshot=snapshot)
                try:
                    self.executor.submit(self._execute,record['resource_id'],snapshot,execute)
                except RuntimeError:
                    self.store.transition(record['resource_id'],'interrupted',code='task_interrupted')
                    raise ServiceError('dependency_not_ready') from None
                return record
            except Exception:self.slots.release();raise

    def _execute(self,rid,snapshot,execute, *, context=None):
        try:
            self.store.transition(rid,'running')
            if context is None:
                payload=execute(snapshot)
                self.store.transition(rid,'succeeded',payload=payload)
            else:
                from .api_v1_billing import BillingJournal
                ledger=BillingJournal(self.store)
                client=None
                try:
                    client=self.billing_backend().client(self.store.get(rid)['kind'],ledger,rid,context)
                    payload=execute(snapshot,client)
                    self.store.transition(rid,'succeeded',payload=payload,
                        billing_finish=lambda state:ledger.finish_in_state(state,client.attempt_ids))
                except Exception:
                    try:ledger.halt('core_rejected')
                    except Exception:raise ServiceError('store_unavailable') from None
                    if client is not None and client.failure_code:raise ServiceError(client.failure_code) from None
                    raise
                finally:
                    if client is not None:client.close()
        except ServiceError as error:self._fail_task(rid,error.code)
        except RetrievalError:self._fail_task(rid,'index_unavailable')
        except OSError:self._fail_task(rid,'store_unavailable')
        except ValueError:self._fail_task(rid,'core_rejected')
        except Exception:self._fail_task(rid,'internal_error')
        finally:self.slots.release()

    def _fail_task(self,rid,code):
        try:
            self.store.transition(rid,'failed',code=code)
        except (OSError,ServiceError):
            # Never label a non-durable result succeeded. Startup will mark a
            # persisted pending task interrupted; this instance fails closed.
            self.store.write_failed=True

    def billing_backend(self):
        from .api_v1_provider import MockBackend
        if type(self.mock_backend) is MockBackend and self.live_backend is None:
            return self.mock_backend
        if self.live_backend is not None and self.mock_backend is None:
            from .api_v1_live import LiveBackend
            if type(self.live_backend) is LiveBackend:
                return self.live_backend
        raise ServiceError('llm_disabled')

    def require_live(self):
        """One gate for profiles/reports: no env, SDK, key or HTTP bypass."""
        if self.mock_backend is not None or self.live_backend is not None:
            self.billing_backend()
            from .api_v1_billing import BillingJournal
            BillingJournal(self.store).require_ready()
            return
        try:
            require_live_authorization()
        except BudgetRejected as error:
            raise ServiceError(error.code) from None


    def submit_llm(self,route,key,value,kind,prepare,execute,requests):
        """Explicit injected backend only; no environment or HTTP enable switch."""
        from .api_v1_provider import MockBackend,build_wire
        from .api_v1_billing import BillingJournal
        with self.gate:
            self.billing_backend()
            found=self.store.lookup(route,key,value)
            if found:return found
            self.require_live()
            if self.executor is None:raise ServiceError('dependency_not_ready')
            if not self.slots.acquire(blocking=False):raise ServiceError('queue_full')
            try:
                snapshot=deepcopy(prepare())
                from .rag_models import canonical_hash
                context=canonical_hash({'kind':kind,'input':value,'snapshot':snapshot})
                planned=list(requests(snapshot))
                if not planned:raise ServiceError('core_rejected')
                ledger=BillingJournal(self.store)
                for request in planned:ledger.check(kind,context,build_wire(kind,request))
                record,_=self.store.create(route,key,value,kind,'llm',snapshot=snapshot)
                try:self.executor.submit(self._execute,record['resource_id'],snapshot,execute,context=context)
                except RuntimeError:
                    self.store.transition(record['resource_id'],'interrupted',code='task_interrupted')
                    raise ServiceError('dependency_not_ready') from None
                return record
            except Exception:self.slots.release();raise
