from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz

from app.document.image_preprocessor import assess_image_quality
from app.schemas.books import QualityWarning, ScanResult
from app.services.file_types import IMAGE_EXTENSIONS, OFFICE_EXTENSIONS
from app.services.ooxml_validation import inspect_ooxml


@dataclass(frozen=True, slots=True)
class PageTextLayer:
    """Text-layer detection result for one PDF page (page is one-based)."""

    page: int
    has_text_layer: bool
    text_length: int


def inspect_pdf_text_layers(file_path: Path) -> tuple[PageTextLayer, ...]:
    """Inspect every page so mixed and late-page scans are not misclassified."""

    results: list[PageTextLayer] = []
    with fitz.open(file_path) as doc:
        for index in range(doc.page_count):
            text = doc.load_page(index).get_text("text").strip()
            results.append(
                PageTextLayer(
                    page=index + 1,
                    has_text_layer=bool(text),
                    text_length=len(text),
                )
            )
    return tuple(results)


def detect_pdf(book_id: str, file_path: Path) -> ScanResult:
    page_layers = inspect_pdf_text_layers(file_path)
    page_count = len(page_layers)
    warnings = [
        QualityWarning(
            page=result.page,
            code="missing_text_layer",
            message="page has no readable text layer",
        )
        for result in page_layers
        if not result.has_text_layer
    ]
    has_text_layer = any(result.has_text_layer for result in page_layers)
    needs_ocr = not page_layers or any(not result.has_text_layer for result in page_layers)
    if page_count == 0:
        warnings.append(QualityWarning(code="empty_pdf", message="PDF has no readable pages"))
    return ScanResult(
        book_id=book_id,
        filename=file_path.name,
        file_type="pdf",
        page_count=page_count,
        has_text_layer=has_text_layer,
        needs_ocr=needs_ocr,
        source_unit="page",
        quality_warnings=warnings,
    )


def detect_document(book_id: str, file_path: Path) -> ScanResult:
    ext = file_path.suffix.lower()
    if ext == ".pdf":
        return detect_pdf(book_id, file_path)
    if ext in OFFICE_EXTENSIONS:
        inspection = inspect_ooxml(file_path, ext)
        return ScanResult(
            book_id=book_id,
            filename=file_path.name,
            file_type=ext.lstrip("."),
            page_count=len(inspection.units),
            has_text_layer=True,
            needs_ocr=False,
            source_unit=inspection.unit_type,
            source_locations=inspection.source_locations(),
            quality_warnings=[
                QualityWarning(
                    code="office_logical_locations",
                    message="Office source positions use document, slide, or worksheet semantics",
                )
            ],
        )
    if ext not in IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported document extension: {ext}")
    try:
        warnings = [QualityWarning(code="image_input", message="image input will be preprocessed and OCRed")]
        warnings.extend(assess_image_quality(file_path, page=1))
    except Exception:
        warnings = [QualityWarning(code="image_quality_unavailable", message="image quality assessment failed")]
    return ScanResult(
        book_id=book_id,
        filename=file_path.name,
        file_type=ext.lstrip("."),
        page_count=1,
        has_text_layer=False,
        needs_ocr=True,
        source_unit="image",
        source_locations=[{"index": 1, "label": "图片 1", "source_format": ext.lstrip(".")}],
        quality_warnings=warnings,
    )
