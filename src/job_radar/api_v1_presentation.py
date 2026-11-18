"""Explicit execution provenance at display only; never rewrites generated claims."""
from __future__ import annotations
from typing import Literal

from .report_models import MatchReport, ReportError
from .report_render import render_match_report

OLD_BANNER = '预设响应只验证工程链路，不证明真实模型分析质量。'
BANNERS = {
    'offline': '本报告未调用 LLM；分析状态为 not_run。',
    'scripted': '本报告使用模拟响应，仅验证工程链路，不代表真实模型分析质量。',
    'live': '本报告包含真实模型响应；结论仍需人工复核。',
}


def render_api_report(report: MatchReport, *, mode: Literal['offline','scripted','live']='offline') -> str:
    """Caller supplies provenance; completed alone never implies a live model."""
    if mode not in BANNERS:
        raise ValueError('invalid_execution_mode')
    # No status/review/claims/version mutation: original renderer validates first.
    markdown = render_match_report(report)
    if mode == 'offline' and (report.status != 'not_run' or any(
            item.status != 'not_analyzed' for item in report.analyses)):
        raise ReportError('analysis_invalid_response')
    if mode == 'live' and not any(item.audit is not None and
            item.audit.provider == 'apinebula' and item.audit.attempts > 0
            for item in report.analyses):
        raise ReportError('analysis_invalid_response')
    # Only replace the exact header occurrence. Never text-replace model/source evidence.
    lines = markdown.splitlines(keepends=True)
    expected = '**检索排名与证据覆盖不是录用概率；unknown不等于不会。'+OLD_BANNER+'**'
    matches = [i for i,line in enumerate(lines[:15]) if line.rstrip('\r\n')==expected]
    if len(matches) != 1:
        raise ReportError('analysis_invalid_response')
    i=matches[0]
    lines[i]=lines[i].replace(OLD_BANNER,BANNERS[mode],1)
    return ''.join(lines)
