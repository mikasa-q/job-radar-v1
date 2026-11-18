"""Stage 1D: in-memory synthetic evidence, no SQLite/network/model call."""
import hashlib
import json
from datetime import datetime, timezone

from pydantic import ValidationError

from job_radar.import_models import ImportedJob
from job_radar.matching_input import build_matching_input, build_matching_request
from job_radar.matching_models import JobCandidate, JobProvenance, SearchPreferences
from job_radar.resume_models import ResumeBlock, ResumeDocument, ResumePage, ResumeProvenance
from job_radar.resume_profile_rules import (
    ProfileEvidenceError, build_rule_profile, validate_profile_evidence,
)
from job_radar.resume_structure import ClientResponse, structure_resume
from job_radar.resume_structure_fake import FakeStructureClient


def main() -> None:
    lines = ["计划学习 Docker", "课程项目：使用 Python 实现查询接口。"]
    document = ResumeDocument(
        provenance=ResumeProvenance(
            source_sha256=hashlib.sha256("\n".join(lines).encode()).hexdigest(),
            parser_version="synthetic-v1", extractor_version="synthetic-text-v1"),
        pages=[ResumePage(page_number=1, width=600.0, height=800.0, blocks=[
            ResumeBlock(block_id=f"p001-b{index:03d}", text=text,
                        bbox=(20.0, float(index*30), 580.0, float(index*30+20)))
            for index, text in enumerate(lines, 1)])],
    )
    ref = {"page": 1, "block_id": "p001-b002", "quote": lines[1]}
    draft = {
        "education": [], "skills": [{"name": "Python", "context": "course",
            "mention": {"value": "Python", "evidence": [ref]}}],
        "projects": [{"nature": {"value": "课程项目", "evidence": [ref]}}],
        "work_experience": [], "summary": None, "warnings": [],
    }
    client = FakeStructureClient([ClientResponse(status="completed", text=json.dumps(draft))])
    structured = structure_resume(document, client=client, resume_id="synthetic-1c")
    rules = build_rule_profile(document, resume_id="synthetic-1b")
    preferences = SearchPreferences()  # all unspecified; no city inference
    projected_rules = build_matching_input(rules, preferences=preferences)
    projected = build_matching_input(structured.profile, preferences=preferences)
    job = ImportedJob(source="manual", title="合成开发实习", url="https://example.org/jobs/1",
                      description="岗位要求：Python；每周至少三天。", skills=["Python"])
    canonical = json.dumps(job.model_dump(), ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    candidate = JobCandidate(
        dataset_id="synthetic-evidence-v1", job_id=1, job=job,
        jd_sha256=hashlib.sha256(job.description.encode()).hexdigest(),
        fact_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
        provenance=[JobProvenance(provenance_id=1, source_file_sha256="1"*64,
            input_format="txt", parser_version="synthetic-txt-v1",
            imported_at=datetime(2026, 11, 11, tzinfo=timezone.utc), import_run_id="synthetic-run")],
        review_status="unconfirmed",
    )
    request = build_matching_request(projected, [candidate], purpose="debug")
    assert [s.name for s in projected.skills] == ["Python"]
    assert request.jobs[0].job.description == job.description
    print("mode=synthetic/offline; client=fake (no real model)")
    print("1B.skills=" + ",".join(s.name for s in projected_rules.skills))
    print("1C.skills=" + ",".join(s.name for s in projected.skills))
    print("planned_Docker=excluded")
    print("preferences=unspecified; education=[] (unknown)")
    print("request=matching-request-v1; purpose=debug; jobs=1 (unranked)")
    print("review_status=unconfirmed/unconfirmed")
    print("evidence=1:p001-b002; provenance_versions=1")
    print("scores=absent; database_reads=0; network_calls=0")
    try:
        build_matching_request(projected, [candidate], purpose="formal")
    except ValidationError as error:
        assert any(e["msg"] == "Value error, formal_requires_confirmed" for e in error.errors())
        print("formal=formal_requires_confirmed")
    else:
        raise AssertionError("Unconfirmed data passed formal gate")
    tampered = structured.profile.model_copy(deep=True)
    tampered.skills[0].mention.evidence[0].quote = "Python fabricated quote"
    try:
        validate_profile_evidence(tampered, document)
    except ProfileEvidenceError as error:
        assert error.code == "quote_mismatch"
        print("upstream_tampered_quote=quote_mismatch")
    else:
        raise AssertionError("Tampered quote passed upstream validation")


if __name__ == "__main__":
    main()
