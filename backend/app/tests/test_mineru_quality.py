from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.document.mineru.quality import (
    DOCUMENT_QUALITY_SCORE_MIN,
    INVALID_CHARACTER_RATIO_MAX,
    PAGE_QUALITY_SCORE_MIN,
    QUALITY_PROTOCOL_VERSION,
    QUALITY_WEIGHTS,
    USABLE_SEMANTIC_CHARACTERS_MIN,
    PageMappingStats,
    evaluate_document_quality,
    evaluate_page_quality,
)
from app.schemas.books import PageResult, TextBlock


ROOT = Path(__file__).resolve().parents[3]


def _page(
    text: str,
    *,
    block_type: str = "paragraph",
    ocr_provider: str | None = None,
    ocr_confidence: float | None = None,
    parser: str = "mineru",
    needs_ocr: bool = False,
    asset_ids: list[str] | None = None,
) -> PageResult:
    return PageResult(
        page=1,
        pdf_page_index=0,
        text=text,
        needs_ocr=needs_ocr,
        blocks=[
            TextBlock(
                block_id="block-1",
                page=1,
                type=block_type,
                text=text,
                bbox=[0, 0, 100, 100],
                source_parser=parser,
                asset_ids=asset_ids or [],
            )
        ],
        ocr_provider=ocr_provider,
        ocr_confidence=ocr_confidence,
        parser=parser,
    )


def _full_stats(*, page: int = 1) -> PageMappingStats:
    return PageMappingStats(
        page=page,
        source_item_count=1,
        mapped_item_count=1,
        referenced_bbox_count=1,
        mapped_bbox_count=1,
    )


def test_runtime_quality_protocol_matches_stage0_frozen_contract() -> None:
    frozen = json.loads((ROOT / "quality" / "stage0" / "acceptance_thresholds.json").read_text(encoding="utf-8"))
    parser = frozen["parser_quality"]
    formula = frozen["quality_score_formula"]

    assert QUALITY_PROTOCOL_VERSION == formula["version"]
    assert QUALITY_WEIGHTS == {
        "valid_character_score": formula["valid_character_score_weight"],
        "content_coverage_score": formula["content_coverage_score_weight"],
        "ocr_confidence_score": formula["ocr_confidence_score_weight"],
        "mapping_completeness_score": formula["mapping_completeness_score_weight"],
        "deduplication_score": formula["deduplication_score_weight"],
    }
    assert sum(QUALITY_WEIGHTS.values()) == pytest.approx(1.0)
    assert PAGE_QUALITY_SCORE_MIN == parser["page_quality_score_min"]
    assert DOCUMENT_QUALITY_SCORE_MIN == parser["document_quality_score_min"]
    assert USABLE_SEMANTIC_CHARACTERS_MIN == parser["usable_semantic_characters_min"]
    assert INVALID_CHARACTER_RATIO_MAX == parser["invalid_character_ratio_max"]


def test_full_native_page_scores_one() -> None:
    result = evaluate_page_quality(
        _page("A native paragraph with enough semantic characters."),
        _full_stats(),
    )

    assert result.quality_score == 1.0
    assert result.passed is True
    assert result.usable_semantic_content is True
    assert result.reasons == []


def test_quality_v1_uses_exact_weighted_components() -> None:
    page = _page(
        "abcdefghijklmnopqrst",
        ocr_provider="paddleocr",
        ocr_confidence=0.5,
        parser="paddleocr",
    )
    stats = PageMappingStats(
        page=1,
        source_item_count=2,
        mapped_item_count=1,
        referenced_bbox_count=0,
        mapped_bbox_count=0,
    )

    result = evaluate_page_quality(page, stats)

    # 0.25*1 valid + 0.30*1 coverage + 0.15*0.5 OCR
    # + 0.20*0.5 mapping + 0.10*1 dedup = 0.825.
    assert result.quality_score == pytest.approx(0.825)
    assert result.mapping_completeness_score == 0.5
    assert result.ocr_confidence_score == 0.5
    assert result.passed is True
    assert "mapping_incomplete" in result.reasons


