"""Optional real-tokenizer budgeting and supervised local encoding.

No runtime imports until explicit client construction/use. A spawned worker is
supervised for timeout/process-tree RSS; a guard is not an OS OOM guarantee.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from .retrieval_models import RetrievalError
from .retrieval_embedding import LocalSnapshotEmbeddingClient, _MODEL_SPECS, _validate_vectors


class LocalTokenBudget:
    def __init__(self, snapshot_path, *, model_id: str, revision: str):
        if model_id not in _MODEL_SPECS:
            raise RetrievalError('dense_model_unsupported')
        path = Path(snapshot_path)
        # Reuse metadata/path checks; construction never loads model weights.
        client = LocalSnapshotEmbeddingClient(path, model_id=model_id, revision=revision)
        try:
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True,
                trust_remote_code=False, use_fast=True)
        except Exception:
            raise RetrievalError('dense_runtime_unavailable') from None
        if not tokenizer.is_fast:
            raise RetrievalError('dense_tokenizer_offsets_unavailable')
        self._tokenizer = tokenizer
        self.tokenizer_id = model_id
        self.revision = revision
        self.max_tokens = client.max_tokens
        conf = path / 'sentence_bert_config.json'
        if conf.is_file():
            try:
                length = json.loads(conf.read_text()).get('max_seq_length')
            except (ValueError, OSError, AttributeError):
                raise RetrievalError('dense_snapshot_invalid') from None
            if type(length) is not int or not 1 <= length <= self.max_tokens:
                raise RetrievalError('dense_model_length_unknown')
            self.max_tokens = length
        self._query_prefix = _MODEL_SPECS[model_id][2]
        self._passage_prefix = _MODEL_SPECS[model_id][3]

    def count(self, text: str, *, kind: str) -> int:
        if kind not in {'query', 'passage'} or not isinstance(text, str):
            raise RetrievalError('dense_tokenizer_input')
        prefix = self._query_prefix if kind == 'query' else self._passage_prefix
        return len(self._tokenizer(prefix + text, add_special_tokens=True, truncation=False)['input_ids'])

    def offsets(self, text: str) -> list[tuple[int, int]]:
        return [tuple(pair) for pair in self._tokenizer(text, add_special_tokens=False,
            truncation=False, return_offsets_mapping=True)['offset_mapping']]


def process_tree_rss(pid: int) -> int:
    """Sample current process+descendant RSS in bytes (ps emits KiB)."""
    output = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,rss='], text=True)
    rows = [tuple(map(int, row.split())) for row in output.splitlines() if row.strip()]
    selected = {pid}
    for _ in range(len(rows) + 1):
        new = {p for p, parent, _ in rows if parent in selected}
        if new <= selected:
            break
        selected |= new
    return sum(rss * 1024 for p, _, rss in rows if p in selected)


class SupervisedSnapshotClient:
    """One short-lived local CPU model process per encode call.

    This deliberately conservative adapter does not claim hot-model performance.
    Benchmark worker measures hot model separately. No implicit model/network
    download; a timeout/exit uses fixed dense error codes.
    """
    def __init__(self, snapshot_path, *, model_id, revision, python_executable,
                 timeout_seconds=120.0, max_rss_bytes=3 * 1024**3):
        checked = LocalSnapshotEmbeddingClient(snapshot_path, model_id=model_id, revision=revision)
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 3600:
            raise ValueError('invalid_dense_timeout')
        if type(max_rss_bytes) is not int or max_rss_bytes < 64 * 1024**2:
            raise ValueError('invalid_dense_rss_budget')
        executable = Path(python_executable)
        if not executable.is_absolute() or not executable.is_file():
            raise ValueError('invalid_dense_python')
        self.model_id, self.revision, self.dimension = checked.model_id, checked.revision, checked.dimension
        self._snapshot = str(Path(snapshot_path).resolve())
        self._python = str(executable)
        self.timeout_seconds, self.max_rss_bytes = float(timeout_seconds), max_rss_bytes
        self.last_measurement = None

    def encode(self, texts: list[str], *, kind: str):
        if kind not in {'query', 'passage'} or not isinstance(texts, list) or any(not isinstance(t, str) for t in texts):
            raise RetrievalError('dense_response_shape')
        if not texts:
            return []
        with tempfile.TemporaryDirectory(prefix='jobradar-dense-') as folder:
            folder = Path(folder)
            inp, out = folder/'input.json', folder/'output.json'
            inp.write_text(json.dumps(dict(snapshot=self._snapshot, model_id=self.model_id,
                revision=self.revision, texts=texts, kind=kind), ensure_ascii=False))
            env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
                PYTHONDONTWRITEBYTECODE='1')
            # Add the currently imported source root explicitly, not editable-install assumptions.
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
            start = time.monotonic(); peak = 0; reason = None
            with (folder/'stderr.log').open('w') as errorlog:
                try:
                    proc = subprocess.Popen([self._python, '-B', '-m', 'job_radar.rag_runtime',
                        str(inp), str(out)], env=env, stdout=subprocess.DEVNULL,
                        stderr=errorlog, start_new_session=True)
                except OSError:
                    raise RetrievalError('dense_worker_start_failed') from None
                try:
                    while proc.poll() is None:
                        peak = max(peak, process_tree_rss(proc.pid))
                        if peak > self.max_rss_bytes:
                            reason = 'dense_resource_error'; break
                        if time.monotonic() - start > self.timeout_seconds:
                            reason = 'dense_encode_timeout'; break
                        time.sleep(0.05)
                except (OSError, ValueError, subprocess.SubprocessError):
                    reason = 'dense_supervision_error'
                finally:
                    # Also reap descendants if a worker leader exits first.
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
            self.last_measurement = dict(seconds=time.monotonic()-start,
                process_tree_peak_rss_bytes=peak, exit_status=proc.returncode, reason=reason)
            if reason:
                raise RetrievalError(reason)
            if proc.returncode != 0 or not out.is_file():
                raise RetrievalError('dense_worker_exit')
            try:
                response = json.loads(out.read_text())
            except Exception:
                raise RetrievalError('dense_response_shape') from None
            if 'error' in response:
                from .retrieval_embedding import DENSE_ERROR_CODES
                code = response['error']
                raise RetrievalError(code if code in DENSE_ERROR_CODES else 'dense_client_error')
            return _validate_vectors(response.get('vectors'), len(texts), self.dimension)


def _worker(inp, out):
    data = json.loads(Path(inp).read_text())
    try:
        import torch
        torch.set_num_threads(1)
        client = LocalSnapshotEmbeddingClient(data['snapshot'], model_id=data['model_id'], revision=data['revision'])
        vectors = client.encode(data['texts'], kind=data['kind'])
        result = {'vectors': vectors}
    except RetrievalError as error:
        result = {'error': error.code}
    Path(out).write_text(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    import sys
    _worker(*sys.argv[1:])
