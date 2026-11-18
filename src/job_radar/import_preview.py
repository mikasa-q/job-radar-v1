"""Read and preview exactly one local job file; never persist imported jobs."""
import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .import_models import ImportedJob
from .importer import ImportParseError, parse_boss_detail_html
from .txt_importer import parse_job_txt


class ImportProvenance(BaseModel):
    """The path and hash identify the bytes read in this preview, not a JD date."""

    model_config = ConfigDict(extra="forbid")

    source_file: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_format: Literal["html", "txt"]
    imported_at: datetime  # UTC preview time; not published/saved/confirmed time.


class ImportPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: ImportedJob
    provenance: ImportProvenance
    review_status: Literal["unconfirmed"] = "unconfirmed"


def preview_file(
    path: str | Path, *, source_url: str | None = None,
) -> ImportPreview:
    """Load one UTF-8 file once; hash original bytes before decoding/normalizing.

    File errors propagate to Python callers. Parse errors use ImportParseError.
    The command below renders them without a traceback and returns status 2.
    """
    source_file = Path(path).expanduser().resolve()
    suffix = source_file.suffix.lower()
    if suffix not in {".html", ".htm", ".txt"}:
        raise ImportParseError("预览只接受一个 .html、.htm 或 .txt 文件。")
    if suffix == ".txt" and source_url is not None:
        raise ImportParseError("TXT 的岗位链接须填写在模板头部，不接受 --source-url。")

    raw = source_file.read_bytes()
    # Hash the same bytes we parse; do not reread or hash normalized JD text.
    digest = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8-sig")
    if suffix == ".txt":
        job = parse_job_txt(text)
        input_format = "txt"
    else:
        job = parse_boss_detail_html(text, source_url=source_url)
        input_format = "html"
    return ImportPreview(
        job=job,
        provenance=ImportProvenance(
            source_file=str(source_file), source_sha256=digest,
            input_format=input_format, imported_at=datetime.now(timezone.utc),
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="单个岗位 HTML/TXT 本地预览，不入库")
    parser.add_argument("path", help="UTF-8 .html/.htm 或 jobradar-txt-v1 .txt 文件")
    parser.add_argument("--source-url", help="仅 HTML：缺少 canonical 时提供原岗位链接")
    args = parser.parse_args(argv)
    try:
        preview = preview_file(args.path, source_url=args.source_url)
    except (OSError, UnicodeError, ImportParseError) as exc:
        print(f"预览失败：{exc}", file=sys.stderr)
        return 2

    provenance = preview.provenance
    print("岗位预览：待人工核对（未入库）")
    print(f"来源文件: {provenance.source_file}")
    print(f"SHA-256: {provenance.source_sha256}")
    print(f"格式: {provenance.input_format}")
    print(f"预览时间（UTC）: {provenance.imported_at.isoformat()}")
    for name, value in preview.job.model_dump(exclude={"description"}).items():
        if isinstance(value, list):
            rendered = "、".join(value) if value else "[]"
        else:
            rendered = value if value else "未知"
        print(f"{name}: {rendered}")
    print("\nJD 正文：")
    print(preview.job.description)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

