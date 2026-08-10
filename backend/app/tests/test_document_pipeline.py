from __future__ import annotations

from pathlib import Path

import fitz

from app.document.detector import detect_document, inspect_pdf_text_layers
from app.document.pdf_extractor import extract_pages


def create_sample_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "第 1 章 遗传因子的发现\n减数分裂是重要过程。", fontsize=12, fontname="china-s")
    doc.save(path)
    doc.close()


def test_detect_pdf_text_layer(tmp_path: Path) -> None:
    pdf = tmp_path / "sample.pdf"
    create_sample_pdf(pdf)
    result = detect_document("book_test", pdf)
    assert result.page_count == 1
    assert result.has_text_layer is True
    assert result.needs_ocr is False


def test_detect_pdf_text_layer_inspects_every_page(tmp_path: Path) -> None:
    pdf = tmp_path / "mixed.pdf"
    doc = fitz.open()
    for index in range(7):
        page = doc.new_page()
        if index != 5:
            page.insert_text((72, 72), f"page {index + 1}")
    doc.save(pdf)
    doc.close()

    page_layers = inspect_pdf_text_layers(pdf)
    result = detect_document("book_mixed", pdf)

    assert len(page_layers) == 7
    assert [page.page for page in page_layers if not page.has_text_layer] == [6]
    assert result.has_text_layer is True
    assert result.needs_ocr is True
    assert [
        warning.page
        for warning in result.quality_warnings
        if warning.code == "missing_text_layer"
    ] == [6]


def test_extract_pages_keeps_chinese_text(tmp_path: Path) -> None:
    pdf = tmp_path / "sample.pdf"
    create_sample_pdf(pdf)
    pages = extract_pages(pdf)
    assert len(pages) == 1
    assert "减数分裂" in pages[0].text
    assert pages[0].blocks
