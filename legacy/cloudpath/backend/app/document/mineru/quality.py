from __future__ import annotations

from collections.abc import Iterable
from html import unescape
import re
import unicodedata
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.books import PageResult, TextBlock


QUALITY_PROTOCOL_VERSION: Final = "quality-v1"
PAGE_QUALITY_SCORE_MIN: Final = 0.60
DOCUMENT_QUALITY_SCORE_MIN: Final = 0.75
USABLE_SEMANTIC_CHARACTERS_MIN: Final = 20
INVALID_CHARACTER_RATIO_MAX: Final = 0.01

QUALITY_WEIGHTS: Final[dict[str, float]] = {
    "valid_character_score": 0.25,
    "content_coverage_score": 0.30,
    "ocr_confidence_score": 0.15,
    "mapping_completeness_score": 0.20,
    "deduplication_score": 0.10,
}

_VISUAL_TYPES = {"figure", "image", "chart"}
_ATOMIC_SEMANTIC_TYPES = {"table", "formula", "equation"}
_OCR_PENDING_TYPES = {"ocr_pending"}
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


class PageMappingStats(BaseModel):
    """Mapper counters used by the frozen quality-v1 calculation.

    ``mapped_item_count`` counts recognized source items before block
    deduplication. This keeps mapping completeness and duplicate suppression as
    independent quality dimensions.
    """

    model_config = ConfigDict(frozen=True)

    page: int = Field(ge=1)
    source_item_count: int = Field(default=0, ge=0)
    mapped_item_count: int = Field(default=0, ge=0)
    duplicate_count: int = Field(default=0, ge=0)
    unknown_item_count: int = Field(default=0, ge=0)
    malformed_item_count: int = Field(default=0, ge=0)
    referenced_asset_count: int = Field(default=0, ge=0)
    mapped_asset_count: int = Field(default=0, ge=0)
    referenced_bbox_count: int = Field(default=0, ge=0)
    mapped_bbox_count: int = Field(default=0, ge=0)

    @property
    def mapping_completeness(self) -> float:
        denominator = (
            self.source_item_count
            + self.referenced_asset_count
            + self.referenced_bbox_count
        )
        if denominator <= 0:
            return 0.0
        numerator = min(self.mapped_item_count, self.source_item_count) + min(
            self.mapped_asset_count,
            self.referenced_asset_count,
        ) + min(self.mapped_bbox_count, self.referenced_bbox_count)
        return _clamp(numerator / denominator)

    @property
    def deduplication_score(self) -> float:
        recognized = self.mapped_item_count
        if recognized <= 0:
            return 1.0 if self.source_item_count == 0 else 0.0
        return _clamp(1.0 - (self.duplicate_count / recognized))


class PageQualityResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    protocol_version: str = QUALITY_PROTOCOL_VERSION
    page: int
    quality_score: float
    passed: bool
    usable_semantic_content: bool
    semantic_character_count: int
    invalid_character_count: int
    invalid_character_ratio: float
    valid_character_score: float
    content_coverage_score: float
    ocr_confidence_score: float
    mapping_completeness_score: float
    deduplication_score: float
    has_ocr_pending: bool = False
    reasons: list[str] = Field(default_factory=list)


class DocumentQualityResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    protocol_version: str = QUALITY_PROTOCOL_VERSION
    quality_score: float
    passed: bool
    expected_page_count: int
    mapped_page_count: int
    source_page_coverage: float
    usable_semantic_page_coverage: float
    invalid_character_ratio: float
    ocr_pending_ratio: float
    missing_pages: list[int] = Field(default_factory=list)
    duplicate_pages: list[int] = Field(default_factory=list)
    problem_pages: list[int] = Field(default_factory=list)
    pages: list[PageQualityResult] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _visible_text(block: TextBlock) -> str:
    value = block.text or ""
    if block.content_format in {"html", "xhtml"} or "<table" in value.lower():
        value = unescape(_TAG_RE.sub(" ", value))
    return _SPACE_RE.sub(" ", value).strip()


def _is_invalid_character(character: str) -> bool:
    if character == "\ufffd":
        return True
    if character in {"\n", "\r", "\t"}:
        return False
    return unicodedata.category(character) in {"Cc", "Cs"}


def _page_ocr_confidence(page: PageResult, semantic: bool, visual_without_semantics: bool) -> float:
    provider = (page.ocr_provider or "").strip().lower()
    parser = (page.parser or "").strip().lower()
    block_providers = {(block.source_parser or "").strip().lower() for block in page.blocks}
    if provider == "mock" or parser == "mock" or "mock" in block_providers:
        return 0.0

    if page.ocr_confidence is not None:
        return _clamp(page.ocr_confidence)
    block_confidences = [
        float(block.confidence)
        for block in page.blocks
        if block.confidence is not None
    ]
    if block_confidences:
        return _clamp(sum(block_confidences) / len(block_confidences))

    if provider or parser in {"ocr", "paddleocr"} or block_providers.intersection({"ocr", "paddleocr"}):
        return 0.0
    if visual_without_semantics:
        return 0.0
    # quality-v1 freezes native structured text at confidence 1.0 when the
    # parser does not expose an OCR confidence value.
    return 1.0 if semantic else 0.0


