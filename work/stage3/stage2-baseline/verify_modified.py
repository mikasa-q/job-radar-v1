from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent / 'src'))
from job_radar.parser import parse_jobs, deduplicate_jobs
jobs = parse_jobs(Path(__file__).parent.joinpath('sample_data/jobs.html').read_text(encoding='utf-8'))
unique = deduplicate_jobs(jobs)
assert len(jobs) == 3
assert len(unique) == 2
assert unique[0]['skills'] == ['Python', 'FastAPI', 'MySQL', 'Git', 'Docker']
assert 'SQL' not in unique[0]['skills']
print(f'modified verified: {len(unique)} unique jobs; MySQL does not create false SQL match')
