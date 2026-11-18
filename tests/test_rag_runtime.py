"""Resource supervision tests; no encoded or fake vectors."""
from pathlib import Path
import json
import os
import sys
import pytest
from job_radar.rag_runtime import SupervisedSnapshotClient,process_tree_rss,LocalTokenBudget
from job_radar.retrieval_models import RetrievalError

REV='a'*40
MID='BAAI/bge-small-zh-v1.5'


def client(tmp_path,script,**kw):
    path=tmp_path/REV;path.mkdir();(path/'config.json').write_text('{}')
    executable=tmp_path/'executable';executable.write_text('#!'+sys.executable+'\n'+script+'\n');executable.chmod(0o755)
    return SupervisedSnapshotClient(path,model_id=MID,revision=REV,python_executable=str(executable),**kw)


def test_supervisor_timeout_terminates_worker(tmp_path):
    c=client(tmp_path,'import time; time.sleep(30)',timeout_seconds=0.1)
    with pytest.raises(RetrievalError,match='dense_encode_timeout'):c.encode(['合成文本'],kind='query')
    assert c.last_measurement['exit_status']==-9
    assert c.last_measurement['seconds']<10


def test_supervisor_process_exit_is_explicit(tmp_path):
    c=client(tmp_path,'raise SystemExit(7)')
    with pytest.raises(RetrievalError,match='dense_worker_exit'):c.encode(['合成文本'],kind='passage')
    assert c.last_measurement['exit_status']==7


def test_supervisor_memory_budget_terminates_worker(tmp_path):
    c=client(tmp_path,'import time; x=bytearray(100*1024**2);time.sleep(30)',max_rss_bytes=64*1024**2)
    with pytest.raises(RetrievalError,match='dense_resource_error'):c.encode(['合成文本'],kind='query')
    assert c.last_measurement['exit_status']==-9


def test_supervisor_fixed_error_and_invalid_output(tmp_path):
    script="import pathlib,sys;pathlib.Path(sys.argv[-1]).write_text('{\"error\":\"secret-exception-not-returned\"}')"
    c=client(tmp_path,script)
    with pytest.raises(RetrievalError,match='^dense_client_error$'):c.encode(['合成文本'],kind='query')


def test_rss_process_tree_positive():
    assert process_tree_rss(os.getpid())>0


@pytest.mark.parametrize('options',[{'timeout_seconds':0},{'max_rss_bytes':1},{'python_executable':'relative'}])
def test_supervisor_config_invalid(tmp_path,options):
    path=tmp_path/REV;path.mkdir();(path/'config.json').write_text('{}')
    args=dict(model_id=MID,revision=REV,python_executable=sys.executable);args.update(options)
    with pytest.raises(ValueError):SupervisedSnapshotClient(path,**args)


def test_missing_snapshot_rejected_without_import(tmp_path):
    with pytest.raises(RetrievalError,match='dense_snapshot_missing'):
        LocalTokenBudget(tmp_path/'missing',model_id=MID,revision=REV)


def test_worker_start_failure_is_explicit_dense_error(tmp_path):
    c=client(tmp_path,'pass')
    Path(c._python).chmod(0o644)
    with pytest.raises(RetrievalError,match='dense_worker_start_failed'):
        c.encode(['合成文本'],kind='query')


def test_supervision_failure_kills_worker(tmp_path,monkeypatch):
    import job_radar.rag_runtime as runtime
    c=client(tmp_path,'import time;time.sleep(30)')
    def broken(pid):raise OSError('ps unavailable')
    monkeypatch.setattr(runtime,'process_tree_rss',broken)
    with pytest.raises(RetrievalError,match='dense_supervision_error'):
        c.encode(['合成文本'],kind='query')
    assert c.last_measurement['exit_status']==-9
