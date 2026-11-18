"""Stage 1B: entirely synthetic text, documents and PDFs; no personal resume."""
import hashlib
import json
import socket
import sqlite3
from pathlib import Path

import pymupdf
import pytest
import requests
from pydantic import ValidationError

from job_radar.parser import extract_skills
from job_radar.resume_models import ResumeBlock, ResumeDocument, ResumePage, ResumeProvenance
from job_radar.resume_pdf import extract_resume_pdf
from job_radar.resume_profile import (
    DateFact, Education, EvidenceRef, ProfileSource, ProjectExperience,
    ResumeProfile, SkillEvidence, TextFact, WorkExperience,
)
from job_radar.resume_profile_rules import (
    PROFILE_RULES_VERSION, ProfileEvidenceError, build_rule_profile,
    source_from_document, validate_profile_evidence,
)


def document(*pages):
    pages = pages or (["合成空画像说明"],)
    digest = hashlib.sha256(json.dumps(pages, ensure_ascii=False).encode()).hexdigest()
    return ResumeDocument(
        provenance=ResumeProvenance(source_sha256=digest,
                                    parser_version="synthetic-v1", extractor_version="synthetic-text-v1"),
        pages=[ResumePage(
            page_number=i, width=600.0, height=800.0,
            text="reading aid deliberately differs from the block source",
            blocks=[ResumeBlock(block_id=f"p{i:03d}-b{j:03d}", text=text,
                                bbox=(20.0, j * 20.0, 580.0, j * 20.0 + 15.0))
                    for j, text in enumerate(lines, 1)],
        ) for i, lines in enumerate(pages, 1)],
    )


def ref(text, page=1, block=1):
    return EvidenceRef(page=page, block_id=f"p{page:03d}-b{block:03d}", quote=text)


def fact(value, quote=None, page=1, block=1):
    return TextFact(value=value, evidence=[ref(value if quote is None else quote, page, block)])


def dated(value, normalized=None):
    return DateFact(value=value, normalized=normalized, evidence=[ref(value)])


def profile(doc, **fields):
    return ResumeProfile(resume_id="synthetic-resume-001", source=source_from_document(doc), **fields)


def assert_code(code, candidate, doc):
    with pytest.raises(ProfileEvidenceError) as captured:
        validate_profile_evidence(candidate, doc)
    assert captured.value.code == code
    assert captured.value.path.startswith("profile.")


def test_unknown_fields_empty_collections_and_review_default():
    doc = document()
    result = profile(doc)
    assert result.schema_version == "resume-profile-v1"
    assert result.profile_version == 1
    assert result.review_status == "unconfirmed"
    assert result.education == result.skills == result.projects == result.work_experience == []
    assert result.summary is None
    education = Education(school=fact("合成大学"))
    assert education.major is education.degree is education.start_date is education.end_date is None
    assert "proficiency" not in SkillEvidence.model_fields
    assert "years" not in SkillEvidence.model_fields
    assert "phone" not in ResumeProfile.model_fields
    assert "preferences" not in ResumeProfile.model_fields


def test_lists_and_nested_facts_are_not_shared():
    doc = document()
    first, second = profile(doc), profile(doc)
    first.warnings.append("synthetic-warning")
    first.education.append(Education(school=fact("合成大学")))
    assert second.warnings == second.education == []
    a, b = ProjectExperience(name=fact("甲项目")), ProjectExperience(name=fact("乙项目"))
    a.description.append(fact("测试描述"))
    assert b.description == []


@pytest.mark.parametrize("extra", ["confidence", "score", "phone", "preferences", "years", "embedding_text"])
def test_extra_profile_fields_are_rejected(extra):
    with pytest.raises(ValidationError):
        profile(document(), **{extra: 1})


