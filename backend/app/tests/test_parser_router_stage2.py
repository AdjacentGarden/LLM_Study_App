from __future__ import annotations

from pathlib import Path

import pytest

from app.document.mineru.exceptions import MinerUStaleResultError
from app.document.parsers.base import ParsedDocument, ParsedPage, ParserUnavailable, ParseRequest
from app.document.parsers.router import ParserRouter
from app.schemas.books import ScanResult, TextBlock


def _page(page: int, parser: str, text: str, *, quality: float = 1.0, needs_ocr: bool = False) -> ParsedPage:
    blocks = []
    if text:
        blocks = [
            TextBlock(
                block_id=f"{parser}_p{page}_b1",
                page=page,
                type="paragraph",
                text=text,
                bbox=[0, 0, 100, 20],
                source_parser=parser,
            )
        ]
    return ParsedPage(
        page_number=page,
        text=text,
        needs_ocr=needs_ocr,
        parser=parser,
        text_blocks=blocks,
        quality_score=quality,
        ocr_provider="paddleocr" if parser == "ocr" else None,
    )


class FixedParser:
    def __init__(self, name: str, pages: list[ParsedPage] | None = None, error: Exception | None = None) -> None:
        self.name = name
        self.pages = pages or []
        self.error = error
        self.calls = 0

    def parse(self, request: ParseRequest) -> ParsedDocument:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ParsedDocument(
            book_id=request.book_id,
            parser_name=self.name,
            pages=self.pages,
            scan=request.scan,
        )


def _request(tmp_path: Path, *, file_type: str = "pdf", pages: int = 2, preferred: str = "mineru") -> ParseRequest:
    return ParseRequest(
        book_id="book_router",
        file_path=tmp_path / f"source.{file_type}",
        artifact_path=tmp_path,
        scan=ScanResult(
            book_id="book_router",
            filename=f"source.{file_type}",
            file_type=file_type,
            page_count=pages,
            has_text_layer=file_type == "pdf",
            needs_ocr=False,
        ),
        preferred_parser=preferred,
    )


def test_mineru_is_first_and_only_when_all_pages_pass(tmp_path: Path) -> None:
    mineru = FixedParser("mineru", [_page(1, "mineru", "MinerU semantic content on the first page."), _page(2, "mineru", "MinerU semantic content on the second page.")])
    pymupdf = FixedParser("pymupdf")
    ocr = FixedParser("ocr")
    marker = FixedParser("marker")

    parsed = ParserRouter([mineru, marker, pymupdf, ocr]).parse(_request(tmp_path))

    assert parsed.parser_name == "mineru"
    assert mineru.calls == 1
    assert pymupdf.calls == ocr.calls == marker.calls == 0
    assert [page.parser for page in parsed.pages] == ["mineru", "mineru"]


def test_mixed_pdf_replaces_only_bad_mineru_page_with_pymupdf(tmp_path: Path) -> None:
    mineru = FixedParser(
        "mineru",
        [
            _page(1, "mineru", "Good MinerU text remains authoritative on page one."),
            _page(2, "mineru", "", quality=0.1, needs_ocr=True),
        ],
    )
    pymupdf = FixedParser(
        "pymupdf",
        [
            _page(1, "pymupdf", "This page must not replace the accepted MinerU page."),
            _page(2, "pymupdf", "Reliable native text layer recovered only for page two."),
        ],
    )
    ocr = FixedParser("ocr")
    marker = FixedParser("marker")

    parsed = ParserRouter([mineru, marker, pymupdf, ocr]).parse(_request(tmp_path))

    assert parsed.parser_name == "mixed"
    assert [page.parser for page in parsed.pages] == ["mineru", "pymupdf"]
    assert "authoritative" in parsed.pages[0].text
    assert ocr.calls == marker.calls == 0
    assert parsed.metadata["parser_report"]["attempts"][1]["pages"] == [2]


