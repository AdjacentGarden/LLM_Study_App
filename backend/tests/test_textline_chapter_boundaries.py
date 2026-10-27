import json

from adaptive_learning.ingestion.chaptering import ChapterBoundaryDetector
from adaptive_learning.ingestion.models import ExtractionMethod, PageExtraction, PageKind, TextBlock
from adaptive_learning.ingestion.quality import evaluate_text_quality


def lines(number, rows, *, method=ExtractionMethod.APPLE_VISION):
    blocks = [
        TextBlock(
            block_id=f"p{number}-b{i}",
            page_number=number,
            text=text,
            bbox=box,
            source_method=method,
            metadata={"text_level": 1} if method == ExtractionMethod.VISION_RESCUE else {},
        )
        for i, (text, box) in enumerate(rows)
    ]
    text = "\n".join(block.text for block in blocks)
    return PageExtraction(
        page_number=number,
        page_kind=PageKind.SCANNED,
        method=method,
        raw_text=text,
        blocks=blocks,
        quality=evaluate_text_quality(text),
        band="accepted",
    )


def body(number, title_rows):
    return lines(
        number,
        [*title_rows, ("正文说明应保留的概念与相关条件，不应改写为标题。", (100, 400, 800, 415))],
    )


def test_chinese_split_display_titles_and_printed_appendix_boundary_preserve_source():
    toc = lines(
        1,
        [
            ("目录", (100, 20, 200, 50)),
            ("第一章 基础与概念", (100, 100, 400, 115)),
            ("1", (850, 100, 880, 115)),
            ("第二章 推理与应用", (100, 300, 400, 315)),
            ("4", (850, 300, 880, 315)),
            ("附录I 名词索引", (100, 500, 400, 515)),
            ("7", (850, 500, 880, 515)),
        ],
    )
    pages = [
        toc,
        body(
            3,
            [
                ("第一章", (100, 80, 300, 120)),
                ("基础与", (100, 140, 400, 180)),
                ("• 旁栏举例", (600, 140, 900, 155)),
                ("概念", (100, 200, 400, 240)),
            ],
        ),
        body(4, [("第一章 基础与概念", (100, 20, 400, 32))]),
        body(6, [("第二章", (100, 80, 300, 120)), ("推理与应用", (100, 140, 500, 180))]),
        body(7, [("推理与应用的其他情况", (100, 80, 700, 120))]),
        body(9, [("附录I", (100, 80, 300, 109)), ("名词索引", (300, 170, 500, 191))]),
    ]
    before = json.dumps([page.model_dump(mode="json") for page in pages], ensure_ascii=False)
    chapters, fallback = ChapterBoundaryDetector().detect(pages)
    assert not fallback
    assert [(c.title, c.start_page, c.end_page) for c in chapters] == [
        ("第一章 基础与概念", 3, 5),
        ("第二章 推理与应用", 6, 8),
        ("附录I 名词索引", 9, 9),
    ]
    assert all(
        c.boundary_evidence["toc_block_ids"] and c.boundary_evidence["body_block_ids"]
        for c in chapters
    )
    assert (
        json.dumps([page.model_dump(mode="json") for page in pages], ensure_ascii=False) == before
    )


def english_pages(*, second_start=5, verified=False):
    toc = lines(
        1,
        [
            ("CONTENTS", (100, 20, 300, 50)),
            ("UNIT1", (140, 100, 200, 115)),
            ("FESTIVALS AND", (140, 130, 340, 145)),
            ("CELEBRATIONS", (140, 155, 340, 170)),
            ("P.1", (70, 145, 115, 160)),
            ("UNIT 2", (140, 300, 210, 315)),
            ("MORALS AND", (140, 330, 340, 345)),
            ("VIRTUES", (140, 355, 340, 370)),
            ("P.3", (70, 345, 115, 360)),
            ("WORKBOOK", (100, 650, 300, 670)),
            ("Unit 1", (380, 650, 440, 665)),
            ("P.7", (480, 650, 530, 665)),
            ("Unit 2", (380, 700, 440, 715)),
            ("P.9", (480, 700, 530, 715)),
            ("APPENDICES", (100, 850, 300, 870)),
            ("P.11", (480, 850, 530, 865)),
        ],
    )
    first = body(
        3,
        [
            ("FESTIVALS AND", (350, 80, 900, 130)),
            ("1", (230, 90, 300, 170)),
            ("UNIT", (80, 150, 210, 183)),
            ("CELEBRATIONS", (350, 140, 900, 190)),
        ],
    )
    second = (
        lines(
            second_start,
            [("UNIT 2 MORALS AND VIRTUES", None)],
            method=ExtractionMethod.VISION_RESCUE,
        )
        if verified
        else body(
            second_start,
            [
                ("MORALS AND", (350, 80, 900, 130)),
                ("UNIT", (80, 150, 210, 183)),
                ("VIRTUES", (350, 140, 900, 190)),
            ],
        )
    )
    return [
        toc,
        first,
        second,
        body(7, [("UNIT2 MORALS AND VIRTUES", (100, 20, 500, 32))]),
        body(9, [("WORKBOOK", (350, 30, 650, 68)), ("UNIT1", (100, 90, 210, 111))]),
        body(11, [("UNIT2", (100, 90, 210, 111))]),
        body(13, [("Appendices 附录", (300, 30, 700, 68))]),
    ]


