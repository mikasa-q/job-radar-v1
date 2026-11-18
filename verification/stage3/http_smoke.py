"""Start real Uvicorn on a free loopback port, exercise HTTP, then stop it."""
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
database = PROJECT / "data/jobs.db"
before = hashlib.sha256(database.read_bytes()).hexdigest()
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
base = f"http://127.0.0.1:{port}"
env = {**os.environ, "JOBRADAR_DB": str(database), "PYTHONDONTWRITEBYTECODE": "1"}
checks = []


def request(path, expected):
    try:
        with urllib.request.urlopen(base + path, timeout=5) as response:
            code, body = response.status, response.read()
    except urllib.error.HTTPError as exc:
        code, body = exc.code, exc.read()
    assert code == expected, (path, code, body)
    checks.append({"path": path, "status": code, "body": body.decode("utf-8")})
    return json.loads(body) if not path.startswith("/docs") else body


with (HERE / "UVICORN.log").open("w", encoding="utf-8") as log:
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "job_radar.api:app", "--app-dir", "src",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=PROJECT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(100):
            assert process.poll() is None, "Uvicorn exited during startup"
            try:
                urllib.request.urlopen(base + "/health", timeout=1).close()
                break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.1)
        else:
            raise RuntimeError("Uvicorn startup timed out")
        assert request("/health", 200) == {"status": "ok"}
        page = request("/jobs?" + urllib.parse.urlencode({"city": "深圳", "limit": 10}), 200)
        assert page["total"] == 2 and len(page["items"]) == 2
        page = request("/jobs?keyword=Python&limit=5&offset=0", 200)
        assert page["total"] == 14 and len(page["items"]) == 5
        request(f"/jobs/{page['items'][0]['id']}", 200)
        stats = request("/jobs/stats", 200)
        assert stats["total"] == 102
        request("/jobs?limit=0", 422)
        request("/jobs/999999999", 404)
        request("/docs", 200)
        request("/openapi.json", 200)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
assert hashlib.sha256(database.read_bytes()).hexdigest() == before
(HERE / "HTTP.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("HTTP: 9 checks passed; 102 jobs; Shenzhen=2; Python=14; 422/404 verified; DB hash unchanged; server stopped")