def test_scanned_pdf_uses_paddle_after_pymupdf_has_no_semantics(tmp_path: Path) -> None:
    mineru = FixedParser("mineru", [_page(1, "mineru", "", quality=0.0, needs_ocr=True)])
    pymupdf = FixedParser("pymupdf", [_page(1, "pymupdf", "", quality=0.0, needs_ocr=True)])
    ocr = FixedParser("ocr", [_page(1, "ocr", "PaddleOCR recovered the scanned lesson text reliably.", quality=0.94)])
    marker = FixedParser("marker")

    parsed = ParserRouter([mineru, marker, pymupdf, ocr]).parse(_request(tmp_path, pages=1))

    assert parsed.parser_name == "ocr"
    assert [attempt["parser"] for attempt in parsed.metadata["parser_attempts"]] == ["mineru", "pymupdf", "ocr"]
    assert marker.calls == 0


def test_explicit_pymupdf_still_uses_ocr_for_scanned_pages(tmp_path: Path) -> None:
    mineru = FixedParser("mineru")
    pymupdf = FixedParser("pymupdf", [_page(1, "pymupdf", "", quality=0.0, needs_ocr=True)])
    ocr = FixedParser("ocr", [_page(1, "ocr", "PaddleOCR recovered this scanned textbook page.", quality=0.93)])
    marker = FixedParser("marker")

    parsed = ParserRouter([mineru, marker, pymupdf, ocr]).parse(
        _request(tmp_path, pages=1, preferred="pymupdf")
    )

    assert parsed.parser_name == "ocr"
    assert [attempt["parser"] for attempt in parsed.metadata["parser_attempts"]] == ["pymupdf", "ocr"]
    assert mineru.calls == marker.calls == 0
    assert pymupdf.calls == ocr.calls == 1


@pytest.mark.parametrize("file_type", ["png", "jpg", "jpeg", "webp"])
def test_image_is_mineru_first_then_directly_paddle(file_type: str, tmp_path: Path) -> None:
    mineru = FixedParser("mineru", [_page(1, "mineru", "", quality=0.0, needs_ocr=True)])
    pymupdf = FixedParser("pymupdf")
    ocr = FixedParser("ocr", [_page(1, "ocr", "PaddleOCR recovered image-based source text.", quality=0.92)])
    marker = FixedParser("marker")

    parsed = ParserRouter([mineru, marker, pymupdf, ocr]).parse(
        _request(tmp_path, file_type=file_type, pages=1)
    )

    assert parsed.parser_name == "ocr"
    assert mineru.calls == ocr.calls == 1
    assert pymupdf.calls == marker.calls == 0


def test_marker_is_only_used_when_explicit(tmp_path: Path) -> None:
    mineru = FixedParser("mineru", error=ParserUnavailable("mineru", "offline"))
    marker = FixedParser("marker", [_page(1, "marker", "Explicit Marker parser output is accepted here.")])
    pymupdf = FixedParser("pymupdf", [_page(1, "pymupdf", "Fallback text that should not be used.")])
    ocr = FixedParser("ocr")

    parsed = ParserRouter([mineru, marker, pymupdf, ocr]).parse(
        _request(tmp_path, pages=1, preferred="marker")
    )

    assert parsed.parser_name == "marker"
    assert marker.calls == 1
    assert mineru.calls == pymupdf.calls == ocr.calls == 0


def test_stale_generation_never_enters_fallback(tmp_path: Path) -> None:
    mineru = FixedParser("mineru", error=MinerUStaleResultError("late result"))
    pymupdf = FixedParser("pymupdf", [_page(1, "pymupdf", "A fallback must never run for a stale worker.")])
    ocr = FixedParser("ocr")
    marker = FixedParser("marker")

    with pytest.raises(MinerUStaleResultError):
        ParserRouter([mineru, marker, pymupdf, ocr]).parse(_request(tmp_path, pages=1))

    assert pymupdf.calls == ocr.calls == marker.calls == 0