@pytest.mark.parametrize(
    ("page", "expected_reason"),
    [
        (
            _page(
                "Mock output contains enough characters but is not real course content.",
                ocr_provider="mock",
                ocr_confidence=0.99,
                parser="mock",
            ),
            "ocr_pending_or_mock",
        ),
        (
            _page(
                "OCR pending for page 1. Install PaddleOCR.",
                block_type="ocr_pending",
            ),
            "ocr_pending_or_mock",
        ),
    ],
)
def test_mock_and_ocr_pending_are_never_accepted(page: PageResult, expected_reason: str) -> None:
    result = evaluate_page_quality(page, _full_stats())

    assert result.passed is False
    assert result.has_ocr_pending is True
    assert expected_reason in result.reasons


def test_asset_path_without_semantic_caption_is_not_usable() -> None:
    page = _page("", block_type="figure", asset_ids=["asset-1"])
    stats = PageMappingStats(
        page=1,
        source_item_count=1,
        mapped_item_count=1,
        referenced_asset_count=1,
        mapped_asset_count=1,
    )

    result = evaluate_page_quality(page, stats)

    assert result.usable_semantic_content is False
    assert result.content_coverage_score == 0.0
    assert result.ocr_confidence_score == 0.0
    assert result.passed is False


def test_short_visual_caption_is_usable_under_frozen_definition() -> None:
    result = evaluate_page_quality(
        _page("图 1", block_type="figure", asset_ids=["asset-1"]),
        PageMappingStats(
            page=1,
            source_item_count=1,
            mapped_item_count=1,
            referenced_asset_count=1,
            mapped_asset_count=1,
        ),
    )

    assert result.semantic_character_count < USABLE_SEMANTIC_CHARACTERS_MIN
    assert result.content_coverage_score == 1.0
    assert result.usable_semantic_content is True
    assert result.passed is True


def test_nonempty_formula_is_atomic_semantic_content() -> None:
    result = evaluate_page_quality(
        _page("x^2", block_type="formula"),
        _full_stats(),
    )

    assert result.semantic_character_count == 3
    assert result.content_coverage_score == 1.0
    assert result.usable_semantic_content is True
    assert result.passed is True


def test_invalid_character_ratio_is_a_hard_failure() -> None:
    result = evaluate_page_quality(
        _page("abcdefghijklmnopqrst\ufffd"),
        _full_stats(),
    )

    assert result.invalid_character_ratio > INVALID_CHARACTER_RATIO_MAX
    assert result.passed is False
    assert "invalid_character_ratio_exceeded" in result.reasons


def test_duplicate_penalty_is_separate_from_mapping_completeness() -> None:
    result = evaluate_page_quality(
        _page("A native paragraph with enough semantic characters."),
        PageMappingStats(
            page=1,
            source_item_count=2,
            mapped_item_count=2,
            duplicate_count=1,
        ),
    )

    assert result.mapping_completeness_score == 1.0
    assert result.deduplication_score == 0.5
    assert result.quality_score == pytest.approx(0.95)
    assert "duplicate_blocks_removed" in result.reasons


def test_document_quality_reports_missing_and_problem_pages() -> None:
    page = _page("A native paragraph with enough semantic characters.")

    result = evaluate_document_quality(
        [page],
        expected_page_count=2,
        mapping_stats=[_full_stats()],
    )

    assert result.passed is False
    assert result.missing_pages == [2]
    assert result.problem_pages == [2]
    assert result.source_page_coverage == 0.5
    assert result.quality_score == 0.5
    assert "source_pages_missing" in result.reasons


def test_explicit_source_page_missing_marker_reduces_coverage() -> None:
    page = _page("A native paragraph with enough semantic characters.").model_copy(
        update={"metadata": {"source_page_missing": True}}
    )

    result = evaluate_document_quality(
        [page],
        expected_page_count=1,
        mapping_stats=[_full_stats()],
    )

    assert result.passed is False
    assert result.mapped_page_count == 0
    assert result.missing_pages == [1]
    assert result.problem_pages == [1]
    assert result.source_page_coverage == 0.0
