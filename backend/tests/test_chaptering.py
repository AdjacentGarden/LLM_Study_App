from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from adaptive_learning.ingestion.chaptering import (
    ChapterBoundaryDetector,
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


def test_book_summary_cannot_reference_unknown_chapter_but_keeps_real_directory() -> None:
    pages = [page(1, ("text", "第一条原文。", None)), page(2, ("text", "第二条原文。", None))]
    fake = FakeSummaryClient(invalid_book_id=True)

    structure = ChapterReconstructor(cast(OpenAICompatibleClient, fake)).reconstruct_book(pages, "书")
    assert len(structure.chapters) == 1
    assert structure.summary == "全书内容：本章围绕核心问题展开，并说明关键过程。"
    assert structure.chapters[0].summary == "本章围绕核心问题展开，并说明关键过程。"


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
    if not artifact.is_file():
        pytest.skip("historical OCR fixture is not distributed with the source checkout")
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


@pytest.mark.parametrize("issue", ["missing_appendix", "unknown", "duplicate", "schema"])
def test_book_summary_retries_with_explicit_coverage_feedback(issue):
    from adaptive_learning.ingestion.models import ChapterDraft

    chapters = [
        ChapterDraft(
            chapter_id=key,
            order=i + 1,
            title=title,
            start_page=i + 1,
            end_page=i + 1,
            summary="已核验的章节摘要。",
            knowledge_points=[],
            source_block_ids=[],
        )
        for i, (key, title) in enumerate(
            [("main", "第一章"), ("workbook", "WORKBOOK"), ("appendix", "附录 索引")]
        )
    ]

    class Client:
        calls = []

        def structured(self, *, user, system, **kwargs):
            payload = json.loads(user)
            self.calls.append(payload)
            assert "附录" in system and payload["required_chapter_ids"] == [
                "main",
                "workbook",
                "appendix",
            ]
            if len(self.calls) == 1:
                if issue == "schema":
                    return {"summary": "摘要", "chapter_ids": "main"}
                ids = {
                    "missing_appendix": ["main", "workbook"],
                    "unknown": ["main", "workbook", "appendix", "invented"],
                    "duplicate": ["main", "workbook", "appendix", "appendix"],
                }[issue]
                return {"summary": "摘要", "chapter_ids": ids}
            assert "validation_feedback" in payload
            assert "required_chapter_ids" in payload["validation_feedback"]
            return {
                "summary": "主章提供讲解，练习册用于练习，附录支持检索。",
                "chapter_ids": payload["required_chapter_ids"],
            }

    client = Client()
    result = ChapterReconstructor(
        cast(OpenAICompatibleClient, client), validation_retries=1
    )._summarize_book(chapters)
    assert "附录" in result and len(client.calls) == 2


def test_book_summary_failure_is_bounded_and_uses_existing_chapters_only():
    from adaptive_learning.ingestion.models import ChapterDraft

    chapter = ChapterDraft(
        chapter_id="appendix",
        order=1,
        title="附录",
        start_page=1,
        end_page=1,
        summary="附录用于检索。",
        knowledge_points=[],
        source_block_ids=[],
    )

    class Client:
        calls = 0

        def structured(self, **kwargs):
            self.calls += 1
            return {"summary": "仅有摘要，但故意不返回所需标识。", "chapter_ids": ["other"]}

    client = Client()
    result = ChapterReconstructor(
        cast(OpenAICompatibleClient, client), validation_retries=1
    )._summarize_book([chapter])
    assert result == "附录：附录用于检索。"
    assert "故意" not in result and "other" not in result
    assert client.calls == 2


def test_short_chapter_accepts_one_real_point_and_one_reference() -> None:
    pages = [page(1, ("heading", "第1章 细胞", 1),
                  ("text", "细胞是生物体结构和功能的基本单位。", None))]

    class Client:
        chapter_calls = 0

        def structured(self, *, user, system, **kwargs):
            payload = json.loads(user)
            if "章节摘要器" in system:
                self.chapter_calls += 1
                evidence = next(item for item in payload["evidence"] if "基本单位" in item["text"])
                return {"summary": "细胞是生物体的基本单位。",
                        "knowledge_points": [{"text": "细胞是生物体结构和功能的基本单位。",
                                              "evidence_ids": [evidence["id"]]}],
                        "evidence_ids": [evidence["id"]]}
            return {"summary": "全章介绍细胞的基本地位。", "chapter_ids": payload["required_chapter_ids"]}

    client = Client()
    result = ChapterReconstructor(cast(OpenAICompatibleClient, client)).reconstruct_book(pages, "细胞")
    assert client.chapter_calls == 1
    chapter = result.chapters[0]
    assert chapter.knowledge_points == ["细胞是生物体结构和功能的基本单位。"]
    assert len(chapter.evidence) == 1
    assert chapter.knowledge_point_evidence[chapter.knowledge_points[0]][0].quote in pages[0].raw_text


def test_failed_chapter_enhancement_keeps_boundaries_and_literal_complete_source() -> None:
    pages = [page(1, ("heading", "第1章 条件", 1),
                  ("text", "只有满足条件甲时，结果乙才可能成立。", None)),
             page(2, ("heading", "第2章 关系", 1),
                  ("text", "结果丙取决于条件丁，不能由未满足条件推断其反面。", None))]

    class Client:
        calls = 0

        def structured(self, **kwargs):
            self.calls += 1
            return {"summary": "无源的模型结论。", "knowledge_points": [
                {"text": "结果一定成立。", "evidence_ids": ["UNKNOWN"]}],
                "evidence_ids": ["UNKNOWN"]}

    client = Client()
    result = ChapterReconstructor(cast(OpenAICompatibleClient, client)).reconstruct_book(pages, "条件")
    assert [(c.title, c.start_page, c.end_page) for c in result.chapters] == [
        ("第1章 条件", 1, 1), ("第2章 关系", 2, 2)]
    assert client.calls == 6
    for chapter in result.chapters:
        original = pages[chapter.start_page - 1].raw_text
        assert chapter.knowledge_points
        assert all(point in original and point.endswith("。") for point in chapter.knowledge_points)
        assert "无源" not in chapter.summary and "一定成立" not in chapter.summary
        assert all(quote.quote in original for quote in chapter.evidence)
    assert all(chapter.title in result.summary for chapter in result.chapters)


def test_source_excerpt_recovery_excludes_skipped_pages_headers_and_cut_sentences() -> None:
    pages = [page(1, ("heading", "第1章 标题。", 1),
                  ("text", "没有结尾的残句", None)),
             page(2, ("text", "跳过页里的语句不能作为知识点。", None)).model_copy(
                 update={"processing_status": "skipped"})]

    class Client:
        calls = 0

        def structured(self, *, user, **kwargs):
            self.calls += 1
            assert all(entry["page_number"] != 2 for entry in json.loads(user).get("evidence", []))
            return {}

    client = Client()
    result = ChapterReconstructor(cast(OpenAICompatibleClient, client)).reconstruct_book(pages, "不完整")
    assert result.chapters[0].title == "第1章 标题。"
    assert result.chapters[0].summary == ''
    assert result.chapters[0].knowledge_points == []
    assert result.chapters[0].evidence == []
    assert result.summary == ''
    assert client.calls == 2


def test_summary_provider_unknown_is_not_replayed_and_can_retain_exact_source() -> None:
    from adaptive_learning.llm.client import LLMError

    class Client:
        calls = 0

        def structured(self, **kwargs):
            self.calls += 1
            raise LLMError("unknown provider outcome")

    client = Client()
    source = "只有条件甲成立，才可以讨论结论乙。"
    result = ChapterReconstructor(cast(OpenAICompatibleClient, client), validation_retries=3).reconstruct_book(
        [page(1, ("text", source, None))], "条件")
    assert client.calls == 2  # One chapter request and one optional book overview; no replay.
    assert result.chapters[0].knowledge_points == [source]
    assert result.chapters[0].summary == source
    assert result.summary == "全书内容：" + source


def test_recovery_never_turns_the_tail_of_a_cut_long_sentence_into_a_point() -> None:
    class Client:
        def structured(self, **kwargs):
            return {}

    source = "条件甲与结论乙之间的限定关系" * 80 + "在本章给定条件下才成立。"
    result = ChapterReconstructor(cast(OpenAICompatibleClient, Client())).reconstruct_book(
        [page(1, ('text', source, None))], '长句')
    assert result.chapters
    assert result.chapters[0].knowledge_points == []
    assert result.chapters[0].summary == ''


def test_excerpt_summary_budget_keeps_one_whole_condition_instead_of_cutting_two() -> None:
    from adaptive_learning.ingestion.models import ChapterDraft

    sources = ['只有' + ('条件甲和条件乙同时成立，' * 28) + '才可讨论结果丙。',
               '除非' + ('前提丁已经得到完整核实，' * 28) + '否则不能推出结论戊。']
    assert all(340 < len(value) <= 500 for value in sources)
    pages = [page(index + 1, ('text', text, None)) for index, text in enumerate(sources)]

    class Client:
        def structured(self, **kwargs):
            return {}

    chapter = ChapterDraft(chapter_id='range', order=1, title='条件', start_page=1, end_page=2,
                           summary='', knowledge_points=[], source_block_ids=[])
    result = ChapterReconstructor(cast(OpenAICompatibleClient, Client()))._summarize(chapter, pages)
    assert len(result.summary) <= 680
    assert result.summary in sources
    assert result.knowledge_points == sources
    assert [source.quote for source in result.evidence] == sources
