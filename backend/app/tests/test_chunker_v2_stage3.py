from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.document.chunk_protocol import FrozenChunkConfig, render_embedding_text
from app.document.chunker_v2 import build_chunks_v2
from app.schemas.books import Asset, Chapter


class WordCounter:
    def encode(self, text: str) -> list[str]:
        return text.split()

    def decode(self, tokens: list[str]) -> str:
        return " ".join(tokens)

    def count(self, text: str) -> int:
        return len(self.encode(text))


class NonShrinkingDecodeCounter(WordCounter):
    def decode(self, tokens: list[str]) -> str:
        # Some malformed OCR token sequences decode into text that re-encodes
        # to the same token count even after one token was removed.
        return " ".join([*tokens, "repeat"])


def _chapter(
    chapter_id: str,
    *,
    title: str,
    level: int = 1,
    start: int = 1,
    end: int = 2,
    parent_id: str | None = None,
) -> Chapter:
    return Chapter(
        chapter_id=chapter_id,
        level=level,
        source_title=title,
        ai_title=title,
        page_start=start,
        page_end=end,
        confidence=100,
        status="confirmed",
        source="test",
        parent_id=parent_id,
    )


def _block(
    block_id: str,
    text: str,
    *,
    block_type: str = "paragraph",
    bbox: list[float] | None = None,
    metadata: dict[str, object] | None = None,
    asset_ids: list[str] | None = None,
    parser: str = "mineru",
    heading_level: int | None = None,
) -> dict[str, object]:
    return {
        "block_id": block_id,
        "type": block_type,
        "text": text,
        "bbox": bbox,
        "metadata": metadata or {},
        "asset_ids": asset_ids or [],
        "source_parser": parser,
        "heading_level": heading_level,
    }


def _page(
    number: int,
    blocks: list[dict[str, object]],
    *,
    quality: float = 1.0,
    ocr_confidence: float | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "page": number,
        "blocks": blocks,
        "quality_score": quality,
        "ocr_confidence": ocr_confidence,
        "needs_ocr": False,
        "parser": "mineru",
        "metadata": metadata or {},
    }


def _write_pages(path: Path, pages: list[dict[str, object]]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pages.json").write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")


def _words(prefix: str, count: int) -> str:
    return " ".join(f"{prefix}{index:04d}" for index in range(count))


def _asset(asset_id: str, caption: str, *, page: int = 1) -> Asset:
    return Asset(
        asset_id=asset_id,
        book_id="book",
        source_type="mineru",
        page=page,
        type="figure",
        caption=caption,
        bbox=[10.0, 10.0, 50.0, 50.0],
        image_url=f"/{asset_id}",
        thumbnail_url=f"/{asset_id}/thumb",
        source_parser="mineru",
    )


def test_normal_chunks_have_stable_ids_overlap_heading_and_deepest_chapter(tmp_path: Path) -> None:
    header = _block(
        "header",
        "CONFIDENTIAL COURSE COPY",
        bbox=[0.0, 0.0, 500.0, 40.0],
        metadata={"page_size": [500.0, 800.0]},
    )
    pages = [
        _page(
            1,
            [
                header,
                _block("title", "Section Alpha", block_type="title", heading_level=2),
                _block("p1", _words("alpha", 200)),
                _block("p2", _words("beta", 200)),
            ],
        ),
        _page(
            2,
            [
                {**header, "block_id": "header2"},
                _block("page-number", "2"),
                _block("p3", _words("gamma", 200)),
                _block("p4", _words("delta", 200)),
            ],
        ),
    ]
    _write_pages(tmp_path, pages)
    chapters = [
        _chapter("parent", title="Parent", level=1),
        _chapter("child", title="Child", level=2, parent_id="parent"),
    ]
    counter = WordCounter()

    first = build_chunks_v2("book", tmp_path, chapters, token_counter=counter)
    second = build_chunks_v2("book", tmp_path, list(reversed(chapters)), token_counter=counter)

    assert len(first) >= 2
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert all(chunk.chapter_id == "child" for chunk in first)
    assert all(chunk.heading_path == ["Parent", "Child", "Section Alpha"] for chunk in first)
    assert all("CONFIDENTIAL" not in chunk.text and chunk.text.strip() != "2" for chunk in first)
    assert all("Parent" not in chunk.text for chunk in first)  # headings are metadata, not display body
    assert len({chunk.content_hash for chunk in first}) == len(first)
    for index, chunk in enumerate(first):
        rendered = render_embedding_text(
            chunk.text,
            chunk.heading_path,
            str(chunk.metadata.get("overlap_text") or ""),
        )
        assert chunk.token_count == counter.count(rendered)
        assert chunk.token_count <= 700
        if index:
            assert 0 < counter.count(str(chunk.metadata["overlap_text"])) <= 80


