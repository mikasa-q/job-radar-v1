"""Read one local PDF and print extracted evidence; never persist a resume."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .resume_models import ResumeDocument
from .resume_pdf import (
    DEFAULT_MAX_BYTES, DEFAULT_MAX_PAGES, ResumePDFError,
    _validate_limits, extract_resume_pdf,
)


class ResumePreview(BaseModel):
    """A local-only file wrapper; this source path is not a matching feature."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_file: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document: ResumeDocument

    @model_validator(mode="after")
    def hashes_match(self):
        if self.source_sha256 != self.document.provenance.source_sha256:
            raise ValueError("预览来源哈希与提取文档不一致。")
        if not Path(self.source_file).is_absolute():
            raise ValueError("本地来源路径必须为绝对路径。")
        return self


def preview_resume_file(
    path: str | Path, *, max_bytes: int = DEFAULT_MAX_BYTES,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> ResumePreview:
    """Read at most max_bytes+1 once; adapter hashes exactly these source bytes.

    A bounded stream read prevents allocating an arbitrarily large file before
    the byte adapter can enforce its size limit. No source file is rewritten.
    """
    _validate_limits(max_bytes, max_pages)
    source_file = Path(path).expanduser().resolve()
    if source_file.suffix.lower() != ".pdf":
        raise ResumePDFError("预览只接受 .pdf 文件。", code="unsupported_extension")
    with source_file.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    document = extract_resume_pdf(
        raw, processed_at=datetime.now(timezone.utc),
        max_bytes=max_bytes, max_pages=max_pages,
    )
    return ResumePreview(
        source_file=str(source_file), source_sha256=document.provenance.source_sha256,
        document=document,
    )


def _print_document(preview: ResumePreview) -> None:
    document = preview.document
    print("简历 PDF 预览：待人工核对（未进入 ResumeProfile）")
    print(f"来源文件: {preview.source_file}")
    print(f"SHA-256: {preview.source_sha256}")
    print(f"页数: {len(document.pages)}")
    print(f"解析器: {document.provenance.parser_version}")
    print(f"提取器: {document.provenance.extractor_version}")
    processed = document.provenance.processed_at
    print(f"处理时间（UTC）: {processed.isoformat() if processed else '未知'}")
    print(f"文档警告: {'、'.join(document.warnings) if document.warnings else '[]'}")
    print("阅读顺序需人工核对；证据引用以文本块原文为准，不代表已完成画像解析。")
    for page in document.pages:
        print(f"\n--- 第 {page.page_number} 页 "
              f"({page.width:.2f} × {page.height:.2f}; rotation={page.rotation}) ---")
        print(f"页面警告: {'、'.join(page.warnings) if page.warnings else '[]'}")
        print("文本：")
        print(page.text)
        print("文本块（未旋转页面坐标）：")
        for block in page.blocks:
            x0, y0, x1, y1 = block.bbox
            print(f"  {block.block_id} [{x0:.2f},{y0:.2f},{x1:.2f},{y1:.2f}] {block.text!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="简历 PDF 本地提取预览，不入库")
    parser.add_argument("path", help="一个本地 .pdf 文件")
    args = parser.parse_args(argv)
    try:
        _print_document(preview_resume_file(args.path))
    except ResumePDFError as exc:
        print(f"简历预览失败 [{exc.code}]：{exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"简历预览失败 [file_error]：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