def evaluate_page_quality(
    page: PageResult,
    stats: PageMappingStats | None = None,
) -> PageQualityResult:
    resolved_stats = stats or PageMappingStats(
        page=page.page,
        source_item_count=len(page.blocks),
        mapped_item_count=len(page.blocks),
        referenced_asset_count=sum(len(block.asset_ids) for block in page.blocks),
        mapped_asset_count=sum(len(block.asset_ids) for block in page.blocks),
    )

    visible_blocks = [(block, _visible_text(block)) for block in page.blocks]
    visible_texts = [text for _, text in visible_blocks if text]
    combined_text = "\n".join(visible_texts)
    semantic_characters = [character for character in combined_text if not character.isspace()]
    invalid_count = sum(_is_invalid_character(character) for character in semantic_characters)
    invalid_ratio = invalid_count / len(semantic_characters) if semantic_characters else 0.0
    valid_character_score = _clamp(1.0 - invalid_ratio) if semantic_characters else 0.0

    visual_semantic = any(block.type.lower() in _VISUAL_TYPES and bool(text) for block, text in visible_blocks)
    atomic_semantic = any(
        block.type.lower() in _ATOMIC_SEMANTIC_TYPES and bool(text)
        for block, text in visible_blocks
    )
    regular_coverage = _clamp(len(semantic_characters) / USABLE_SEMANTIC_CHARACTERS_MIN)
    content_coverage_score = 1.0 if visual_semantic or atomic_semantic else regular_coverage
    usable_semantic = bool(
        visual_semantic
        or atomic_semantic
        or len(semantic_characters) >= USABLE_SEMANTIC_CHARACTERS_MIN
    )

    pending_type = any(block.type.lower() in _OCR_PENDING_TYPES for block in page.blocks)
    pending_text = any(text.casefold().startswith("ocr pending") for text in visible_texts)
    mock_provider = (page.ocr_provider or "").strip().lower() == "mock"
    mock_parser = (page.parser or "").strip().lower() == "mock"
    mock_block = any((block.source_parser or "").strip().lower() == "mock" for block in page.blocks)
    has_ocr_pending = pending_type or pending_text or mock_provider or mock_parser or mock_block

    visual_without_semantics = any(block.type.lower() in _VISUAL_TYPES for block in page.blocks) and not visual_semantic
    ocr_confidence_score = _page_ocr_confidence(page, bool(semantic_characters), visual_without_semantics)
    mapping_completeness_score = resolved_stats.mapping_completeness
    deduplication_score = resolved_stats.deduplication_score

    score = (
        QUALITY_WEIGHTS["valid_character_score"] * valid_character_score
        + QUALITY_WEIGHTS["content_coverage_score"] * content_coverage_score
        + QUALITY_WEIGHTS["ocr_confidence_score"] * ocr_confidence_score
        + QUALITY_WEIGHTS["mapping_completeness_score"] * mapping_completeness_score
        + QUALITY_WEIGHTS["deduplication_score"] * deduplication_score
    )
    score = round(_clamp(score), 6)

    reasons: list[str] = []
    if not usable_semantic:
        reasons.append("no_usable_semantic_content")
    if invalid_ratio > INVALID_CHARACTER_RATIO_MAX:
        reasons.append("invalid_character_ratio_exceeded")
    if mapping_completeness_score < 1.0:
        reasons.append("mapping_incomplete")
    if resolved_stats.duplicate_count:
        reasons.append("duplicate_blocks_removed")
    if has_ocr_pending:
        reasons.append("ocr_pending_or_mock")
    if page.needs_ocr:
        reasons.append("page_requires_ocr")
    if score < PAGE_QUALITY_SCORE_MIN:
        reasons.append("page_quality_below_threshold")

    passed = bool(
        score >= PAGE_QUALITY_SCORE_MIN
        and usable_semantic
        and invalid_ratio <= INVALID_CHARACTER_RATIO_MAX
        and not has_ocr_pending
        and not page.needs_ocr
    )
    return PageQualityResult(
        page=page.page,
        quality_score=score,
        passed=passed,
        usable_semantic_content=usable_semantic,
        semantic_character_count=len(semantic_characters),
        invalid_character_count=invalid_count,
        invalid_character_ratio=round(invalid_ratio, 6),
        valid_character_score=round(valid_character_score, 6),
        content_coverage_score=round(content_coverage_score, 6),
        ocr_confidence_score=round(ocr_confidence_score, 6),
        mapping_completeness_score=round(mapping_completeness_score, 6),
        deduplication_score=round(deduplication_score, 6),
        has_ocr_pending=has_ocr_pending,
        reasons=reasons,
    )


