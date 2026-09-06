from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from adaptive_learning.ingestion.chaptering import (
    ChapterBoundaryDetector,
    ChapteringValidationError,
    ChapterReconstructor,
    _split_evidence,
    load_normalized_pages,
)
from adaptive_learning.ingestion.models import (
    ExtractionMethod,
    PageExtraction,
    PageKind,
    TextBlock,
)
from adaptive_learning.ingestion.quality import evaluate_text_quality, quality_band
from adaptive_learning.llm.client import OpenAICompatibleClient


def page(number: int, *blocks: tuple[str, str, int | None]) -> PageExtraction:
    normalized_blocks = [
        TextBlock(
            block_id=f"b_{number}_{index}",
            page_number=number,
            block_type=block_type,
            bbox=(10, 50 + index * 100, 900, 120 + index * 100),
            text=text,
            source_method=ExtractionMethod.MINERU_VLM,
            metadata={"text_level": level},
        )
        for index, (block_type, text, level) in enumerate(blocks)
    ]
    raw_text = "\n".join(block.text for block in normalized_blocks)
    quality = evaluate_text_quality(raw_text)
    return PageExtraction(
        page_number=number,
        page_kind=PageKind.SCANNED,
        method=ExtractionMethod.MINERU_VLM,
        raw_text=raw_text,
        blocks=normalized_blocks,
        quality=quality,
        band=quality_band(quality.overall_score, accept=0.9, review=0.72),
    )


def test_chinese_toc_and_repeated_footers_do_not_create_false_boundaries() -> None:
    pages = [
        page(
            1,
            ("text", "目录", 1),
            ("text", "第1章 起点 …… 3", 1),
            ("text", "第2章 深入 …… 5", 1),
            ("text", "第3章 应用 …… 8", 1),
        ),
        page(2, ("text", "前言", 1)),
        page(3, ("text", "第1章 起点", 1), ("text", "正文一", None)),
        page(4, ("footer", "第1章 起点", None), ("text", "正文二", None)),
        page(5, ("text", "第2章 深入", 1), ("text", "正文三", None)),
        page(6, ("footer", "第2章 深入", None), ("text", "正文四", None)),
        page(8, ("text", "第3章 应用", 1), ("text", "正文五", None)),
    ]

    chapters, fallback = ChapterBoundaryDetector().detect(pages)

    assert not fallback
    assert [(item.start_page, item.end_page) for item in chapters] == [
        (3, 4),
        (5, 7),
        (8, 8),
    ]
    assert [item.title for item in chapters] == ["第1章 起点", "第2章 深入", "第3章 应用"]


def test_english_chapters_are_supported_and_preferred_over_container_parts() -> None:
    pages = [
        page(1, ("text", "Part I Foundations", 1)),
        page(2, ("text", "Chapter 1: Systems", 1)),
        page(3, ("text", "body", None)),
        page(4, ("text", "Chapter II - Feedback", 1)),
        page(5, ("text", "body", None)),
    ]

    chapters, fallback = ChapterBoundaryDetector().detect(pages)

    assert not fallback
    assert [chapter.title for chapter in chapters] == [
        "Chapter 1 Systems",
        "Chapter II Feedback",
    ]
    assert [(chapter.start_page, chapter.end_page) for chapter in chapters] == [(2, 3), (4, 5)]


def test_book_without_explicit_chapters_falls_back_to_one_honest_range() -> None:
    pages = [page(1, ("text", "随笔开头", 1)), page(2, ("text", "连续正文", None))]

    chapters, fallback = ChapterBoundaryDetector().detect(pages)

    assert fallback
    assert len(chapters) == 1
    assert chapters[0].title == "全书内容"
    assert (chapters[0].start_page, chapters[0].end_page) == (1, 2)


def test_chapter_ids_are_stable_across_reconstruction_runs() -> None:
    pages = [page(1, ("text", "第1章 开始", 1)), page(2, ("text", "正文", None))]
    detector = ChapterBoundaryDetector()

    first, _ = detector.detect(pages)
    second, _ = detector.detect(pages)

    assert first[0].chapter_id == second[0].chapter_id