def test_non_shrinking_overlap_decode_drops_overlap_instead_of_looping(tmp_path: Path) -> None:
    _write_pages(
        tmp_path,
        [
            _page(
                1,
                [
                    _block("first", "one two three four"),
                    _block("second", "five six seven eight"),
                ],
            )
        ],
    )
    config = FrozenChunkConfig(
        target_tokens=5,
        max_tokens=7,
        min_tokens=2,
        overlap_tokens=1,
        atomic_content_hard_max_tokens=7,
    )

    chunks = build_chunks_v2(
        "book",
        tmp_path,
        [_chapter("chapter", title="Chapter", end=1)],
        config=config,
        token_counter=NonShrinkingDecodeCounter(),
    )

    assert len(chunks) == 2
    assert chunks[1].metadata["overlap_text"] == ""
    assert "unstable_overlap_dropped" in chunks[1].metadata["warnings"]


def test_paragraphs_merge_across_pages_with_accurate_range(tmp_path: Path) -> None:
    _write_pages(
        tmp_path,
        [
            _page(1, [_block("p1", _words("one", 40))]),
            _page(2, [_block("p2", _words("two", 40))]),
        ],
    )
    chunks = build_chunks_v2(
        "book",
        tmp_path,
        [_chapter("chapter", title="Chapter")],
        token_counter=WordCounter(),
    )
    assert len(chunks) == 1
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 2)
    assert chunks[0].source_block_ids == ["p1", "p2"]
    assert "short_tail" in chunks[0].metadata["warnings"]


def test_quality_v1_components_reproduce_native_score_and_quarantine_low_page(tmp_path: Path) -> None:
    text = "Cells are the basic structural and functional units of life."
    _write_pages(
        tmp_path,
        [
            _page(1, [_block("native", text)], quality=0.98185, ocr_confidence=0.879),
            _page(2, [_block("low", "This page remains auditable but cannot enter retrieval.")], quality=0.3),
        ],
    )
    chunks = build_chunks_v2(
        "book",
        tmp_path,
        [_chapter("chapter", title="Chapter")],
        token_counter=WordCounter(),
    )
    assert len(chunks) == 2  # quality buckets are never merged
    native = next(chunk for chunk in chunks if "native" in chunk.source_block_ids)
    low = next(chunk for chunk in chunks if "low" in chunk.source_block_ids)
    components = native.metadata["quality_components"]
    assert native.quality_score == pytest.approx(0.98185)
    assert components["valid_character_score"] == 1.0
    assert components["content_coverage_score"] == 1.0
    assert components["ocr_confidence_score"] == 0.879
    assert components["mapping_completeness_score"] == 1.0
    assert components["deduplication_score"] == 1.0
    assert native.metadata["quality_formula_version"] == "quality-v1"
    assert native.metadata["indexable"] is True
    assert low.metadata["indexable"] is False
    assert low.metadata["quarantined"] is True
    assert low.metadata["index_status"] == "quarantined"


def test_block_confidence_wins_over_page_average_and_multiple_low_components_quarantine(tmp_path: Path) -> None:
    weak = _block(
        "weak",
        "z",
        metadata={"mapping_completeness_score": 0.0, "duplicate_count": 1, "mapped_item_count": 1},
    )
    weak["confidence"] = 0.1
    _write_pages(tmp_path, [_page(1, [weak], quality=0.98, ocr_confidence=0.98)])
    chunks = build_chunks_v2(
        "book",
        tmp_path,
        [_chapter("chapter", title="Chapter", end=1)],
        token_counter=WordCounter(),
    )
    assert len(chunks) == 1
    chunk = chunks[0]
    components = chunk.metadata["quality_components"]
    assert components["ocr_confidence_score"] == 0.1
    assert components["mapping_completeness_score"] == 0.0
    assert components["deduplication_score"] == 0.0
    assert chunk.quality_score == pytest.approx(0.28)
    assert chunk.metadata["indexable"] is False
    assert chunk.metadata["quarantined"] is True
    assert chunk.metadata["quality_threshold"] == 0.45


