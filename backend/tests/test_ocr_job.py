import json
from pathlib import Path

from adaptive_learning.ingestion.ocr_job import (
    evaluate_page,
    infer_log_duration,
    load_pages,
    mineru_subprocess_environment,
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

    environment = mineru_subprocess_environment()

    assert environment["NO_PROXY"] == environment["no_proxy"]
    entries = set(environment["NO_PROXY"].split(","))
    assert {"internal.example", "localhost", "127.0.0.1", "::1"} <= entries
