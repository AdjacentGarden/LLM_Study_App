from __future__ import annotations

import json
from pathlib import Path
import shutil
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.document.detector import detect_document
from app.document.mineru.mapper import map_mineru_document
from app.document.mineru.models import MinerUResultDocument
from app.document.office_preview import render_office_preview
from app.document.page_artifacts import write_parsed_document_artifacts
from app.document.parsers.base import ParseRequest, ParsedDocument, ParserUnavailable, parsed_page_from_page_result
from app.document.parsers.mineru_parser import _annotate_source_semantics
from app.document.parsers.router import ParserRouter
from app.document.chunker import build_chunks
from app.main import app
from app.rag.service import _citation_location
from app.schemas.books import Asset, Chapter, ScanResult
from app.services.storage import artifact_dir, original_file_path


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "quality" / "stage0" / "fixtures"
MINERU_RESULTS = ROOT / "quality" / "stage0" / "mineru_probe"


def _result_document(extension: str) -> MinerUResultDocument:
    payload = json.loads((MINERU_RESULTS / f"sample_{extension}_result.json").read_text(encoding="utf-8"))
    return MinerUResultDocument.model_validate(payload["results"]["sample"])


def _mapped_office(tmp_path: Path, extension: str):
    source = FIXTURES / f"sample.{extension}"
    scan = detect_document(f"book_{extension}", source)
    request = ParseRequest(
        book_id=f"book_{extension}",
        file_path=source,
        artifact_path=tmp_path / extension / "artifacts",
        scan=scan,
        preferred_parser="mineru",
    )
    mapped = map_mineru_document(
        request.book_id,
        _result_document(extension),
        backend="pipeline",
        version="3.4.4",
        asset_root=tmp_path / extension / "assets",
        expected_page_count=scan.page_count,
    )
    return request, _annotate_source_semantics(request, mapped)


def _office_chunks(tmp_path: Path, extension: str):
    request, mapped = _mapped_office(tmp_path, extension)
    pages = [parsed_page_from_page_result(page, "mineru") for page in mapped.pages]
    document = ParsedDocument(
        book_id=request.book_id,
        parser_name="mineru",
        pages=pages,
        scan=request.scan,
        metadata={"mineru_version": "3.4.4"},
        assets=mapped.assets,
    )
    request.artifact_path.mkdir(parents=True, exist_ok=True)
    write_parsed_document_artifacts(document, request.artifact_path)
    chapters = [
        Chapter(
            chapter_id=f"chapter_{extension}",
            level=1,
            source_title=f"{extension.upper()} source",
            ai_title=f"{extension.upper()} source",
            page_start=1,
            page_end=request.scan.page_count,
            confidence=100,
            status="confirmed",
            source="stage5_test",
        )
    ]
    return request, build_chunks(request.book_id, request.artifact_path, chapters, assets=mapped.assets)


def test_office_detector_uses_frozen_location_semantics() -> None:
    docx = detect_document("book_docx", FIXTURES / "sample.docx")
    pptx = detect_document("book_pptx", FIXTURES / "sample.pptx")
    xlsx = detect_document("book_xlsx", FIXTURES / "sample.xlsx")

    assert (docx.source_unit, docx.page_count) == ("document", 1)
    assert docx.source_locations[0]["has_stable_page"] is False
    assert "标题路径" in str(docx.source_locations[0]["label"])

    assert (pptx.source_unit, pptx.page_count) == ("slide", 2)
    assert [item["slide_number"] for item in pptx.source_locations] == [1, 2]
    assert pptx.source_locations[0]["slide_title"] == "Cellular respiration"

    assert (xlsx.source_unit, xlsx.page_count) == ("sheet", 2)
    assert [item["sheet_name"] for item in xlsx.source_locations] == ["Growth Data", "Notes"]
    assert xlsx.source_locations[0]["cell_range"] == "A1:D5"


