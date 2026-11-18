"""Offline evidence tests: synthetic PDFs never contain a real person's resume.

Chinese fixture text uses PyMuPDF's built-in CJK font, not Helvetica: generating
missing-glyph dots would test a broken fixture rather than Chinese extraction.
"""
import hashlib
import math
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymupdf
import pytest
import requests
from pydantic import ValidationError

from job_radar.resume_models import (
    ResumeBlock, ResumeDocument, ResumePage, ResumeProvenance,
)
from job_radar.resume_pdf import (
    DEFAULT_MAX_BYTES, DEFAULT_MAX_PAGES, EXTRACTOR_VERSION,
    ResumePDFError, extract_resume_pdf,
)
from job_radar.resume_preview import ResumePreview, main, preview_resume_file


WHEN = datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc)


def make_pdf(*pages: list[str]) -> bytes:
    """Create a text PDF with an actual CJK mapping; no file or font download."""
    with pymupdf.open() as document:
        for lines in pages:
            page = document.new_page(width=640, height=480)
            for index, line in enumerate(lines):
                page.insert_text((30, 50 + index * 30), line,
                                 fontname="china-s", fontsize=12)
        return document.tobytes()


def make_blank_pdf(*, count: int = 1) -> bytes:
    return make_pdf(*([] for _ in range(count)))


def make_image_pdf(*, with_text_page=False) -> bytes:
    # Render locally, then embed pixels. The visible words must not accidentally
    # retain a hidden text layer and make this supposed image-only case pass.
    with pymupdf.open(stream=make_pdf(["图片里的 Python 技能"]), filetype="pdf") as source:
        png = source[0].get_pixmap().tobytes("png")
    with pymupdf.open() as document:
        if with_text_page:
            page = document.new_page(width=640, height=480)
            page.insert_text((30, 50), "Python project evidence", fontsize=12)
        page = document.new_page(width=640, height=480)
        page.insert_image(page.rect, stream=png)
        return document.tobytes()


def error_code(raw, expected, **kwargs):
    with pytest.raises(ResumePDFError) as captured:
        extract_resume_pdf(raw, **kwargs)
    assert captured.value.code == expected


def test_pinned_extractor_version_and_default_limits():
    assert pymupdf.version[0] == "1.26.7"
    assert EXTRACTOR_VERSION == "pymupdf-1.26.7"
    assert DEFAULT_MAX_BYTES == 10 * 1024 * 1024
    assert DEFAULT_MAX_PAGES == 10


def test_extracts_chinese_english_pages_blocks_and_byte_identity():
    raw = make_pdf(["姓名：李同学", "Python / FastAPI 项目", "教育：本科"],
                   ["项目：JobRadar", "技能：Git Docker"])
    result = extract_resume_pdf(raw, processed_at=WHEN)
    assert result.schema_version == "resume-document-v1"
    assert [page.page_number for page in result.pages] == [1, 2]
    assert "姓名：李同学" in result.pages[0].text
    assert "Python / FastAPI 项目" in result.pages[0].text
    assert "JobRadar" in result.pages[1].text
    assert result.provenance.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.provenance.extractor_version == EXTRACTOR_VERSION
    assert result.provenance.processed_at == WHEN
    assert result.provenance.processed_at.utcoffset() == timedelta(0)
    for page in result.pages:
        assert page.width == 640 and page.height == 480
        assert page.blocks
        for block in page.blocks:
            assert block.block_id
            x0, y0, x1, y1 = block.bbox
            assert all(math.isfinite(value) for value in block.bbox)
            assert x0 <= x1 and y0 <= y1
            assert x1 > 0 and y1 > 0


def test_same_bytes_and_timestamp_produce_identical_evidence_and_stable_ids():
    raw = make_pdf(["Python / FastAPI 项目"], ["第二页 Git 项目"])
    first = extract_resume_pdf(raw, processed_at=WHEN)
    second = extract_resume_pdf(raw, processed_at=WHEN)
    assert first.model_dump() == second.model_dump()
    ids = [block.block_id for page in first.pages for block in page.blocks]
    assert len(ids) == len(set(ids))


