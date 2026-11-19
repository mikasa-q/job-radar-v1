"""One-request offline PDF smoke test with synthetic jobs and resume."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.api_v1 import create_app
from job_radar.api_v1_pdf_report import PDF_REPORT_ROUTE
from stage5_offline import prepare


def verify(client, root):
    database = root / 'synthetic-evidence.sqlite3'
    before = database.read_bytes()
    files = {'file': ('synthetic-resume.pdf', (root / 'synthetic-resume.pdf').read_bytes(), 'application/pdf')}
    response = client.post(PDF_REPORT_ROUTE, files=files)
    assert response.status_code == 201, response.text
    assert response.headers['content-type'].startswith('text/markdown')
    assert '未调用 LLM' in response.text
    row = client.get(response.headers['location']).json()['data']
    assert row['payload']['markdown'] == response.text and row['payload']['report']['status'] == 'not_run'
    replay = client.post(PDF_REPORT_ROUTE, files=files,
                         headers={'Idempotency-Key': response.headers['idempotency-key']})
    assert replay.status_code == 201 and replay.headers['location'] == response.headers['location']
    assert replay.text == response.text
    disabled = client.post(PDF_REPORT_ROUTE, files=files, params={'mode': 'llm'})
    assert disabled.status_code == 503 and disabled.json()['error']['code'] == 'llm_disabled'
    assert database.read_bytes() == before
    (root / 'match-report.md').write_text(response.text, encoding='utf-8')
    (root / 'match-report.json').write_text(json.dumps(row['payload']['report'], ensure_ascii=False, indent=2) + '\n',
                                          encoding='utf-8')
    print('ONE_PDF_REPORT_OK http=201 replay=unchanged llm=disabled real_calls=0')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.expanduser().absolute()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    with TestClient(create_app(prepare(root))) as client:
        verify(client, root)


if __name__ == '__main__':
    main()
