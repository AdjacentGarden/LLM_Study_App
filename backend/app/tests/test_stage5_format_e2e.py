from __future__ import annotations

from io import BytesIO
from pathlib import Path
import shutil

from PIL import Image

from app.core.config import get_settings
from app.document.parser_report import PageParserDecision, ParserAttempt, ParserReport
from app.document.parsers.base import ParsedDocument, ParsedPage
from app.document.pipeline import parse_document
from app.rag.service import answer_query
from app.schemas.books import Chapter, QualityWarning, RagQuery, TextBlock
from app.services.storage import artifact_dir, original_file_path
from app.services.upload_validation import validate_saved_upload


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "quality" / "stage0" / "fixtures"


def _single_image(path: Path, image_format: str) -> Path:
    buffer = BytesIO()
    Image.new("RGB", (320, 180), color=(245, 248, 255)).save(buffer, format=image_format)
    path.write_bytes(buffer.getvalue())
    return path


class _SemanticFixtureRouter:
    """Deterministic parser boundary used to exercise the complete app pipeline.

    Real MinerU response mapping for Office is covered separately.  This
    fixture isolates format routing, normalized publication, Chunk V2,
    embedding/index publication, retrieval, and citation contracts.
    """

    def parse(self, request) -> ParsedDocument:
        pages: list[ParsedPage] = []
        for page_number in range(1, request.scan.page_count + 1):
            location = next(
                (
                    item
                    for item in request.scan.source_locations
                    if int(item.get("index", -1)) == page_number
                ),
                {},
            )
            source_metadata = {
                "source_format": request.scan.file_type,
                "source_unit": request.scan.source_unit,
                **location,
            }
            paragraph_metadata = dict(source_metadata)
            if request.scan.file_type == "docx":
                paragraph_metadata.update(
                    {"document_block_number": 2, "has_stable_page": False, "source_label": "块 2"}
                )
            blocks = [
                TextBlock(
                    block_id=f"p{page_number:03d}_heading",
                    page=page_number,
                    type="heading",
                    text=f"Stage 5 {request.scan.file_type.upper()} source",
                    heading_level=1,
                    source_parser="mineru",
                    metadata=source_metadata,
                ),
                TextBlock(
                    block_id=f"p{page_number:03d}_paragraph",
                    page=page_number,
                    type="paragraph",
                    text=(
                        "Stage five retrieval evidence explains mitochondrial ATP production, "
                        "cellular respiration, membrane gradients, and verifiable source semantics. "
                    )
                    * 3,
                    source_parser="mineru",
                    confidence=0.99,
                    metadata=paragraph_metadata,
                ),
            ]
            pages.append(
                ParsedPage(
                    page_number=page_number,
                    text="\n".join(block.text for block in blocks),
                    needs_ocr=False,
                    parser="mineru",
                    text_blocks=blocks,
                    quality_warnings=[],
                    quality_score=0.99,
                    metadata=source_metadata,
                )
            )

        report = ParserReport(
            book_id=request.book_id,
            requested_parser="mineru",
            final_parser="mineru",
            source_page_count=request.scan.page_count,
            output_page_count=len(pages),
            attempts=[
                ParserAttempt(
                    parser="mineru",
                    status="succeeded",
                    duration_ms=1,
                    quality_score=0.99,
                )
            ],
            pages=[
                PageParserDecision(page=page.page_number, parser="mineru", quality_score=0.99)
                for page in pages
            ],
            mineru_backend="fixture-contract",
            mineru_version="3.4.4",
        )
        return ParsedDocument(
            book_id=request.book_id,
            parser_name="mineru",
            pages=pages,
            scan=request.scan,
            metadata={
                "parser_report": report.model_dump(mode="json"),
                "mineru_backend": "fixture-contract",
                "mineru_version": "3.4.4",
            },
        )


def test_every_target_format_completes_parse_index_and_rag_query(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("BOOKCOURSE_DATABASE_URL", "")
    get_settings.cache_clear()

    single_gif = _single_image(tmp_path / "single.gif", "GIF")
    single_tiff = _single_image(tmp_path / "single.tiff", "TIFF")
    sources = {
        "pdf": FIXTURES / "native_text.pdf",
        "png": FIXTURES / "source_diagram.png",
        "jpg": FIXTURES / "sample.jpg",
        "jpeg": FIXTURES / "sample.jpeg",
        "jp2": FIXTURES / "sample.jp2",
        "webp": FIXTURES / "sample.webp",
        "gif": single_gif,
        "bmp": FIXTURES / "sample.bmp",
        "tif": FIXTURES / "sample.tif",
        "tiff": single_tiff,
        "docx": FIXTURES / "sample.docx",
        "pptx": FIXTURES / "sample.pptx",
        "xlsx": FIXTURES / "sample.xlsx",
    }
    router = _SemanticFixtureRouter()
    monkeypatch.setattr("app.document.pipeline.get_parser_router", lambda: router)
    monkeypatch.setattr(
        "app.document.pipeline.recognize_chapters",
        lambda book_id, filename, artifacts, page_count: [
            Chapter(
                chapter_id=f"chapter_{book_id}",
                level=1,
                source_title="Stage 5 retrieval evidence",
                ai_title="Stage 5 retrieval evidence",
                page_start=1,
                page_end=page_count,
                confidence=100,
                status="confirmed",
                source="stage5_e2e_fixture",
            )
        ],
    )

    results: dict[str, dict[str, object]] = {}
    for extension, source in sources.items():
        book_id = f"book_stage5_{extension}"
        destination = original_file_path(book_id, f"source.{extension}")
        shutil.copyfile(source, destination)
        validate_saved_upload(destination, destination.name)

        scan = parse_document(book_id, destination, artifact_dir(book_id))
        response = answer_query(
            RagQuery(book_id=book_id, question="How does mitochondrial ATP production work?")
        )
        assert response.citations, extension
        citation = response.citations[0]
        results[extension] = {
            "unit": scan.source_unit,
            "location_type": citation.location_type,
            "location_label": citation.location_label,
        }

    assert set(results) == set(sources)
    assert results["pptx"]["location_type"] == "slide"
    assert "幻灯片" in str(results["pptx"]["location_label"])
    assert results["xlsx"]["location_type"] == "sheet"
    assert "工作表" in str(results["xlsx"]["location_label"])
    assert results["docx"]["location_type"] == "document"
    assert "页" not in str(results["docx"]["location_label"])
