from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.document.parser_report import PageParserDecision, ParserReport
from app.document.parsers.base import ParsedDocument, ParsedPage
from app.document.pipeline import parse_document
from app.schemas.books import Chapter, Chunk, ScanResult
from app.services.artifact_store import RagBundleBuild


def test_parse_pipeline_reserves_early_and_publishes_index_before_ready(
    monkeypatch,
    tmp_path: Path,
) -> None:
    book_id = "stage4_pipeline_hook"
    events: list[str] = []
    scan = ScanResult(
        book_id=book_id,
        filename="source.pdf",
        file_type="pdf",
        page_count=1,
        has_text_layer=True,
        needs_ocr=False,
    )
    report = ParserReport(
        book_id=book_id,
        requested_parser="mineru",
        final_parser="mineru",
        source_page_count=1,
        output_page_count=1,
        pages=[PageParserDecision(page=1, parser="mineru", quality_score=0.95)],
    )
    parsed = ParsedDocument(
        book_id=book_id,
        parser_name="mineru",
        pages=[
            ParsedPage(
                page_number=1,
                text="selective membrane transport",
                needs_ocr=False,
                parser="mineru",
                quality_score=0.95,
            )
        ],
        scan=scan,
        metadata={
            "parser_report": report.model_dump(mode="json"),
            "mineru_backend": "pipeline",
        },
    )
    chapter = Chapter(
        chapter_id="chapter-1",
        level=1,
        source_title="Membrane",
        ai_title="Membrane",
        page_start=1,
        page_end=1,
        confidence=95,
        status="已确认",
        source="toc",
    )
    chunk = Chunk(
        chunk_id="chunk-1",
        book_id=book_id,
        chapter_id=chapter.chapter_id,
        page_start=1,
        page_end=1,
        content_type="paragraph",
        text="selective membrane transport",
        parser="mineru",
        chunk_version="v2",
        quality_score=0.95,
        token_count=4,
        metadata={"indexable": True, "quality_threshold": 0.45},
    )
    bundle = RagBundleBuild(book_id=book_id, build_id="build-stage4", assets=[], chunks=[])
    index_reservation = object()

    monkeypatch.setattr("app.document.pipeline.mark_rag_bundle_building", lambda _: (events.append("bundle_reserved"), bundle)[1])
    monkeypatch.setattr(
        "app.document.pipeline.reserve_index_build",
        lambda candidate, build_id, cause: (
            events.append("index_reserved"),
            index_reservation,
        )[1],
    )
    monkeypatch.setattr("app.document.pipeline.detect_document", lambda *_: (events.append("detected"), scan)[1])
    monkeypatch.setattr(
        "app.document.pipeline.get_parser_router",
        lambda: SimpleNamespace(parse=lambda _: parsed),
    )
    monkeypatch.setattr("app.document.pipeline.write_parsed_document_artifacts", lambda *_: None)
    monkeypatch.setattr("app.document.pipeline.write_parser_report", lambda *_: None)
    monkeypatch.setattr("app.document.pipeline.atomic_write_json", lambda *_: None)
    monkeypatch.setattr("app.document.pipeline.analyze_toc_structure", lambda *_: object())
    monkeypatch.setattr("app.document.pipeline.write_toc_analysis", lambda *_: None)
    monkeypatch.setattr("app.document.pipeline.recognize_chapters", lambda *_: [chapter])
    monkeypatch.setattr(
        "app.document.pipeline.write_chapters_for_build",
        lambda *_, **__: events.append("chapters_written"),
    )
    monkeypatch.setattr("app.document.pipeline.build_chunks", lambda *_args, **_kwargs: [chunk])
    monkeypatch.setattr("app.document.pipeline.bind_assets_to_chunks", lambda chunks, _assets: chunks)
    monkeypatch.setattr(
        "app.document.pipeline.write_assets_and_chunks",
        lambda *_, **__: events.append("artifacts_published"),
    )
    monkeypatch.setattr(
        "app.document.pipeline.publish_index_build",
        lambda reservation, chunks: events.append("index_ready"),
    )

    progress: list[str] = []
    result = parse_document(
        book_id,
        tmp_path / "source.pdf",
        tmp_path / "artifacts",
        on_progress=lambda stage, _value, _message: progress.append(stage),
    )

    assert result == scan
    assert events.index("index_reserved") < events.index("detected")
    assert events.index("artifacts_published") < events.index("index_ready")
    assert progress.index("rag_indexing") < progress.index("course_ready")
