"""Minimal offline draft parser. Domain evidence validation is a separate boundary."""
from .report_models import MatchAnalysisDraft, ReportError, strict_json

def parse_analysis(text: str, *, max_output_bytes: int = 128*1024) -> MatchAnalysisDraft:
    try:
        if not isinstance(text,str) or len(text.encode('utf-8'))>max_output_bytes:
            raise ReportError('analysis_output_limit')
        data=strict_json(text)
    except ReportError: raise
    except (ValueError,TypeError,UnicodeError,RecursionError): raise ReportError('analysis_invalid_json') from None
    try: return MatchAnalysisDraft.model_validate(data)
    except (ValueError,TypeError,RecursionError): raise ReportError('analysis_schema_error') from None


def assemble_report(packet, analyses, *, report_id, generated_at):
    """Local assembly, no I/O/model; enforce every output citation belongs to packet."""
    from .report_models import EvidencePacket, JobAnalysis, MatchReport
    from .rag_models import canonical_hash
    packet=EvidencePacket.model_validate(packet)
    analyses=[JobAnalysis.model_validate(a) for a in analyses]
    from .report_analysis import validate_analysis
    records={e.evidence_id:e for e in packet.evidence}
    for item in analyses:
        if item.draft is not None:
            validate_analysis(item.draft,packet,item.key)
            for claim in item.draft.claims:
                for ref in claim.evidence_ids:
                    e=records.get(ref)
                    if e is None or (e.key is not None and e.key!=item.key):
                        raise ReportError('analysis_evidence_scope')
    chosen=[a for a in analyses if a.key in packet.selected_keys]
    if not chosen or all(a.status=='not_analyzed' for a in chosen): status='not_run'
    elif all(a.status=='completed' for a in chosen): status='completed'
    elif any(a.status=='completed' for a in chosen): status='partial'
    else: status='failed'
    return MatchReport(report_id=report_id,generated_at=generated_at,packet=packet,packet_sha256=canonical_hash(packet),status=status,analyses=analyses)
