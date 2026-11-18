"""Quickly inspect the bundled, unmodified parser without running network requests."""
from pathlib import Path
import importlib.util

root = Path(__file__).parent
baseline = root / 'verification' / 'baseline'
spec = importlib.util.spec_from_file_location('baseline_parser', baseline / 'src' / 'job_radar' / 'parser.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
jobs = module.parse_jobs((baseline / 'sample_data' / 'jobs.html').read_text(encoding='utf-8'))
print(f'baseline parsed: {len(jobs)} unique: {len(module.deduplicate_jobs(jobs))}')
print(f'baseline first skills: {jobs[0]["skills"]}')