def test_exact_duplicates_merge_all_provenance_and_keep_highest_quality(tmp_path: Path) -> None:
    weak = _block(
        "weak",
        "z",
        metadata={"mapping_completeness_score": 0.0, "duplicate_count": 1, "mapped_item_count": 1},
    )
    weak["confidence"] = 0.0
    _write_pages(
        tmp_path,
        [
            _page(1, [_block("strong", "z")], quality=1.0),
            _page(2, [weak], quality=0.2, ocr_confidence=0.9),
        ],
    )
    chunks = build_chunks_v2(
        "book",
        tmp_path,
        [_chapter("chapter", title="Chapter")],
        token_counter=WordCounter(),
    )
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.quality_score == pytest.approx(0.715)
    assert chunk.metadata["indexable"] is True
    assert chunk.metadata["deduplicated_occurrence_count"] == 2
    assert len(chunk.metadata["source_locations"]) == 2
    assert set(chunk.source_block_ids) == {"strong", "weak"}
    assert (chunk.page_start, chunk.page_end) == (1, 2)


def test_large_table_splits_by_rows_repeats_header_and_keeps_xlsx_semantics(tmp_path: Path) -> None:
    rows = "".join(
        f"<tr><td>row{index}</td><td>value {index} has detail words</td></tr>"
        for index in range(1, 9)
    )
    table = f"<table><tr><th>Name</th><th>Value</th></tr>{rows}</table>"
    _write_pages(
        tmp_path,
        [
            _page(
                1,
                [
                    _block(
                        "table",
                        "Name Value row data",
                        block_type="table",
                        metadata={
                            "table_body": table,
                            "sheet_name": "Growth Data",
                            "cell_range": "A1:B9",
                        },
                    )
                ],
                metadata={"source_format": "xlsx"},
            )
        ],
    )
    config = FrozenChunkConfig(
        target_tokens=12,
        max_tokens=18,
        min_tokens=5,
        overlap_tokens=2,
        atomic_content_hard_max_tokens=24,
    )
    chunks = build_chunks_v2(
        "book",
        tmp_path,
        [_chapter("chapter", title="Chapter", end=1)],
        config=config,
        token_counter=WordCounter(),
    )

    assert len(chunks) >= 2
    assert all(chunk.content_type == "table" for chunk in chunks)
    assert all("Sheet: Growth Data (A1:B9)" in chunk.text for chunk in chunks)
    assert all("Name | Value" in chunk.text for chunk in chunks)
    assert all(" | " not in line for chunk in chunks for line in chunk.text.splitlines() if line.startswith("row"))
    assert all(chunk.metadata["repeated_header"] is True for chunk in chunks)
    assert all(chunk.metadata["sheet_name"] == "Growth Data" for chunk in chunks)
    assert all(chunk.metadata["table_rows"][0] == ["Name", "Value"] for chunk in chunks)
    assert all(chunk.metadata["table_header_flags"][0] is True for chunk in chunks)
    assert all(any(flag is False for flag in chunk.metadata["table_header_flags"]) for chunk in chunks)
    assert all((chunk.token_count or 0) <= 24 for chunk in chunks)


def test_table_data_cells_form_contiguous_search_text_and_keep_structured_rows(tmp_path: Path) -> None:
    _write_pages(
        tmp_path,
        [
            _page(
                1,
                [
                    _block(
                        "cell-cycle-table",
                        "Stage Chromosome state",
                        block_type="table",
                        metadata={
                            "table_body": (
                                "<table><tr><th>Stage</th><th>Chromosome state</th></tr>"
                                "<tr><td>G1</td><td>unreplicated</td></tr>"
                                "<tr><td>G2</td><td>replicated</td></tr></table>"
                            )
                        },
                    )
                ],
            )
        ],
    )

    chunks = build_chunks_v2(
        "book",
        tmp_path,
        [_chapter("chapter", title="Cell Cycle", end=1)],
        token_counter=WordCounter(),
    )

    assert len(chunks) == 1
    chunk = chunks[0]
    assert "Stage | Chromosome state" in chunk.text
    assert "G2 replicated" in chunk.text
    assert "G2 | replicated" not in chunk.text
    assert chunk.metadata["table_rows"] == [
        ["Stage", "Chromosome state"],
        ["G1", "unreplicated"],
        ["G2", "replicated"],
    ]
    assert chunk.metadata["table_header_flags"] == [True, False, False]