def test_block_quotes_preserve_native_whitespace_and_line_breaks():
    raw = make_pdf(["  Python  /  FastAPI 项目  "])
    with pymupdf.open(stream=raw, filetype="pdf") as pdf:
        native = [block[4].replace("\r\n", "\n").replace("\r", "\n")
                  for block in pdf[0].get_text("blocks", sort=True)
                  if block[6] == 0 and block[4].strip()]
    result = extract_resume_pdf(raw, processed_at=WHEN)
    assert [block.text for block in result.pages[0].blocks] == native
    assert "Python  /  FastAPI" in result.pages[0].blocks[0].text


def test_adapter_never_reads_files_calls_network_or_connects_database(monkeypatch):
    raw = make_pdf(["Python / FastAPI 项目"])

    def unexpected(*args, **kwargs):
        raise AssertionError("PDF bytes adapter must not perform application I/O")

    monkeypatch.setattr(Path, "read_bytes", unexpected)
    monkeypatch.setattr(Path, "write_bytes", unexpected)
    monkeypatch.setattr(Path, "write_text", unexpected)
    monkeypatch.setattr(requests.sessions.Session, "request", unexpected)
    monkeypatch.setattr(sqlite3, "connect", unexpected)
    assert extract_resume_pdf(raw, processed_at=WHEN).pages[0].blocks


@pytest.mark.parametrize(("raw", "code"), [
    (b"", "empty_pdf"), (b"not a pdf", "invalid_pdf"),
    (b"%PDF-1.7\nnot a valid object or cross-reference", "invalid_pdf"),
])
def test_empty_non_pdf_and_corrupt_pdf_have_stable_error_codes(raw, code):
    error_code(raw, code)


@pytest.mark.parametrize("raw", [None, "pdf", bytearray(b"pdf"), memoryview(b"pdf"), 12])
def test_adapter_accepts_bytes_only(raw):
    error_code(raw, "invalid_input")


@pytest.mark.parametrize("kwargs", [
    {"max_bytes": 0}, {"max_bytes": -1}, {"max_bytes": True},
    {"max_bytes": 1.5}, {"max_bytes": "100"},
    {"max_pages": 0}, {"max_pages": -1}, {"max_pages": True},
    {"max_pages": 1.5}, {"max_pages": "10"},
])
def test_resource_limit_parameters_are_positive_integers(kwargs):
    error_code(make_pdf(["Python"]), "invalid_limit", **kwargs)


def test_size_limit_uses_raw_bytes_and_accepts_exact_limit():
    raw = make_pdf(["Python / FastAPI 项目"])
    assert extract_resume_pdf(raw, max_bytes=len(raw), processed_at=WHEN)
    error_code(raw, "size_limit", max_bytes=len(raw) - 1)


def test_default_ten_mebibyte_boundary():
    raw = make_pdf(["Python / FastAPI 项目"])
    # Trailing PDF whitespace is legal and does not alter the evidence text.
    at_limit = raw + b"\n" * (DEFAULT_MAX_BYTES - len(raw))
    assert extract_resume_pdf(at_limit, processed_at=WHEN).pages[0].blocks
    error_code(at_limit + b"\n", "size_limit")


def test_default_ten_page_boundary():
    at_limit = make_pdf(*(["Python 项目"] for _ in range(10)))
    assert len(extract_resume_pdf(at_limit, processed_at=WHEN).pages) == 10
    over_limit = make_pdf(*(["Python 项目"] for _ in range(11)))
    error_code(over_limit, "page_limit")


def test_hash_mismatch_and_naive_timestamp_are_rejected():
    raw = make_pdf(["Python / FastAPI 项目"])
    error_code(raw, "hash_mismatch", source_sha256="0" * 64)
    error_code(raw, "invalid_timestamp", processed_at=datetime(2026, 1, 1))


def test_adapter_does_not_invent_processing_time_when_caller_omits_it():
    result = extract_resume_pdf(make_pdf(["Python project"]))
    assert result.provenance.processed_at is None


def test_aware_timestamp_is_normalized_to_utc():
    local = datetime(2026, 11, 9, 13, tzinfo=timezone(timedelta(hours=8)))
    result = extract_resume_pdf(make_pdf(["Python 项目"]), processed_at=local)
    assert result.provenance.processed_at == local
    assert result.provenance.processed_at.utcoffset() == timedelta(0)