def test_english_split_units_workbook_repeated_ordinals_and_appendices_are_separate():
    chapters, fallback = ChapterBoundaryDetector().detect(english_pages())
    assert not fallback
    assert [(c.title, c.start_page, c.end_page) for c in chapters] == [
        ("UNIT1 FESTIVALS AND CELEBRATIONS", 3, 4),
        ("UNIT 2 MORALS AND VIRTUES", 5, 8),
        ("WORKBOOK Unit 1", 9, 10),
        ("WORKBOOK Unit 2", 11, 12),
        ("APPENDICES", 13, 13),
    ]
    assert chapters[2].boundary_evidence["pdf_page_offset"] == 2


def test_verified_vision_rescue_heading_needs_no_bbox_for_toc_evidence():
    chapters, _ = ChapterBoundaryDetector().detect(english_pages(verified=True))
    second = next(c for c in chapters if c.start_page == 5)
    assert second.title == "UNIT 2 MORALS AND VIRTUES"
    assert second.boundary_evidence["body_block_ids"] == ["p5-b0"]
    assert second.boundary_evidence["toc_block_ids"]


def test_inconsistent_printed_page_offsets_do_not_guess_workbook_subdivisions():
    chapters, _ = ChapterBoundaryDetector().detect(english_pages(second_start=6))
    assert not any(c.title.startswith("WORKBOOK Unit") for c in chapters)
    assert any(c.title == "WORKBOOK" for c in chapters)


def test_unverified_textline_title_without_toc_remains_honest_fallback():
    pages = [body(1, [("UNIT", (100, 80, 300, 120)), ("EXPERIMENTS", (350, 80, 900, 130))])]
    chapters, fallback = ChapterBoundaryDetector().detect(pages)
    assert fallback and chapters[0].title == "全书内容"


def test_ordinary_uniting_word_is_not_an_english_chapter_label():
    from adaptive_learning.ingestion.chaptering import _heading

    assert _heading("Uniting the examples") is None
    assert _heading("UNIT1 FESTIVALS") == ("chapter", "1", "UNIT1 FESTIVALS")


def test_verified_toc_keeps_skipped_workbook_start_without_borrowing_toc_source_ids():
    pages = english_pages()
    skipped = next(p for p in pages if p.page_number == 11)
    skipped.processing_status = "skipped"
    skipped.raw_text = ""
    skipped.blocks = []
    pages.append(body(12, [("本单元后续页面中实际可读的练习内容。", (100, 100, 800, 115))]))
    pages.sort(key=lambda p: p.page_number)
    chapters, _ = ChapterBoundaryDetector().detect(pages)
    workbook = next(c for c in chapters if c.title == "WORKBOOK Unit 2")
    assert (workbook.start_page, workbook.end_page) == (11, 12)
    assert workbook.boundary_evidence["method"] == "toc_subdivision_verified_offset_skipped_start"
    assert workbook.boundary_evidence["body_block_ids"] == []
    assert workbook.boundary_evidence["toc_block_ids"]
    assert workbook.source_block_ids and all(key.startswith("p12-") for key in workbook.source_block_ids)
    assert not set(workbook.boundary_evidence["toc_block_ids"]) & set(workbook.source_block_ids)
