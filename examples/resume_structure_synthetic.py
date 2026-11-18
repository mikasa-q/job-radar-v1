"""Stage 1C-1 demo: synthetic PDF + hand-scripted response, no model call."""
import json

import pymupdf

from job_radar.resume_pdf import extract_resume_pdf
from job_radar.resume_structure import ClientResponse, ResumeStructureError, structure_resume
from job_radar.resume_structure_fake import FakeStructureClient


def fact(value: str, page: int):
    return {"value": value, "evidence": [{
        "page": page, "block_id": f"p{page:03d}-b001", "quote": value,
    }]}


def main() -> None:
    with pymupdf.open() as pdf:
        for text in ["教育：合成大学，本科在读。", "课程项目：使用 Python 实现合成查询接口。"]:
            page = pdf.new_page(width=640, height=480)
            page.insert_text((30, 50), text, fontname="china-s", fontsize=12)
        raw = pdf.tobytes()
    document = extract_resume_pdf(raw)
    data = {
        "education": [{"school": fact("合成大学", 1), "degree": fact("本科", 1),
                       "status": fact("在读", 1)}],
        "skills": [{"name": "Python", "mention": fact("Python", 2), "context": "course"}],
        "projects": [{"nature": fact("课程项目", 2),
                      "description": [fact("使用 Python 实现合成查询接口", 2)]}],
        "work_experience": [], "summary": None, "warnings": [],
    }
    client = FakeStructureClient([ClientResponse(status="completed", text=json.dumps(data))])
    result = structure_resume(document, client=client, resume_id="synthetic-structure-demo")
    print("client=fake (scripted; no model call)")
    print(f"education={result.profile.education[0].school.value}/{result.profile.education[0].status.value}")
    print(f"project={result.profile.projects[0].nature.value}")
    print("skills=" + ",".join(skill.name for skill in result.profile.skills))
    print(f"review_status={result.profile.review_status}")
    print(f"client_calls={len(client.requests)}")

    data["skills"][0]["mention"]["evidence"][0]["quote"] = "Python fabricated quote"
    changed = FakeStructureClient([ClientResponse(status="completed", text=json.dumps(data))])
    try:
        structure_resume(document, client=changed, resume_id="synthetic-structure-demo")
    except ResumeStructureError as error:
        if (error.code, error.evidence_code) != ("evidence_error", "quote_mismatch"):
            raise
        print(f"tampered_quote={error.code}/{error.evidence_code}")
    else:
        raise AssertionError("Tampered evidence unexpectedly passed")


if __name__ == "__main__":
    main()
