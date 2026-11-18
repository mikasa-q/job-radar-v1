import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from job_radar.api import create_app, get_database_path
from job_radar.storage import init_db, upsert_jobs


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "jobs.db"
    upsert_jobs(path, [
        {"title": "Python 后端", "source": "demo", "city": "深圳",
         "company": "甲公司", "url": "https://example.com/1",
         "description": "FastAPI 服务", "skills": ["Python", "FastAPI"],
         "crawled_at": "2026-11-01T00:00:00Z"},
        {"title": "Python Engineer", "source": "python.org", "city": "Remote",
         "url": "https://example.com/2", "skills": ["Python"],
         "crawled_at": "2026-11-01T00:00:00Z"},
        {"title": "C++ Developer 100%", "source": "demo", "city": "北京",
         "url": "https://example.com/3", "description": "C++", "skills": []},
    ])
    return path


@pytest.fixture
def client(database):
    with TestClient(create_app(database)) as client:
        yield client


def test_health_and_no_database_creation(tmp_path):
    path = tmp_path / "missing" / "jobs.db"
    with TestClient(create_app(path)) as client:
        assert client.get("/health").json() == {"status": "ok"}
    assert not path.exists()


def test_list_contract_and_pagination(client):
    response = client.get("/jobs", params={"limit": 1, "offset": 1})
    assert response.status_code == 200
    page = response.json()
    assert (page["total"], page["limit"], page["offset"]) == (3, 1, 1)
    assert page["items"][0]["title"] == "Python Engineer"
    assert page["items"][0]["skills"] == ["Python"]
    assert "skills_json" not in page["items"][0]


def test_combined_filter_and_total_before_pagination(client):
    page = client.get("/jobs", params={"keyword": "python", "limit": 1}).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    page = client.get("/jobs", params={
        "city": "深圳", "keyword": "FASTAPI", "skill": "python", "source": "demo",
    }).json()
    assert page["total"] == 1
    assert page["items"][0]["city"] == "深圳"


def test_skill_is_exact_not_substring(client):
    assert client.get("/jobs?skill=Py").json()["total"] == 0
    assert client.get("/jobs?skill=Python").json()["total"] == 2


def test_no_match_is_empty_page_not_404(client):
    response = client.get("/jobs", params={"city": "shenzhen"})
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"limit": "abc"}, {"offset": -1},
    {"keyword": ""}, {"city": "x" * 101},
])
def test_invalid_parameters_return_422(client, params):
    response = client.get("/jobs", params=params)
    assert response.status_code == 422
    assert "detail" in response.json()


def test_detail_and_missing_id(client):
    item = client.get("/jobs").json()["items"][0]
    response = client.get(f"/jobs/{item['id']}")
    assert response.status_code == 200
    assert response.json() == item
    assert client.get("/jobs/999999").status_code == 404
    assert client.get("/jobs/0").status_code == 422
    assert client.get("/jobs/not-an-int").status_code == 422


def test_stats_static_route_not_shadowed(client):
    response = client.get("/jobs/stats")
    assert response.status_code == 200
    stats = response.json()
    assert stats["total"] == 3
    assert stats["by_source"] == [
        {"value": "demo", "count": 2}, {"value": "python.org", "count": 1},
    ]
    assert sum(group["count"] for group in stats["by_city"]) == 3


def test_missing_database_returns_503_without_creating_it(tmp_path):
    path = tmp_path / "typo.db"
    with TestClient(create_app(path)) as client:
        for url in ["/jobs", "/jobs/stats", "/jobs/1"]:
            assert client.get(url).status_code == 503
    assert not path.exists()


@pytest.mark.parametrize("contents", [b"not a database", b""])
def test_corrupt_or_missing_schema_returns_503(tmp_path, contents):
    path = tmp_path / "bad.db"
    path.write_bytes(contents)
    with TestClient(create_app(path)) as client:
        response = client.get("/jobs")
    assert response.status_code == 503
    assert str(path) not in response.text


def test_initialized_empty_db_returns_200(tmp_path):
    path = tmp_path / "empty.db"
    init_db(path)
    with TestClient(create_app(path)) as client:
        assert client.get("/jobs").json() == {
            "total": 0, "limit": 20, "offset": 0, "items": [],
        }


def test_sql_injection_and_literal_wildcards(client):
    assert client.get("/jobs", params={"keyword": "' OR 1=1 --"}).json()["total"] == 0
    assert client.get("/jobs", params={"keyword": "%"}).json()["total"] == 1
    assert client.get("/jobs", params={"keyword": "_"}).json()["total"] == 0
    assert client.get("/jobs").json()["total"] == 3


def test_read_requests_leave_database_bytes_unchanged(client, database):
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    client.get("/jobs")
    client.get("/jobs/stats")
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_concurrent_requests_open_their_own_connections(client):
    with ThreadPoolExecutor(max_workers=4) as executor:
        codes = list(executor.map(lambda _: client.get("/jobs").status_code, range(12)))
    assert codes == [200] * 12


def test_database_path_dependency_override(database, tmp_path):
    app = create_app(tmp_path / "missing.db")
    app.dependency_overrides[get_database_path] = lambda: database
    with TestClient(app) as client:
        assert client.get("/jobs").json()["total"] == 3


def test_env_path_and_absolute_default(monkeypatch, tmp_path):
    path = tmp_path / "custom.db"
    monkeypatch.setenv("JOBRADAR_DB", str(path))
    assert create_app().state.database_path == path
    monkeypatch.delenv("JOBRADAR_DB")
    monkeypatch.chdir(tmp_path)
    assert create_app().state.database_path.is_absolute()
    assert create_app().state.database_path.name == "jobs.db"


def test_docs_and_openapi_contract(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/jobs/stats" in schema["paths"]
    assert "JobsPage" in schema["components"]["schemas"]
    assert "post" not in schema["paths"]["/jobs"]
