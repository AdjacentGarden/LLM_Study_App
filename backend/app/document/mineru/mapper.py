from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
from collections.abc import Callable
from typing import Any

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from app.document.mineru.exceptions import MinerUStaleResultError
from app.document.mineru.models import MinerUResultDocument
from app.document.mineru.quality import (
    DocumentQualityResult,
    PageMappingStats,
    evaluate_document_quality,
    evaluate_page_quality,
)
from app.schemas.books import Asset, LayoutRegion, PageResult, QualityWarning, TextBlock


MAPPER_VERSION = "mineru-mapper-v1"
MAX_DECODED_IMAGE_BYTES = 32 * 1024 * 1024
MAX_DECODED_IMAGE_PIXELS = 40_000_000

_DATA_URI_RE = re.compile(
    r"^data:(image/[a-z0-9.+-]+);base64,([a-z0-9+/=\s]+)$",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"\s+")
_FORMULA_TYPES = {"equation", "interline_equation", "equation_interline"}
_TEXT_TYPES = {
    "text",
    "title",
    "paragraph",
    "header",
    "footer",
    "page_number",
    "aside_text",
    "page_footnote",
    "abstract",
}
_VISUAL_TYPES = {"image", "chart"}
_SUPPORTED_IMAGE_FORMATS: dict[str, tuple[str, str]] = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
    "BMP": ("bmp", "image/bmp"),
    "TIFF": ("tiff", "image/tiff"),
}
_MIME_ALIASES = {"image/jpg": "image/jpeg", "image/x-png": "image/png"}

GenerationGuard = Callable[[], bool]


class MinerUMappingResult(BaseModel):
    """Normalized MinerU output. Mapping never writes page artifacts.

    Only decoded image assets are written below ``asset_root``. The caller owns
    publication of ``pages.json``, raw structured JSON, and ``assets.json``.
    """

    model_config = ConfigDict(frozen=True)

    mapper_version: str = MAPPER_VERSION
    book_id: str
    backend: str
    version: str
    pages: list[PageResult] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    warnings: list[QualityWarning] = Field(default_factory=list)
    stats: list[PageMappingStats] = Field(default_factory=list)
    quality: DocumentQualityResult
    raw_content: list[dict[str, Any]] = Field(default_factory=list)
    raw_middle: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class _DecodedImage:
    data: bytes
    extension: str
    mime_type: str
    content_hash: str
    width: int
    height: int


def _normalize_text(value: str) -> str:
    return _SPACE_RE.sub(" ", value).strip()


def _content_hash(value: str) -> str:
    return sha256(_normalize_text(value).casefold().encode("utf-8")).hexdigest()


def _as_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    stripped = value.strip()
    if stripped.lower().startswith("data:image/"):
        return ""
    return stripped


def _text_list(value: object) -> list[str]:
    if isinstance(value, str):
        text = _as_text(value)
        return [text] if text else []
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = _as_text(item.get("text")) or _as_text(item.get("content"))
        else:
            text = _as_text(item)
        if text:
            values.append(text)
    return values


def _join_parts(*groups: object) -> str:
    parts: list[str] = []
    for group in groups:
        parts.extend(_text_list(group))
    return "\n".join(parts)


class _TableTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "tr" and self._row:
            self.rows.append(self._row)
            self._row = []
        if tag.lower() in {"td", "th"}:
            self._cell = []

    def handle_data(self, data: str) -> None:
        text = _normalize_text(data)
        if text and self._cell is not None:
            self._cell.append(text)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._cell is not None:
            self._row.append(_normalize_text(" ".join(self._cell)))
            self._cell = None
        elif lowered == "tr":
            if self._row:
                self.rows.append(self._row)
            self._row = []

    def text(self) -> str:
        if self._row:
            self.rows.append(self._row)
            self._row = []
        return "\n".join(" ".join(cell for cell in row if cell) for row in self.rows if any(row))


