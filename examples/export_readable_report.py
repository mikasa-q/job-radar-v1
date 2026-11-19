"""Render an existing JobRadar report locally; no provider, key or store writes."""
from __future__ import annotations

import argparse
from pathlib import Path

from job_radar.api_v1_billing import validate_billing
from job_radar.api_v1_models import ReportPayload, Resource
from job_radar.api_v1_store import ServiceError
from job_radar.rag_models import canonical_hash
from job_radar.report_models import MatchReport, strict_json
from job_radar.report_render import BANNERS, ReportMode, export_match_report


def from_store(path: Path, report_id: str) -> tuple[MatchReport, ReportMode | None]:
    # Atomic store replacement means this read sees one snapshot; do not call
    # ResourceStore.open(), which can persist startup recovery for old tasks.
    saved = strict_json(path.read_text(encoding='utf-8'))
    state = saved['content']
    if canonical_hash(state) != saved['sha256']:
        raise ValueError('report_store_hash')
    validate_billing(state, path.parent)
    entry = state['records'][report_id]
    data = entry['resource']
    row = Resource.model_validate(data)
    if row.kind != 'report' or row.resource_id != report_id or row.state != 'succeeded':
        raise ValueError('report_not_succeeded')
    if canonical_hash(row.payload) != row.payload_sha256:
        raise ValueError('report_payload_hash')
    payload = ReportPayload.model_validate(data['payload'])
    if row.execution_mode == 'offline':
        mode = 'offline'
    elif payload.report.report_id.startswith('live-'):
        # Service assigns this prefix, not the model. Rendering also requires
        # a compatible provider audit; a completed draft alone is not live.
        mode = 'live'
    elif payload.report.report_id.startswith('mock-'):
        mode = 'scripted'
    else:
        mode = None
    return payload.report, mode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--report-json', type=Path, help='original match-report.json')
    source.add_argument('--store-state', type=Path, help='existing api-store/state.json (read only)')
    parser.add_argument('--report-id', help='resource ID (rpt_…) for --store-state')
    parser.add_argument('--mode', choices=BANNERS, help='known provenance for --report-json; default unmarked')
    parser.add_argument('--output', type=Path, required=True, help='new private output directory')
    args = parser.parse_args(argv)
    if args.store_state and (not args.report_id or args.mode is not None):
        parser.error('--store-state requires --report-id and determines provenance from the saved report')
    if args.report_json and args.report_id:
        parser.error('--report-id requires --store-state')
    try:
        if args.store_state:
            report, mode = from_store(args.store_state.expanduser().absolute(), args.report_id)
        else:
            report = MatchReport.model_validate(strict_json(args.report_json.expanduser().read_text(encoding='utf-8')))
            mode = args.mode
        audit, readable = export_match_report(report, args.output, mode=mode)
    except (OSError, ValueError, KeyError, TypeError, RecursionError, ServiceError):
        parser.exit(1, '无法导出：请检查输入是否为完整、通过校验的报告，以及输出目录是否为新目录。\n')
    print(f'Markdown: {readable}\nJSON: {audit}')


if __name__ == '__main__':
    main()
