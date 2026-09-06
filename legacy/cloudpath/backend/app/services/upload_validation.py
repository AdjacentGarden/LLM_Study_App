from __future__ import annotations

from pathlib import Path
import math

import fitz
from PIL import Image, UnidentifiedImageError

from app.core.config import get_settings
from app.core.errors import AppError
from app.services.file_types import (
    ALLOWED_CONTENT_TYPES_BY_EXTENSION,
    IMAGE_EXTENSIONS,
    OFFICE_EXTENSIONS,
)
from app.services.ooxml_validation import OOXMLInspection, inspect_ooxml


GENERIC_CONTENT_TYPES = {"application/octet-stream", "binary/octet-stream"}
EXPECTED_PIL_FORMATS = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".jp2": "JPEG2000",
    ".png": "PNG",
    ".webp": "WEBP",
    ".gif": "GIF",
    ".bmp": "BMP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}


def _normalized_content_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    value = content_type.split(";", 1)[0].strip().lower()
    return value or None


def validate_declared_content_type(filename: str, content_type: str | None) -> None:
    normalized = _normalized_content_type(content_type)
    if normalized is None or normalized in GENERIC_CONTENT_TYPES:
        return
    ext = Path(filename).suffix.lower()
    allowed = ALLOWED_CONTENT_TYPES_BY_EXTENSION.get(ext, set())
    if normalized not in allowed:
        raise AppError(
            "file_content_type_mismatch",
            "文件 MIME 类型与扩展名不匹配",
            details={"extension": ext, "content_type": normalized, "allowed_content_types": sorted(allowed)},
        )


def _content_kind(path: Path, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    with path.open("rb") as handle:
        header = handle.read(16)
    if ext == ".pdf":
        if header.startswith(b"%PDF-"):
            return "pdf"
    elif ext in {".jpg", ".jpeg"}:
        if header.startswith(b"\xff\xd8\xff"):
            return "image"
    elif ext == ".png":
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image"
    elif ext == ".webp":
        if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return "image"
    elif ext == ".jp2":
        if header.startswith(b"\x00\x00\x00\x0cjP  \r\n\x87\n") or header.startswith(b"\xff\x4f\xff\x51"):
            return "image"
    elif ext == ".gif":
        if header.startswith((b"GIF87a", b"GIF89a")):
            return "image"
    elif ext == ".bmp":
        if header.startswith(b"BM"):
            return "image"
    elif ext in {".tif", ".tiff"}:
        if header.startswith((b"II*\x00", b"MM\x00*")):
            return "image"
    elif ext in OFFICE_EXTENSIONS:
        if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise AppError("encrypted_office_unsupported", "暂不支持带密码的 Office 文件")
        if header.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
            return "office"
    raise AppError("file_content_mismatch", "文件内容与扩展名不匹配或文件已损坏", details={"extension": ext})


def _validate_pdf(path: Path) -> None:
    settings = get_settings()
    try:
        # Validate from an in-memory stream. On Windows, MuPDF can retain a
        # handle to a corrupt path after ``fitz.open(path)`` raises, preventing
        # the upload route from deleting the rejected file.
        pdf_bytes = path.read_bytes()
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise AppError("invalid_pdf", "PDF 文件无法打开或已损坏", details={"type": exc.__class__.__name__}) from None
    try:
        if document.is_encrypted or document.needs_pass:
            raise AppError("encrypted_pdf_unsupported", "暂不支持加密 PDF")
        if document.page_count < 1:
            raise AppError("invalid_pdf", "PDF 文件没有可解析页面")
        if document.page_count > settings.max_pdf_pages:
            raise AppError(
                "pdf_page_limit_exceeded",
                "PDF 页数超过限制",
                details={"page_count": document.page_count, "max_pdf_pages": settings.max_pdf_pages},
            )
        for page_number in range(document.page_count):
            rectangle = document.load_page(page_number).rect
            render_pixels = math.ceil(rectangle.width * 2) * math.ceil(rectangle.height * 2)
            if render_pixels > settings.max_pdf_render_pixels:
                raise AppError(
                    "pdf_page_pixel_limit_exceeded",
                    "PDF 页面尺寸超过安全渲染限制",
                    details={
                        "page": page_number + 1,
                        "render_pixels": render_pixels,
                        "max_pdf_render_pixels": settings.max_pdf_render_pixels,
                    },
                )
    finally:
        document.close()


def _validate_image(path: Path, extension: str) -> None:
    settings = get_settings()
    try:
        with Image.open(path) as image:
            actual_format = str(image.format or "").upper()
            expected_format = EXPECTED_PIL_FORMATS.get(extension)
            if expected_format is None or actual_format != expected_format:
                raise AppError(
                    "file_content_mismatch",
                    "图片内容与扩展名不匹配",
                    details={"extension": extension, "detected_format": actual_format or "unknown"},
                )
            width, height = image.size
            pixels = width * height
            if pixels > settings.max_image_pixels:
                raise AppError(
                    "image_pixel_limit_exceeded",
                    "图片像素超过限制",
                    details={"width": width, "height": height, "max_image_pixels": settings.max_image_pixels},
                )
            frame_count = int(getattr(image, "n_frames", 1) or 1)
            if frame_count > 1 and extension == ".gif":
                raise AppError(
                    "animated_gif_unsupported",
                    "仅支持单帧 GIF，动画 GIF 会导致内容丢失",
                    details={"frame_count": frame_count},
                )
            if frame_count > 1 and extension in {".tif", ".tiff"}:
                raise AppError(
                    "multipage_tiff_unsupported",
                    "仅支持单页 TIFF/TIF，多页文件会导致内容丢失",
                    details={"frame_count": frame_count},
                )
            image.verify()
    except AppError:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise AppError("invalid_image", "图片文件无法打开或已损坏", details={"type": exc.__class__.__name__}) from exc


def validate_saved_upload(path: Path, filename: str) -> OOXMLInspection | None:
    extension = Path(filename).suffix.lower()
    kind = _content_kind(path, filename)
    if kind == "pdf":
        _validate_pdf(path)
        return None
    if kind == "image":
        if extension not in IMAGE_EXTENSIONS:
            raise AppError("file_content_mismatch", "图片内容与扩展名不匹配")
        _validate_image(path, extension)
        return None
    return inspect_ooxml(path, extension)