def _table_search_text(body: str) -> str:
    if "<table" not in body.casefold():
        return body
    parser = _TableTextExtractor()
    try:
        parser.feed(body)
        parser.close()
        return parser.text() or _normalize_text(re.sub(r"<[^>]+>", " ", body))
    except Exception:
        return _normalize_text(re.sub(r"<[^>]+>", " ", body))


def _safe_heading_level(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        level = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return level if 1 <= level <= 6 else None


def _stable_block_id(
    *,
    page: int,
    block_type: str,
    text: str,
    bbox: list[float] | None,
    source_identity: object,
) -> str:
    payload = json.dumps(
        {
            "page": page,
            "type": block_type,
            "text": _normalize_text(text).casefold(),
            "bbox": [round(value, 3) for value in bbox] if bbox else None,
            "source_identity": source_identity,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = sha256(payload.encode("utf-8")).hexdigest()[:16]
    safe_type = re.sub(r"[^a-z0-9]+", "_", block_type.lower()).strip("_") or "block"
    return f"p{page:03d}_mineru_{safe_type}_{digest}"


def _middle_page_data(
    middle: dict[str, Any],
) -> tuple[dict[int, tuple[float, float]], dict[int, float], set[int], list[QualityWarning]]:
    page_sizes: dict[int, tuple[float, float]] = {}
    confidences: dict[int, float] = {}
    present: set[int] = set()
    warnings: list[QualityWarning] = []
    raw_pages = middle.get("pdf_info")
    if not isinstance(raw_pages, list):
        return page_sizes, confidences, present, [
            QualityWarning(code="mineru_middle_pages_missing", message="MinerU middle_json has no pdf_info page list")
        ]

    for raw_page in raw_pages:
        if not isinstance(raw_page, dict) or not isinstance(raw_page.get("page_idx"), int):
            warnings.append(QualityWarning(code="mineru_middle_page_invalid", message="MinerU middle_json contains an invalid page entry"))
            continue
        page_index = int(raw_page["page_idx"])
        page_number = page_index + 1
        if page_index < 0:
            warnings.append(QualityWarning(code="mineru_page_index_invalid", message="MinerU returned a negative page index"))
            continue
        if page_index in present:
            warnings.append(QualityWarning(page=page_number, code="mineru_middle_page_duplicate", message="MinerU middle_json contains a duplicate page index"))
            continue
        present.add(page_index)
        size = raw_page.get("page_size")
        if (
            isinstance(size, list)
            and len(size) == 2
            and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in size)
            and float(size[0]) > 0
            and float(size[1]) > 0
        ):
            page_sizes[page_index] = (float(size[0]), float(size[1]))
        scores = _collect_scores(raw_page)
        if scores:
            confidences[page_index] = sum(scores) / len(scores)
    return page_sizes, confidences, present, warnings


def _collect_scores(value: object) -> list[float]:
    scores: list[float] = []
    if isinstance(value, dict):
        score = value.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool) and 0 <= float(score) <= 1:
            scores.append(float(score))
        for child in value.values():
            if isinstance(child, (dict, list)):
                scores.extend(_collect_scores(child))
    elif isinstance(value, list):
        for child in value:
            scores.extend(_collect_scores(child))
    return scores


def _canonical_bbox(
    raw_bbox: object,
    page_size: tuple[float, float] | None,
) -> list[float] | None:
    if not isinstance(raw_bbox, list) or len(raw_bbox) != 4 or page_size is None:
        return None
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in raw_bbox):
        return None
    normalized = [float(value) for value in raw_bbox]
    if any(value < 0 or value > 1000 for value in normalized):
        return None
    x0, y0, x1, y1 = normalized
    if x1 < x0 or y1 < y0:
        return None
    width, height = page_size
    return [
        round(x0 * width / 1000.0, 3),
        round(y0 * height / 1000.0, 3),
        round(x1 * width / 1000.0, 3),
        round(y1 * height / 1000.0, 3),
    ]


def _safe_image_name(image_path: object) -> str | None:
    if not isinstance(image_path, str) or not image_path.strip():
        return None
    normalized = image_path.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if ".." in path.parts or not path.name or path.name in {".", ".."}:
        return None
    return path.name


