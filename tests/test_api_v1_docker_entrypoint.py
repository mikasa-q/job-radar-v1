"""Launcher behavior used by Compose, without credentials or external HTTP."""
import logging

from fastapi.testclient import TestClient
import pytest
import uvicorn

from job_radar import api_v1_serve as serve
from test_api_v1 import fixtures


@pytest.fixture(autouse=True)
def launcher_logs(monkeypatch):
    for name in ('openai', 'httpx', 'httpx2', 'httpcore', 'httpcore2'):
        logger = logging.getLogger(name)
        monkeypatch.setattr(logger, 'level', logger.level)


def test_offline_container_uses_disposable_lexical_index(tmp_path, monkeypatch):
    settings = fixtures(tmp_path)
    evidence_before = settings.evidence_path.read_bytes()
    index_roots = []

    def no_live(*_args, **_kwargs):
        pytest.fail('offline startup must not attach a live backend or read credentials')

    def run(app, **options):
        assert options['host'] == '0.0.0.0'
        with TestClient(app) as client:
            service = app.state.offline_service
            assert service.live_backend is None
            assert service.index().manifest().dense is None
            index_roots.append(service.settings.index_root)
            ready = client.get('/v1/ready')
            assert ready.status_code == 200
            assert ready.json()['data'] == dict(status='ready', evidence=True, index=True,
                                               real_llm=False, hybrid=False)

    monkeypatch.setattr(serve, 'create_live_app', no_live)
    monkeypatch.setattr(serve, '_read_key', no_live)
    monkeypatch.setattr(uvicorn, 'run', run)
    serve.main(['--offline', '--temporary-lexical-index', '--host', '0.0.0.0',
                '--store-root', str(settings.store_root), '--evidence-db', str(settings.evidence_path),
                '--dataset-id', settings.dataset_id])
    assert len(index_roots) == 1 and not index_roots[0].exists()
    assert settings.evidence_path.read_bytes() == evidence_before


@pytest.mark.parametrize('options', [
    ['--offline', '--temporary-lexical-index'],
    ['--temporary-lexical-index', '--evidence-db', 'db'],
    ['--offline', '--temporary-lexical-index', '--index-root', 'derived', '--evidence-db', 'db'],
    ['--host', '192.0.2.1'],
])
def test_rejects_invalid_launch_options(tmp_path, options):
    with pytest.raises(SystemExit) as error:
        serve.main(['--store-root', str(tmp_path / 'store'), *options])
    assert error.value.code == 2


def test_fail_before_start_if_temporary_index_cannot_be_built(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(uvicorn, 'run', lambda *a, **k: pytest.fail('unexpected server start'))
    store = tmp_path / 'store'
    with pytest.raises(SystemExit) as error:
        serve.main(['--store-root', str(store), '--offline', '--temporary-lexical-index',
                    '--evidence-db', str(tmp_path / 'missing.sqlite3')])
    assert error.value.code == 1
    assert '构建离线索引' in capsys.readouterr().err
    assert not store.exists()
