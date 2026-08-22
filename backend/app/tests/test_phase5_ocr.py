from __future__ import annotations

from pathlib import Path
import json
import threading

import cv2  # type: ignore
import fitz
import numpy as np
import pytest
from PIL import Image

from app.core.config import get_settings
from app.document.detector import detect_document
from app.document.image_preprocessor import assess_image_quality, preprocess_image
from app.document.layout import get_layout_service
from app.document.ocr import (
    MockOCRAdapter,
    OCRUnavailable,
    PaddleOCRAdapter,
    PaddleOCRVLAdapter,
    _paddle_adapter,
    _paddle_vl_adapter,
    _paddle_vl_blocks,
    _paddle_v3_lines,
    _restore_visual_word_spacing,
    get_ocr_adapter,
)
from app.document.ocr_pipeline import extract_ocr_pages, write_ocr_page_artifacts
from app.document.pipeline import DocumentParseQualityError, parse_document
from app.schemas.books import ScanResult, TextBlock


def _write_test_image(path: Path, dark: bool = False) -> None:
    image = np.zeros((160, 220), dtype=np.uint8) if dark else np.full((160, 220), 245, dtype=np.uint8)
    if not dark:
        cv2.putText(image, "BookCourse", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, 0, 2)
    cv2.imwrite(str(path), image)


def test_image_quality_and_preprocess(tmp_path: Path) -> None:
    image_path = tmp_path / "dark.png"
    output_path = tmp_path / "processed.png"
    _write_test_image(image_path, dark=True)

    warnings = assess_image_quality(image_path, page=1)
    preprocess_warnings = preprocess_image(image_path, output_path, page=1)

    assert any(warning.code == "too_dark" for warning in warnings)
    assert any(warning.code == "too_dark" for warning in preprocess_warnings)
    assert output_path.exists()


def test_mock_ocr_writes_blocks_and_confidence(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOOKCOURSE_OCR_PROVIDER", "mock")
    get_settings.cache_clear()
    image_path = tmp_path / "page.png"
    artifact_path = tmp_path / "artifacts"
    _write_test_image(image_path)
    scan = ScanResult(
        book_id="book_ocr",
        filename=image_path.name,
        file_type="png",
        page_count=1,
        has_text_layer=False,
        needs_ocr=True,
    )

    result = write_ocr_page_artifacts("book_ocr", image_path, artifact_path, scan)
    pages = json.loads((artifact_path / "pages.json").read_text(encoding="utf-8"))
    blocks = (artifact_path / "text_blocks.jsonl").read_text(encoding="utf-8").splitlines()
    layout_regions = (artifact_path / "layout_regions.jsonl").read_text(encoding="utf-8").splitlines()

    assert result.needs_ocr is True
    assert pages[0]["ocr_provider"] == "mock"
    assert pages[0]["blocks"][0]["confidence"] == 0.01
    assert pages[0]["blocks"][0]["low_confidence"] is True
    assert pages[0]["layout_regions"]
    assert pages[0]["preprocessed_image_url"].endswith("/preprocessed/page_001.png")
    assert blocks
    assert layout_regions
    get_settings.cache_clear()


def test_layout_service_detects_regions(tmp_path: Path) -> None:
    image_path = tmp_path / "layout.png"
    _write_test_image(image_path)

    regions = get_layout_service().detect_regions(image_path, page=1)

    assert regions
    assert regions[0].bbox


@pytest.mark.parametrize("provider", ["paddle", "paddleocr"])
def test_paddleocr_provider_fails_closed(monkeypatch, provider: str) -> None:
    monkeypatch.setenv("BOOKCOURSE_OCR_PROVIDER", provider)
    get_settings.cache_clear()
    _paddle_adapter.cache_clear()

    def fail_init(self) -> None:
        raise ImportError("raw upstream initialization detail")

    monkeypatch.setattr("app.document.ocr.PaddleOCRAdapter.__init__", fail_init)
    with pytest.raises(OCRUnavailable) as exc_info:
        get_ocr_adapter()

    assert exc_info.value.provider == "paddleocr"
    assert exc_info.value.reason == "initialization_failed"
    assert "raw upstream" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    get_settings.cache_clear()
    _paddle_adapter.cache_clear()


def test_unknown_ocr_provider_does_not_fall_back_to_mock(monkeypatch) -> None:
    monkeypatch.setenv("BOOKCOURSE_OCR_PROVIDER", "typo-provider")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="BOOKCOURSE_OCR_PROVIDER"):
        get_ocr_adapter()
    get_settings.cache_clear()


