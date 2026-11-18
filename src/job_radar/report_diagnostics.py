"""Opt-in local rejection sidecars, separate from MatchReport and model wire data.

The complete bounded response text is diagnostic data, never a successful draft.
Nothing is persisted unless the caller explicitly supplies a sink.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .rag_models import canonical_hash
from .report_models import MatchAnalysisDraft, PROMPT_VERSION, ReportError, strict_json
from .retrieval_models import JobKey

MAX_REJECTED_DRAFT_BYTES = 1024 * 1024


class RequirementComparison(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    claim_id: str
    subject: str
    actual_requirement_kind: Literal['required', 'bonus', 'mention', 'unknown']
    expected_requirement_kinds: list[Literal['required', 'bonus', 'mention']]
    outcome: Literal['matched', 'unknown_not_checked', 'subject_not_matched', 'kind_mismatch']
    matching_rules: list[dict]


class AnalysisRejection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal['match-analysis-rejection-v1'] = 'match-analysis-rejection-v1'
    key: JobKey
    prompt_version: str
    packet_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    error_code: str = Field(pattern=r'^[a-z][a-z0-9_]{0,79}$')
    raw_draft_text: str
    draft_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    parsed_draft: dict | None
    requirement_comparisons: list[RequirementComparison]
    source: Literal['local_validation_rejection'] = 'local_validation_rejection'


RejectionSink = Callable[[AnalysisRejection], None]


def build_rejection(raw_text, error_code, packet, key) -> AnalysisRejection:
    """Inspect a rejected response without repairing it or changing validation."""
    if not isinstance(raw_text, str):
        raise ReportError('analysis_diagnostic_invalid_text')
    encoded = raw_text.encode('utf-8')
    if len(encoded) > MAX_REJECTED_DRAFT_BYTES:
        raise ReportError('analysis_diagnostic_output_limit')
    parsed = None
    comparisons = []
    try:
        draft = MatchAnalysisDraft.model_validate(strict_json(raw_text))
    except (ValueError, TypeError, RecursionError):
        # Invalid JSON/schema still retains the complete exact response text.
        pass
    else:
        parsed = draft.model_dump(mode='json')
        ranked = next(row for row in packet.retrieval.matching.results if row.key == key)
        for claim in draft.claims:
            rules = [(i, rule) for i, rule in enumerate(ranked.rules.skills)
                     if any(claim.subject.casefold().strip() == name.casefold().strip()
                            for name in rule.alternatives)]
            expected = list(dict.fromkeys(rule.kind for _, rule in rules))
            if claim.requirement_kind == 'unknown':
                outcome = 'unknown_not_checked'
            elif not rules:
                outcome = 'subject_not_matched'
            elif claim.requirement_kind not in expected:
                outcome = 'kind_mismatch'
            else:
                outcome = 'matched'
            matching = []
            for index, rule in rules:
                path = f'rules.skills[{index}]'
                ids = [record.evidence_id for record in packet.evidence
                       if record.kind == 'rule' and record.key == key and record.path == path
                       and strict_json(record.value_json) == rule.model_dump(mode='json')]
                matching.append({'path': path, 'alternatives': list(rule.alternatives),
                                 'kind': rule.kind, 'evidence_ids': ids})
            comparisons.append(RequirementComparison(
                claim_id=claim.claim_id, subject=claim.subject,
                actual_requirement_kind=claim.requirement_kind,
                expected_requirement_kinds=expected, outcome=outcome,
                matching_rules=matching))
    return AnalysisRejection(
        key=key, prompt_version=PROMPT_VERSION, packet_sha256=canonical_hash(packet),
        error_code=error_code, raw_draft_text=raw_text,
        draft_sha256=hashlib.sha256(encoded).hexdigest(), parsed_draft=parsed,
        requirement_comparisons=comparisons)


class FileRejectionSink:
    """New private directory; exclusive per-failure files; no stdout or wire I/O."""
    def __init__(self, directory: str | Path):
        self.directory = Path(directory).expanduser().absolute()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.saved_paths: list[Path] = []

    def __call__(self, rejection: AnalysisRejection) -> None:
        # Detach the event so a custom sink cannot alter the caller's draft/packet.
        checked = AnalysisRejection.model_validate_json(rejection.model_dump_json())
        payload = (checked.model_dump_json(indent=2) + '\n').encode('utf-8')
        path = self.directory / f'rejected-{len(self.saved_paths) + 1:04d}.json'
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(payload)
            reopened = AnalysisRejection.model_validate_json(path.read_text())
            if reopened != checked:
                raise ReportError('analysis_diagnostic_roundtrip')
        except Exception:
            path.unlink(missing_ok=True)
            raise
        self.saved_paths.append(path)