@pytest.mark.parametrize("model,data", [
    (EvidenceRef, {"page": True, "block_id": "p001-b001", "quote": "Python"}),
    (EvidenceRef, {"page": "1", "block_id": "p001-b001", "quote": "Python"}),
    (EvidenceRef, {"page": 0, "block_id": "p001-b001", "quote": "Python"}),
    (EvidenceRef, {"page": 2, "block_id": "p001-b001", "quote": "Python"}),
    (EvidenceRef, {"page": 1, "block_id": "b1", "quote": "Python"}),
    (EvidenceRef, {"page": 1, "block_id": "p001-b001", "quote": "  \n"}),
    (TextFact, {"value": "Python", "evidence": []}),
    (TextFact, {"value": "  ", "evidence": [ref("Python").model_dump()]}),
    (TextFact, {"value": "Django", "evidence": [ref("Python").model_dump()]}),
])
def test_invalid_reference_and_fact_contracts(model, data):
    with pytest.raises(ValidationError):
        model.model_validate(data)


@pytest.mark.parametrize("field,value", [("resume_id", " "), ("profile_version", True),
                                         ("profile_version", 0), ("schema_version", "v0"),
                                         ("review_status", "automatic"), ("warnings", [" "])])
def test_strict_profile_fields(field, value):
    data = profile(document()).model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        ResumeProfile.model_validate(data)


@pytest.mark.parametrize("entry", [Education, ProjectExperience, WorkExperience])
def test_empty_experience_items_rejected(entry):
    with pytest.raises(ValidationError):
        entry()


@pytest.mark.parametrize("extra", ["proficiency", "years", "confidence"])
def test_skill_does_not_allow_inferred_proficiency_or_years(extra):
    with pytest.raises(ValidationError):
        SkillEvidence(name="Python", mention=fact("Python"), **{extra: 2})


@pytest.mark.parametrize("raw,normalized", [
    ("2025", "2025"), ("2025.09", "2025-09"), ("2025/9", "2025-09"),
    ("2025年9月", "2025-09"), ("2025-09", "2025-09"), ("2025年", "2025"),
    ("至今", None), ("预计 2027 年毕业", None), ("2025 秋季", None),
])
def test_dates_keep_raw_text_and_only_explicit_normalization(raw, normalized):
    result = dated(raw, normalized)
    assert result.value == raw and result.normalized == normalized


@pytest.mark.parametrize("raw,normalized", [
    ("2025.09", "2026-09"), ("2025", "2025-01"), ("至今", "2026-11"),
    ("预计 2027 年毕业", "2027"), ("2025.13", None), ("2025.00", None),
    ("0000", None), ("2025", "2025-13"),
])
def test_dates_reject_guessed_precision_or_invalid_months(raw, normalized):
    with pytest.raises(ValidationError):
        dated(raw, normalized)


@pytest.mark.parametrize("entry", [Education, ProjectExperience, WorkExperience])
def test_reversed_explicit_date_ranges_rejected_even_without_normalization(entry):
    with pytest.raises(ValidationError):
        entry(start_date=dated("2026.02"), end_date=dated("2025.12"))
    with pytest.raises(ValidationError):
        entry(start_date=dated("2025.10"), end_date=dated("2025.09"))
    assert entry(start_date=dated("2025"), end_date=dated("2025.01"))
    assert entry(start_date=dated("2025.09"), end_date=dated("至今"))


def test_all_education_project_work_summary_fields_carry_individual_evidence():
    doc = document([
        "教育：合成大学，计算机，本科在读，2025.09 入学，预计 2029 年毕业。",
        "课程项目：合成查询系统；角色：开发者；使用 Python 实现接口。",
        "合成公司，后端实习生，实习；维护 FastAPI 接口。",
        "自述：喜欢编写可复核的测试。",
    ])
    education = Education(
        school=fact("合成大学", doc.pages[0].blocks[0].text),
        major=fact("计算机", doc.pages[0].blocks[0].text),
        degree=fact("本科", doc.pages[0].blocks[0].text),
        status=fact("在读", doc.pages[0].blocks[0].text),
        start_date=DateFact(**fact("2025.09", doc.pages[0].blocks[0].text).model_dump(), normalized="2025-09"),
        end_date=DateFact(**fact("预计 2029 年毕业", doc.pages[0].blocks[0].text).model_dump()),
    )
    project_text, work_text = doc.pages[0].blocks[1].text, doc.pages[0].blocks[2].text
    candidate = profile(doc, education=[education], projects=[ProjectExperience(
        name=fact("合成查询系统", project_text, block=2),
        nature=fact("课程项目", project_text, block=2), role=fact("开发者", project_text, block=2),
        description=[fact("使用 Python 实现接口", project_text, block=2)],
        technologies=[SkillEvidence(name="Python", mention=fact("Python", project_text, block=2), context="course")],
    )], work_experience=[WorkExperience(
        company=fact("合成公司", work_text, block=3), role=fact("后端实习生", work_text, block=3),
        employment_type=fact("实习", work_text, block=3),
        description=[fact("维护 FastAPI 接口", work_text, block=3)],
        technologies=[SkillEvidence(name="FastAPI", mention=fact("FastAPI", work_text, block=3), context="internship")],
    )], summary=fact("喜欢编写可复核的测试", doc.pages[0].blocks[3].text, block=4))
    result = validate_profile_evidence(candidate, doc)
    assert result.education[0].status.value == "在读"
    assert result.education[0].end_date.normalized is None
    assert result.projects[0].nature.value == "课程项目"
    assert result.work_experience[0].employment_type.value == "实习"
    assert result.review_status == "unconfirmed"
    assert result is not candidate
    assert ResumeProfile.model_validate_json(result.model_dump_json()) == result
    assert ResumeProfile.model_json_schema()["additionalProperties"] is False