def test_formula_owns_nearby_explanations_without_splitting_latex(tmp_path: Path) -> None:
    latex = r"E = mc^{2}"
    formula_asset = _asset("formula-asset", "Rendered formula image")
    _write_pages(
        tmp_path,
        [
            _page(
                1,
                [
                    _block("before", "Mass and energy are equivalent in this relation."),
                    _block(
                        "formula",
                        latex,
                        block_type="formula",
                        metadata={"text_format": "latex"},
                        asset_ids=["formula-asset"],
                    ),
                    _block("after", "The speed of light supplies the conversion factor."),
                ],
            )
        ],
    )
    config = FrozenChunkConfig(
        target_tokens=20,
        max_tokens=30,
        min_tokens=5,
        overlap_tokens=2,
        atomic_content_hard_max_tokens=40,
    )
    chunks = build_chunks_v2(
        "book",
        tmp_path,
        [_chapter("chapter", title="Physics", end=1)],
        assets=[formula_asset],
        config=config,
        token_counter=WordCounter(),
    )
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.content_type == "formula"
    assert latex in chunk.text
    assert "Mass and energy" in chunk.text
    assert "speed of light" in chunk.text
    assert chunk.metadata["latex"] == latex
    assert set(chunk.source_block_ids) == {"before", "formula", "after"}
    assert chunk.asset_ids == ["formula-asset"]
    assert formula_asset.source_chunk_ids == [chunk.chunk_id]
    assert (chunk.token_count or 0) <= 40


def test_formula_hard_overflow_fails_instead_of_cutting_latex(tmp_path: Path) -> None:
    latex = " ".join(f"x_{{{index}}}" for index in range(30))
    _write_pages(tmp_path, [_page(1, [_block("formula", latex, block_type="formula")])])
    config = FrozenChunkConfig(
        target_tokens=10,
        max_tokens=15,
        min_tokens=5,
        overlap_tokens=2,
        atomic_content_hard_max_tokens=20,
    )
    with pytest.raises(ValueError, match="Formula exceeds the atomic hard limit"):
        build_chunks_v2(
            "book",
            tmp_path,
            [_chapter("chapter", title="Math", end=1)],
            config=config,
            token_counter=WordCounter(),
        )


def test_figures_bind_exact_assets_and_caption_only_assets_get_v2_chunks(tmp_path: Path) -> None:
    _write_pages(
        tmp_path,
        [
            _page(
                1,
                [
                    _block(
                        "slide-body",
                        "ATP production occurs across the membrane.",
                        metadata={"slide_number": 3, "slide_title": "Respiration"},
                        asset_ids=["asset-inline"],
                    ),
                    _block(
                        "figure",
                        "Figure 1. Mitochondrial membrane diagram.",
                        block_type="figure",
                        asset_ids=["asset-1", "missing"],
                    ),
                    _block("empty-figure", "", block_type="figure", asset_ids=["asset-empty-block"]),
                    _block("filtered-header", "Deck header", block_type="header", asset_ids=["asset-filtered"]),
                ],
                metadata={"source_format": "pptx"},
            )
        ],
    )
    assets = [
        _asset("asset-1", "Block-owned diagram"),
        _asset("asset-2", "Figure 2. Caption supplied by the exact Asset."),
        _asset("asset-2-copy", "Figure 2. Caption supplied by the exact Asset."),
        _asset("asset-empty-block", "Caption recovered from an Asset whose visual block was empty."),
        _asset("asset-filtered", "Caption recovered even though a header block referenced the Asset."),
        _asset("asset-inline", "Inline asset already consumed by its paragraph."),
        _asset("asset-empty", ""),
    ]
    chunks = build_chunks_v2(
        "book",
        tmp_path,
        [_chapter("chapter", title="Respiration", end=1)],
        assets=assets,
        token_counter=WordCounter(),
    )

    figures = [chunk for chunk in chunks if chunk.content_type == "figure"]
    assert len(figures) == 4
    assert {tuple(chunk.asset_ids) for chunk in figures} == {
        ("asset-1",),
        ("asset-2", "asset-2-copy"),
        ("asset-empty-block",),
        ("asset-filtered",),
    }
    assert all("missing" not in chunk.asset_ids and "asset-empty" not in chunk.asset_ids for chunk in figures)
    caption_asset = next(chunk for chunk in figures if chunk.asset_ids == ["asset-2", "asset-2-copy"])
    assert caption_asset.metadata["asset_binding"] == "exact_asset_caption"
    assert caption_asset.metadata["duplicate_provenance_merged"] is True
    assert assets[1].source_chunk_ids == [caption_asset.chunk_id]
    assert assets[2].source_chunk_ids == [caption_asset.chunk_id]
    slide = next(chunk for chunk in chunks if chunk.content_type == "text")
    assert slide.asset_ids == ["asset-inline"]
    assert slide.text.startswith("Slide 3: Respiration")
    assert slide.metadata["slide_number"] == 3
    assert slide.metadata["source_format"] == "pptx"


