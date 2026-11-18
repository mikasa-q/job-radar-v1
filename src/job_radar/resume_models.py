"""PDF extraction evidence, before any semantic ResumeProfile inference.

Block text is the evidence source. Page text is a reading aid produced by the
engine's spatial sorting and may contain different whitespace from the blocks.
"""
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ResumeBlock(BaseModel):
    """One text block returned by the PDF engine, with page-local coordinates."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    block_id: str = Field(pattern=r"^p[0-9]{3,}-b[0-9]{3,}$")
    text: str = Field(min_length=1)
    bbox: tuple[float, float, float, float]

    @field_validator("text")
    @classmethod
    def require_content(cls, value: str) -> str:
        # Validate emptiness without rewriting the quote source: leading spaces
        # and the engine's final newline are meaningful evidence bytes.
        if not value.strip():
            raise ValueError("简历文本块不能为空。")
        return value

    @field_validator("bbox")
    @classmethod
    def valid_bbox(cls, value: tuple[float, float, float, float]):
        # Real PDF glyph bounds can extend slightly outside the page, so a
        # negative edge is not itself an error. Non-finite floats are forbidden.
        if value[2] < value[0] or value[3] < value[1]:
            raise ValueError("文本块坐标顺序无效。")
        return value


class ResumePage(BaseModel):
    """Page dimensions/bboxes share PyMuPDF's unrotated coordinate space."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    page_number: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: Literal[0, 90, 180, 270] = 0
    text: str = ""
    blocks: list[ResumeBlock] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_block_identity_and_page(self):
        for index, block in enumerate(self.blocks, start=1):
            expected = f"p{self.page_number:03d}-b{index:03d}"
            if block.block_id != expected:
                raise ValueError("文本块编号必须与页码、顺序一致。")
            x0, y0, x1, y1 = block.bbox
            if x1 < 0 or y1 < 0 or x0 > self.width or y0 > self.height:
                raise ValueError("文本块坐标与页面范围没有交集。")
        return self


class ResumeProvenance(BaseModel):
    """Timestamp is supplied by the file layer, never invented by the adapter."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_version: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    processed_at: datetime | None = None

    @field_validator("processed_at")
    @classmethod
    def require_aware_utc(cls, value: datetime | None):
        if value is not None:
            if value.utcoffset() is None:
                raise ValueError("处理时间必须带时区。")
            return value.astimezone(timezone.utc)
        return value


class ResumeDocument(BaseModel):
    """Structured PDF evidence, before ResumeProfile semantic extraction."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["resume-document-v1"] = "resume-document-v1"
    provenance: ResumeProvenance
    pages: list[ResumePage] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("pages")
    @classmethod
    def page_numbers_are_contiguous(cls, value: list[ResumePage]) -> list[ResumePage]:
        expected = list(range(1, len(value) + 1))
        actual = [page.page_number for page in value]
        if actual != expected:
            raise ValueError("简历页码必须从 1 连续编号。")
        identities = [block.block_id for page in value for block in page.blocks]
        if len(identities) != len(set(identities)):
            raise ValueError("文本块编号必须在文档内唯一。")
        return value

