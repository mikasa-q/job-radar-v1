"""Run with the project's Python. All PDF content is synthesized in memory."""
import pymupdf

from job_radar.resume_pdf import extract_resume_pdf
from job_radar.resume_profile import ResumeProfile
from job_radar.resume_profile_rules import (
    ProfileEvidenceError, build_rule_profile, validate_profile_evidence,
)


def main() -> None:
    with pymupdf.open() as pdf:
        for text in ["计划学习 Docker", "课程项目：使用 Python 和 FastAPI 实现合成接口"]:
            page = pdf.new_page(width=640, height=480)
            page.insert_text((30, 50), text, fontname="china-s", fontsize=12)
        raw = pdf.tobytes()
    document = extract_resume_pdf(raw)
    profile = build_rule_profile(document, resume_id="synthetic-demo-001")
    print("skills=" + ",".join(skill.name for skill in profile.skills))
    reference = profile.skills[0].mention.evidence[0]
    print(f"evidence={reference.page}:{reference.block_id}")
    print(f"review_status={profile.review_status}")
    print(f"education={profile.education} (not extracted)")

    changed = ResumeProfile.model_validate_json(profile.model_dump_json())
    changed.skills[0].mention.evidence[0].quote = "Python 是我精通的技能"
    try:
        validate_profile_evidence(changed, document)
    except ProfileEvidenceError as error:
        if error.code != "quote_mismatch":
            raise
        print(f"tampered_quote={error.code}")
    else:
        raise AssertionError("Tampered quote unexpectedly passed validation")


if __name__ == "__main__":
    main()
