from __future__ import annotations

from collections.abc import Callable, Iterable
from hashlib import sha256
import json
from pathlib import Path
import time
from PIL import Image

from app.core.config import get_settings
from app.document.image_preprocessor import preprocess_image, render_pdf_page
from app.document.layout import get_layout_service
from app.document.ocr import OCRAdapter, OCRUnavailable, get_ocr_adapter, get_text_ocr_adapter
from app.document.ocr_cache import OCRResultCache
from app.schemas.books import LayoutRegion, PageResult, QualityWarning, ScanResult, TextBlock


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".jp2", ".bmp", ".gif", ".tif", ".tiff"}
PADDLE_NORMALIZE_EXTENSIONS = {".jp2", ".gif"}


def _normalize_paddle_source(source: Path, target: Path) -> Path:
    """Convert validated single-frame formats Paddle does not accept to PNG."""

    if source.suffix.lower() not in PADDLE_NORMALIZE_EXTENSIONS:
        return source
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.convert("RGB").save(target, format="PNG")
    return target


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _scale_bbox(bbox: list[float] | None, factor: float) -> list[float] | None:
    if bbox is None:
        return None
    return [round(float(value) * factor, 3) for value in bbox]


def _normalize_ocr_block(block: TextBlock, *, page: int, provider: str, scale: float) -> TextBlock:
    normalized = _normalize_text(block.text)
    digest = sha256(f"{page}\0{provider}\0{block.block_id}\0{normalized}".encode("utf-8")).hexdigest()
    return block.model_copy(
        update={
            "bbox": _scale_bbox(block.bbox, scale),
            "source_parser": provider,
            "source_block_id": block.block_id,
            "content_hash": digest,
            "metadata": {**block.metadata, "coordinate_scale_to_source": scale},
        }
    )


def _normalize_layout_region(region: LayoutRegion, *, scale: float) -> LayoutRegion:
    return region.model_copy(update={"bbox": _scale_bbox(region.bbox, scale) or region.bbox, "source": "ocr_layout"})