def test_paddleocr_vl_provider_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("BOOKCOURSE_OCR_PROVIDER", "paddleocr-vl")
    get_settings.cache_clear()
    _paddle_vl_adapter.cache_clear()

    def fail_init(self) -> None:
        raise ImportError("private upstream detail")

    monkeypatch.setattr(PaddleOCRVLAdapter, "__init__", fail_init)
    with pytest.raises(OCRUnavailable) as exc_info:
        get_ocr_adapter()

    assert exc_info.value.provider == "paddleocr-vl-1.6"
    assert exc_info.value.reason == "initialization_failed"
    assert "private upstream" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    get_settings.cache_clear()
    _paddle_vl_adapter.cache_clear()


def test_paddleocr_vl_structured_result_contract_is_mapped() -> None:
    results = [
        {
            "res": {
                "parsing_res_list": [
                    {
                        "block_bbox": np.asarray([10, 20, 210, 52]),
                        "block_label": "doc_title",
                        "block_content": "Cell Structure",
                        "block_order": 1,
                    },
                    {
                        "block_bbox": np.asarray([[10, 70], [220, 70], [220, 160], [10, 160]]),
                        "block_label": "table",
                        "block_content": "| Part | Function |",
                        "block_order": 2,
                    },
                ]
            }
        }
    ]

    blocks = list(_paddle_vl_blocks(results))

    assert blocks == [
        {"bbox": [10.0, 20.0, 210.0, 52.0], "text": "Cell Structure", "confidence": None, "label": "doc_title"},
        {"bbox": [10.0, 70.0, 220.0, 160.0], "text": "| Part | Function |", "confidence": None, "label": "table"},
    ]

def test_paddle_v3_result_contract_is_mapped() -> None:
    results = [
        {
            "rec_texts": ["CELL DIAGRAM", "Synthetic fixture"],
            "rec_scores": np.asarray([0.99, 0.93]),
            "rec_polys": np.asarray(
                [
                    [[10, 20], [110, 20], [110, 40], [10, 40]],
                    [[10, 60], [150, 60], [150, 80], [10, 80]],
                ]
            ),
        }
    ]

    lines = list(_paddle_v3_lines(results))

    assert lines == [
        ([10.0, 20.0, 110.0, 40.0], "CELL DIAGRAM", pytest.approx(0.99)),
        ([10.0, 60.0, 150.0, 80.0], "Synthetic fixture", pytest.approx(0.93)),
    ]


def test_isolated_paddle_worker_response_is_normalized(tmp_path: Path) -> None:
    class LiveWorker:
        def is_alive(self) -> bool:
            return True

    class Connection:
        def __init__(self) -> None:
            self.sent: list[dict[str, object]] = []

        def send(self, payload: dict[str, object]) -> None:
            self.sent.append(payload)

        def poll(self, timeout: float) -> bool:
            assert timeout == 12.0
            return True

        def recv(self) -> dict[str, object]:
            return {
                "request_id": 1,
                "ok": True,
                "lines": [([1, 2, 9, 10], "  worker text  ", 0.91), ("bad", "ignored", "bad")],
            }

    connection = Connection()
    adapter = PaddleOCRAdapter.__new__(PaddleOCRAdapter)
    adapter._connection = connection
    adapter._worker = LiveWorker()
    adapter._worker_lock = threading.Lock()
    adapter._request_number = 0
    adapter._recognition_timeout = 12.0

    lines = adapter._worker_lines(tmp_path / "page.png")

    assert lines == [([1.0, 2.0, 9.0, 10.0], "worker text", 0.91)]
    assert connection.sent == [
        {"op": "recognize", "request_id": 1, "image_path": str(tmp_path / "page.png")}
    ]