def test_valid_png_bytes_cannot_be_mislabelled_as_pdf():
    with pymupdf.open(stream=make_pdf(["Python"]), filetype="pdf") as document:
        png = document[0].get_pixmap().tobytes("png")
    error_code(png, "invalid_pdf")


def test_heavily_truncated_pdf_is_not_silently_repaired_to_success():
    raw = make_pdf(["Python project"])
    error_code(raw[:len(raw) // 3], "invalid_pdf")


def test_recoverable_cross_reference_damage_is_explicitly_warned():
    raw = make_pdf(["Python project"])
    damaged = raw[:raw.index(b"\nxref")] + b"\n%%EOF\n"
    with pymupdf.open(stream=damaged, filetype="pdf") as native:
        assert native.is_repaired
    result = extract_resume_pdf(damaged, processed_at=WHEN)
    assert "Python project" in result.pages[0].text
    assert "pdf_repaired" in result.warnings
    assert result.provenance.source_sha256 == hashlib.sha256(damaged).hexdigest()


def test_overlapping_column_geometry_is_only_a_warning():
    with pymupdf.open() as document:
        page = document.new_page(width=640, height=480)
        page.insert_text((30, 70), "Python project", fontsize=12)
        # A small vertical offset leaves two native blocks with intersecting
        # y ranges; exactly aligned lines may be merged by MuPDF into one block.
        page.insert_text((350, 80), "Education 2026", fontsize=12)
        raw = document.tobytes()
    result = extract_resume_pdf(raw, processed_at=WHEN)
    assert "possible_layout_issue" in result.pages[0].warnings
    assert "possible_layout_issue" in result.warnings
    assert len(result.pages[0].blocks) == 2


def test_password_protected_pdf_is_not_silently_empty():
    with pymupdf.open(stream=make_pdf(["Python 项目"]), filetype="pdf") as document:
        raw = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                               owner_pw="test-owner", user_pw="test-user")
    error_code(raw, "password_required")


def test_blank_pdf_and_image_only_pdf_are_not_empty_successes():
    error_code(make_blank_pdf(), "no_extractable_text")
    error_code(make_image_pdf(), "no_extractable_text")


def test_blank_page_is_retained_with_explicit_observation():
    result = extract_resume_pdf(make_pdf(["Python 简历内容"], []), processed_at=WHEN)
    assert len(result.pages) == 2
    assert result.pages[1].page_number == 2
    assert result.pages[1].text == "" and result.pages[1].blocks == []
    assert "no_extractable_text" in result.pages[1].warnings
    assert "some_pages_no_extractable_text" in result.warnings


def test_image_page_in_mixed_document_keeps_page_and_does_not_run_ocr(monkeypatch):
    raw = make_image_pdf(with_text_page=True)
    monkeypatch.setattr(pymupdf.Page, "get_textpage_ocr",
                        lambda *args, **kwargs: pytest.fail("OCR must not run"))
    result = extract_resume_pdf(raw, processed_at=WHEN)
    assert len(result.pages) == 2
    assert "Python project evidence" in result.pages[0].text
    assert result.pages[1].text == "" and result.pages[1].blocks == []
    assert "no_extractable_text" in result.pages[1].warnings


def test_small_text_page_has_low_content_warning_without_scanner_diagnosis():
    result = extract_resume_pdf(make_pdf(["Python"], ["完整文字页面 " * 12]), processed_at=WHEN)
    assert "low_text_content" in result.pages[0].warnings
    warnings = result.warnings + result.pages[0].warnings
    assert not any("scanned" in warning or "scan_confirmed" in warning for warning in warnings)


def test_two_column_coordinates_are_preserved_without_claiming_layout_repair():
    with pymupdf.open() as document:
        page = document.new_page(width=640, height=480)
        for x, y, text in [(30, 70, "Python project"), (350, 100, "Education 2026")]:
            page.insert_text((x, y), text, fontsize=12)
        raw = document.tobytes()
    result = extract_resume_pdf(raw, processed_at=WHEN)
    blocks = result.pages[0].blocks
    assert "Python" in result.pages[0].text and "Education" in result.pages[0].text
    assert any(block.bbox[0] < 100 for block in blocks)
    assert any(block.bbox[0] > 300 for block in blocks)


@pytest.mark.parametrize("bbox", [
    (float("nan"), 0.0, 1.0, 2.0), (0.0, 0.0, float("inf"), 2.0),
    (3.0, 0.0, 1.0, 2.0), (0.0, 3.0, 1.0, 2.0),
])
def test_contract_rejects_nonfinite_and_reversed_coordinates(bbox):
    with pytest.raises(ValidationError):
        ResumeBlock(block_id="p001-b001", text="Python", bbox=bbox)


def test_contract_preserves_block_text_and_accepts_real_negative_edge_coordinate():
    text = "  Python\n"
    block = ResumeBlock(block_id="p001-b001", text=text,
                        bbox=(-1.0, 0.0, 10.0, 10.0))
    assert block.text == text


@pytest.mark.parametrize("model", [ResumeBlock, ResumePage, ResumeProvenance, ResumeDocument])
def test_contract_rejects_extra_fields(model):
    result = extract_resume_pdf(make_pdf(["Python 项目"]), processed_at=WHEN)
    examples = {
        ResumeBlock: result.pages[0].blocks[0].model_dump(),
        ResumePage: result.pages[0].model_dump(),
        ResumeProvenance: result.provenance.model_dump(),
        ResumeDocument: result.model_dump(),
    }
    with pytest.raises(ValidationError):
        model.model_validate({**examples[model], "unexpected": 1})


def test_contract_rejects_noncontiguous_page_numbers_and_duplicate_block_ids():
    result = extract_resume_pdf(make_pdf(["Python"], ["Git"]), processed_at=WHEN)
    invalid = result.model_dump()
    invalid["pages"][1]["page_number"] = 3
    with pytest.raises(ValidationError):
        ResumeDocument.model_validate(invalid)
    invalid = result.model_dump()
    invalid["pages"][1]["blocks"][0]["block_id"] = invalid["pages"][0]["blocks"][0]["block_id"]
    with pytest.raises(ValidationError):
        ResumeDocument.model_validate(invalid)


def test_page_contract_rejects_nonfinite_dimensions_and_wholly_external_block():
    for dimension in (float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            ResumePage(page_number=1, width=dimension, height=10.0)
    outside = ResumeBlock(block_id="p001-b001", text="Python",
                          bbox=(1000.0, 1000.0, 1010.0, 1010.0))
    with pytest.raises(ValidationError):
        ResumePage(page_number=1, width=10.0, height=10.0, blocks=[outside])


def test_contract_lists_are_independent_and_utc_timestamp_is_required():
    first = ResumePage(page_number=1, width=10.0, height=10.0)
    second = ResumePage(page_number=1, width=10.0, height=10.0)
    first.warnings.append("warning")
    assert second.warnings == []
    with pytest.raises(ValidationError):
        ResumeProvenance(source_sha256="a" * 64, parser_version="v1",
                         extractor_version=EXTRACTOR_VERSION,
                         processed_at=datetime(2026, 1, 1))


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_page_dimensions_and_blocks_share_unrotated_coordinates(rotation):
    with pymupdf.open(stream=make_pdf(["Python project"]), filetype="pdf") as source:
        source[0].set_rotation(rotation)
        raw = source.tobytes()
    result = extract_resume_pdf(raw, processed_at=WHEN)
    page = result.pages[0]
    assert page.rotation == rotation
    assert page.width == 640.0 and page.height == 480.0
    assert "Python" in page.text
    assert page.blocks[0].bbox[0] == 30.0


def test_preview_contract_rejects_mismatched_hash_and_relative_source():
    document = extract_resume_pdf(make_pdf(["Python project"]), processed_at=WHEN)
    with pytest.raises(ValidationError):
        ResumePreview(source_file="/tmp/example.pdf", source_sha256="0" * 64,
                      document=document)
    with pytest.raises(ValidationError):
        ResumePreview(source_file="example.pdf",
                      source_sha256=document.provenance.source_sha256,
                      document=document)


def test_preview_hashes_original_bytes_resolves_path_and_does_not_write(tmp_path, monkeypatch):
    path = tmp_path / "resume.PDF"
    raw = make_pdf(["姓名：李同学", "Python 项目经历"])
    path.write_bytes(raw)
    monkeypatch.chdir(tmp_path)
    before = hashlib.sha256(raw).hexdigest()

    def unexpected(*args, **kwargs):
        raise AssertionError("preview must not write, network, or connect a database")

    monkeypatch.setattr(Path, "write_bytes", unexpected)
    monkeypatch.setattr(Path, "write_text", unexpected)
    monkeypatch.setattr(requests.sessions.Session, "request", unexpected)
    monkeypatch.setattr(sqlite3, "connect", unexpected)
    lower = datetime.now(timezone.utc)
    preview = preview_resume_file("resume.PDF")
    upper = datetime.now(timezone.utc)
    assert preview.source_file == str(path.resolve())
    assert preview.source_sha256 == before
    assert preview.document.provenance.source_sha256 == before
    assert lower <= preview.document.provenance.processed_at <= upper
    assert path.read_bytes() == raw


def test_preview_uses_one_bounded_file_read_then_hashes_same_bytes(tmp_path, monkeypatch):
    path = tmp_path / "resume.pdf"
    raw = make_pdf(["Python project"])
    path.write_bytes(raw)
    original_open = Path.open
    opens, reads = [], []

    class ObservedFile:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            self.handle.__enter__()
            return self
        def __exit__(self, *args):
            return self.handle.__exit__(*args)
        def read(self, size=-1):
            reads.append(size)
            assert 0 < size <= DEFAULT_MAX_BYTES + 1
            return self.handle.read(size)
        def __getattr__(self, name):
            return getattr(self.handle, name)

    def observed_open(target, *args, **kwargs):
        opens.append(target)
        return ObservedFile(original_open(target, *args, **kwargs))

    monkeypatch.setattr(Path, "open", observed_open)
    preview = preview_resume_file(path)
    assert opens == [path.resolve()] and len(reads) == 1
    assert preview.source_sha256 == hashlib.sha256(raw).hexdigest()


def test_preview_does_not_read_entire_oversized_input(tmp_path, monkeypatch):
    path = tmp_path / "large.pdf"
    path.write_bytes(b"%PDF-" + b" " * (DEFAULT_MAX_BYTES + 1))
    monkeypatch.setattr(Path, "read_bytes", lambda *args: pytest.fail("unbounded read_bytes"))
    with pytest.raises(ResumePDFError) as error:
        preview_resume_file(path)
    assert error.value.code == "size_limit"


def test_preview_cli_prints_text_blocks_and_metadata(tmp_path, capsys):
    path = tmp_path / "简历.pdf"
    raw = make_pdf(["姓名：李同学", "Python / FastAPI 项目"])
    path.write_bytes(raw)
    assert main([str(path)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert "简历 PDF 预览：待人工核对" in output.out
    assert "p001-b001" in output.out and "Python / FastAPI 项目" in output.out
    assert "pymupdf-1.26.7" in output.out
    assert hashlib.sha256(raw).hexdigest() in output.out
    assert "未进入 ResumeProfile" in output.out


@pytest.mark.parametrize(("kind", "code"), [
    ("missing", "file_error"), ("bad", "invalid_pdf"),
    ("empty", "empty_pdf"), ("extension", "unsupported_extension"),
])
def test_cli_errors_have_status_two_stable_code_and_no_partial_record(tmp_path, capsys, kind, code):
    path = tmp_path / "resume.pdf"
    if kind == "bad":
        path.write_bytes(b"bad")
    elif kind == "empty":
        path.write_bytes(b"")
    elif kind == "extension":
        path = tmp_path / "resume.txt"
        path.write_text("not pdf")
    assert main([str(path)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "简历预览失败" in output.err and code in output.err
    assert "Traceback" not in output.err


def test_module_command_works_outside_project_directory(tmp_path):
    path = tmp_path / "resume.pdf"
    path.write_bytes(make_pdf(["姓名：李同学", "Python / FastAPI 项目"]))
    result = subprocess.run(
        [sys.executable, "-B", "-m", "job_radar.resume_preview", str(path)],
        cwd=tmp_path, capture_output=True, text=True,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )
    assert result.returncode == 0, result.stderr
    assert "p001-b001" in result.stdout and "Python / FastAPI 项目" in result.stdout
