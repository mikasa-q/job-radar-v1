"""Executable stage-two/stage-three comparisons and separate-copy rollback."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]


def run_tests(root):
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=root, env=env, capture_output=True, text=True,
    )


def confirm(result, expected, log):
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(rf"\b{expected} passed\b", result.stdout), result.stdout


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(mode):
    if mode == "baseline":
        confirm(run_tests(HERE / "baseline"), 39, HERE / "BASELINE.log")
        print("BASELINE: 39 passed; FastAPI module absent")
    elif mode == "modified":
        confirm(run_tests(PROJECT), 67, HERE / "MODIFIED.log")
        assert (PROJECT / "src/job_radar/api.py").is_file()
        print("MODIFIED: 67 passed; FastAPI query endpoints present")
    elif mode == "rollback":
        with tempfile.TemporaryDirectory(prefix="rollback-", dir=PROJECT / "work/stage3") as folder:
            target = Path(folder) / "project"
            shutil.copytree(PROJECT, target, ignore=shutil.ignore_patterns(
                ".venv", "verification", "work", "__pycache__", ".pytest_cache",
            ))
            db_before = digest(target / "data/jobs.db")
            result = subprocess.run(
                [str(HERE / "ROLLBACK.sh"), str(target)], capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            manifest = json.loads((HERE / "changes.json").read_text(encoding="utf-8"))
            for relative, hashes in manifest.items():
                path = target / relative
                assert (digest(path) if path.is_file() else None) == hashes["before"], relative
            assert digest(target / "data/jobs.db") == db_before
            confirm(run_tests(target), 39, HERE / "ROLLBACK.log")
            env = {**os.environ, "PYTHONPATH": str(target / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
            cli = subprocess.run(
                [sys.executable, "-m", "job_radar.cli", "--database", "data/jobs.db",
                 "--query-city", "深圳", "--output", str(Path(folder) / "restored.json")],
                cwd=target, env=env, capture_output=True, text=True,
            )
            assert cli.returncode == 0, cli.stderr
            assert len(json.loads((Path(folder) / "restored.json").read_text())) == 2
            with (HERE / "ROLLBACK.log").open("a", encoding="utf-8") as log:
                log.write(result.stdout + cli.stdout)
        assert (PROJECT / "src/job_radar/api.py").is_file()
        print("ROLLBACK: 39 passed; stage-two CLI restored; 2 Shenzhen jobs; DB hash unchanged; working API kept")
    else:
        raise SystemExit("Expected baseline, modified, or rollback")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "modified")
