from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from app.schemas.books import Asset, LayoutRegion, PageResult, QualityWarning, ScanResult, TextBlock


class ParserUnavailable(RuntimeError):
    def __init__(self, parser_name: str, reason: str) -> None:
        self.parser_name = parser_name
        self.reason = reason
        super().__init__(f"{parser_name} unavailable: {reason}")


@dataclass(frozen=True)
class ParseRequest:
    book_id: str
    file_path: Path
    artifact_path: Path
    scan: ScanResult
    preferred_parser: str = "auto"
    # Stage 2 reserves the MinerU generation before the worker is queued.  The
    # worker must carry that exact generation through mapping, fallbacks and
    # the final artifact commit so a late worker cannot overwrite a newer run.
    expected_generation: int | None = None
    cloudpath_job_id: str | None = None
    progress_callback: Callable[[str, int, str], None] | None = None


@dataclass(frozen=True)
class ParsedPage:
    page_number: int
    text: str
    needs_ocr: bool
    parser: str
    text_blocks: list[TextBlock] = field(default_factory=list)
    layout_regions: list[LayoutRegion] = field(default_factory=list)
    quality_warnings: list[QualityWarning] = field(default_factory=list)
    ocr_provider: str | None = None
    ocr_confidence: float | None = None
    quality_score: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedDocument:
    book_id: str
    parser_name: str
    pages: list[ParsedPage]
    scan: ScanResult
    metadata: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    assets: list[Asset] = field(default_factory=list)


class DocumentParser(Protocol):
    name: str

    def parse(self, request: ParseRequest) -> ParsedDocument:
        ...


def parsed_page_from_page_result(page: PageResult, parser_name: str) -> ParsedPage:
    confidence = page.ocr_confidence
    explicit_quality = getattr(page, "quality_score", None)
    quality_score = explicit_quality if explicit_quality is not None else (None if confidence is None else max(0.0, min(1.0, confidence)))
    if quality_score is None and page.text.strip():
        quality_score = 0.85
    return ParsedPage(
        page_number=page.page,
        text=page.text,
        needs_ocr=page.needs_ocr,
        parser=getattr(page, "parser", None) or parser_name,
        text_blocks=page.blocks,
        layout_regions=page.layout_regions,
        quality_warnings=page.quality_warnings,
        ocr_provider=page.ocr_provider,
        ocr_confidence=page.ocr_confidence,
        quality_score=quality_score,
        metadata=page.metadata,
    )