def test_forged_company_cannot_borrow_project_quote():
    with pytest.raises(ValidationError):
        WorkExperience(company=fact("不存在的公司", "课程项目：使用 Python"))


@pytest.mark.parametrize("field,value", [
    ("source_sha256", "0" * 64), ("parser_version", "different-parser"),
    ("extractor_version", "different-extractor"),
])
def test_profile_is_bound_to_hash_and_both_extraction_versions(field, value):
    doc = document(["Python 项目"])
    candidate = profile(doc)
    setattr(candidate.source, field, value)
    assert_code("source_mismatch", candidate, doc)


def test_source_does_not_accept_invalid_hash_or_extra_local_path():
    source = source_from_document(document()).model_dump()
    for patch in ({"source_sha256": "bad"}, {"local_path": "/private/synthetic.pdf"}):
        with pytest.raises(ValidationError):
            ProfileSource.model_validate({**source, **patch})


def test_missing_page_missing_block_and_quote_mismatch_have_distinct_codes():
    doc = document(["Python 项目"])
    for reference, expected in [
        (ref("Python", page=2), "missing_page"),
        (ref("Python", block=2), "missing_block"),
        (ref("Python Django"), "quote_mismatch"),
    ]:
        candidate = profile(doc, summary=TextFact(value="Python", evidence=[reference]))
        assert_code(expected, candidate, doc)


def test_verbatim_whitespace_is_preserved_and_page_reading_aid_is_not_quote_source():
    doc = document(["  Python  / FastAPI\n项目\n"])
    result = build_rule_profile(doc, resume_id="synthetic")
    assert result.skills[0].mention.evidence[0].quote == "  Python  / FastAPI\n项目\n"
    candidate = profile(doc, summary=fact("reading aid"))
    assert_code("quote_mismatch", candidate, doc)
    candidate = profile(doc, summary=fact("Python / FastAPI"))
    assert_code("quote_mismatch", candidate, doc)


def test_second_bad_reference_is_not_hidden_by_first_valid_one():
    doc = document(["Python 项目"])
    candidate = profile(doc, summary=TextFact(value="Python", evidence=[ref("Python"), ref("Docker")]))
    assert_code("quote_mismatch", candidate, doc)


def test_mutable_lists_and_model_copy_updates_are_revalidated_at_boundary():
    doc = document(["Python 项目"])
    candidate = profile(doc, summary=fact("Python"))
    candidate.summary.evidence.clear()
    with pytest.raises(ValidationError):
        validate_profile_evidence(candidate, doc)
    candidate = profile(doc).model_copy(update={"profile_version": True})
    with pytest.raises(ValidationError):
        validate_profile_evidence(candidate, doc)
    doc.pages[0].blocks[0].text = " "
    with pytest.raises(ValidationError):
        build_rule_profile(doc, resume_id="synthetic")


@pytest.mark.parametrize("review", ["unconfirmed", "confirmed", "rejected"])
def test_validator_preserves_review_status_and_never_approves(review):
    doc = document(["Python 项目"])
    assert validate_profile_evidence(profile(doc, review_status=review), doc).review_status == review
    assert build_rule_profile(doc, resume_id="synthetic").review_status == "unconfirmed"