def _image_payloads(images: dict[str, str] | None) -> dict[str, str]:
    payloads: dict[str, str] = {}
    for name, payload in (images or {}).items():
        if not isinstance(name, str) or not isinstance(payload, str):
            continue
        normalized = name.replace("\\", "/")
        path = PurePosixPath(normalized)
        if len(path.parts) != 1 or path.name in {"", ".", ".."}:
            continue
        payloads[path.name] = payload
    return payloads


def _decode_image(data_uri: str) -> _DecodedImage:
    matched = _DATA_URI_RE.fullmatch(data_uri.strip())
    if matched is None:
        raise ValueError("unsupported MinerU image data URI")
    declared_mime = _MIME_ALIASES.get(matched.group(1).lower(), matched.group(1).lower())
    encoded = re.sub(r"\s+", "", matched.group(2))
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("invalid MinerU image base64") from None
    if not data or len(data) > MAX_DECODED_IMAGE_BYTES:
        raise ValueError("MinerU image size is invalid")
    try:
        with Image.open(BytesIO(data)) as probe:
            image_format = (probe.format or "").upper()
            width, height = probe.size
            probe.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValueError("MinerU image bytes are invalid") from None
    if image_format not in _SUPPORTED_IMAGE_FORMATS:
        raise ValueError("MinerU image format is unsupported")
    extension, actual_mime = _SUPPORTED_IMAGE_FORMATS[image_format]
    if declared_mime != actual_mime:
        raise ValueError("MinerU image MIME does not match its bytes")
    if width <= 0 or height <= 0 or width * height > MAX_DECODED_IMAGE_PIXELS:
        raise ValueError("MinerU image dimensions are invalid")
    return _DecodedImage(
        data=data,
        extension=extension,
        mime_type=actual_mime,
        content_hash=sha256(data).hexdigest(),
        width=width,
        height=height,
    )


def _guard_current(generation_guard: GenerationGuard | None) -> None:
    if generation_guard is not None and not generation_guard():
        raise MinerUStaleResultError("MinerU mapping belongs to an obsolete parse generation")


def _write_asset_files(
    asset_root: Path,
    asset_id: str,
    image: _DecodedImage,
    *,
    generation_guard: GenerationGuard | None,
) -> None:
    _guard_current(generation_guard)
    asset_root.mkdir(parents=True, exist_ok=True)
    image_path = asset_root / f"{asset_id}.{image.extension}"
    if not image_path.exists():
        _guard_current(generation_guard)
        image_path.write_bytes(image.data)
        _guard_current(generation_guard)
    thumbnail_path = asset_root / f"thumb_{asset_id}.png"
    if not thumbnail_path.exists():
        _guard_current(generation_guard)
        with Image.open(BytesIO(image.data)) as source:
            source.load()
            thumbnail = source.copy()
            thumbnail.thumbnail((320, 320))
            if thumbnail.mode not in {"RGB", "RGBA", "L", "LA", "P"}:
                thumbnail = thumbnail.convert("RGBA")
            thumbnail.save(thumbnail_path, format="PNG")
        _guard_current(generation_guard)


def _map_asset(
    *,
    book_id: str,
    page: int,
    image_path: object,
    caption: str,
    bbox: list[float] | None,
    asset_type: str,
    image_payloads: dict[str, str],
    asset_root: Path,
    generation_guard: GenerationGuard | None,
) -> tuple[Asset | None, str | None]:
    image_name = _safe_image_name(image_path)
    if image_name is None:
        return None, "mineru_image_path_invalid"
    payload = image_payloads.get(image_name)
    if payload is None:
        return None, "mineru_image_payload_missing"
    try:
        decoded = _decode_image(payload)
    except ValueError:
        return None, "mineru_image_payload_invalid"
    identity = f"{book_id}\0{page}\0{image_name}\0{decoded.content_hash}"
    asset_id = f"mineru_{sha256(identity.encode('utf-8')).hexdigest()[:20]}"
    _write_asset_files(
        asset_root,
        asset_id,
        decoded,
        generation_guard=generation_guard,
    )
    return (
        Asset(
            asset_id=asset_id,
            book_id=book_id,
            source_type="mineru",
            page=page,
            type=asset_type,
            caption=caption,
            bbox=bbox,
            image_url=f"/api/books/{book_id}/assets/{asset_id}/file",
            thumbnail_url=f"/api/books/{book_id}/assets/{asset_id}/thumbnail",
            source_page_image_url=None,
            review_status="ready" if caption.strip() else "needs_review",
            source_parser="mineru",
            content_hash=decoded.content_hash,
            metadata={
                "mineru_image_name": image_name,
                "mime_type": decoded.mime_type,
                "width": decoded.width,
                "height": decoded.height,
            },
        ),
        None,
    )