def extract_ocr_pages(
    book_id: str,
    file_path: Path,
    artifact_path: Path,
    scan: ScanResult,
    *,
    page_numbers: Iterable[int] | None = None,
    on_page_done: Callable[[int, int], None] | None = None,
) -> tuple[list[PageResult], ScanResult]:
    """Return OCR pages without replacing normalized page artifacts.

    Rendering/preprocessing images are diagnostic artifacts, but ``pages`` and
    block JSON are owned by the final mixed-parser publisher.
    """

    settings = get_settings()
    adapter = get_ocr_adapter()  # fail closed when PaddleOCR is unavailable
    layout_service = get_layout_service()
    ocr_cache = OCRResultCache(
        settings.storage_root / "_cache" / "ocr",
        enabled=settings.ocr_cache_enabled,
        ttl_seconds=settings.ocr_cache_ttl_seconds,
        max_items=settings.ocr_cache_max_items,
    )
    preprocessed_dir = artifact_path / "preprocessed"
    preprocessed_dir.mkdir(parents=True, exist_ok=True)
    selected = set(page_numbers) if page_numbers is not None else set(range(1, scan.page_count + 1))
    pages: list[PageResult] = []
    warnings: list[QualityWarning] = list(scan.quality_warnings)

    if file_path.suffix.lower() == ".pdf":
        selected_pages = [
            page_number
            for page_number in sorted(selected)
            if 1 <= page_number <= scan.page_count
        ]

        def rendered_pages():
            for page_number in selected_pages:
                rendered = render_pdf_page(
                    file_path,
                    page_number - 1,
                    preprocessed_dir / f"rendered_page_{page_number:03d}.png",
                    zoom=settings.ocr_render_zoom,
                )
                yield page_number, rendered, 1.0 / settings.ocr_render_zoom

        source_images = rendered_pages()
        total_pages = len(selected_pages)
    elif file_path.suffix.lower() in IMAGE_EXTENSIONS and 1 in selected:
        source_images = [(1, file_path, 1.0)]
        total_pages = 1
    else:
        source_images = []
        total_pages = 0

    for completed, (page_number, source_image, coordinate_scale) in enumerate(source_images, start=1):
        page_started = time.perf_counter()
        ocr_attempts = 0
        ocr_inference_calls = 0
        ocr_cache_hits = 0

        def recognize(path: Path, recognition_adapter: OCRAdapter = adapter) -> tuple[list[TextBlock], list[QualityWarning]]:
            nonlocal ocr_attempts, ocr_inference_calls, ocr_cache_hits
            ocr_attempts += 1
            if not ocr_cache.enabled:
                ocr_inference_calls += 1
                return recognition_adapter.recognize(path, page_number)
            key = ocr_cache.key_for(
                path,
                page=page_number,
                provider=recognition_adapter.name,
                model=settings.ocr_model,
                language=settings.ocr_language,
                device=settings.ocr_device,
                pipeline_version=settings.ocr_vl_pipeline_version,
                quality_profile=settings.ocr_quality_profile,
            )
            cached = ocr_cache.get(key)
            if cached is not None:
                ocr_cache_hits += 1
                return cached
            ocr_inference_calls += 1
            result = recognition_adapter.recognize(path, page_number)
            ocr_cache.put(key, *result)
            return result

        paddle_source = _normalize_paddle_source(
            source_image,
            preprocessed_dir / f"normalized_source_{page_number:03d}.png",
        )
        processed = preprocessed_dir / f"page_{page_number:03d}.png"
        page_warnings = preprocess_image(paddle_source, processed, page=page_number)
        if paddle_source != source_image:
            page_warnings.append(
                QualityWarning(
                    page=page_number,
                    code="ocr_source_normalized",
                    message="source image was losslessly normalized to PNG for OCR compatibility",
                )
            )
        uses_structured_vl = adapter.name.startswith("paddleocr-vl")
        selected_variant = "source"
        selected_provider = adapter.name
        # PaddleOCR-VL already performs full-page layout analysis. Avoid a
        # second OpenCV pass for the same page; its structured blocks remain
        # the authoritative reading-order evidence.
        layout_regions = [] if uses_structured_vl else [
            _normalize_layout_region(region, scale=coordinate_scale)
            for region in layout_service.detect_regions(processed, page_number)
        ]
        # PaddleOCR generally preserves small glyphs better on the rendered or
        # original source.  The binarized image remains useful for layout and
        # becomes an OCR fallback only when the primary pass is insufficient.
        raw_blocks, ocr_warnings = recognize(paddle_source)
        primary_semantic = sum(
            len(block.text.strip())
            for block in raw_blocks
            if block.type != "ocr_pending"
            and block.text.strip()
            and (block.confidence is None or block.confidence >= settings.ocr_text_confidence_threshold)
        )
        primary_weighted = [
            (block.confidence, max(1, len(block.text.strip())))
            for block in raw_blocks
            if block.type != "ocr_pending" and block.text.strip() and block.confidence is not None
        ]
        primary_confidence = (
            sum(value * weight for value, weight in primary_weighted) / sum(weight for _, weight in primary_weighted)
            if primary_weighted
            else None
        )
        retry_for_quality = primary_semantic < settings.ocr_retry_min_semantic_chars or (
            settings.ocr_quality_profile != "fast"
            and primary_confidence is not None
            and primary_confidence < settings.ocr_low_confidence_threshold
        )
        if settings.ocr_adaptive_retry and retry_for_quality and paddle_source.resolve() != processed.resolve():
            processed_blocks, processed_warnings = recognize(processed)
            processed_semantic = sum(
                len(block.text.strip())
                for block in processed_blocks
                if block.type != "ocr_pending"
                and block.text.strip()
                and (block.confidence is None or block.confidence >= settings.ocr_text_confidence_threshold)
            )
            processed_weighted = [
                (block.confidence, max(1, len(block.text.strip())))
                for block in processed_blocks
                if block.type != "ocr_pending" and block.text.strip() and block.confidence is not None
            ]
            processed_confidence = (
                sum(value * weight for value, weight in processed_weighted) / sum(weight for _, weight in processed_weighted)
                if processed_weighted
                else None
            )
            primary_evidence_score = primary_semantic * (primary_confidence if primary_confidence is not None else 0.85)
            processed_evidence_score = processed_semantic * (processed_confidence if processed_confidence is not None else 0.85)
            if processed_evidence_score > primary_evidence_score:
                raw_blocks = processed_blocks
                selected_variant = "preprocessed"
                ocr_warnings = [
                    *processed_warnings,
                    QualityWarning(
                        page=page_number,
                        code="ocr_preprocessed_fallback",
                        message="OCR used the preprocessed page because it produced more usable text",
                    ),
                ]
        selected_semantic = sum(
            len(block.text.strip())
            for block in raw_blocks
            if block.type != "ocr_pending"
            and block.text.strip()
            and (block.confidence is None or block.confidence >= settings.ocr_text_confidence_threshold)
        )
        if uses_structured_vl and settings.ocr_text_fallback_enabled and selected_semantic < settings.ocr_retry_min_semantic_chars:
            try:
                text_adapter = get_text_ocr_adapter()
                text_blocks, text_warnings = recognize(paddle_source, text_adapter)
                text_semantic = sum(
                    len(block.text.strip())
                    for block in text_blocks
                    if block.type != "ocr_pending"
                    and block.text.strip()
                    and (block.confidence is None or block.confidence >= settings.ocr_text_confidence_threshold)
                )
                if text_semantic > selected_semantic:
                    raw_blocks = text_blocks
                    selected_provider = text_adapter.name
                    selected_variant = "text_fallback"
                    ocr_warnings = [
                        *text_warnings,
                        QualityWarning(
                            page=page_number,
                            code="ocr_text_fallback",
                            message="PP-OCRv6 text recognition recovered a weak PaddleOCR-VL page",
                        ),
                    ]
            except OCRUnavailable as exc:
                ocr_warnings = [
                    *ocr_warnings,
                    QualityWarning(
                        page=page_number,
                        code="ocr_text_fallback_unavailable",
                        message=f"text OCR fallback unavailable: {exc.reason}",
                    ),
                ]
        blocks = [
            _normalize_ocr_block(block, page=page_number, provider=selected_provider, scale=coordinate_scale)
            for block in raw_blocks
        ]
        usable_blocks = [
            block
            for block in blocks
            if block.type != "ocr_pending"
            and block.text.strip()
            and (block.confidence is None or block.confidence >= settings.ocr_text_confidence_threshold)
        ]
        page_text = "\n".join(block.text for block in usable_blocks)
        weighted = [
            (block.confidence, max(1, len(block.text.strip())))
            for block in usable_blocks
            if block.confidence is not None
        ]
        confidence = (
            sum(value * weight for value, weight in weighted) / sum(weight for _, weight in weighted)
            if weighted
            else None
        )
        semantic_chars = len(_normalize_text(page_text))
        if confidence is not None and semantic_chars >= 20:
            quality_score = confidence
            quality_basis = "model_confidence"
        elif selected_provider.startswith("paddleocr-vl") and semantic_chars >= 20 and blocks:
            # PaddleOCR-VL exposes ordered semantic blocks but not a calibrated
            # per-block probability. Keep model confidence as None and use a
            # conservative structural quality score solely for the parse gate.
            quality_score = min(0.92, 0.82 + min(len(blocks), 10) * 0.01)
            quality_basis = "structured_vl_heuristic"
        else:
            quality_score = 0.0
            quality_basis = "insufficient_evidence"
        needs_ocr = selected_provider == "mock" or semantic_chars < 20 or quality_score < 0.60
        combined_warnings = [*page_warnings, *ocr_warnings]
        if selected_provider == "mock":
            # Preserve the diagnostic block in page artifacts, but never use
            # its instructional placeholder as semantic page text.
            page_text = ""
            quality_score = 0.0
        warnings.extend(combined_warnings)
        pages.append(
            PageResult(
                page=page_number,
                pdf_page_index=page_number - 1,
                text=page_text,
                needs_ocr=needs_ocr,
                blocks=blocks,
                ocr_provider=selected_provider,
                ocr_confidence=confidence,
                quality_warnings=combined_warnings,
                preprocessed_image_url=f"/api/books/{book_id}/artifacts/preprocessed/page_{page_number:03d}.png",
                layout_regions=layout_regions,
                parser="ocr",
                quality_score=max(0.0, min(1.0, quality_score)),
                metadata={
                    "coordinate_space": "source",
                    "ocr_provider": selected_provider,
                    "ocr_configured_provider": adapter.name,
                    "ocr_model": settings.ocr_model,
                    "ocr_quality_profile": settings.ocr_quality_profile,
                    "ocr_attempts": ocr_attempts,
                    "ocr_inference_calls": ocr_inference_calls,
                    "ocr_cache_hits": ocr_cache_hits,
                    "ocr_selected_variant": selected_variant,
                    "ocr_duration_ms": round((time.perf_counter() - page_started) * 1000, 2),
                    "quality_basis": quality_basis,
                },
            )
        )
        if on_page_done:
            on_page_done(completed, total_pages)
        if file_path.suffix.lower() == ".pdf":
            # The normalized/preprocessed page is the published diagnostic
            # artifact. The source render can always be reproduced from the
            # original PDF, so remove it after a successful OCR page instead
            # of retaining a second full-page image for the entire book.
            source_image.unlink(missing_ok=True)

    updated_scan = scan.model_copy(
        update={
            "quality_warnings": warnings,
            "needs_ocr": any(page.needs_ocr for page in pages) if pages else scan.needs_ocr,
        }
    )
    return pages, updated_scan


