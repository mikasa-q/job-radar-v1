"""Deterministic summary of actual pytest, CLI, and rollback executions."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys

project = Path(__file__).resolve().parents[1]
verification = project / 'verification'
mode = sys.argv[1]

def run_tests(folder):
    env = {**os.environ, 'PYTHONPATH': str(folder / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'}
    return subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider', 'tests'],
        cwd=folder, env=env, text=True, capture_output=True,
    )

if mode == 'baseline':
    result = run_tests(verification / 'baseline')
    (verification / 'BASELINE.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode != 1 or '1 failed, 2 passed' not in result.stdout:
        print(result.stdout + result.stderr)
        raise SystemExit(3)
    print('BASELINE: 1 failed, 2 passed (stale fixture assertion)')
    raise SystemExit(1)
elif mode == 'modified':
    result = run_tests(project)
    (verification / 'MODIFIED.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode or '39 passed' not in result.stdout:
        print(result.stdout + result.stderr)
        raise SystemExit(3)
    print('MODIFIED: 39 passed')
elif mode == 'rollback':
    target = verification / 'rollback-copy'
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(project, target, ignore=shutil.ignore_patterns('.venv', 'verification', '__pycache__', '.pytest_cache'))
    restored = subprocess.run([str(project / 'ROLLBACK.sh'), str(target)], capture_output=True, text=True)
    assert restored.returncode == 0, restored.stderr
    manifest = json.loads((verification / 'baseline-hashes.json').read_text())
    for rel, expected in manifest.items():
        assert hashlib.sha256((target / rel).read_bytes()).hexdigest() == expected, rel
    assert not (target / 'src/job_radar/sources.py').exists()
    # The copy may include newly added tests; verify the original stale
    # assertion is still the only failure rather than relying on a fixed count.
    result = run_tests(target)
    assert result.returncode == 1 and 'test_parse_and_extract_skills' in result.stdout
    output = target / 'restored.jobs.json'
    env = {**os.environ, 'PYTHONPATH': str(target / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'}
    cli = subprocess.run([sys.executable, '-m', 'job_radar.cli', 'sample_data/jobs.html', '--output', str(output)],
                         cwd=target, env=env, text=True, capture_output=True)
    assert cli.returncode == 0, cli.stderr
    assert len(json.loads(output.read_text(encoding='utf-8'))) == 2
    (verification / 'ROLLBACK.log').write_text(restored.stdout + result.stdout + result.stderr + cli.stdout, encoding='utf-8')
    shutil.rmtree(target)
    print('ROLLBACK: 13 hashes restored; 2 jobs; baseline 1 failed, 2 passed')
else:
    raise SystemExit('Expected baseline, modified, or rollback')
