from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings
from app.document.parsers.base import ParsedDocument, ParsedPage, ParserUnavailable, ParseRequest
from app.document.parsers.router import ParserRouter
from app.rag.retrieval import retrieve_chunks
from app.schemas.books import Chunk, ScanResult
from app.services.artifact_store import write_chunks


class UnavailableParser:
    def __init__(self, name: str) -> None:
        self.name = name

    def parse(self, request: ParseRequest) -> ParsedDocument:
        raise ParserUnavailable(self.name, "not configured")


class GoodParser:
    name = "pymupdf"

    def parse(self, request: ParseRequest) -> ParsedDocument:
        return ParsedDocument(
            book_id=request.book_id,
            parser_name=self.name,
            pages=[
                ParsedPage(
                    page_number=1,
                    text="Reliable native text from the PyMuPDF fallback parser.",
                    needs_ocr=False,
                    parser=self.name,
                    quality_score=1.0,
                )
            ],
            scan=request.scan,
            metadata={"ok": True},
        )


def test_parser_router_falls_back_to_available_parser(tmp_path: Path) -> None:
    scan = ScanResult(
        book_id="book_parser",
        filename="sample.pdf",
        file_type="pdf",
        page_count=1,
        has_text_layer=True,
        needs_ocr=False,
    )
    router = ParserRouter(
        parsers=[
            UnavailableParser("mineru"),
            UnavailableParser("marker"),
            GoodParser(),
            UnavailableParser("ocr"),
        ]
    )

    parsed = router.parse(
        ParseRequest(
            book_id="book_parser",
            file_path=tmp_path / "sample.pdf",
            artifact_path=tmp_path,
            scan=scan,
            preferred_parser="auto",
        )
    )

    assert parsed.parser_name == "pymupdf"
    assert parsed.metadata["parser_fallbacks"] == ["mineru:mineru_unavailable"]
    assert not any(attempt["parser"] == "marker" for attempt in parsed.metadata["parser_attempts"])


def test_hybrid_retrieval_requires_evidence_for_hashing_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("BOOKCOURSE_RAG_INDEX_PROVIDER", "pgvector")
    get_settings.cache_clear()

    write_chunks(
        "book_retrieval",
        [
            Chunk(
                chunk_id="chunk_meiosis",
                book_id="book_retrieval",
                chapter_id="c1",
                page_start=1,
                page_end=1,
                content_type="text",
                text="\u540c\u6e90\u67d3\u8272\u4f53\u5728\u51cf\u6570\u5206\u88c2\u7b2c\u4e00\u6b21\u5206\u88c2\u540e\u671f\u5206\u79bb\u3002",
                key_concepts=["\u540c\u6e90\u67d3\u8272\u4f53", "\u51cf\u6570\u5206\u88c2"],
            )
        ],
    )

    hit = retrieve_chunks("book_retrieval", "\u540c\u6e90\u67d3\u8272\u4f53\u4ec0\u4e48\u65f6\u5019\u5206\u79bb")
    miss = retrieve_chunks("book_retrieval", "Newton law outside this biology chapter")

    assert hit
    assert hit[0].bm25_score > 0
    assert hit[0].rerank_score > 0
    assert miss == []