def test_short_tail_merges_when_below_max_and_warns_when_it_cannot(tmp_path: Path) -> None:
    chapter = [_chapter("chapter", title="C", end=1)]
    counter = WordCounter()

    merge_path = tmp_path / "merge"
    _write_pages(
        merge_path,
        [_page(1, [_block("main", _words("main", 9)), _block("tail", _words("tail", 2))])],
    )
    merge_config = FrozenChunkConfig(
        target_tokens=12,
        max_tokens=15,
        min_tokens=5,
        overlap_tokens=2,
        atomic_content_hard_max_tokens=20,
    )
    merged = build_chunks_v2(
        "book",
        merge_path,
        chapter,
        config=merge_config,
        token_counter=counter,
    )
    assert len(merged) == 1
    assert set(merged[0].source_block_ids) == {"main", "tail"}

    warning_path = tmp_path / "warning"
    _write_pages(
        warning_path,
        [_page(1, [_block("main", _words("main", 13)), _block("tail", _words("tail", 3))])],
    )
    warning_config = FrozenChunkConfig(
        target_tokens=14,
        max_tokens=15,
        min_tokens=5,
        overlap_tokens=2,
        atomic_content_hard_max_tokens=20,
    )
    warned = build_chunks_v2(
        "book",
        warning_path,
        chapter,
        config=warning_config,
        token_counter=counter,
    )
    assert len(warned) == 2
    assert "short_tail" in warned[-1].metadata["warnings"]


def test_office_location_boundaries_never_cross_slides_or_unrelated_sheet_ranges(tmp_path: Path) -> None:
    pptx_path = tmp_path / "pptx"
    _write_pages(
        pptx_path,
        [
            _page(
                1,
                [_block("s1", "First slide semantic body.", metadata={"slide_number": 1, "slide_title": "One"})],
                metadata={"source_format": "pptx"},
            ),
            _page(
                2,
                [_block("s2", "Second slide semantic body.", metadata={"slide_number": 2, "slide_title": "Two"})],
                metadata={"source_format": "pptx"},
            ),
        ],
    )
    slides = build_chunks_v2(
        "book",
        pptx_path,
        [_chapter("chapter", title="Deck")],
        token_counter=WordCounter(),
    )
    assert len(slides) == 2
    assert [chunk.metadata["slide_number"] for chunk in slides] == [1, 2]

    xlsx_path = tmp_path / "xlsx"
    _write_pages(
        xlsx_path,
        [
            _page(
                1,
                [
                    _block("r1", "Adjacent first range.", metadata={"sheet_name": "Data", "cell_range": "A1:B2"}),
                    _block("r2", "Adjacent second range.", metadata={"sheet_name": "Data", "cell_range": "A3:B4"}),
                    _block("r3", "Separated third range.", metadata={"sheet_name": "Data", "cell_range": "A10:B11"}),
                ],
                metadata={"source_format": "xlsx"},
            )
        ],
    )
    ranges = build_chunks_v2(
        "book",
        xlsx_path,
        [_chapter("chapter", title="Workbook", end=1)],
        token_counter=WordCounter(),
    )
    assert len(ranges) == 2
    assert set(ranges[0].metadata["cell_ranges"]) == {"A1:B2", "A3:B4"}
    assert ranges[1].metadata["cell_range"] == "A10:B11"
