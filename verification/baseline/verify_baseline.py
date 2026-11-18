from pathlib import Path
import importlib.util

root = Path(__file__).parent
spec = importlib.util.spec_from_file_location('baseline_parser', root.parent.parent / 'work' / 'job_radar_baseline' / 'parser.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
jobs = module.parse_jobs((root / 'sample_data' / 'jobs.html').read_text(encoding='utf-8'))
print(f'baseline parsed: {len(jobs)} unique: {len(module.deduplicate_jobs(jobs))}')
print(f'baseline first skills: {jobs[0]["skills"]}')
