import sqlite3

import pytest

from job_radar import storage


def test_write_connection_is_closed_and_rolls_back(tmp_path, monkeypatch):
    real_connect = sqlite3.connect
    opened = []

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(storage.sqlite3, "connect", connect)
    path = tmp_path / "jobs.db"
    storage.init_db(path)
    with pytest.raises(sqlite3.ProgrammingError):
        opened[-1].execute("SELECT 1")
    with pytest.raises(RuntimeError):
        with storage._connect(path) as connection:
            connection.execute("INSERT INTO jobs(source,title,url) VALUES('demo','test','/1')")
            raise RuntimeError("test rollback")
    assert storage.count_jobs(path) == 0


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (1, -1)])
def test_query_rejects_invalid_pagination(tmp_path, limit, offset):
    with pytest.raises(ValueError):
        storage.query_jobs(tmp_path / "jobs.db", limit=limit, offset=offset)


def test_read_connection_is_read_only_and_closed(tmp_path):
    path = tmp_path / "jobs.db"
    storage.init_db(path)
    with storage._read_connection(path) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM jobs")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
