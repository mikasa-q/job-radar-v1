"""Restore the pre-update files, after verifying every baseline SHA-256."""
from pathlib import Path
import hashlib
import json
import shutil
import sys

verification = Path(__file__).resolve().parent
baseline = verification / 'baseline'
manifest = json.loads((verification / 'baseline-hashes.json').read_text())
target = Path(sys.argv[1]).resolve()
if not target.is_dir():
    raise SystemExit('Target directory does not exist')
for rel, expected in manifest.items():
    actual = hashlib.sha256((baseline / rel).read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f'Baseline hash mismatch: {rel}')
for rel in manifest:
    destination = target / rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(baseline / rel, destination)
for rel in [
    'NOTES.md', 'src/job_radar/sources.py',
    'src/job_radar/config.py', 'src/job_radar/storage.py',
    'sample_data/boss-home.html', 'sample_data/python-org.html', 'sample_data/snapshots.json',
    'data/boss-home.jobs.json', 'data/python-org.jobs.json', 'data/python-org.snapshot.jobs.json',
    'data/python-org.latest.html', 'data/boss-live.jobs.json',
    'tests/test_sources.py', 'tests/test_crawler_cli.py', 'tests/test_filters.py', 'tests/test_config.py', 'tests/test_storage.py',
]:
    (target / rel).unlink(missing_ok=True)
for rel, expected in manifest.items():
    assert hashlib.sha256((target / rel).read_bytes()).hexdigest() == expected, rel
print('ROLLBACK RESTORED: 13 original file hashes match; real-source adapter removed')
