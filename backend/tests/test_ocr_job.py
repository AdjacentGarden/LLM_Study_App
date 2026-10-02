import json
import os
from pathlib import Path

from adaptive_learning.ingestion.ocr_job import (
    evaluate_page,
    infer_log_duration,
    load_pages,
    mineru_subprocess_environment,
    rapidocr_blocks,
)


def test_load_pages_preserves_page_boundaries_and_blocks(tmp_path: Path) -> None:
    source = tmp_path / "book_content_list.json"
    source.write_text(
        json.dumps(
            [
                {"page_idx": 0, "type": "title", "text": "第一章 基础"},
                {"page_idx": 0, "type": "text", "text": "这是清晰、连续的教材正文。"},
                {"page_idx": 2, "type": "table", "table_body": "| A | B |"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    pages = load_pages(source, expected_pages=3)

    assert len(pages) == 3
    assert len(pages[0]["blocks"]) == 2
    assert pages[1]["blocks"] == []
    assert "A" in pages[2]["text"]


def test_evaluate_page_flags_missing_output() -> None:
    report = evaluate_page({"page_number": 4, "blocks": [], "text": ""})

    assert report["band"] == "missing"
    assert report["heuristic_score"] < 0.2


def test_load_pages_quarantines_foreign_generation_runaway(tmp_path: Path) -> None:
    source = tmp_path / "book_content_list.json"
    source.write_text(
        json.dumps(
            [
                {"page_idx": 0, "type": "title", "text": "第三章 基因的本质"},
                {"page_idx": 0, "type": "text", "text": "这是应保留的中文正文。"},
                {"page_idx": 0, "type": "text", "text": "small, " * 2_000},
                {"page_idx": 0, "type": "text", "text": "invented English background " * 80},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    page = load_pages(source, expected_pages=1)[0]

    assert "应保留" in page["text"]
    assert "invented English" not in page["text"]
    assert len(page["discarded_blocks"]) == 2


def test_infer_log_duration_uses_first_and_last_timestamps(tmp_path: Path) -> None:
    log = tmp_path / "mineru.log"
    log.write_text(
        "2026-08-29 12:35:44.923 | start\n"
        "progress without timestamp\n"
        "2026-08-29 12:38:39.676 | completed\n",
        encoding="utf-8",
    )

    assert infer_log_duration(log) == 174.753


def test_load_pages_removes_vlm_refusal_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "book_content_list.json"
    source.write_text(
        json.dumps(
            [
                {"page_idx": 0, "type": "text", "text": "可核验的教材正文。"},
                {
                    "page_idx": 0,
                    "type": "header",
                    "text": "The image is too blurry to recognize any text content.",
                },
                {"page_idx": 0, "type": "header", "text": "No"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    page = load_pages(source, expected_pages=1)[0]

    assert page["text"] == "可核验的教材正文。"
    assert len(page["discarded_blocks"]) == 2


def test_mineru_environment_bypasses_proxy_for_loopback(monkeypatch) -> None:
    monkeypatch.setenv("NO_PROXY", "internal.example")
    monkeypatch.setenv("no_proxy", "stale.example")

    # Windows environment names are case-insensitive; preserve the effective
    # configured value instead of assuming two distinct NO_PROXY variables.
    configured_host = os.environ["NO_PROXY"]
    environment = mineru_subprocess_environment()

    assert environment["NO_PROXY"] == environment["no_proxy"]
    entries = set(environment["NO_PROXY"].split(","))
    assert {configured_host, "localhost", "127.0.0.1", "::1"} <= entries


def test_rapidocr_blocks_preserve_lines_and_mark_large_chapter_headings() -> None:
    blocks = rapidocr_blocks(
        page_index=4,
        page_height=1600,
        texts=["第九章", "静电场及其应用", "这是教材正文。"],
        boxes=[
            [[300, 120], [520, 120], [520, 190], [300, 190]],
            [[300, 220], [720, 220], [720, 285], [300, 285]],
            [[120, 500], [900, 500], [900, 530], [120, 530]],
        ],
        scores=[0.99, 0.98, 0.97],
    )

    assert [block["page_idx"] for block in blocks] == [4, 4, 4]
    assert blocks[0]["type"] == "title"
    assert blocks[0]["text_level"] == 1
    assert blocks[1]["type"] == "title"
    assert blocks[2]["type"] == "text"


def test_rapidocr_blocks_preserve_empty_visual_page_boundary() -> None:
    blocks = rapidocr_blocks(
        page_index=2, page_height=1600, texts=[], boxes=[], scores=[]
    )

    assert blocks == [
        {
            "page_idx": 2,
            "type": "image",
            "text": "",
            "bbox": None,
            "score": None,
            "text_level": None,
        }
    ]