def _write_blocks(book_id: str, pages: list[PageResult], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for page in pages:
            for block in page.blocks:
                handle.write(json.dumps({**block.model_dump(mode="json"), "book_id": book_id}, ensure_ascii=False) + "\n")


def _write_layout_regions(book_id: str, pages: list[PageResult], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for page in pages:
            for region in page.layout_regions:
                handle.write(json.dumps({**region.model_dump(mode="json"), "book_id": book_id}, ensure_ascii=False) + "\n")


def write_ocr_page_artifacts(
    book_id: str,
    file_path: Path,
    artifact_path: Path,
    scan: ScanResult,
    on_page_done: Callable[[int, int], None] | None = None,
) -> ScanResult:
    """Compatibility wrapper used by focused OCR tests and legacy callers."""

    artifact_path.mkdir(parents=True, exist_ok=True)
    pages, updated_scan = extract_ocr_pages(
        book_id,
        file_path,
        artifact_path,
        scan,
        on_page_done=on_page_done,
    )
    (artifact_path / "pages.json").write_text(
        json.dumps([page.model_dump(mode="json") for page in pages], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_blocks(book_id, pages, artifact_path / "text_blocks.jsonl")
    _write_layout_regions(book_id, pages, artifact_path / "layout_regions.jsonl")
    (artifact_path / "scan_result.json").write_text(updated_scan.model_dump_json(indent=2), encoding="utf-8")
    return updated_scan


__all__ = ["IMAGE_EXTENSIONS", "OCRUnavailable", "extract_ocr_pages", "write_ocr_page_artifacts"]