def test_evidence_units_never_insert_or_rewrite_source_characters() -> None:
    source = "第一句话没有多余空格。第二句话紧接着。" + "很长的连续内容" * 100

    units = _split_evidence(source, limit=80)

    assert len(units) > 2
    assert all(unit in source for unit in units)
    assert all(len(unit) <= 80 for unit in units)


class FakeSummaryClient:
    def __init__(self, *, invalid_first_quote: bool = False, invalid_book_id: bool = False) -> None:
        self.invalid_first_quote = invalid_first_quote
        self.invalid_book_id = invalid_book_id
        self.chapter_calls = 0

    def structured(self, *, system: str, user: str, **_: object) -> dict[str, Any]:
        payload = json.loads(user)
        if "章节摘要器" in system:
            self.chapter_calls += 1
            evidence = payload["evidence"]
            first_id = (
                "UNKNOWN"
                if self.invalid_first_quote and self.chapter_calls == 1
                else evidence[0]["id"]
            )
            return {
                "summary": "本章围绕核心问题展开，并说明关键过程。",
                "knowledge_points": [
                    {"text": "核心问题", "evidence_ids": [evidence[0]["id"]]},
                    {"text": "关键过程", "evidence_ids": [evidence[-1]["id"]]},
                    {"text": "实际结果", "evidence_ids": [evidence[-1]["id"]]},
                ],
                "evidence_ids": [first_id, evidence[-1]["id"]],
            }
        chapter_ids = [item["chapter_id"] for item in payload["chapters"]]
        if self.invalid_book_id:
            chapter_ids.append("chapter_unknown")
        return {"summary": "全书围绕各章主线逐步展开。", "chapter_ids": chapter_ids}


def test_summary_retries_invalid_quote_and_keeps_verbatim_evidence() -> None:
    pages = [
        page(1, ("text", "第一条可核验原文。", None)),
        page(2, ("text", "第二条可核验原文。", None)),
    ]
    fake = FakeSummaryClient(invalid_first_quote=True)
    reconstructor = ChapterReconstructor(cast(OpenAICompatibleClient, fake))

    structure = reconstructor.reconstruct_book(pages, "测试书")

    assert fake.chapter_calls == 2
    assert structure.summary == "全书围绕各章主线逐步展开。"
    assert len(structure.chapters[0].evidence) == 2
    assert structure.chapters[0].evidence[0].quote == "第一条可核验原文。"
    assert structure.chapters[0].knowledge_point_evidence["核心问题"][0].page_number == 1


def test_book_summary_cannot_reference_unknown_chapter() -> None:
    pages = [page(1, ("text", "第一条原文。", None)), page(2, ("text", "第二条原文。", None))]
    fake = FakeSummaryClient(invalid_book_id=True)

    with pytest.raises(ChapteringValidationError, match="every known chapter"):
        ChapterReconstructor(cast(OpenAICompatibleClient, fake)).reconstruct_book(pages, "书")


def test_load_normalized_pages_preserves_heading_metadata(tmp_path: Path) -> None:
    source = tmp_path / "pages.jsonl"
    source.write_text(
        json.dumps(
            {
                "page_number": 1,
                "text": "第1章 开始",
                "blocks": [
                    {
                        "block_index": 7,
                        "type": "text",
                        "bbox": [10, 20, 300, 80],
                        "text": "第1章 开始",
                        "raw": {"text_level": 1},
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    pages = load_normalized_pages(source)

    assert pages[0].blocks[0].block_id == "block_1_7"
    assert pages[0].blocks[0].metadata["text_level"] == 1


def test_real_biology_artifact_has_six_verified_body_chapters() -> None:
    artifact = Path(__file__).parents[2] / "artifacts" / "ocr" / "biology-20260829" / "pages.jsonl"
    pages = load_normalized_pages(artifact)

    chapters, fallback = ChapterBoundaryDetector().detect(pages)

    assert not fallback
    assert len(chapters) == 6
    assert [(chapter.start_page, chapter.end_page) for chapter in chapters] == [
        (10, 35),
        (36, 55),
        (56, 73),
        (74, 91),
        (92, 103),
        (104, 125),
    ]
    assert chapters[0].title == "第2章 基因和染色体的关系"
    assert chapters[-1].title == "第 7 章 现代生物进化理论"
