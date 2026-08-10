from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SupportedFileType:
    extension: str
    category: str
    display_name: str
    content_types: frozenset[str]


SUPPORTED_FILE_TYPES: tuple[SupportedFileType, ...] = (
    SupportedFileType(".pdf", "pdf", "PDF", frozenset({"application/pdf"})),
    SupportedFileType(".png", "image", "PNG", frozenset({"image/png"})),
    SupportedFileType(".jpg", "image", "JPG", frozenset({"image/jpeg", "image/jpg"})),
    SupportedFileType(".jpeg", "image", "JPEG", frozenset({"image/jpeg", "image/jpg"})),
    SupportedFileType(".jp2", "image", "JPEG 2000", frozenset({"image/jp2", "image/jpeg2000"})),
    SupportedFileType(".webp", "image", "WEBP", frozenset({"image/webp"})),
    SupportedFileType(".gif", "image", "GIF", frozenset({"image/gif"})),
    SupportedFileType(".bmp", "image", "BMP", frozenset({"image/bmp", "image/x-ms-bmp"})),
    SupportedFileType(".tif", "image", "TIF", frozenset({"image/tiff", "image/tif"})),
    SupportedFileType(".tiff", "image", "TIFF", frozenset({"image/tiff", "image/tif"})),
    SupportedFileType(
        ".docx",
        "office",
        "DOCX",
        frozenset({"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
    ),
    SupportedFileType(
        ".pptx",
        "office",
        "PPTX",
        frozenset({"application/vnd.openxmlformats-officedocument.presentationml.presentation"}),
    ),
    SupportedFileType(
        ".xlsx",
        "office",
        "XLSX",
        frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
    ),
)

FILE_TYPE_BY_EXTENSION = {item.extension: item for item in SUPPORTED_FILE_TYPES}
ALLOWED_EXTENSIONS = frozenset(FILE_TYPE_BY_EXTENSION)
ALLOWED_CONTENT_TYPES_BY_EXTENSION = {
    extension: item.content_types for extension, item in FILE_TYPE_BY_EXTENSION.items()
}
IMAGE_EXTENSIONS = frozenset(
    extension for extension, item in FILE_TYPE_BY_EXTENSION.items() if item.category == "image"
)
OFFICE_EXTENSIONS = frozenset(
    extension for extension, item in FILE_TYPE_BY_EXTENSION.items() if item.category == "office"
)


def supported_file_type_message() -> str:
    names = [item.display_name for item in SUPPORTED_FILE_TYPES]
    return "仅支持 " + "、".join(names) + " 文件"


__all__ = [
    "ALLOWED_CONTENT_TYPES_BY_EXTENSION",
    "ALLOWED_EXTENSIONS",
    "FILE_TYPE_BY_EXTENSION",
    "IMAGE_EXTENSIONS",
    "OFFICE_EXTENSIONS",
    "SUPPORTED_FILE_TYPES",
    "SupportedFileType",
    "supported_file_type_message",
]
