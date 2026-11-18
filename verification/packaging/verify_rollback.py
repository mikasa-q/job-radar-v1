"""Exercise packaging rollback on an isolated checkout and virtual environment."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
ENV = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
records = []


def run(command, cwd, expected=0, env=None):
    result = subprocess.run(command, cwd=cwd, env=env or ENV, capture_output=True, text=True)
    records.append({"command": [str(value) for value in command], "cwd": str(cwd),
                    "stdout": result.stdout, "stderr": result.stderr,
                    "exit_status": result.returncode})
    assert result.returncode == expected, result.stdout + result.stderr
    return result


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


working_import = run([sys.executable, "-c", "import job_radar; print(job_radar.__file__)"], PROJECT)
db_before = digest(PROJECT / "data/jobs.db")
with tempfile.TemporaryDirectory(prefix="job-radar-packaging-rollback-") as folder:
    target = Path(folder) / "project"
    shutil.copytree(PROJECT, target, ignore=shutil.ignore_patterns(
        ".git", ".venv", "verification", "work", "__pycache__", ".pytest_cache", "*.egg-info",
    ))
    run([sys.executable, "-m", "venv", "--without-pip", str(target / ".venv")], PROJECT)
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site = Path("lib") / version / "site-packages"
    # Copy only the already-verified environment's dependencies. Install the
    # project afresh so no .pth can point the rollback test at the working tree.
    shutil.copytree(PROJECT / ".venv" / site, target / ".venv" / site,
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns(
                        "__editable__*", "job_radar*", "__pycache__",
                    ))
    python = target / ".venv/bin/python"
    run([str(python), "-m", "pip", "install", "--no-deps", "-e", "."], target,
        env={**ENV, "PIP_INDEX_URL": "https://pypi.org/simple"})
    installed = run([str(python), "-c", "import job_radar; print(job_radar.__file__)"], target)
    assert str(target / "src/job_radar/__init__.py") in installed.stdout
    copy_db_before = digest(target / "data/jobs.db")
    run([str(HERE / "ROLLBACK.sh"), str(target)], target)
    manifest = json.loads((HERE / "baseline-hashes.json").read_text())
    for relative, expected in manifest.items():
        path = target / relative
        assert (digest(path) if path.is_file() else None) == expected, relative
    plain = run([str(python), "-c", "import job_radar"], target, expected=1)
    assert "ModuleNotFoundError: No module named 'job_radar'" in plain.stderr
    restored = run([str(python), "-m", "pytest", "-q"], target,
                   env={**ENV, "PYTHONPATH": "src"})
    assert "67 passed" in restored.stdout
    assert digest(target / "data/jobs.db") == copy_db_before
assert digest(PROJECT / "data/jobs.db") == db_before
assert run([sys.executable, "-c", "import job_radar; print(job_radar.__file__)"], PROJECT).stdout == working_import.stdout
(HERE / "ROLLBACK_DETAILS.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
print("ROLLBACK: 5 paths restored on isolated copy; 67 passed with PYTHONPATH; plain import returns ModuleNotFoundError; working install and database unchanged")
