"""Verified OCR headings may add sections without inventing section evidence."""

import hashlib
import json
from typing import Any, cast

from adaptive_learning.assessment.item_generation import (
    DiagnosticItemGenerator,
    structure_fingerprint,
)
from adaptive_learning.assessment.models import DiagnosticItem, LearnerProfile, ResponseType
from adaptive_learning.ingestion.chaptering import ChapterReconstructor
from adaptive_learning.ingestion.models import (
    BookStructure,
    ChapterDraft,
    ExtractionMethod,
    PageExtraction,
    PageKind,
    TextBlock,
)
from adaptive_learning.ingestion.quality import evaluate_text_quality, quality_band
from adaptive_learning.llm.client import OpenAICompatibleClient
from adaptive_learning.personalization.generator import ChapterCourseCompiler, chapter_fingerprint
from adaptive_learning.personalization.policy import PersonalizationPolicy


def page(number: int, *rows: tuple[str, str, int | None]) -> PageExtraction:
    blocks = [
        TextBlock(
            block_id=f"block_{number}_{index}",
            page_number=number,
            block_type=kind,
            bbox=(10, 40 + index * 70, 400, 90 + index * 70),
            text=text,
            source_method=ExtractionMethod.MINERU_VLM,
            metadata={"text_level": level},
        )
        for index, (kind, text, level) in enumerate(rows)
    ]
    raw = "\n".join(block.text for block in blocks)
    quality = evaluate_text_quality(raw)
    return PageExtraction(
        page_number=number,
        page_kind=PageKind.SCANNED,
        method=ExtractionMethod.MINERU_VLM,
        raw_text=raw,
        blocks=blocks,
        quality=quality,
        band=quality_band(quality.overall_score, accept=0.9, review=0.72),
    )


class EvidenceClient:
    def structured(self, *, system: str, user: str, **_: object) -> dict[str, Any]:
        payload = json.loads(user)
        if "章节摘要器" not in system:
            return {
                "summary": "书籍摘要。",
                "chapter_ids": [chapter["chapter_id"] for chapter in payload["chapters"]],
            }
        evidence = payload["evidence"]
        body = [item for item in evidence if "。" in item["text"]]
        return {
            "summary": "本章有三条可核验结论。",
            "evidence_ids": [item["id"] for item in body[:3]],
            "knowledge_points": [
                {"text": item["text"], "evidence_ids": [item["id"]]}
                for item in body[:3]
            ],
        }


def test_same_page_sections_split_blocks_and_inherit_only_local_quotes() -> None:
    pages = [
        page(
            1,
            ("title", "第1章 形状", 1),
            ("title", "第一节", 1),
            ("title", "三角形", 1),
            ("text", "三角形有三条边。", None),
            ("text", "三角形内角和为180度。", None),
            ("title", "第二节 正方形", 1),
            ("text", "正方形有四条边。", None),
        )
    ]
    reconstructor = ChapterReconstructor(cast(OpenAICompatibleClient, EvidenceClient()))
    structure = reconstructor.reconstruct_book(pages, "几何")

    assert len(structure.chapters) == 3
    root, triangle, square = structure.chapters
    assert [section.title for section in [triangle, square]] == [
        "第一节 三角形", "第二节 正方形"
    ]
    assert [section.parent_id for section in [triangle, square]] == [
        root.chapter_id, root.chapter_id
    ]
    assert [section.level for section in [triangle, square]] == [2, 2]
    assert [section.heading_block_id for section in [triangle, square]] == [
        "block_1_1", "block_1_5"
    ]
    assert (triangle.start_page, triangle.end_page) == (1, 1)
    assert (square.start_page, square.end_page) == (1, 1)
    assert set(triangle.source_block_ids).isdisjoint(square.source_block_ids)
    assert set(triangle.knowledge_points) == {
        "三角形有三条边。", "三角形内角和为180度。"
    }
    assert square.knowledge_points == ["正方形有四条边。"]
    assert all(
        quote.quote.startswith("三角形")
        for quotes in triangle.knowledge_point_evidence.values()
        for quote in quotes
    )
    assert all(
        quote.quote.startswith("正方形")
        for quotes in square.knowledge_point_evidence.values()
        for quote in quotes
    )
    profile = LearnerProfile(user_id="learner", book_id="geometry")
    decision = PersonalizationPolicy().decide(profile, triangle.chapter_id)
    course = ChapterCourseCompiler().compile(
        chapter=triangle, profile=profile, decision=decision
    )
    assert course.chapter_id == triangle.chapter_id
    assert all(
        citation.quote.startswith("三角形")
        for point in course.knowledge_points for citation in point.citations
    )
    assert reconstructor.reconstruct_book(pages, "几何").chapters[1].chapter_id == triangle.chapter_id