def test_aliases_case_boundaries_and_canonical_order_with_original_text():
    doc = document(["课程项目：PYTHON3、fastapi、py.test、Tool-Calling、MySQL、PostgreSQL、SQLAlchemy、Dockerfile、RAGtime、SQL。"])
    result = build_rule_profile(doc, resume_id="synthetic")
    assert [s.name for s in result.skills] == ["Python", "FastAPI", "pytest", "Tool Calling", "MySQL", "SQL"]
    assert result.skills[0].mention.value == "PYTHON3"
    assert result.skills[2].mention.value == "py.test"
    assert all(s.context == "course" for s in result.skills)


def test_repeated_skill_retains_page_and_block_evidence_but_deduplicates_in_block():
    doc = document(["Python Python"], ["项目中使用 Python"])
    result = build_rule_profile(doc, resume_id="synthetic")
    assert len(result.skills) == 2
    assert [(s.mention.evidence[0].page, s.mention.evidence[0].block_id) for s in result.skills] == [
        (1, "p001-b001"), (2, "p002-b001"),
    ]


@pytest.mark.parametrize("text", [
    "计划学习 Docker", "暂未使用 Redis", "不会 MySQL", "没有 Python 项目经验", "不熟悉 FastAPI",
    "Docker 尚未使用", "无 Docker 使用经验", "将学习 Git", "希望掌握 Linux", "准备学习 pytest",
    "想学习 RAG", "岗位要求：掌握 Docker", "求职目标：Python 开发", "Future: Docker",
    "No Docker experience", "Not familiar with Docker", "Never used Python", "Without Redis experience",
    "I plan to learn FastAPI", "I will learn Python", "I haven't used Docker", "I don’t know SQL",
    "Requirements: Python and Docker", "Docker 待掌握", "未接触 MCP",
])
def test_negation_future_and_requirements_are_not_positive_skills(text):
    result = build_rule_profile(document([text]), resume_id="synthetic")
    assert result.skills == []
    assert "no_positive_skill_evidence" in result.warnings
    assert "excluded_skill_context:p001-b001" in result.warnings


def test_plan_sentence_does_not_hide_later_independent_positive_block():
    result = build_rule_profile(document(["计划学习 Docker", "项目中使用 Python"]), resume_id="synthetic")
    assert [s.name for s in result.skills] == ["Python"]
    assert result.skills[0].mention.evidence[0].block_id == "p001-b002"


def test_entire_mixed_block_is_excluded_conservatively_without_fake_sentence_semantics():
    result = build_rule_profile(document(["使用 Python；计划学习 Docker。"]), resume_id="synthetic")
    assert result.skills == []
    assert "excluded_skill_context:p001-b001" in result.warnings


def test_risky_headings_cover_separate_blocks_and_pages_until_explicit_safe_heading():
    doc = document(["学习计划", "Docker", "Redis"], ["FastAPI", "项目经历", "项目中使用 Python"])
    result = build_rule_profile(doc, resume_id="synthetic")
    assert [s.name for s in result.skills] == ["Python"]
    assert result.skills[0].mention.evidence[0].page == 2
    assert "excluded_skill_context:p002-b001" in result.warnings


def test_heading_with_same_line_data_is_not_promoted():
    result = build_rule_profile(document(["学习计划：Docker", "Redis", "技能", "Python"]), resume_id="synthetic")
    assert [s.name for s in result.skills] == ["Python"]


@pytest.mark.parametrize("text,context", [
    ("课程项目：Python", "course"), ("实习项目：FastAPI", "internship"),
    ("项目：Redis", "project"), ("工作经历：MySQL", "work"),
    ("技术栈：Docker", "skill_list"), ("Git", "unspecified"),
])
def test_context_labels_are_local_text_hints_not_job_classification(text, context):
    result = build_rule_profile(document([text]), resume_id="synthetic")
    assert result.skills[0].context == context
    assert result.projects == result.work_experience == []


