"""Offline Stage 3 -> report demo. No credentials/provider/network, no real quality claim."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
from job_radar.report_synthetic import synthetic_inputs,synthetic_manifest
from job_radar.rag_engine import match_jobs_indexed
from job_radar.rag_index import DerivedIndex
from job_radar.report_models import AnalysisResponse,CallAudit
from job_radar.report_engine import generate_match_report
from job_radar.report_render import export_match_report

from job_radar.report_demo_client import SyntheticReportClient

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    doc,request=synthetic_inputs()
    with tempfile.TemporaryDirectory(prefix='stage4-synthetic-') as temp:
        index=DerivedIndex(Path(temp)/'derived');index.update(request.jobs,manifest=synthetic_manifest(request.jobs))
        retrieval=match_jobs_indexed(request,index=index)
    client=SyntheticReportClient()
    report=generate_match_report(request,retrieval,doc,report_id='synthetic-stage4-demo',generated_at=datetime.now(timezone.utc),client=client)
    jp,mp=export_match_report(report,args.output)
    print('mode=offline; real_requests=0; scripted_responses_not_model_quality')
    print(f'retrieval={retrieval.matching.status}; analysis={report.status}; scripted_calls={client.calls}')
    print(f'json={jp}\nmarkdown={mp}')
    return 0 if report.status=='completed' else 1

if __name__=='__main__':raise SystemExit(main())
