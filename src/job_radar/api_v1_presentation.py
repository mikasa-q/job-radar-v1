"""HTTP callers supply execution provenance to the local report renderer."""
from __future__ import annotations

from .report_models import MatchReport
from .report_render import BANNERS, ReportMode, render_match_report


def render_api_report(report: MatchReport, *, mode: ReportMode = 'offline') -> str:
    if mode not in BANNERS:
        raise ValueError('invalid_execution_mode')
    return render_match_report(report, mode=mode)