def test_unknown_skills_are_not_fuzzily_guessed_and_missing_is_not_negative():
    result = build_rule_profile(document(["使用 Rust 与 Django 开发合成系统"]), resume_id="synthetic")
    assert result.skills == []  # outside the deliberately small baseline vocabulary
    assert "no_positive_skill_evidence" in result.warnings
    assert not any("不会" in warning for warning in result.warnings)
    assert result.education == result.projects == result.work_experience == []
    assert "not_extracted:education" in result.warnings


def test_exact_unknown_skill_can_be_represented_manually_with_evidence():
    doc = document(["使用 Rust 开发合成项目"])
    candidate = profile(doc, skills=[SkillEvidence(name="Rust", mention=fact("Rust"))])
    assert validate_profile_evidence(candidate, doc).skills[0].name == "Rust"


@pytest.mark.parametrize("name,mention", [("Docker", "Python"), ("Rust", "Python"), ("python", "Python")])
def test_skill_names_cannot_be_changed_to_unrelated_or_noncanonical_known_name(name, mention):
    doc = document(["项目中使用 Python"])
    assert_code("skill_name_mismatch", profile(doc, skills=[SkillEvidence(name=name, mention=fact(mention))]), doc)


def test_short_quote_cannot_hide_full_block_negation_or_plan_section():
    for doc, reference in [
        (document(["暂未使用 Docker"]), ref("Docker")),
        (document(["学习计划", "Docker"]), ref("Docker", block=2)),
    ]:
        candidate = profile(doc, skills=[SkillEvidence(name="Docker", mention=TextFact(value="Docker", evidence=[reference]))])
        assert_code("non_positive_skill_context", candidate, doc)


def test_rules_preserve_quality_warnings_determinism_and_do_not_mutate_document():
    doc = document(["Python 项目"])
    doc.warnings.append("pdf_repaired")
    doc.pages[0].warnings.extend(["possible_layout_issue", "low_text_content"])
    before = doc.model_dump_json()
    first = build_rule_profile(doc, resume_id="synthetic")
    second = build_rule_profile(doc, resume_id="synthetic")
    assert first == second and doc.model_dump_json() == before
    assert first.builder_version == PROFILE_RULES_VERSION
    assert "pdf_repaired" in first.warnings
    assert "page_1:possible_layout_issue" in first.warnings
    assert "page_1:low_text_content" in first.warnings
    assert first.summary is None


def test_old_job_skill_extractor_is_unchanged_and_not_the_resume_semantic_gate():
    assert extract_skills("计划学习 Docker，暂未使用 Redis") == ["Redis", "Docker"]
    assert build_rule_profile(document(["计划学习 Docker，暂未使用 Redis"]), resume_id="synthetic").skills == []


def test_pure_profile_layer_has_no_application_io_or_clock(monkeypatch):
    doc = document(["课程项目：Python FastAPI"])

    def unexpected(*args, **kwargs):
        raise AssertionError("1B must not use file/network/database I/O")

    monkeypatch.setattr(Path, "read_bytes", unexpected)
    monkeypatch.setattr(Path, "read_text", unexpected)
    monkeypatch.setattr(Path, "write_bytes", unexpected)
    monkeypatch.setattr(Path, "write_text", unexpected)
    monkeypatch.setattr(requests.sessions.Session, "request", unexpected)
    monkeypatch.setattr(socket, "create_connection", unexpected)
    monkeypatch.setattr(sqlite3, "connect", unexpected)
    assert len(build_rule_profile(doc, resume_id="synthetic").skills) == 2


def test_synthetic_pdf_to_document_to_profile_with_correct_page_and_verbatim_quote():
    with pymupdf.open() as pdf:
        for text in ["计划学习 Docker", "课程项目：使用 Python 和 FastAPI 实现合成接口"]:
            page = pdf.new_page(width=640, height=480)
            page.insert_text((30, 50), text, fontname="china-s", fontsize=12)
        raw = pdf.tobytes()
    original_hash = hashlib.sha256(raw).hexdigest()
    doc = extract_resume_pdf(raw)
    result = build_rule_profile(doc, resume_id="synthetic-pdf-001")
    assert [s.name for s in result.skills] == ["Python", "FastAPI"]
    assert result.source.source_sha256 == original_hash == hashlib.sha256(raw).hexdigest()
    for skill in result.skills:
        reference = skill.mention.evidence[0]
        assert reference.page == 2
        assert reference.block_id == "p002-b001"
        assert reference.quote in doc.pages[1].blocks[0].text
    changed = ResumeProfile.model_validate_json(result.model_dump_json())
    changed.skills[0].mention.evidence[0].quote = "Python 是我精通的技能"
    assert_code("quote_mismatch", changed, doc)


