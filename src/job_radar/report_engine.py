"""Stage 4 explicit, serial, optional analysis. Retrieval is never reranked."""
from __future__ import annotations
from datetime import datetime
from .matching_models import MatchingRequest
from .rag_models import IndexedMatchingResult
from .resume_models import ResumeDocument
from .report_models import AnalysisConfig, AnalysisClient, JobAnalysis, MatchReport, ReportError
from .report_core import assemble_report

def generate_match_report(request: MatchingRequest, result: IndexedMatchingResult,
    document: ResumeDocument, *, report_id: str, generated_at: datetime,
    client: AnalysisClient | None = None, config: AnalysisConfig | None = None,
    rejection_sink=None) -> MatchReport:
    from .report_evidence import build_evidence_packet, validate_evidence_packet
    from .report_analysis import analyze_job,validate_analysis
    cfg=AnalysisConfig() if config is None else AnalysisConfig.model_validate(config)
    packet=build_evidence_packet(request,result,document,config=cfg)
    packet=validate_evidence_packet(packet,request,result,document,config=cfg)
    # Validate local identity and clock BEFORE any provider request.
    initial=[JobAnalysis(key=row.key,status='not_analyzed') for row in packet.retrieval.matching.results]
    report=assemble_report(packet,initial,report_id=report_id,generated_at=generated_at)
    analyses=[]; stopped=False
    for row in packet.retrieval.matching.results:
        if client is None or stopped or row.key not in packet.selected_keys:
            analyses.append(JobAnalysis(key=row.key,status='not_analyzed')); continue
        try:
            analysis=JobAnalysis.model_validate(analyze_job(packet,row.key,client=client,config=cfg,rejection_sink=rejection_sink))
            if analysis.key!=row.key or analysis.status=='not_analyzed': raise ReportError('analysis_invalid_response')
            if analysis.draft is not None: validate_analysis(analysis.draft,packet,row.key)
        except ReportError as error:
            analysis=JobAnalysis(key=row.key,status='failed',error_code=error.code if error.code.startswith('analysis_') else 'analysis_validation_error')
        except Exception:
            analysis=JobAnalysis(key=row.key,status='failed',error_code='analysis_client_error')
        analyses.append(analysis)
        if analysis.status=='failed':
            stopped=True
            halt=getattr(client,'halt',None)
            if callable(halt):
                try: halt('analysis_validation_failed')
                except Exception: pass
    # Defend against an injected client mutating shared request/packet state.
    validate_evidence_packet(packet,request,result,document,config=cfg)
    return assemble_report(packet,analyses,report_id=report_id,generated_at=generated_at)
