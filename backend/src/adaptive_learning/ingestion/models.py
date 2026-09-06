from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ExtractionMethod(StrEnum):
    NATIVE = "native"
    MINERU_PIPELINE = "mineru_pipeline"
    MINERU_VLM = "mineru_vlm"
    VISION_RESCUE = "vision_rescue"
    CONSENSUS = "consensus"


class PageKind(StrEnum):
    DIGITAL = "digital"
    SCANNED = "scanned"
    MIXED = "mixed"


class QualityBand(StrEnum):
    ACCEPTED = "accepted"
    REVIEW = "review"
    RESCUE = "rescue"


class TextBlock(BaseModel):
    block_id: str
    page_number: int = Field(ge=1)
    block_type: str = "text"
    bbox: tuple[float, float, float, float] | None = None
    text: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_method: ExtractionMethod
    metadata: dict[str, Any] = Field(default_factory=dict)


class QualitySignals(BaseModel):
    character_score: float = Field(ge=0, le=1)
    language_score: float = Field(ge=0, le=1)
    layout_score: float = Field(ge=0, le=1)
    coverage_score: float = Field(ge=0, le=1)
    agreement_score: float = Field(ge=0, le=1)
    overall_score: float = Field(ge=0, le=1)
    suspicious_fragments: list[str] = Field(default_factory=list)


class PageExtraction(BaseModel):
    page_number: int
    page_kind: PageKind
    method: ExtractionMethod
    raw_text: str
    cleaned_text: str = ""
    blocks: list[TextBlock] = Field(default_factory=list)
    quality: QualitySignals
    band: QualityBand
    edit_ledger: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_fragments: list[str] = Field(default_factory=list)


class SourceQuote(BaseModel):
    page_number: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=500)


class ChapterDraft(BaseModel):
    chapter_id: str
    order: int
    title: str
    start_page: int
    end_page: int
    summary: str
    knowledge_points: list[str]
    source_block_ids: list[str]
    evidence: list[SourceQuote] = Field(default_factory=list)
    knowledge_point_evidence: dict[str, list[SourceQuote]] = Field(default_factory=dict)


class BookStructure(BaseModel):
    title: str
    summary: str
    chapters: list[ChapterDraft]
    source_page_count: int = Field(ge=1)
    used_fallback_chapter: bool = False


class BookReconstruction(BaseModel):
    book_id: str
    title: str
    page_count: int
    full_text: str
    chapters: list[ChapterDraft]
    pages: list[PageExtraction]
    quality_score: float
    needs_human_review: bool
    book_summary: str = ""
