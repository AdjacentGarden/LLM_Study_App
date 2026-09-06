from __future__ import annotations

import json
from pathlib import Path

from app.document.toc_analyzer import analyze_toc_structure, chapters_from_toc_analysis


def _block(page: int, text: str, bbox: list[float]) -> dict:
    return {
        "block_id": f"p{page}_{abs(hash(text))}",
        "page": page,
        "type": "paragraph",
        "text": text,
        "bbox": bbox,
        "font_size": 16 if "章" in text or "节" in text else 10,
        "confidence": 0.92,
        "low_confidence": False,
    }


def test_toc_analysis_maps_printed_pages_and_verifies_titles(tmp_path: Path) -> None:
    pages = [
        {
            "page": 1,
            "pdf_page_index": 0,
            "text": "目录\n第 1 章 遗传因子的发现 …… 3\n第 1 节 孟德尔的豌豆杂交实验 …… 4\n第 2 章 基因和染色体的关系 …… 6",
            "needs_ocr": False,
            "blocks": [],
        },
        {"page": 2, "pdf_page_index": 1, "text": "前言", "needs_ocr": False, "blocks": []},
        {
            "page": 3,
            "pdf_page_index": 2,
            "text": "第 1 章 遗传因子的发现",
            "needs_ocr": False,
            "blocks": [_block(3, "第 1 章 遗传因子的发现", [40, 50, 360, 90]), _block(3, "3", [190, 760, 210, 780])],
        },
        {
            "page": 4,
            "pdf_page_index": 3,
            "text": "第 1 节 孟德尔的豌豆杂交实验",
            "needs_ocr": False,
            "blocks": [_block(4, "第 1 节 孟德尔的豌豆杂交实验", [40, 55, 420, 96]), _block(4, "4", [190, 760, 210, 780])],
        },
        {"page": 5, "pdf_page_index": 4, "text": "正文", "needs_ocr": False, "blocks": [_block(5, "5", [190, 760, 210, 780])]},
        {
            "page": 6,
            "pdf_page_index": 5,
            "text": "第 2 章 基因和染色体的关系",
            "needs_ocr": False,
            "blocks": [_block(6, "第 2 章 基因和染色体的关系", [40, 50, 420, 90]), _block(6, "6", [190, 760, 210, 780])],
        },
    ]
    (tmp_path / "pages.json").write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")

    analysis = analyze_toc_structure("book_toc", tmp_path, page_count=6)
    chapters = chapters_from_toc_analysis("book_toc", analysis)

    assert analysis.status == "ready"
    assert analysis.toc_pages[0].page == 1
    assert analysis.page_map[2].printed_page == 3
    assert chapters[0].source_title.startswith("第 1 章")
    assert chapters[0].page_start == 3
    assert chapters[0].page_end == 5
    assert chapters[1].level == 2
    assert chapters[1].parent_id == chapters[0].chapter_id
    assert chapters[1].confidence >= 80