def test_visual_gap_restores_ocr_word_spacing_on_frozen_fixture() -> None:
    fixture = Path(__file__).resolve().parents[3] / "quality" / "stage0" / "fixtures" / "source_diagram.png"

    restored = _restore_visual_word_spacing(fixture, [116, 118, 191, 132], "CELLDIAGRAM")

    assert restored == "CELL DIAGRAM"


def test_ocr_pipeline_uses_original_before_lossy_preprocessing(monkeypatch, tmp_path: Path) -> None:
    image_path = tmp_path / "source.png"
    _write_test_image(image_path)
    scan = ScanResult(
        book_id="book_source_first",
        filename=image_path.name,
        file_type="png",
        page_count=1,
        has_text_layer=False,
        needs_ocr=True,
    )

    class SourceFirstAdapter:
        name = "paddleocr"

        def __init__(self) -> None:
            self.paths: list[Path] = []

        def recognize(self, path: Path, page: int):
            self.paths.append(path)
            if path == image_path:
                return (
                    [
                        TextBlock(
                            block_id="source_text",
                            page=page,
                            type="ocr_text",
                            text="BookCourse source image semantic text",
                            bbox=[10, 20, 180, 50],
                            confidence=0.98,
                        )
                    ],
                    [],
                )
            return [], []

    adapter = SourceFirstAdapter()
    monkeypatch.setattr("app.document.ocr_pipeline.get_ocr_adapter", lambda: adapter)

    pages, _ = extract_ocr_pages("book_source_first", image_path, tmp_path / "artifacts", scan)

    assert pages[0].text == "BookCourse source image semantic text"
    assert pages[0].needs_ocr is False
    assert adapter.paths == [image_path]


def test_ocr_pipeline_accepts_structured_vl_without_fabricated_confidence(monkeypatch, tmp_path: Path) -> None:
    image_path = tmp_path / "structured.png"
    _write_test_image(image_path)
    scan = ScanResult(
        book_id="book_structured_vl",
        filename=image_path.name,
        file_type="png",
        page_count=1,
        has_text_layer=False,
        needs_ocr=True,
    )

    class StructuredAdapter:
        name = "paddleocr-vl-1.6"

        def recognize(self, path: Path, page: int):
            return ([TextBlock(
                block_id="vl_heading",
                page=page,
                type="heading",
                text="Structured document heading and reliable semantic paragraph",
                bbox=[10, 20, 200, 70],
                confidence=None,
            )], [])

    monkeypatch.setattr("app.document.ocr_pipeline.get_ocr_adapter", lambda: StructuredAdapter())
    pages, _ = extract_ocr_pages(scan.book_id, image_path, tmp_path / "artifacts", scan)

    assert pages[0].needs_ocr is False
    assert pages[0].ocr_confidence is None
    assert pages[0].quality_score == pytest.approx(0.83)
    assert pages[0].metadata["quality_basis"] == "structured_vl_heuristic"