def _block_shape(item: dict[str, Any]) -> tuple[str, str, int | None, str | None, dict[str, object]] | None:
    source_type = str(item.get("type") or "").strip().lower()
    metadata: dict[str, object] = {"mineru_type": source_type}
    image_path: str | None = None

    if source_type in _TEXT_TYPES:
        text = _as_text(item.get("text")) or _as_text(item.get("content"))
        if not text:
            return None
        heading_level = _safe_heading_level(item.get("text_level"))
        block_type = "title" if source_type == "title" or heading_level is not None else source_type
        if block_type == "text":
            block_type = "paragraph"
        if heading_level is not None:
            metadata["text_level"] = heading_level
        return block_type, text, heading_level, None, metadata

    if source_type == "list":
        items = _text_list(item.get("list_items"))
        if items:
            metadata["list_items"] = items
            return "list", "\n".join(f"- {value}" for value in items), None, "markdown", metadata
        text = _as_text(item.get("text"))
        return ("list", text, None, "markdown", metadata) if text else None

    if source_type in _FORMULA_TYPES:
        text = _as_text(item.get("text")) or _as_text(item.get("content"))
        image_path = _as_text(item.get("img_path")) or None
        metadata.update({"text_format": item.get("text_format") or "latex"})
        if image_path:
            metadata["image_path"] = image_path
        if not text and not image_path:
            return None
        return "formula", text, None, "latex", metadata

    if source_type == "table":
        captions = _text_list(item.get("table_caption"))
        footnotes = _text_list(item.get("table_footnote"))
        body = _as_text(item.get("table_body"))
        image_path = _as_text(item.get("img_path")) or None
        metadata.update({"table_caption": captions, "table_body": body, "table_footnote": footnotes})
        if image_path:
            metadata["image_path"] = image_path
        text = _join_parts(captions, _table_search_text(body), footnotes)
        if not text and not image_path:
            return None
        content_format = "html" if "<table" in body.lower() else "structured_text"
        return "table", text, None, content_format, metadata

    if source_type in _VISUAL_TYPES:
        caption_key = "image_caption" if source_type == "image" else "chart_caption"
        footnote_key = "image_footnote" if source_type == "image" else "chart_footnote"
        captions = _text_list(item.get(caption_key))
        footnotes = _text_list(item.get(footnote_key))
        analysis = _as_text(item.get("content"))
        image_path = _as_text(item.get("img_path")) or None
        metadata.update({caption_key: captions, footnote_key: footnotes, "analysis": analysis})
        if image_path:
            metadata["image_path"] = image_path
        text = _join_parts(captions, analysis, footnotes)
        if not text and not image_path:
            return None
        return ("figure" if source_type == "image" else "chart"), text, None, None, metadata

    return None


