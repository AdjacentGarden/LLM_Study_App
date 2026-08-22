from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.document.mineru.exceptions import MinerUStaleResultError
from app.document.page_artifacts import atomic_write_json, write_parsed_document_artifacts
from app.document.pipeline import DocumentParseQualityError, _document_quality_gate_fails, parse_document
from app.document.parsers.base import ParsedDocument, ParsedPage, ParserUnavailable, ParseRequest
from app.document.parsers.router import ParserRouter
from app.schemas.books import ScanResult, TextBlock


class Unavailable:
    def __init__(self, name: str, detail: str = "not available") -> None:
        self.name = name
        self.detail = detail

    def parse(self, request: ParseRequest) -> ParsedDocument:
        raise ParserUnavailable(self.name, self.detail)


def test_quality_gate_accepts_sparse_degraded_pages_in_a_high_quality_long_book() -> None:
    assert not _document_quality_gate_fails(
        page_count=292,
        document_quality=0.966,
        degraded_pages={3, 53, 80, 81, 176, 221, 224, 230, 267, 273},
        missing_pages=[],
    )


@pytest.mark.parametrize(
    ("page_count", "quality", "degraded"),
    [(20, 0.95, {1}), (100, 0.95, set(range(1, 7))), (292, 0.74, {3})],
)
def test_quality_gate_still_rejects_short_or_broadly_degraded_documents(
    page_count: int,
    quality: float,
    degraded: set[int],
) -> None:
    assert _document_quality_gate_fails(
        page_count=page_count,
        document_quality=quality,
        degraded_pages=degraded,
        missing_pages=[],
    )


def _scan() -> ScanResult:
    return ScanResult(
        book_id="book_report",
        filename="source.pdf",
        file_type="pdf",
        page_count=1,
        has_text_layer=False,
        needs_ocr=True,
    )


def test_full_parser_failure_still_publishes_sanitized_report(monkeypatch, tmp_path: Path) -> None:
    scan = _scan()
    router = ParserRouter(
        [
            Unavailable("mineru", r"upstream failed at D:\secret\lesson.pdf with private body"),
            Unavailable("marker"),
            Unavailable("pymupdf"),
            Unavailable("ocr"),
        ]
    )
    monkeypatch.setattr("app.document.pipeline.detect_document", lambda *_: scan)
    monkeypatch.setattr("app.document.pipeline.get_parser_router", lambda: router)
    monkeypatch.setenv("BOOKCOURSE_PARSER_PROVIDER", "mineru")
    get_settings.cache_clear()

    with pytest.raises(DocumentParseQualityError):
        parse_document("book_report", tmp_path / "source.pdf", tmp_path / "artifacts")

    report_text = (tmp_path / "artifacts" / "parser_report.json").read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["final_parser"] == "unrecoverable"
    assert report["missing_pages"] == []  # an explicit unrecoverable page preserves source coverage
    assert report["pages"][0]["status"] == "unrecoverable"
    assert [attempt["parser"] for attempt in report["attempts"]] == ["mineru", "pymupdf", "ocr"]
    assert "D:\\secret" not in report_text
    assert "private body" not in report_text
    get_settings.cache_clear()


def test_unified_page_writer_preserves_parser_quality_and_structure(tmp_path: Path) -> None:
    block = TextBlock(
        block_id="stable_block",
        page=1,
        type="table",
        text="Header | Value\nATP | 36",
        bbox=[10, 20, 200, 120],
        source_parser="mineru",
        content_format="html",
        metadata={"table_body": "<table><tr><td>ATP</td></tr></table>"},
    )
    document = ParsedDocument(
        book_id="book_write",
        parser_name="mineru",
        pages=[
            ParsedPage(
                page_number=1,
                text=block.text,
                needs_ocr=False,
                parser="mineru",
                text_blocks=[block],
                quality_score=0.91,
            )
        ],
        scan=_scan().model_copy(update={"book_id": "book_write"}),
        metadata={"mineru_middle_json": {"pdf_info": []}, "mineru_content_list": [{"type": "table"}]},
    )

    write_parsed_document_artifacts(document, tmp_path)

    pages = json.loads((tmp_path / "pages.json").read_text(encoding="utf-8"))
    assert pages[0]["parser"] == "mineru"
    assert pages[0]["quality_score"] == 0.91
    assert pages[0]["blocks"][0]["metadata"]["table_body"].startswith("<table>")
    assert (tmp_path / "mineru_middle.json").exists()
    assert (tmp_path / "mineru_content_list.json").exists()


def test_generation_guard_never_replaces_current_artifact(tmp_path: Path) -> None:
    target = tmp_path / "parser_report.json"
    target.write_text('{"generation":2}', encoding="utf-8")

    with pytest.raises(MinerUStaleResultError):
        atomic_write_json(target, {"generation": 1}, generation_guard=lambda: False)

    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 2}
    assert not list(tmp_path.glob("*.tmp"))