def test_mineru_mapping_and_chunker_preserve_office_locations(tmp_path: Path) -> None:
    _, pptx_chunks = _office_chunks(tmp_path, "pptx")
    _, xlsx_chunks = _office_chunks(tmp_path, "xlsx")
    _, docx_chunks = _office_chunks(tmp_path, "docx")

    assert pptx_chunks
    assert {chunk.metadata.get("slide_number") for chunk in pptx_chunks} == {2}
    assert all(chunk.metadata.get("source_format") == "pptx" for chunk in pptx_chunks)

    assert xlsx_chunks
    assert {chunk.metadata.get("sheet_name") for chunk in xlsx_chunks} == {"Growth Data", "Notes"}
    assert all(chunk.metadata.get("cell_range") for chunk in xlsx_chunks)

    assert docx_chunks
    assert all(chunk.metadata.get("has_stable_page") is False for chunk in docx_chunks)
    assert all(chunk.metadata.get("document_block_start") is not None for chunk in docx_chunks)
    assert all(chunk.heading_path for chunk in docx_chunks)


def test_citation_labels_never_call_office_locations_pdf_pages(tmp_path: Path) -> None:
    expected = {
        "pptx": ("slide", "幻灯片"),
        "xlsx": ("sheet", "工作表"),
        "docx": ("document", "块"),
    }
    for extension, (location_type, marker) in expected.items():
        _, chunks = _office_chunks(tmp_path, extension)
        actual_type, label = _citation_location(SimpleNamespace(chunk=chunks[0]))
        assert actual_type == location_type
        assert marker in label
        if extension == "docx":
            assert "页" not in label


def test_office_asset_uses_preview_endpoint_not_pdf_renderer(tmp_path: Path) -> None:
    request, mapped = _mapped_office(tmp_path, "pptx")
    source_asset = Asset(
        asset_id="pptx_asset",
        book_id=request.book_id,
        source_type="extracted",
        page=2,
        type="figure",
        caption="Mitochondrial diagram",
        image_url=f"/api/books/{request.book_id}/assets/pptx_asset/file",
        thumbnail_url=f"/api/books/{request.book_id}/assets/pptx_asset/thumbnail",
    )
    annotated = _annotate_source_semantics(request, mapped.model_copy(update={"assets": [source_asset]}))
    assert annotated.assets[0].source_page_image_url == f"/api/books/{request.book_id}/pages/2/image"
    assert annotated.assets[0].metadata["slide_number"] == 2


def test_office_preview_is_safe_text_svg(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path / "storage"))
    get_settings.cache_clear()
    book_id = "book_preview"
    target = original_file_path(book_id, "sample.pptx")
    shutil.copyfile(FIXTURES / "sample.pptx", target)
    root = artifact_dir(book_id)
    scan = detect_document(book_id, target)
    (root / "scan_result.json").write_text(scan.model_dump_json(indent=2), encoding="utf-8")
    (root / "pages.json").write_text(
        json.dumps(
            [
                {"page": 1, "text": '<script>alert("x")</script>\u0000 Cellular respiration'},
                {"page": 2, "text": "ATP production and mitochondrial evidence"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    preview = render_office_preview(root, 1)
    preview_text = preview.read_text(encoding="utf-8")
    assert "<script>" not in preview_text
    assert "&lt;script&gt;" in preview_text
    assert "\u0000" not in preview_text

    response = TestClient(app).get(f"/api/books/{book_id}/pages/1/image")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert b"PPTX" in response.content


def test_office_parser_failure_does_not_enter_pdf_or_ocr_fallback(tmp_path: Path) -> None:
    calls: list[str] = []

    class FailedMinerU:
        name = "mineru"

        def parse(self, request: ParseRequest):
            calls.append(self.name)
            raise ParserUnavailable(self.name, "offline")

    class ForbiddenFallback:
        def __init__(self, name: str) -> None:
            self.name = name

        def parse(self, request: ParseRequest):
            calls.append(self.name)
            raise AssertionError(f"{self.name} must not parse Office inputs")

    scan = ScanResult(
        book_id="book_office_failure",
        filename="source.pptx",
        file_type="pptx",
        page_count=2,
        has_text_layer=True,
        needs_ocr=False,
        source_unit="slide",
    )
    router = ParserRouter([FailedMinerU(), ForbiddenFallback("pymupdf"), ForbiddenFallback("ocr")])
    result = router.parse(
        ParseRequest(
            book_id=scan.book_id,
            file_path=tmp_path / scan.filename,
            artifact_path=tmp_path / "artifacts",
            scan=scan,
            preferred_parser="mineru",
        )
    )

    assert calls == ["mineru"]
    assert all(page.parser == "unrecoverable" for page in result.pages)
    assert result.metadata["parser_report"]["final_parser"] == "unrecoverable"
