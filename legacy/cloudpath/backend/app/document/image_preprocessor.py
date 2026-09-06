from __future__ import annotations

from pathlib import Path

import fitz

from app.core.config import get_settings
from app.schemas.books import QualityWarning


def _cv2():
    import cv2  # type: ignore

    return cv2


def _read_grayscale(image_path: Path):
    cv2 = _cv2()
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        # OpenCV builds vary in JPEG 2000 and GIF codec support.  Pillow is
        # already the authoritative upload decoder, so use it as the safe
        # single-frame fallback and keep the downstream numpy contract.
        try:
            import numpy as np
            from PIL import Image

            with Image.open(image_path) as source:
                image = np.asarray(source.convert("L"), dtype=np.uint8)
        except Exception as exc:
            raise ValueError(f"unable to read image: {image_path}") from exc
    return image


def assess_image_quality(image_path: Path, page: int | None = None) -> list[QualityWarning]:
    cv2 = _cv2()
    settings = get_settings()
    image = _read_grayscale(image_path)
    warnings: list[QualityWarning] = []
    brightness = float(image.mean())
    blur_score = float(cv2.Laplacian(image, cv2.CV_64F).var())
    height, width = image.shape[:2]
    border = max(4, min(height, width) // 40)
    border_pixels = [
        image[:border, :],
        image[-border:, :],
        image[:, :border],
        image[:, -border:],
    ]
    dark_border_ratio = sum(float((section < 80).mean()) for section in border_pixels) / len(border_pixels)

    if brightness < settings.image_dark_threshold:
        warnings.append(QualityWarning(page=page, code="too_dark", message="image is too dark for reliable OCR"))
    if brightness > settings.image_bright_threshold:
        warnings.append(QualityWarning(page=page, code="too_bright", message="image is too bright for reliable OCR"))
    if blur_score < settings.image_blur_threshold:
        warnings.append(QualityWarning(page=page, code="blurry", message="image may be blurry"))
    if dark_border_ratio > settings.image_dark_border_ratio:
        warnings.append(QualityWarning(page=page, code="incomplete_crop", message="page border may be cropped or contain dark edges"))
    return warnings


def preprocess_image(image_path: Path, output_path: Path, page: int | None = None) -> list[QualityWarning]:
    cv2 = _cv2()
    settings = get_settings()
    image = _read_grayscale(image_path)
    warnings = assess_image_quality(image_path, page=page)
    normalized = cv2.equalizeHist(image)
    denoised = cv2.medianBlur(normalized, settings.preprocess_median_kernel)
    threshold = cv2.adaptiveThreshold(
        denoised,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        settings.preprocess_adaptive_block_size,
        settings.preprocess_adaptive_c,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), threshold)
    return warnings


def render_pdf_page(pdf_path: Path, page_index: int, output_path: Path, zoom: float | None = None) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    render_zoom = zoom if zoom is not None else get_settings().ocr_render_zoom
    with fitz.open(pdf_path) as doc:
        page = doc.load_page(page_index)
        matrix = fitz.Matrix(render_zoom, render_zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        pix.save(output_path)
    return output_path
