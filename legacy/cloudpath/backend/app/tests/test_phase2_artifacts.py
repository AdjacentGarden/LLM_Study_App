from __future__ import annotations

from pathlib import Path

import fitz

from app.assets.source_figure_extractor import extract_source_figures
from app.document.chapter_recognizer import recognize_chapters
from app.document.chunker import build_chunks
from app.schemas.books import Chapter, PageResult


def test_biology_seed_chapters_for_scanned_demo(tmp_path: Path) -> None:
    pages = [
        PageResult(page=index + 1, pdf_page_index=index, text="", needs_ocr=True, blocks=[]).model_dump()
        for index in range(125)
    ]
    (tmp_path / "pages.json").write_text(__import__("json").dumps(pages), encoding="utf-8")
    chapters = recognize_chapters("book_test", "生物 必修2 遗传与进化.pdf", tmp_path, 125)
    assert any(chapter.chapter_id == "c2s1" for chapter in chapters)
    assert all(chapter.page_start <= chapter.page_end for chapter in chapters)


def test_chunker_excludes_ocr_pending_placeholder_from_rag(tmp_path: Path) -> None:
    pages = [PageResult(page=1, pdf_page_index=0, text="", needs_ocr=True, blocks=[]).model_dump()]
    (tmp_path / "pages.json").write_text(__import__("json").dumps(pages), encoding="utf-8")
    chapters = [
        Chapter(
            chapter_id="ch_001",
            level=1,
            source_title="整本文档",
            ai_title="课程：整本文档",
            page_start=1,
            page_end=1,
            confidence=20,
            status="需 OCR 复核",
            source="test",
        )
    ]
    chunks = build_chunks("book_test", tmp_path, chapters)
    assert chunks == []


def test_source_figure_extractor_crops_asset(tmp_path: Path) -> None:
    pdf = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(100, 120, 300, 260), color=(1, 0, 0), fill=(1, 0.9, 0.9))
    doc.save(pdf)
    doc.close()

    chapters = [
        Chapter(
            chapter_id="ch_001",
            level=1,
            source_title="测试章节",
            ai_title="课程：测试章节",
            page_start=1,
            page_end=1,
            confidence=90,
            status="匹配良好",
            source="test",
        )
    ]
    assets = extract_source_figures("book_test", pdf, tmp_path / "assets", chapters)
    assert assets
    assert assets[0].source_type == "extracted"
    assert assets[0].page == 1
    assert assets[0].bbox
    assert assets[0].source_page_image_url
    assert (tmp_path / "assets" / f"{assets[0].asset_id}.png").exists()
    assert (tmp_path / "assets" / f"thumb_{assets[0].asset_id}.png").exists()


def test_source_figure_extractor_extracts_embedded_image(tmp_path: Path) -> None:
    pdf = tmp_path / "embedded.pdf"
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 140, 140), 0)
    pix.clear_with(180)
    page.insert_image(fitz.Rect(80, 100, 220, 240), pixmap=pix)
    doc.save(pdf)
    doc.close()

    chapters = [
        Chapter(
            chapter_id="ch_001",
            level=1,
            source_title="含图章节",
            ai_title="课程：含图章节",
            page_start=1,
            page_end=1,
            confidence=90,
            status="匹配良好",
            source="test",
        )
    ]
    assets = extract_source_figures("book_embed", pdf, tmp_path / "assets_embed", chapters)
    embedded = [asset for asset in assets if asset.asset_id.startswith("img_")]
    assert embedded
    assert embedded[0].bbox
    assert embedded[0].thumbnail_url != embedded[0].image_url


def test_chunks_do_not_cross_chapter_boundaries(tmp_path: Path) -> None:
    pages = [
        {
            "page": 1,
            "pdf_page_index": 0,
            "text": "第一章内容",
            "needs_ocr": False,
            "blocks": [{"block_id": "p1_b001", "text": "第一章内容"}],
        },
        {
            "page": 2,
            "pdf_page_index": 1,
            "text": "第二章内容",
            "needs_ocr": False,
            "blocks": [{"block_id": "p2_b001", "text": "第二章内容"}],
        },
    ]
    (tmp_path / "pages.json").write_text(__import__("json").dumps(pages), encoding="utf-8")
    chapters = [
        Chapter(chapter_id="ch_001", level=1, source_title="第一章", ai_title="课程：第一章", page_start=1, page_end=1, confidence=90, status="匹配良好", source="test"),
        Chapter(chapter_id="ch_002", level=1, source_title="第二章", ai_title="课程：第二章", page_start=2, page_end=2, confidence=90, status="匹配良好", source="test"),
    ]
    chunks = build_chunks("book_test", tmp_path, chapters)
    assert all(chunk.page_start >= 1 and chunk.page_end <= 2 for chunk in chunks)
    assert all(
        (chunk.chapter_id == "ch_001" and chunk.page_start == 1) or (chunk.chapter_id == "ch_002" and chunk.page_start == 2)
        for chunk in chunks
    )