def test_toc_footer_and_numbered_exercise_do_not_become_sections() -> None:
    pages = [
        page(1, ("title", "目录", 1), ("title", "第一节 目录记录", 1)),
        page(2, ("title", "Chapter 1 Geometry", 1)),
        page(3, ("footer", "Section 1 Running footer", 1)),
        page(4, ("text", "1.1 This is body text", None)),
        page(5, ("title", "2.1 Wrong parent", 1)),
        page(6, ("title", "1.1 Genuine section", 1)),
    ]

    structure = ChapterReconstructor().reconstruct_book(pages, "Geometry")

    assert [item.title for item in structure.chapters] == [
        "Chapter 1 Geometry", "1.1 Genuine section"
    ]
    assert structure.chapters[1].knowledge_points == []
    assert structure.chapters[1].parent_id == structure.chapters[0].chapter_id


def test_explicit_second_level_ocr_heading_is_a_section_without_number() -> None:
    pages = [
        page(1, ("title", "Chapter 1 Geometry", 1),
             ("title", "Triangles", 2), ("text", "A triangle has three sides.", None),
             ("title", "A = b + c", 2)),
    ]

    structure = ChapterReconstructor().reconstruct_book(pages, "Geometry")

    assert [item.title for item in structure.chapters] == [
        "Chapter 1 Geometry", "Triangles"
    ]
    assert structure.chapters[1].heading_block_id == "block_1_1"
    assert structure.chapters[1].parent_id == structure.chapters[0].chapter_id


def test_legacy_fingerprints_stay_stable_when_hierarchy_fields_are_absent() -> None:
    chapter = ChapterDraft(
        chapter_id="legacy", order=1, title="Legacy", start_page=1,
        end_page=2, summary="", knowledge_points=[], source_block_ids=[],
    )
    assert chapter_fingerprint(chapter) == (
        "0be98452fc9f23da5331fc194ad43edfa5cd297339f57db86a5a686a26131695"
    )
    structure = BookStructure(
        title="Legacy", summary="", chapters=[chapter], source_page_count=2
    )
    payload = structure.model_dump_json(exclude_none=True)
    assert "parent_id" not in payload
    assert "heading_block_id" not in payload
    assert structure_fingerprint(structure) == hashlib.sha256(payload.encode()).hexdigest()


def test_diagnostic_generation_uses_parent_evidence_once() -> None:
    root = ChapterDraft(
        chapter_id="chapter", order=1, title="Chapter", start_page=1,
        end_page=1, summary="", knowledge_points=["Point"], source_block_ids=["b1"],
        knowledge_point_evidence={"Point": []},
    )
    child = root.model_copy(
        update={"chapter_id": "section", "parent_id": "chapter", "level": 2}
    )
    structure = BookStructure(
        title="Book", summary="", chapters=[root, child], source_page_count=1
    )

    class RecordingGenerator(DiagnosticItemGenerator):
        def __init__(self) -> None:
            self.points_per_chapter = 3

        def _select_points(self, chapter):
            return [(chapter.chapter_id, "Point", [])]

        def _generate_chapter(self, _book_title, chapter, _selected):
            return [
                DiagnosticItem(
                    item_id=chapter.chapter_id,
                    chapter_id=chapter.chapter_id,
                    knowledge_point_ids=["point"],
                    prompt="Question", response_type=ResponseType.SINGLE_CHOICE,
                )
            ]

    assert [item.chapter_id for item in RecordingGenerator().generate(structure)] == [
        "chapter"
    ]