def test_inconsistent_date_notation_is_not_accepted_as_normalized_date():
    for raw in ["2025.09月年", "2025年09年", "2025/09月"]:
        assert dated(raw).normalized is None
        with pytest.raises(ValidationError):
            dated(raw, "2025-09")


def test_negative_heading_persists_but_new_safe_heading_resumes_extraction():
    result = build_rule_profile(document(["暂未使用", "Docker", "技能", "Python"]), resume_id="synthetic")
    assert [s.name for s in result.skills] == ["Python"]


def test_alias_cannot_be_repurposed_as_an_unknown_canonical_name():
    doc = document(["Python3 项目"])
    assert_code("skill_name_mismatch", profile(doc, skills=[
        SkillEvidence(name="Python3", mention=fact("Python3")),
    ]), doc)


def test_error_text_contains_only_code_and_field_path_not_resume_text():
    doc = document(["合成保密文字 Python 项目"])
    candidate = profile(doc, summary=fact("Python", "Python 合成保密文字"))
    with pytest.raises(ProfileEvidenceError) as captured:
        validate_profile_evidence(candidate, doc)
    assert "合成保密文字" not in str(captured.value)
    assert str(captured.value).startswith("quote_mismatch: profile.summary")


def test_assignment_validation_is_not_a_substitute_for_document_validation():
    candidate = profile(document())
    with pytest.raises(ValidationError):
        candidate.profile_version = "2"


def test_blank_pages_preserve_page_positions_and_no_skill_success_is_explicit():
    doc = document([], ["无词表技能的合成自述"])
    result = build_rule_profile(doc, resume_id="synthetic")
    assert result.skills == []
    assert result.review_status == "unconfirmed"
    assert "no_positive_skill_evidence" in result.warnings


def test_nested_project_reference_is_checked_not_only_top_level_skill_list():
    doc = document(["课程项目：Python"])
    candidate = profile(doc, projects=[ProjectExperience(
        name=fact("合成项目", "合成项目", block=9),
    )])
    assert_code("missing_block", candidate, doc)


@pytest.mark.parametrize("text", ["Pythonista", "MyPython", "Python_foo"])
def test_manual_skill_cannot_use_a_substring_as_token_evidence(text):
    doc = document([text])
    candidate = profile(doc, skills=[SkillEvidence(name="Python", mention=fact("Python", text))])
    assert_code("skill_mention_mismatch", candidate, doc)


def test_every_skill_quote_must_contain_the_named_mention():
    doc = document(["Python 项目", "纯文字说明"])
    candidate = profile(doc, skills=[SkillEvidence(name="Python", mention=TextFact(
        value="Python", evidence=[ref("Python 项目"), ref("纯文字说明", block=2)],
    ))])
    assert_code("skill_mention_mismatch", candidate, doc)


def test_documented_synthetic_demo_runs_without_user_files():
    import subprocess
    import sys

    example = Path(__file__).resolve().parents[1] / "examples" / "resume_profile_synthetic.py"
    result = subprocess.run([sys.executable, "-B", str(example)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "skills=Python,FastAPI\n"
        "evidence=2:p002-b001\n"
        "review_status=unconfirmed\n"
        "education=[] (not extracted)\n"
        "tampered_quote=quote_mismatch\n"
    )


def test_short_quote_cannot_cut_a_suffix_to_manufacture_a_skill_token():
    doc = document(["Pythonista"])
    candidate = profile(doc, skills=[SkillEvidence(name="Python", mention=fact("Python"))])
    assert_code("skill_mention_mismatch", candidate, doc)


def test_repeated_quote_uses_a_real_boundary_in_the_original_block():
    doc = document(["Pythonista，然后使用 Python"])
    candidate = profile(doc, skills=[SkillEvidence(name="Python", mention=fact("Python"))])
    assert validate_profile_evidence(candidate, doc).skills[0].name == "Python"
