"""Pure Stage 1D projection and assembly; I/O stays with the calling layer.

Before projecting, the upstream caller MUST use validate_profile_evidence with
its original ResumeDocument (1B and 1C already do so). These functions recheck
structure, hashes and review gates, not document quotes or semantic truth.
"""
from __future__ import annotations

from .matching_models import (
    JobCandidate, MatchingInput, MatchingPurpose, MatchingRequest, SearchPreferences, _plain,
)
from .resume_profile import ResumeProfile


def build_matching_input(
    profile: ResumeProfile, *, preferences: SearchPreferences,
) -> MatchingInput:
    """Preserve facts, evidence, unknowns, order and status in a detached copy."""
    if not isinstance(profile, ResumeProfile):
        raise TypeError("expected_resume_profile")
    if not isinstance(preferences, SearchPreferences):
        raise TypeError("expected_search_preferences")
    checked = ResumeProfile.model_validate(_plain(profile))
    data = checked.model_dump()
    data["profile_schema_version"] = data.pop("schema_version")
    data["profile_id"] = data.pop("resume_id")
    data["preferences"] = _plain(preferences)
    return MatchingInput.model_validate(data)


def build_matching_request(
    profile: MatchingInput, jobs: list[JobCandidate], *, purpose: MatchingPurpose,
) -> MatchingRequest:
    """Assemble only; preserve supplied order, even when there are zero jobs.

    Every candidate is revalidated, including mutated nested fields and lists.
    Caller-supplied confirmed states are preserved, not independently attested.
    """
    if not isinstance(profile, MatchingInput):
        raise TypeError("expected_matching_input")
    if not isinstance(jobs, list) or any(not isinstance(item, JobCandidate) for item in jobs):
        raise TypeError("expected_job_candidates")
    return MatchingRequest.model_validate({
        "purpose": purpose, "profile": _plain(profile),
        "jobs": [_plain(item) for item in jobs],
    })
