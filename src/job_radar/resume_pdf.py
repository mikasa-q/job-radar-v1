"""Offline bytes-to-evidence adapter for the pinned PyMuPDF engine.

API references (verified on 1.26.7):
https://pymupdf.readthedocs.io/en/latest/recipes-text.html
https://pymupdf.readthedocs.io/en/latest/page.html#Page.get_text
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Final

import pymupdf

from .resume_models import ResumeBlock, ResumeDocument, ResumePage, ResumeProvenance


PARSER_VERSION: Final[str] = "resume-pdf-v1"
EXTRACTOR_VERSION: Final[str] = f"pymupdf-{pymupdf.version[0]}"
DEFAULT_MAX_BYTES: Final[int] = 10 * 1024 * 1024
DEFAULT_MAX_PAGES: Final[int] = 10
LOW_TEXT_CHARS: Final[int] = 40


class ResumePDFError(ValueError):
    """An explicit extraction failure; code is stable, detail is human-readable."""

    def __init__(self, message: str, *, code: str = "invalid_pdf"):
        super().__init__(message)
        self.code = code


def _validate_limits(max_bytes: int, max_pages: int) -> None:
    # bool is an int subclass but not a meaningful resource limit.
    if (type(max_bytes) is not int or type(max_pages) is not int
            or max_bytes <= 0 or max_pages <= 0):
        raise ResumePDFError("资源上限必须为正整数。", code="invalid_limit")


def _normalise_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _page_warnings(text: str, blocks: list[ResumeBlock]) -> list[str]:
    # These are observable quality hints, not claims that OCR/layout repair ran.
    warnings: list[str] = []
    content = "".join(block.text for block in blocks)
    characters = sum(not char.isspace() for char in content)
    if not characters:
        return ["no_extractable_text"]
    if characters < LOW_TEXT_CHARS:
        warnings.append("low_text_content")
    if "\ufffd" in text or "\ufffd" in content:
        warnings.append("replacement_characters")
    # Separate columns / table cells can overlap vertically without overlapping
    # horizontally. Flag geometry conservatively; do not reorder blocks or
    # promise a general multi-column detector.
    # This hint is intentionally bounded: it is not worth an unbounded O(n^2)
    # layout pass over an unusual PDF just to suggest human review.
    geometry_sample = blocks[:200]
    for index, left in enumerate(geometry_sample):
        for right in geometry_sample[index + 1:]:
            x0, y0, x1, y1 = left.bbox
            rx0, ry0, rx1, ry1 = right.bbox
            vertically_overlapping = min(y1, ry1) > max(y0, ry0)
            horizontally_separate = x1 <= rx0 or rx1 <= x0
            if vertically_overlapping and horizontally_separate:
                warnings.append("possible_layout_issue")
                return warnings
    return warnings


def extract_resume_pdf(
    pdf_bytes: bytes, *, source_sha256: str | None = None,
    processed_at: datetime | None = None, max_bytes: int = DEFAULT_MAX_BYTES,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> ResumeDocument:
    """Convert PDF bytes into text/coordinate evidence, with no file or clock I/O.

    The file layer supplies processed_at, if needed. The byte hash is verified
    here so callers cannot mislabel evidence. sort=True assists reading order,
    but page.text is not the quote source: engine block text is retained without
    stripping spaces or terminal newlines. Block IDs are stable only within this
    parser/extractor version and the same source bytes.
    """
    _validate_limits(max_bytes, max_pages)
    if type(pdf_bytes) is not bytes:
        raise ResumePDFError("PDF 输入必须是 bytes。", code="invalid_input")
    if not pdf_bytes:
        raise ResumePDFError("PDF 文件为空。", code="empty_pdf")
    if len(pdf_bytes) > max_bytes:
        raise ResumePDFError(f"PDF 超过本地大小上限（{max_bytes} bytes）。", code="size_limit")
    if processed_at is not None and (
        not isinstance(processed_at, datetime) or processed_at.utcoffset() is None
    ):
        raise ResumePDFError("processed_at 必须是带时区的 datetime。", code="invalid_timestamp")
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    if source_sha256 is not None and source_sha256 != digest:
        raise ResumePDFError("source_sha256 与 PDF 字节不一致。", code="hash_mismatch")
    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ResumePDFError("PDF 内容损坏或不是有效 PDF。", code="invalid_pdf") from exc
    try:
        if not document.is_pdf:
            raise ResumePDFError("输入内容不是 PDF。", code="invalid_pdf")
        if document.needs_pass:
            raise ResumePDFError("PDF 需要密码，暂不提取。", code="password_required")
        if document.page_count > max_pages:
            raise ResumePDFError(f"PDF 页数超过本地上限（{max_pages} 页）。", code="page_limit")
        if document.page_count == 0:
            raise ResumePDFError("PDF 没有页面。", code="no_pages")

        pages: list[ResumePage] = []
        for page_number, page in enumerate(document, start=1):
            # Both outputs use the same text page / flags. Image payloads are
            # excluded: 1A extracts text only and never invokes OCR.
            textpage = page.get_textpage(flags=pymupdf.TEXTFLAGS_TEXT)
            text = _normalise_text(page.get_text("text", sort=True, textpage=textpage))
            raw_blocks = page.get_text("blocks", sort=True, textpage=textpage)
            blocks: list[ResumeBlock] = []
            for raw in raw_blocks:
                if len(raw) < 7 or raw[6] != 0:
                    continue
                block_text = _normalise_text(raw[4])
                if not block_text.strip():
                    continue
                try:
                    blocks.append(ResumeBlock(
                        block_id=f"p{page_number:03d}-b{len(blocks) + 1:03d}",
                        text=block_text, bbox=tuple(float(value) for value in raw[:4]),
                    ))
                except (TypeError, ValueError) as exc:
                    raise ResumePDFError(f"第 {page_number} 页文本块坐标无效。",
                                         code="invalid_text_block") from exc
            # get_text bboxes are unrotated even if page.rect is rotated. Keep
            # size and block coordinates in the same coordinate system.
            rect = page.rect * page.derotation_matrix
            pages.append(ResumePage(
                page_number=page_number, width=float(rect.width), height=float(rect.height),
                rotation=page.rotation, text=text, blocks=blocks,
                warnings=_page_warnings(text, blocks),
            ))
        if not any(page.blocks for page in pages):
            raise ResumePDFError(
                "PDF 没有可提取文字；请检查空白/图像页面，扫描版另需 OCR。",
                code="no_extractable_text",
            )
        warnings: list[str] = []
        # MuPDF may repair cross-reference damage while opening a PDF. A usable
        # result is still provisional: do not silently label repair as fidelity.
        if document.is_repaired:
            warnings.append("pdf_repaired")
        if any("low_text_content" in page.warnings for page in pages):
            warnings.append("low_text_content")
        if any("no_extractable_text" in page.warnings for page in pages):
            warnings.append("some_pages_no_extractable_text")
        for warning in ("replacement_characters", "possible_layout_issue"):
            if any(warning in page.warnings for page in pages):
                warnings.append(warning)
        return ResumeDocument(
            provenance=ResumeProvenance(
                source_sha256=digest, parser_version=PARSER_VERSION,
                extractor_version=EXTRACTOR_VERSION, processed_at=processed_at,
            ),
            pages=pages, warnings=warnings,
        )
    except ResumePDFError:
        raise
    except Exception as exc:
        raise ResumePDFError("PDF 文本提取失败，请检查文件内容。", code="extract_failed") from exc
    finally:
        document.close()