def evaluate_document_quality(
    pages: Iterable[PageResult],
    *,
    expected_page_count: int,
    mapping_stats: Iterable[PageMappingStats] | None = None,
) -> DocumentQualityResult:
    page_list = list(pages)
    stats_by_page = {item.page: item for item in (mapping_stats or [])}
    first_by_page: dict[int, PageResult] = {}
    duplicate_pages: list[int] = []
    for page in page_list:
        if page.page in first_by_page:
            duplicate_pages.append(page.page)
        else:
            first_by_page[page.page] = page

    expected = max(0, int(expected_page_count))
    expected_pages = set(range(1, expected + 1))
    present_expected = expected_pages.intersection(first_by_page)
    explicitly_missing = {
        page_number
        for page_number in present_expected
        if bool(first_by_page[page_number].metadata.get("source_page_missing"))
    }
    mapped_expected = sorted(present_expected)
    missing_pages = sorted(expected_pages.difference(first_by_page).union(explicitly_missing))
    page_results = [
        evaluate_page_quality(first_by_page[page_number], stats_by_page.get(page_number))
        for page_number in mapped_expected
    ]

    quality_sum = sum(item.quality_score for item in page_results)
    document_score = quality_sum / expected if expected else 0.0
    source_coverage = (len(mapped_expected) - len(explicitly_missing)) / expected if expected else 0.0
    usable_coverage = (
        sum(item.usable_semantic_content for item in page_results) / expected
        if expected
        else 0.0
    )
    semantic_character_count = sum(item.semantic_character_count for item in page_results)
    invalid_character_count = sum(item.invalid_character_count for item in page_results)
    invalid_ratio = invalid_character_count / semantic_character_count if semantic_character_count else 0.0
    ocr_pending_ratio = sum(item.has_ocr_pending for item in page_results) / expected if expected else 0.0
    problem_pages = sorted(
        set(missing_pages).union(item.page for item in page_results if not item.passed)
    )

    reasons: list[str] = []
    if expected <= 0:
        reasons.append("invalid_expected_page_count")
    if missing_pages:
        reasons.append("source_pages_missing")
    if duplicate_pages:
        reasons.append("duplicate_page_numbers")
    if source_coverage < 1.0:
        reasons.append("source_page_coverage_below_threshold")
    if usable_coverage < 1.0:
        reasons.append("usable_semantic_page_coverage_below_threshold")
    if invalid_ratio > INVALID_CHARACTER_RATIO_MAX:
        reasons.append("invalid_character_ratio_exceeded")
    if ocr_pending_ratio > 0.0:
        reasons.append("ocr_pending_present")
    if document_score < DOCUMENT_QUALITY_SCORE_MIN:
        reasons.append("document_quality_below_threshold")

    passed = bool(
        expected > 0
        and not missing_pages
        and not duplicate_pages
        and source_coverage == 1.0
        and usable_coverage == 1.0
        and invalid_ratio <= INVALID_CHARACTER_RATIO_MAX
        and ocr_pending_ratio == 0.0
        and document_score >= DOCUMENT_QUALITY_SCORE_MIN
        and all(item.passed for item in page_results)
    )
    return DocumentQualityResult(
        quality_score=round(_clamp(document_score), 6),
        passed=passed,
        expected_page_count=expected,
        mapped_page_count=len(mapped_expected) - len(explicitly_missing),
        source_page_coverage=round(source_coverage, 6),
        usable_semantic_page_coverage=round(usable_coverage, 6),
        invalid_character_ratio=round(invalid_ratio, 6),
        ocr_pending_ratio=round(ocr_pending_ratio, 6),
        missing_pages=missing_pages,
        duplicate_pages=sorted(set(duplicate_pages)),
        problem_pages=problem_pages,
        pages=page_results,
        reasons=reasons,
    )


__all__ = [
    "DOCUMENT_QUALITY_SCORE_MIN",
    "DocumentQualityResult",
    "INVALID_CHARACTER_RATIO_MAX",
    "PAGE_QUALITY_SCORE_MIN",
    "PageMappingStats",
    "PageQualityResult",
    "QUALITY_PROTOCOL_VERSION",
    "QUALITY_WEIGHTS",
    "USABLE_SEMANTIC_CHARACTERS_MIN",
    "evaluate_document_quality",
    "evaluate_page_quality",
]