def test_ocr_pipeline_uses_text_specialist_after_weak_vl_result(monkeypatch, tmp_path: Path) -> None:
    image_path = tmp_path / "tiny-text.png"
    _write_test_image(image_path)
    scan = ScanResult(
        book_id="book_vl_text_fallback",
        filename=image_path.name,
        file_type="png",
        page_count=1,
        has_text_layer=False,
        needs_ocr=True,
        source_unit="image",
    )

    class WeakVLAdapter:
        name = "paddleocr-vl-1.6"

        def recognize(self, path: Path, page: int):
            return [], []

    class TextSpecialistAdapter:
        name = "paddleocr"

        def recognize(self, path: Path, page: int):
            return ([TextBlock(
                block_id="small_text",
                page=page,
                type="ocr_text",
                text="Tiny but reliable textbook text",
                bbox=[10, 20, 180, 50],
                confidence=0.97,
            )], [])

    monkeypatch.setattr("app.document.ocr_pipeline.get_ocr_adapter", lambda: WeakVLAdapter())
    monkeypatch.setattr("app.document.ocr_pipeline.get_text_ocr_adapter", lambda: TextSpecialistAdapter())

    pages, _ = extract_ocr_pages(scan.book_id, image_path, tmp_path / "artifacts", scan)

    assert pages[0].text == "Tiny but reliable textbook text"
    assert pages[0].ocr_provider == "paddleocr"
    assert pages[0].metadata["ocr_configured_provider"] == "paddleocr-vl-1.6"
    assert pages[0].metadata["ocr_selected_variant"] == "text_fallback"
    assert any(warning.code == "ocr_text_fallback" for warning in pages[0].quality_warnings)


@pytest.mark.parametrize(("extension", "image_format"), [("jp2", "JPEG2000"), ("gif", "GIF")])
def test_ocr_pipeline_normalizes_paddle_incompatible_formats(
    monkeypatch,
    tmp_path: Path,
    extension: str,
    image_format: str,
) -> None:
    source = tmp_path / f"source.{extension}"
    Image.new("RGB", (220, 160), color=(245, 245, 245)).save(source, format=image_format)
    scan = ScanResult(
        book_id=f"book_{extension}",
        filename=source.name,
        file_type=extension,
        page_count=1,
        has_text_layer=False,
        needs_ocr=True,
        source_unit="image",
    )

    class NormalizedAdapter:
        name = "paddleocr"

        def __init__(self) -> None:
            self.paths: list[Path] = []

        def recognize(self, path: Path, page: int):
            self.paths.append(path)
            return (
                [
                    TextBlock(
                        block_id="normalized_text",
                        page=page,
                        type="ocr_text",
                        text="Normalized image semantic evidence",
                        bbox=[10, 20, 180, 50],
                        confidence=0.98,
                    )
                ],
                [],
            )

    adapter = NormalizedAdapter()
    monkeypatch.setattr("app.document.ocr_pipeline.get_ocr_adapter", lambda: adapter)
    pages, _ = extract_ocr_pages(scan.book_id, source, tmp_path / "artifacts", scan)

    assert pages[0].needs_ocr is False
    assert adapter.paths[0].suffix == ".png"
    assert adapter.paths[0].name.startswith("normalized_source_")
    assert any(warning.code == "ocr_source_normalized" for warning in pages[0].quality_warnings)

def test_scanned_pdf_parse_runs_ocr_pipeline(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("BOOKCOURSE_OCR_PROVIDER", "mock")
    monkeypatch.setenv("BOOKCOURSE_PARSER_PROVIDER", "ocr")
    get_settings.cache_clear()
    pdf_path = tmp_path / "scanned.pdf"
    doc = fitz.open()
    page = doc.new_page(width=300, height=420)
    page.draw_rect(fitz.Rect(40, 80, 260, 220), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
    doc.save(pdf_path)
    doc.close()

    scan = detect_document("book_scan", pdf_path)
    assert scan.has_text_layer is False
    with pytest.raises(DocumentParseQualityError):
        parse_document("book_scan", pdf_path, tmp_path / "artifacts")
    pages = json.loads((tmp_path / "artifacts" / "pages.json").read_text(encoding="utf-8"))

    assert pages[0]["ocr_provider"] == "mock"
    assert pages[0]["text"] == ""
    assert pages[0]["blocks"]
    assert (tmp_path / "artifacts" / "preprocessed" / "page_001.png").exists()
    assert not (tmp_path / "artifacts" / "preprocessed" / "rendered_page_001.png").exists()
    get_settings.cache_clear()