def map_mineru_document(
    book_id: str,
    document: MinerUResultDocument,
    *,
    backend: str,
    version: str,
    asset_root: Path,
    expected_page_count: int | None = None,
    generation_guard: GenerationGuard | None = None,
) -> MinerUMappingResult:
    """Map one MinerU protocol-v2 document into CloudPath models.

    ``expected_page_count`` should be supplied from upload validation for PDF
    and image inputs. If omitted, it is derived from MinerU's page indices.
    The function never writes or replaces document artifacts such as
    ``pages.json``; it only materializes validated image/thumbnail files under
    the caller-provided ``asset_root``.
    """

    _guard_current(generation_guard)
    raw_content = deepcopy(document.content_list or [])
    raw_middle = deepcopy(document.middle_json or {})
    page_sizes, page_confidences, middle_pages, warnings = _middle_page_data(raw_middle)
    image_payloads = _image_payloads(document.images)

    observed_indices = set(middle_pages)
    for item in raw_content:
        if isinstance(item, dict) and isinstance(item.get("page_idx"), int) and int(item["page_idx"]) >= 0:
            observed_indices.add(int(item["page_idx"]))
    inferred_page_count = max(observed_indices) + 1 if observed_indices else 0
    page_count = inferred_page_count if expected_page_count is None else max(0, int(expected_page_count))

    counters: dict[int, dict[str, int]] = {
        page: {
            "source_item_count": 0,
            "mapped_item_count": 0,
            "duplicate_count": 0,
            "unknown_item_count": 0,
            "malformed_item_count": 0,
            "referenced_asset_count": 0,
            "mapped_asset_count": 0,
            "referenced_bbox_count": 0,
            "mapped_bbox_count": 0,
        }
        for page in range(1, page_count + 1)
    }
    blocks_by_page: dict[int, list[TextBlock]] = {page: [] for page in counters}
    page_warnings: dict[int, list[QualityWarning]] = {page: [] for page in counters}
    seen_blocks: dict[int, set[str]] = {page: set() for page in counters}
    assets_by_id: dict[str, Asset] = {}

    def add_warning(page: int | None, code: str, message: str) -> None:
        warning = QualityWarning(page=page, code=code, message=message)
        warnings.append(warning)
        if page is not None and page in page_warnings:
            page_warnings[page].append(warning)

    for content_index, raw_item in enumerate(raw_content):
        if not isinstance(raw_item, dict):
            add_warning(None, "mineru_content_item_invalid", "MinerU content_list contains a non-object item")
            continue
        raw_page_index = raw_item.get("page_idx")
        if not isinstance(raw_page_index, int) or isinstance(raw_page_index, bool):
            add_warning(None, "mineru_content_page_missing", "MinerU content item has no valid page index")
            continue
        page = raw_page_index + 1
        if raw_page_index < 0 or page not in counters:
            add_warning(None, "mineru_content_page_out_of_range", "MinerU content item page is outside the validated source range")
            continue

        counter = counters[page]
        counter["source_item_count"] += 1
        raw_bbox = raw_item.get("bbox")
        if raw_bbox is not None:
            counter["referenced_bbox_count"] += 1
        bbox = _canonical_bbox(raw_bbox, page_sizes.get(raw_page_index))
        if bbox is not None:
            counter["mapped_bbox_count"] += 1
        elif raw_bbox is not None:
            add_warning(page, "mineru_bbox_unmapped", "MinerU bbox could not be converted to source-page coordinates")

        shaped = _block_shape(raw_item)
        source_type = str(raw_item.get("type") or "").strip().lower()
        if shaped is None:
            if source_type not in _TEXT_TYPES.union({"list", "table"}).union(_FORMULA_TYPES).union(_VISUAL_TYPES):
                counter["unknown_item_count"] += 1
                add_warning(page, "mineru_content_type_unknown", "MinerU returned an unsupported content type")
            else:
                counter["malformed_item_count"] += 1
                add_warning(page, "mineru_content_item_malformed", "MinerU returned an empty or malformed content item")
            continue

        block_type, text, heading_level, content_format, metadata = shaped
        counter["mapped_item_count"] += 1
        metadata.update(
            {
                "mineru_content_index": content_index,
                "normalized_bbox": deepcopy(raw_bbox) if isinstance(raw_bbox, list) else None,
                "page_size": list(page_sizes[raw_page_index]) if raw_page_index in page_sizes else None,
            }
        )

        image_path = metadata.get("image_path")
        asset_ids: list[str] = []
        if image_path:
            counter["referenced_asset_count"] += 1
            asset_caption = text if block_type in {"figure", "chart"} else _join_parts(
                metadata.get("table_caption") if block_type == "table" else []
            )
            asset, asset_error = _map_asset(
                book_id=book_id,
                page=page,
                image_path=image_path,
                caption=asset_caption,
                bbox=bbox,
                asset_type=block_type,
                image_payloads=image_payloads,
                asset_root=asset_root,
                generation_guard=generation_guard,
            )
            if asset is not None:
                assets_by_id.setdefault(asset.asset_id, asset)
                asset_ids.append(asset.asset_id)
                counter["mapped_asset_count"] += 1
            elif asset_error is not None:
                add_warning(page, asset_error, "MinerU referenced image could not be safely materialized")

        block_id = _stable_block_id(
            page=page,
            block_type=block_type,
            text=text,
            bbox=bbox,
            source_identity={
                "image_path": metadata.get("image_path"),
                "table_body": metadata.get("table_body"),
                "text_level": metadata.get("text_level"),
            },
        )
        if block_id in seen_blocks[page]:
            counter["duplicate_count"] += 1
            add_warning(page, "mineru_block_duplicate", "Duplicate MinerU block was removed")
            continue
        seen_blocks[page].add(block_id)
        blocks_by_page[page].append(
            TextBlock(
                block_id=block_id,
                page=page,
                type=block_type,
                text=text,
                bbox=bbox,
                confidence=None,
                heading_level=heading_level,
                source_parser="mineru",
                source_block_id=None,
                content_format=content_format,
                content_hash=_content_hash(text),
                asset_ids=asset_ids,
                metadata=metadata,
            )
        )

    stats = [PageMappingStats(page=page, **counters[page]) for page in sorted(counters)]
    stats_by_page = {item.page: item for item in stats}
    pages: list[PageResult] = []
    for page in sorted(counters):
        page_index = page - 1
        blocks = blocks_by_page[page]
        page_text = "\n".join(block.text for block in blocks if block.text.strip())
        source_missing = page_index not in middle_pages and counters[page]["source_item_count"] == 0
        if source_missing:
            add_warning(page, "mineru_source_page_missing", "MinerU returned no structured data for a source page")
        layout_regions = [
            LayoutRegion(
                region_id=block.block_id.replace("_mineru_", "_layout_mineru_", 1),
                page=page,
                type=block.type,
                bbox=block.bbox,
                confidence=page_confidences.get(page_index, 1.0),
                source="mineru",
            )
            for block in blocks
            if block.bbox is not None
        ]
        initial_page = PageResult(
            page=page,
            pdf_page_index=page_index,
            text=page_text,
            needs_ocr=not bool(page_text.strip()),
            blocks=blocks,
            ocr_confidence=page_confidences.get(page_index),
            quality_warnings=page_warnings[page],
            layout_regions=layout_regions,
            parser="mineru",
            source_width=page_sizes.get(page_index, (None, None))[0],
            source_height=page_sizes.get(page_index, (None, None))[1],
            metadata={
                "mapper_version": MAPPER_VERSION,
                "mineru_backend": backend,
                "mineru_version": version,
                "source_page_missing": source_missing,
            },
        )
        initial_quality = evaluate_page_quality(initial_page, stats_by_page[page])
        if not initial_quality.passed:
            add_warning(page, "mineru_page_quality_rejected", "MinerU page did not pass the frozen quality-v1 gate")
        pages.append(
            initial_page.model_copy(
                update={
                    "needs_ocr": not initial_quality.passed,
                    "quality_score": initial_quality.quality_score,
                    "quality_warnings": page_warnings[page],
                }
            )
        )

    quality = evaluate_document_quality(
        pages,
        expected_page_count=page_count,
        mapping_stats=stats,
    )
    _guard_current(generation_guard)
    return MinerUMappingResult(
        book_id=book_id,
        backend=backend,
        version=version,
        pages=pages,
        assets=list(assets_by_id.values()),
        warnings=warnings,
        stats=stats,
        quality=quality,
        raw_content=raw_content,
        raw_middle=raw_middle,
    )


__all__ = ["MAPPER_VERSION", "MinerUMappingResult", "map_mineru_document"]
