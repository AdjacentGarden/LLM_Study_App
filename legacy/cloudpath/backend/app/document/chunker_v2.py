from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

from app.document.chunk_protocol import (
    BgeM3TokenCounter,
    FrozenChunkConfig,
    is_chunk_indexable,
    normalize_for_hash,
    render_embedding_text,
)
from app.schemas.books import Asset, Chapter, Chunk


QUALITY_VERSION = "quality-v1"
_NORMAL_TYPES = {"text", "paragraph", "list", "body", "slide_text", "note", "notes"}
_HEADING_TYPES = {"title", "heading", "section_title", "slide_title"}
_FIGURE_TYPES = {"figure", "image", "chart", "diagram"}
_DISCARD_TYPES = {"header", "footer", "page_number", "watermark", "ocr_pending"}
_PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:(?:page|p\.?|第)\s*)?(?:[-–—]\s*)?"
    r"(?:\d{1,5}|[ivxlcdm]{1,10})"
    r"(?:\s*(?:/|of|页，共)\s*\d{1,5}\s*页?)?"
    r"(?:\s*[-–—])?\s*$",
    re.IGNORECASE,
)
_CELL_RANGE_RE = re.compile(r"^\s*\$?([A-Z]{1,3})\$?(\d+)(?::\$?([A-Z]{1,3})\$?(\d+))?\s*$", re.IGNORECASE)


@dataclass(slots=True)
class _Unit:
    order: int
    chapter_id: str
    page_start: int
    page_end: int
    content_type: str
    body: str
    heading_path: tuple[str, ...]
    source_block_ids: tuple[str, ...]
    quality_score: float
    parser: str | None
    parser_version: str | None
    asset_ids: tuple[str, ...] = ()
    bbox: list[float] | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def indexable_bucket(self) -> bool:
        return bool(self.metadata.get("indexable", False))


@dataclass(slots=True)
class _Draft:
    order: int
    chapter_id: str
    page_start: int
    page_end: int
    content_type: str
    body: str
    heading_path: tuple[str, ...]
    source_block_ids: tuple[str, ...]
    quality_score: float
    parser: str | None
    parser_version: str | None
    asset_ids: tuple[str, ...] = ()
    bbox: list[float] | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    overlap_text: str = ""
    overlap_page_start: int | None = None
    overlap_source_block_ids: tuple[str, ...] = ()


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[list[str], bool]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._row_has_header = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag == "tr":
            self._row = []
            self._row_has_header = False
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
            self._row_has_header = self._row_has_header or tag == "th"
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(_clean(" ".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append((self._row, self._row_has_header))
            self._row = None
            self._cell = None


class _CounterAdapter:
    def __init__(self, counter: object) -> None:
        self.raw = counter

    def count(self, text: str) -> int:
        method = getattr(self.raw, "count", None)
        if callable(method):
            return int(method(text))
        method = getattr(self.raw, "count_tokens", None)
        if callable(method):
            return int(method(text))
        if callable(self.raw):
            return int(self.raw(text))
        raise TypeError("token_counter must expose count(text), count_tokens(text), or be callable")

    def encode(self, text: str) -> list[Any] | None:
        method = getattr(self.raw, "encode", None)
        if not callable(method):
            return None
        return list(method(text))

    def decode(self, tokens: Sequence[Any]) -> str | None:
        method = getattr(self.raw, "decode", None)
        if not callable(method):
            return None
        return str(method(tokens))

    def prefix(self, text: str, maximum: int) -> tuple[str, str]:
        text = _clean(text)
        if maximum <= 0 or not text:
            return "", text
        tokens = self.encode(text)
        if tokens is not None and len(tokens) > maximum:
            left = self.decode(tokens[:maximum])
            right = self.decode(tokens[maximum:])
            if left is not None and right is not None:
                return _clean(left), _clean(right)
        if self.count(text) <= maximum:
            return text, ""

        # Test doubles and future tokenizer adapters may only expose count().
        # Binary-searching character boundaries preserves all source text and
        # delegates the actual token accounting to that counter.
        low, high = 1, len(text)
        best = 0
        while low <= high:
            middle = (low + high) // 2
            candidate = text[:middle].rstrip()
            if candidate and self.count(candidate) <= maximum:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best <= 0:
            return text[:1], text[1:].lstrip()
        boundary = best
        for matched in re.finditer(r"(?:\s+|(?<=[。！？.!?;；]))", text[: best + 1]):
            if matched.end() >= max(1, best // 2):
                boundary = matched.end()
        return _clean(text[:boundary]), _clean(text[boundary:])

    def suffix(self, text: str, maximum: int) -> str:
        text = _clean(text)
        if maximum <= 0 or not text:
            return ""
        tokens = self.encode(text)
        if tokens is not None and len(tokens) > maximum:
            decoded = self.decode(tokens[-maximum:])
            if decoded is not None:
                return _clean(decoded)
        if self.count(text) <= maximum:
            return text
        low, high = 0, len(text) - 1
        best = len(text) - 1
        while low <= high:
            middle = (low + high) // 2
            candidate = text[middle:].lstrip()
            if candidate and self.count(candidate) <= maximum:
                best = middle
                high = middle - 1
            else:
                low = middle + 1
        return _clean(text[best:])


def _clean(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[\t \f\v]+", " ", line).strip() for line in text.split("\n")]
    result: list[str] = []
    for line in lines:
        if line or (result and result[-1]):
            result.append(line)
    return "\n".join(result).strip()


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in items:
        item = _clean(raw)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result)


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _block_metadata(block: Mapping[str, object]) -> Mapping[str, object]:
    return _as_mapping(block.get("metadata"))


def _resolve_config(config: FrozenChunkConfig | Mapping[str, object] | object | None) -> FrozenChunkConfig:
    if config is None:
        return FrozenChunkConfig.from_settings()
    if isinstance(config, FrozenChunkConfig):
        return config
    if isinstance(config, Mapping):
        accepted = {
            name: config[name]
            for name in (
                "target_tokens",
                "max_tokens",
                "min_tokens",
                "overlap_tokens",
                "atomic_content_hard_max_tokens",
                "quality_threshold",
                "chunk_version",
            )
            if name in config
        }
        return FrozenChunkConfig(**accepted)
    accepted = {
        name: getattr(config, name)
        for name in (
            "target_tokens",
            "max_tokens",
            "min_tokens",
            "overlap_tokens",
            "atomic_content_hard_max_tokens",
            "quality_threshold",
            "chunk_version",
        )
        if hasattr(config, name)
    }
    return FrozenChunkConfig(**accepted)


def _chapter_path(chapter: Chapter, chapters: Mapping[str, Chapter]) -> tuple[str, ...]:
    path: list[str] = []
    current: Chapter | None = chapter
    visited: set[str] = set()
    while current is not None and current.chapter_id not in visited:
        visited.add(current.chapter_id)
        title = _clean(current.ai_title or current.source_title)
        if title:
            path.append(title)
        current = chapters.get(current.parent_id or "")
    return tuple(reversed(path))


def _page_chapter(page: int, chapters: Sequence[Chapter]) -> Chapter | None:
    candidates = [chapter for chapter in chapters if chapter.page_start <= page <= chapter.page_end]
    if not candidates:
        return None
    # Deepest chapter wins. Narrower ranges make malformed same-level overlaps
    # deterministic, and chapter_id removes dependence on input list order.
    return max(
        candidates,
        key=lambda item: (
            int(item.level),
            -(int(item.page_end) - int(item.page_start)),
            int(item.page_start),
            item.chapter_id,
        ),
    )


def _is_page_number(text: str) -> bool:
    return bool(_PAGE_NUMBER_RE.fullmatch(text))


def _is_margin_block(block: Mapping[str, object]) -> bool:
    bbox = block.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return False
    try:
        top, bottom = float(bbox[1]), float(bbox[3])
    except (TypeError, ValueError):
        return False
    metadata = _block_metadata(block)
    size = metadata.get("page_size")
    if isinstance(size, list) and len(size) == 2:
        try:
            height = float(size[1])
        except (TypeError, ValueError):
            height = 0.0
        if height > 0:
            return top <= height * 0.1 or bottom >= height * 0.9
    if 0.0 <= top <= 1.0 and 0.0 <= bottom <= 1.0:
        return top <= 0.1 or bottom >= 0.9
    return False


def _repeated_marginal_texts(pages: Sequence[Mapping[str, object]]) -> set[str]:
    by_text: dict[str, set[int]] = defaultdict(set)
    total_pages = max(1, len(pages))
    for page in pages:
        number = int(page.get("page") or 0)
        for raw_block in page.get("blocks", []) if isinstance(page.get("blocks"), list) else []:
            if not isinstance(raw_block, Mapping) or not _is_margin_block(raw_block):
                continue
            block_type = _clean(raw_block.get("type")).lower()
            if block_type in _HEADING_TYPES.union({"table", "formula"}).union(_FIGURE_TYPES):
                continue
            text = normalize_for_hash(_clean(raw_block.get("text")))
            if 1 < len(text) <= 120:
                by_text[text].add(number)
    return {
        text
        for text, occurrences in by_text.items()
        if len(occurrences) >= 2 and len(occurrences) / total_pages >= 0.5
    }


def _heading_path(base: Sequence[str], stack: Sequence[tuple[int, str]]) -> tuple[str, ...]:
    result: list[str] = []
    for item in [*base, *(text for _, text in stack)]:
        cleaned = _clean(item).strip("*# ")
        if not cleaned:
            continue
        if result and normalize_for_hash(result[-1]) == normalize_for_hash(cleaned):
            continue
        result.append(cleaned)
    return tuple(result)


def _clamp(value: object, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _quality_for(
    page: Mapping[str, object],
    block: Mapping[str, object],
    *,
    content_type: str,
    body: str,
) -> tuple[float, dict[str, object], float]:
    """Recompute the frozen quality-v1 components for one semantic unit."""

    semantic_characters = [character for character in body if not character.isspace()]
    invalid_count = sum(
        character == "\ufffd" or unicodedata.category(character) in {"Cc", "Cs"}
        for character in semantic_characters
    )
    invalid_ratio = invalid_count / len(semantic_characters) if semantic_characters else 0.0
    valid_character_score = _clamp(1.0 - invalid_ratio) if semantic_characters else 0.0
    content_coverage_score = (
        1.0
        if content_type in {"table", "formula", "figure", "chart"} and semantic_characters
        else _clamp(len(semantic_characters) / 20.0)
    )

    parser = _clean(block.get("source_parser") or page.get("parser")).lower()
    provider = _clean(page.get("ocr_provider")).lower()
    if parser == "mock" or provider == "mock":
        ocr_confidence_score = 0.0
    elif page.get("ocr_confidence") is not None or block.get("confidence") is not None:
        # Page confidence is an aggregate and must not hide a specifically
        # low-confidence OCR block. When both exist, use the conservative one.
        confidence_values = [
            _clamp(value)
            for value in (block.get("confidence"), page.get("ocr_confidence"))
            if value is not None
        ]
        ocr_confidence_score = min(confidence_values)
    elif parser in {"ocr", "paddleocr"} or provider in {"ocr", "paddleocr"}:
        ocr_confidence_score = 0.0
    else:
        ocr_confidence_score = 1.0 if semantic_characters else 0.0

    metadata = _block_metadata(block)
    page_metadata = _as_mapping(page.get("metadata"))
    mapping_completeness_score = _clamp(
        metadata.get("mapping_completeness_score", page_metadata.get("mapping_completeness_score", 1.0)),
        1.0,
    )
    duplicate_count = int(metadata.get("duplicate_count", page_metadata.get("duplicate_count", 0)) or 0)
    mapped_count = int(metadata.get("mapped_item_count", page_metadata.get("mapped_item_count", 1)) or 1)
    deduplication_score = _clamp(1.0 - duplicate_count / max(1, mapped_count), 1.0)
    score = round(
        0.25 * valid_character_score
        + 0.30 * content_coverage_score
        + 0.15 * ocr_confidence_score
        + 0.20 * mapping_completeness_score
        + 0.10 * deduplication_score,
        6,
    )
    try:
        page_gate_score = _clamp(page.get("quality_score"), score) if page.get("quality_score") is not None else score
    except (TypeError, ValueError):
        page_gate_score = 0.0
    if page.get("needs_ocr") is True or parser == "mock":
        page_gate_score = 0.0
    components: dict[str, object] = {
        "valid_character_score": round(valid_character_score, 6),
        "content_coverage_score": round(content_coverage_score, 6),
        "ocr_confidence_score": round(ocr_confidence_score, 6),
        "mapping_completeness_score": round(mapping_completeness_score, 6),
        "deduplication_score": round(deduplication_score, 6),
        "semantic_character_count": len(semantic_characters),
        "invalid_character_count": invalid_count,
        "invalid_character_ratio": round(invalid_ratio, 6),
        "weights": {
            "valid_character_score": 0.25,
            "content_coverage_score": 0.30,
            "ocr_confidence_score": 0.15,
            "mapping_completeness_score": 0.20,
            "deduplication_score": 0.10,
        },
        "weighted_score": score,
        "source_page_quality_gate": round(page_gate_score, 6),
    }
    return score, components, page_gate_score


def _parser_version(page: Mapping[str, object], block: Mapping[str, object]) -> str | None:
    metadata = {**_as_mapping(page.get("metadata")), **_block_metadata(block)}
    for key in ("parser_version", "mapper_version", "mineru_version", "version"):
        value = _clean(metadata.get(key))
        if value:
            return value
    return None


def _semantic_metadata(page: Mapping[str, object], block: Mapping[str, object]) -> dict[str, object]:
    page_meta = _as_mapping(page.get("metadata"))
    block_meta = _block_metadata(block)
    result: dict[str, object] = {}
    aliases = {
        "slide_number": ("slide_number", "slide", "slide_index"),
        "slide_title": ("slide_title",),
        "speaker_notes": ("speaker_notes", "notes"),
        "sheet_name": ("sheet_name", "worksheet", "sheet"),
        "cell_range": ("cell_range", "range"),
        "source_format": ("source_format", "file_type", "document_type"),
        "source_unit": ("source_unit",),
        "source_label": ("source_label",),
        "has_stable_page": ("has_stable_page",),
        "document_block_number": ("document_block_number", "block_number"),
    }
    for output, keys in aliases.items():
        for source in (block_meta, page_meta):
            value = next((source.get(key) for key in keys if source.get(key) not in (None, "")), None)
            if value not in (None, ""):
                result[output] = value
                break
    return result


def _bbox_union(units: Sequence[_Unit]) -> list[float] | None:
    if not units or len({unit.page_start for unit in units}.union(unit.page_end for unit in units)) != 1:
        return None
    boxes = [unit.bbox for unit in units if isinstance(unit.bbox, list) and len(unit.bbox) == 4]
    if len(boxes) != len(units):
        return None
    return [
        min(float(box[0]) for box in boxes),
        min(float(box[1]) for box in boxes),
        max(float(box[2]) for box in boxes),
        max(float(box[3]) for box in boxes),
    ]


def _plain_table_rows(text: str) -> list[tuple[list[str], bool]]:
    rows: list[tuple[list[str], bool]] = []
    for line in _clean(text).splitlines():
        if not line.strip():
            continue
        cells = [_clean(cell) for cell in re.split(r"\s*\|\s*|\t+", line.strip(" |"))]
        rows.append((cells or [line], False))
    return rows


def _table_rows(block: Mapping[str, object]) -> list[tuple[list[str], bool]]:
    metadata = _block_metadata(block)
    body = _clean(metadata.get("table_body"))
    if "<table" in body.lower():
        parser = _TableParser()
        parser.feed(body)
        parser.close()
        if parser.rows:
            return parser.rows
    raw_rows = metadata.get("table_rows")
    if isinstance(raw_rows, list):
        rows = []
        for index, raw in enumerate(raw_rows):
            if isinstance(raw, list):
                rows.append(([_clean(cell) for cell in raw], index == 0))
        if rows:
            return rows
    return _plain_table_rows(_clean(block.get("text")))


def _render_row(row: Sequence[str]) -> str:
    return " | ".join(_clean(cell) for cell in row)


def _render_data_row(row: Sequence[str]) -> str:
    """Render cells as one searchable phrase while structured rows retain columns."""

    return " ".join(cell for cell in (_clean(value) for value in row) if cell)


def _table_headers(rows: Sequence[tuple[list[str], bool]]) -> tuple[list[list[str]], list[list[str]]]:
    if not rows:
        return [], []
    header_count = 0
    for _, is_header in rows:
        if not is_header:
            break
        header_count += 1
    if header_count == 0:
        header_count = 1
    # Spreadsheet exports often put a merged table title above the actual
    # column names. Keep both so every split remains intelligible.
    if header_count == 1 and len(rows) > 1:
        populated = sum(bool(cell) for cell in rows[0][0])
        next_populated = sum(bool(cell) for cell in rows[1][0])
        if populated <= 1 < next_populated:
            header_count = 2
    return [row for row, _ in rows[:header_count]], [row for row, _ in rows[header_count:]]


def _unit_prefix(metadata: Mapping[str, object]) -> list[str]:
    result: list[str] = []
    if metadata.get("slide_number") is not None:
        label = f"Slide {metadata['slide_number']}"
        if metadata.get("slide_title"):
            label += f": {_clean(metadata['slide_title'])}"
        result.append(label)
    elif metadata.get("slide_title"):
        result.append(f"Slide: {_clean(metadata['slide_title'])}")
    if metadata.get("sheet_name"):
        label = f"Sheet: {_clean(metadata['sheet_name'])}"
        if metadata.get("cell_range"):
            label += f" ({_clean(metadata['cell_range'])})"
        result.append(label)
    return result


def _base_unit(
    *,
    order: int,
    chapter: Chapter,
    page: Mapping[str, object],
    block: Mapping[str, object],
    content_type: str,
    body: str,
    heading_path: tuple[str, ...],
    config: FrozenChunkConfig,
    asset_ids: Iterable[str] = (),
    metadata: Mapping[str, object] | None = None,
) -> _Unit:
    cleaned_body = _clean(body)
    score, quality_components, source_page_gate = _quality_for(
        page,
        block,
        content_type=content_type,
        body=cleaned_body,
    )
    semantic = {**_semantic_metadata(page, block), **dict(metadata or {})}
    semantic.update(
        {
            "quality_version": QUALITY_VERSION,
            "quality_formula_version": QUALITY_VERSION,
            "quality_components": quality_components,
            "indexable": (
                is_chunk_indexable(score, config=config)
                and source_page_gate >= config.quality_threshold
            ),
        }
    )
    bbox = block.get("bbox")
    normalized_bbox = [float(value) for value in bbox] if isinstance(bbox, list) and len(bbox) == 4 else None
    return _Unit(
        order=order,
        chapter_id=chapter.chapter_id,
        page_start=int(page.get("page") or 1),
        page_end=int(page.get("page") or 1),
        content_type=content_type,
        body=cleaned_body,
        heading_path=heading_path,
        source_block_ids=_unique([_clean(block.get("block_id"))]),
        quality_score=score,
        parser=_clean(block.get("source_parser") or page.get("parser")) or None,
        parser_version=_parser_version(page, block),
        asset_ids=_unique(asset_ids),
        bbox=normalized_bbox,
        metadata=semantic,
    )


def _collect_units(
    pages: Sequence[Mapping[str, object]],
    chapters: Sequence[Chapter],
    assets: Mapping[str, Asset],
    config: FrozenChunkConfig,
) -> list[_Unit]:
    chapter_by_id = {chapter.chapter_id: chapter for chapter in chapters}
    bases = {chapter.chapter_id: _chapter_path(chapter, chapter_by_id) for chapter in chapters}
    heading_stacks: dict[str, list[tuple[int, str]]] = defaultdict(list)
    repeated_margins = _repeated_marginal_texts(pages)
    units: list[_Unit] = []
    referenced_asset_ids: set[str] = set()
    pages_by_number = {int(page.get("page") or 0): page for page in pages}

    for page in sorted(pages, key=lambda item: int(item.get("page") or 0)):
        page_number = int(page.get("page") or 0)
        chapter = _page_chapter(page_number, chapters)
        if chapter is None:
            continue
        stack = heading_stacks[chapter.chapter_id]
        raw_blocks = page.get("blocks", [])
        if not isinstance(raw_blocks, list):
            continue
        for block_index, raw_block in enumerate(raw_blocks):
            if not isinstance(raw_block, Mapping):
                continue
            order = page_number * 1_000_000 + block_index * 100
            block_type = _clean(raw_block.get("type")).lower() or "paragraph"
            text = _clean(raw_block.get("text"))
            raw_asset_ids = raw_block.get("asset_ids", [])
            exact_block_asset_ids = [
                str(asset_id)
                for asset_id in raw_asset_ids
                if isinstance(raw_asset_ids, list) and str(asset_id) in assets
            ]
            normalized = normalize_for_hash(text)
            if (
                block_type in _DISCARD_TYPES
                or _is_page_number(text)
                or normalized in repeated_margins
                or _clean(raw_block.get("source_parser")).lower() == "mock"
            ):
                continue
            if block_type in _HEADING_TYPES or raw_block.get("heading_level") is not None:
                if not text:
                    continue
                try:
                    level = max(1, int(raw_block.get("heading_level") or 1))
                except (TypeError, ValueError):
                    level = 1
                stack[:] = [(item_level, item_text) for item_level, item_text in stack if item_level < level]
                stack.append((level, text))
                continue
            if not text and block_type not in _FIGURE_TYPES.union({"table", "formula"}):
                continue

            path = _heading_path(bases[chapter.chapter_id], stack)
            metadata = _block_metadata(raw_block)
            if block_type == "table":
                rows = _table_rows(raw_block)
                semantic_table_text = text or "\n".join(_render_row(row) for row, _ in rows)
                captions = metadata.get("table_caption")
                caption_values = captions if isinstance(captions, list) else [captions] if captions else []
                units.append(
                    _base_unit(
                        order=order,
                        chapter=chapter,
                        page=page,
                        block=raw_block,
                        content_type="table",
                        body=semantic_table_text,
                        heading_path=path,
                        config=config,
                        asset_ids=exact_block_asset_ids,
                        metadata={
                            "table_rows": [row for row, _ in rows],
                            "table_header_flags": [flag for _, flag in rows],
                            "table_captions": [_clean(value) for value in caption_values if _clean(value)],
                        },
                    )
                )
                referenced_asset_ids.update(exact_block_asset_ids)
                continue
            if block_type == "formula":
                latex = text or _clean(metadata.get("latex"))
                if latex:
                    units.append(
                        _base_unit(
                            order=order,
                            chapter=chapter,
                            page=page,
                            block=raw_block,
                            content_type="formula",
                            body=latex,
                            heading_path=path,
                            config=config,
                            asset_ids=exact_block_asset_ids,
                            metadata={"latex": latex},
                        )
                    )
                    referenced_asset_ids.update(exact_block_asset_ids)
                continue
            if block_type in _FIGURE_TYPES:
                if not text:
                    # A path or bitmap alone is not semantic retrieval content.
                    continue
                units.append(
                    _base_unit(
                        order=order,
                        chapter=chapter,
                        page=page,
                        block=raw_block,
                        content_type="figure" if block_type in {"figure", "image", "diagram"} else "chart",
                        body=text,
                        heading_path=path,
                        config=config,
                        asset_ids=exact_block_asset_ids,
                        metadata={"source_visual_type": block_type, "asset_binding": "exact_block_reference"},
                    )
                )
                referenced_asset_ids.update(exact_block_asset_ids)
                continue

            prefix = _unit_prefix(_semantic_metadata(page, raw_block))
            body = "\n".join([*prefix, text]) if prefix else text
            units.append(
                _base_unit(
                    order=order,
                    chapter=chapter,
                    page=page,
                    block=raw_block,
                    content_type="paragraph" if block_type in _NORMAL_TYPES else block_type,
                    body=body,
                    heading_path=path,
                    config=config,
                    asset_ids=exact_block_asset_ids,
                )
            )
            referenced_asset_ids.update(exact_block_asset_ids)

    # Some MinerU/OCR adapters expose an Asset with a semantic caption but no
    # corresponding normalized visual block. The caption is itself an exact
    # asset-owned semantic source, so preserve it as a V2 figure rather than
    # falling back to a legacy page-nearest binding.
    for asset_index, asset in enumerate(sorted(assets.values(), key=lambda item: (item.page or 0, item.asset_id))):
        if asset.asset_id in referenced_asset_ids or not _clean(asset.caption) or asset.page is None:
            continue
        page_number = int(asset.page)
        page = pages_by_number.get(page_number)
        chapter = _page_chapter(page_number, chapters)
        if page is None or chapter is None:
            continue
        synthetic_block: dict[str, object] = {
            "block_id": f"asset:{asset.asset_id}",
            "type": "figure",
            "text": asset.caption,
            "bbox": asset.bbox,
            "source_parser": asset.source_parser,
            "asset_ids": [asset.asset_id],
            "metadata": dict(asset.metadata),
        }
        units.append(
            _base_unit(
                order=page_number * 1_000_000 + 900_000 + asset_index,
                chapter=chapter,
                page=page,
                block=synthetic_block,
                content_type="figure",
                body=asset.caption,
                heading_path=bases[chapter.chapter_id],
                config=config,
                asset_ids=[asset.asset_id],
                metadata={
                    "source_visual_type": asset.type,
                    "asset_binding": "exact_asset_caption",
                    "asset_source_type": asset.source_type,
                },
            )
        )
    return sorted(units, key=lambda item: item.order)


def _weighted_quality(units: Sequence[_Unit]) -> float:
    total = sum(max(1, len(unit.body)) for unit in units)
    return sum(unit.quality_score * max(1, len(unit.body)) for unit in units) / max(1, total)


def _combine_parser(values: Iterable[str | None]) -> str | None:
    unique = _unique(value for value in values if value)
    return unique[0] if len(unique) == 1 else "mixed" if unique else None


def _column_number(value: str) -> int:
    result = 0
    for character in value.upper():
        result = result * 26 + ord(character) - ord("A") + 1
    return result


def _cell_rectangle(value: object) -> tuple[int, int, int, int] | None:
    matched = _CELL_RANGE_RE.fullmatch(_clean(value))
    if matched is None:
        return None
    start_column = _column_number(matched.group(1))
    start_row = int(matched.group(2))
    end_column = _column_number(matched.group(3) or matched.group(1))
    end_row = int(matched.group(4) or matched.group(2))
    return (
        min(start_column, end_column),
        min(start_row, end_row),
        max(start_column, end_column),
        max(start_row, end_row),
    )


def _office_locations_compatible(first: _Unit, second: _Unit) -> bool:
    first_format = _clean(first.metadata.get("source_format")).lower().lstrip(".")
    second_format = _clean(second.metadata.get("source_format")).lower().lstrip(".")
    if first_format and second_format and first_format != second_format:
        return False

    ppt = "ppt" in first_format or "ppt" in second_format or any(
        unit.metadata.get("slide_number") is not None for unit in (first, second)
    )
    if ppt:
        return (
            first.metadata.get("slide_number") == second.metadata.get("slide_number")
            and first.metadata.get("slide_title") == second.metadata.get("slide_title")
        )

    spreadsheet = "xls" in first_format or "xls" in second_format or any(
        unit.metadata.get("sheet_name") not in (None, "") for unit in (first, second)
    )
    if not spreadsheet:
        return True
    if first.metadata.get("sheet_name") != second.metadata.get("sheet_name"):
        return False
    first_range = first.metadata.get("cell_range")
    second_range = second.metadata.get("cell_range")
    if first_range in (None, "") and second_range in (None, ""):
        return True
    if first_range in (None, "") or second_range in (None, ""):
        return False
    if _clean(first_range).upper() == _clean(second_range).upper():
        return True
    first_rectangle = _cell_rectangle(first_range)
    second_rectangle = _cell_rectangle(second_range)
    if first_rectangle is None or second_rectangle is None:
        return False
    first_left, first_top, first_right, first_bottom = first_rectangle
    second_left, second_top, second_right, second_bottom = second_rectangle
    rows_touch = second_top <= first_bottom + 1 and first_top <= second_bottom + 1
    columns_touch = second_left <= first_right + 1 and first_left <= second_right + 1
    return rows_touch and columns_touch


def _aggregate_quality_components(units: Sequence[_Unit]) -> dict[str, object]:
    names = (
        "valid_character_score",
        "content_coverage_score",
        "ocr_confidence_score",
        "mapping_completeness_score",
        "deduplication_score",
    )
    weights = [max(1, len(unit.body)) for unit in units]
    total = max(1, sum(weights))
    aggregated: dict[str, object] = {}
    for name in names:
        values: list[float] = []
        for unit in units:
            components = _as_mapping(unit.metadata.get("quality_components"))
            values.append(_clamp(components.get(name), 0.0))
        aggregated[name] = round(sum(value * weight for value, weight in zip(values, weights)) / total, 6)
    aggregated["weights"] = {
        "valid_character_score": 0.25,
        "content_coverage_score": 0.30,
        "ocr_confidence_score": 0.15,
        "mapping_completeness_score": 0.20,
        "deduplication_score": 0.10,
    }
    aggregated["weighted_score"] = round(
        0.25 * float(aggregated["valid_character_score"])
        + 0.30 * float(aggregated["content_coverage_score"])
        + 0.15 * float(aggregated["ocr_confidence_score"])
        + 0.20 * float(aggregated["mapping_completeness_score"])
        + 0.10 * float(aggregated["deduplication_score"]),
        6,
    )
    aggregated["aggregation"] = "semantic_character_weighted_mean"
    aggregated["source_scores"] = [round(unit.quality_score, 6) for unit in units]
    aggregated["source_page_quality_gates"] = [
        _as_mapping(unit.metadata.get("quality_components")).get("source_page_quality_gate")
        for unit in units
    ]
    return aggregated


def _combine_metadata(units: Sequence[_Unit], *, config: FrozenChunkConfig) -> dict[str, object]:
    metadata: dict[str, object] = {
        "quality_version": QUALITY_VERSION,
        "quality_formula_version": QUALITY_VERSION,
        "quality_components": _aggregate_quality_components(units),
        "indexable": all(unit.indexable_bucket for unit in units),
    }
    for key in (
        "slide_number",
        "slide_title",
        "speaker_notes",
        "sheet_name",
        "cell_range",
        "source_format",
        "source_unit",
        "has_stable_page",
    ):
        values = [unit.metadata.get(key) for unit in units if unit.metadata.get(key) not in (None, "")]
        if values and all(value == values[0] for value in values):
            metadata[key] = values[0]
        elif values:
            metadata[f"{key}s"] = list(dict.fromkeys(str(value) for value in values))
    block_numbers = sorted(
        {
            int(value)
            for unit in units
            if (value := unit.metadata.get("document_block_number")) is not None
        }
    )
    if block_numbers:
        metadata["document_block_start"] = block_numbers[0]
        metadata["document_block_end"] = block_numbers[-1]
    if not metadata["indexable"]:
        metadata.update({"quarantined": True, "index_status": "quarantined", "quality_threshold": config.quality_threshold})
    return metadata


def _draft_from_units(
    units: Sequence[_Unit],
    *,
    body: str | None = None,
    content_type: str | None = None,
    config: FrozenChunkConfig,
    metadata: Mapping[str, object] | None = None,
    warnings: Iterable[str] = (),
) -> _Draft:
    combined_metadata = _combine_metadata(units, config=config)
    combined_metadata.update(dict(metadata or {}))
    return _Draft(
        order=min(unit.order for unit in units),
        chapter_id=units[0].chapter_id,
        page_start=min(unit.page_start for unit in units),
        page_end=max(unit.page_end for unit in units),
        content_type=content_type or units[0].content_type,
        body=_clean(body if body is not None else "\n\n".join(unit.body for unit in units)),
        heading_path=units[0].heading_path,
        source_block_ids=_unique(block_id for unit in units for block_id in unit.source_block_ids),
        quality_score=_weighted_quality(units),
        parser=_combine_parser(unit.parser for unit in units),
        parser_version=_combine_parser(unit.parser_version for unit in units),
        asset_ids=_unique(asset_id for unit in units for asset_id in unit.asset_ids),
        bbox=_bbox_union(units),
        metadata=combined_metadata,
        warnings=_unique([*(warning for unit in units for warning in unit.warnings), *warnings]),
    )


def _merge_draft_metadata(first: _Draft, second: _Draft) -> dict[str, object]:
    metadata = {**first.metadata, **second.metadata}
    names = (
        "valid_character_score",
        "content_coverage_score",
        "ocr_confidence_score",
        "mapping_completeness_score",
        "deduplication_score",
    )
    first_components = _as_mapping(first.metadata.get("quality_components"))
    second_components = _as_mapping(second.metadata.get("quality_components"))
    first_weight, second_weight = max(1, len(first.body)), max(1, len(second.body))
    total = first_weight + second_weight
    components: dict[str, object] = {}
    for name in names:
        components[name] = round(
            (
                _clamp(first_components.get(name)) * first_weight
                + _clamp(second_components.get(name)) * second_weight
            ) / total,
            6,
        )
    components["weights"] = {
        "valid_character_score": 0.25,
        "content_coverage_score": 0.30,
        "ocr_confidence_score": 0.15,
        "mapping_completeness_score": 0.20,
        "deduplication_score": 0.10,
    }
    components["weighted_score"] = round(
        0.25 * float(components["valid_character_score"])
        + 0.30 * float(components["content_coverage_score"])
        + 0.15 * float(components["ocr_confidence_score"])
        + 0.20 * float(components["mapping_completeness_score"])
        + 0.10 * float(components["deduplication_score"]),
        6,
    )
    components["aggregation"] = "semantic_character_weighted_mean"
    components["source_scores"] = [
        *list(first_components.get("source_scores", [first.quality_score])),
        *list(second_components.get("source_scores", [second.quality_score])),
    ]
    metadata["quality_components"] = components
    metadata["indexable"] = first.metadata.get("indexable") is not False and second.metadata.get("indexable") is not False
    return metadata


def _embedding_count(counter: _CounterAdapter, body: str, heading: Sequence[str], overlap: str = "") -> int:
    return counter.count(render_embedding_text(body, heading, overlap))


def _split_text_to_limit(
    text: str,
    *,
    heading: Sequence[str],
    limit: int,
    counter: _CounterAdapter,
    fixed_prefix: str = "",
) -> list[str]:
    remaining = _clean(text)
    result: list[str] = []
    while remaining:
        candidate = "\n".join(part for part in (fixed_prefix, remaining) if part)
        if _embedding_count(counter, candidate, heading) <= limit:
            result.append(remaining)
            break
        overhead = _embedding_count(counter, fixed_prefix, heading) if fixed_prefix else _embedding_count(counter, "", heading)
        allowance = max(1, limit - overhead)
        piece, tail = counter.prefix(remaining, allowance)
        if not piece or tail == remaining:
            piece, tail = remaining[:1], remaining[1:]
        result.append(piece)
        remaining = tail
    return result


def _formula_context(units: Sequence[_Unit], config: FrozenChunkConfig, counter: _CounterAdapter) -> list[_Unit]:
    claimed: set[int] = set()
    replacements: dict[int, _Unit] = {}
    for index, unit in enumerate(units):
        if unit.content_type != "formula":
            continue
        selected = [unit]
        before: _Unit | None = None
        after: _Unit | None = None
        for candidate_index, position in ((index - 1, "before"), (index + 1, "after")):
            if not (0 <= candidate_index < len(units)) or candidate_index in claimed:
                continue
            candidate = units[candidate_index]
            if (
                candidate.content_type != "paragraph"
                or candidate.chapter_id != unit.chapter_id
                or candidate.heading_path != unit.heading_path
                or candidate.indexable_bucket != unit.indexable_bucket
                or abs(candidate.page_start - unit.page_start) > 1
            ):
                continue
            trial = [candidate, *selected] if position == "before" else [*selected, candidate]
            formula_body = _render_formula(trial, unit)
            if _embedding_count(counter, formula_body, unit.heading_path) <= config.atomic_content_hard_max_tokens:
                selected = trial
                if position == "before":
                    before = candidate
                else:
                    after = candidate
                claimed.add(candidate_index)
        body = _render_formula(selected, unit)
        metadata = {
            **unit.metadata,
            "latex": unit.body,
            "explanation_before": before.body if before else "",
            "explanation_after": after.body if after else "",
            "quality_components": _aggregate_quality_components(selected),
        }
        replacements[index] = replace(
            unit,
            page_start=min(item.page_start for item in selected),
            page_end=max(item.page_end for item in selected),
            body=body,
            source_block_ids=_unique(block_id for item in selected for block_id in item.source_block_ids),
            quality_score=_weighted_quality(selected),
            parser=_combine_parser(item.parser for item in selected),
            parser_version=_combine_parser(item.parser_version for item in selected),
            bbox=_bbox_union(selected),
            metadata=metadata,
        )
    return [replacements.get(index, unit) for index, unit in enumerate(units) if index not in claimed]


def _render_formula(selected: Sequence[_Unit], formula: _Unit) -> str:
    before = [item.body for item in selected if item.order < formula.order]
    after = [item.body for item in selected if item.order > formula.order]
    parts: list[str] = []
    if before:
        parts.extend(["Explanation:", "\n\n".join(before)])
    parts.extend(["Formula (LaTeX):", formula.body])
    if after:
        parts.extend(["Explanation:", "\n\n".join(after)])
    return "\n".join(parts)


def _normal_drafts(units: Sequence[_Unit], config: FrozenChunkConfig, counter: _CounterAdapter) -> list[_Draft]:
    if not units:
        return []
    expanded: list[_Unit] = []
    for unit in units:
        pieces = _split_text_to_limit(
            unit.body,
            heading=unit.heading_path,
            limit=config.target_tokens,
            counter=counter,
        )
        expanded.extend(replace(unit, body=piece) for piece in pieces)

    drafts: list[_Draft] = []
    current: list[_Unit] = []
    for unit in expanded:
        candidate = [*current, unit]
        body = "\n\n".join(item.body for item in candidate)
        if current and _embedding_count(counter, body, unit.heading_path) > config.target_tokens:
            drafts.append(_draft_from_units(current, content_type="text", config=config))
            current = [unit]
        else:
            current = candidate
    if current:
        drafts.append(_draft_from_units(current, content_type="text", config=config))

    if len(drafts) >= 2 and _embedding_count(counter, drafts[-1].body, drafts[-1].heading_path) < config.min_tokens:
        previous, tail = drafts[-2], drafts[-1]
        merged_body = f"{previous.body}\n\n{tail.body}"
        if _embedding_count(counter, merged_body, previous.heading_path) <= config.max_tokens:
            merged = _Draft(
                order=previous.order,
                chapter_id=previous.chapter_id,
                page_start=min(previous.page_start, tail.page_start),
                page_end=max(previous.page_end, tail.page_end),
                content_type="text",
                body=merged_body,
                heading_path=previous.heading_path,
                source_block_ids=_unique([*previous.source_block_ids, *tail.source_block_ids]),
                quality_score=(
                    previous.quality_score * max(1, len(previous.body))
                    + tail.quality_score * max(1, len(tail.body))
                ) / max(1, len(previous.body) + len(tail.body)),
                parser=_combine_parser([previous.parser, tail.parser]),
                parser_version=_combine_parser([previous.parser_version, tail.parser_version]),
                asset_ids=_unique([*previous.asset_ids, *tail.asset_ids]),
                bbox=None if previous.page_start != tail.page_end else previous.bbox,
                metadata=_merge_draft_metadata(previous, tail),
                warnings=_unique([*previous.warnings, *tail.warnings]),
            )
            drafts[-2:] = [merged]
        else:
            drafts[-1] = replace(tail, warnings=_unique([*tail.warnings, "short_tail"]))
    elif len(drafts) == 1 and _embedding_count(counter, drafts[0].body, drafts[0].heading_path) < config.min_tokens:
        drafts[0] = replace(drafts[0], warnings=_unique([*drafts[0].warnings, "short_tail"]))

    if len(drafts) >= 2 and _embedding_count(counter, drafts[0].body, drafts[0].heading_path) < config.min_tokens:
        head, following = drafts[0], drafts[1]
        merged_body = f"{head.body}\n\n{following.body}"
        if _embedding_count(counter, merged_body, head.heading_path) <= config.max_tokens:
            merged = replace(
                following,
                order=head.order,
                page_start=min(head.page_start, following.page_start),
                body=merged_body,
                source_block_ids=_unique([*head.source_block_ids, *following.source_block_ids]),
                quality_score=(
                    head.quality_score * max(1, len(head.body))
                    + following.quality_score * max(1, len(following.body))
                ) / max(1, len(head.body) + len(following.body)),
                parser=_combine_parser([head.parser, following.parser]),
                parser_version=_combine_parser([head.parser_version, following.parser_version]),
                metadata=_merge_draft_metadata(head, following),
                warnings=_unique([*head.warnings, *following.warnings]),
            )
            drafts[:2] = [merged]
        else:
            drafts[0] = replace(head, warnings=_unique([*head.warnings, "short_tail"]))

    for index in range(1, len(drafts)):
        previous, current = drafts[index - 1], drafts[index]
        main_tokens = _embedding_count(counter, current.body, current.heading_path)
        # 80 is the configured ceiling. For a deliberately short chunk, lower
        # the overlap so duplicated context remains at most 20% of its rendered
        # embedding text (o / (main + o) <= .20).
        overlap_budget = min(config.overlap_tokens, max(0, main_tokens // 4))
        overlap = counter.suffix(previous.body, overlap_budget)
        overlap_warning: str | None = None
        for _ in range(max(1, config.overlap_tokens + 1)):
            if not overlap:
                break
            rendered_tokens = _embedding_count(counter, current.body, current.heading_path, overlap)
            overlap_tokens = counter.count(overlap)
            if (
                rendered_tokens <= config.max_tokens
                and overlap_tokens / max(1, rendered_tokens) <= 0.20
            ):
                break
            next_budget = max(0, overlap_tokens - 1)
            if next_budget == 0:
                overlap = ""
                break
            reduced = counter.suffix(overlap, next_budget)
            # Fast-tokenizer decode/encode is not guaranteed to preserve a
            # strict token decrease for malformed OCR text. Without this guard
            # a one-token-at-a-time overlap adjustment can loop forever.
            if not reduced or reduced == overlap or counter.count(reduced) >= overlap_tokens:
                overlap = ""
                overlap_warning = "unstable_overlap_dropped"
                break
            overlap = reduced
        else:
            overlap = ""
            overlap_warning = "overlap_adjustment_limit_reached"
        drafts[index] = replace(
            current,
            overlap_text=overlap,
            overlap_page_start=previous.page_end,
            overlap_source_block_ids=(previous.source_block_ids[-1],) if overlap and previous.source_block_ids else (),
            warnings=_unique([*current.warnings, overlap_warning] if overlap_warning else current.warnings),
        )
    return drafts


def _table_drafts(unit: _Unit, config: FrozenChunkConfig, counter: _CounterAdapter) -> list[_Draft]:
    raw_rows = unit.metadata.get("table_rows")
    flags = unit.metadata.get("table_header_flags")
    rows: list[tuple[list[str], bool]] = []
    if isinstance(raw_rows, list):
        for index, raw in enumerate(raw_rows):
            if isinstance(raw, list):
                flag = bool(flags[index]) if isinstance(flags, list) and index < len(flags) else False
                rows.append(([_clean(cell) for cell in raw], flag))
    headers, body_rows = _table_headers(rows)
    captions = unit.metadata.get("table_captions")
    prefix = [*_unit_prefix(unit.metadata)]
    if isinstance(captions, list):
        prefix.extend(_clean(value) for value in captions if _clean(value))
    header_text = "\n".join(_render_row(row) for row in headers)
    fixed = "\n".join([*prefix, header_text]).strip()
    if fixed and _embedding_count(counter, fixed, unit.heading_path) >= config.atomic_content_hard_max_tokens:
        raise ValueError(
            f"Table header exceeds the atomic hard limit for source block {unit.source_block_ids[0]}"
        )

    if not rows:
        pieces = _split_text_to_limit(
            unit.body,
            heading=unit.heading_path,
            limit=config.atomic_content_hard_max_tokens,
            counter=counter,
        )
        return [
            _draft_from_units(
                [unit],
                body=piece,
                content_type="table",
                config=config,
                metadata={"table_part": index + 1, "table_part_count": len(pieces), "repeated_header": False},
                warnings=("atomic_table_overflow_split",) if len(pieces) > 1 else (),
            )
            for index, piece in enumerate(pieces)
        ]

    rendered_rows: list[tuple[str, list[str]]] = []
    for row in body_rows:
        rendered = _render_data_row(row)
        candidate = "\n".join(part for part in (fixed, rendered) if part)
        if _embedding_count(counter, candidate, unit.heading_path) <= config.atomic_content_hard_max_tokens:
            rendered_rows.append((rendered, list(row)))
            continue
        pieces = _split_text_to_limit(
            rendered,
            heading=unit.heading_path,
            limit=config.atomic_content_hard_max_tokens,
            counter=counter,
            fixed_prefix=fixed,
        )
        rendered_rows.extend((piece, list(row)) for piece in pieces)
    if not rendered_rows:
        rendered_rows = [("", [])]

    groups: list[list[tuple[str, list[str]]]] = []
    current: list[tuple[str, list[str]]] = []
    for row in rendered_rows:
        candidate_rows = [*current, row]
        body = "\n".join([fixed, *(text for text, _ in candidate_rows)]).strip()
        if current and _embedding_count(counter, body, unit.heading_path) > config.max_tokens:
            groups.append(current)
            current = [row]
        else:
            current = candidate_rows
    if current:
        groups.append(current)

    drafts: list[_Draft] = []
    for index, group in enumerate(groups):
        body = "\n".join([fixed, *(text for text, _ in group)]).strip()
        structured_rows = [list(row) for _, row in group if row]
        table_rows = [[*row] for row in headers] + structured_rows
        warnings: list[str] = []
        if _embedding_count(counter, body, unit.heading_path) > config.max_tokens:
            warnings.append("atomic_content_above_standard_max")
        if _embedding_count(counter, body, unit.heading_path) > config.atomic_content_hard_max_tokens:
            warnings.append("atomic_table_overflow_split")
        drafts.append(
            _draft_from_units(
                [unit],
                body=body,
                content_type="table",
                config=config,
                metadata={
                    "table_part": index + 1,
                    "table_part_count": len(groups),
                    "repeated_header": bool(headers and len(groups) > 1),
                    "table_header": header_text,
                    "table_row_count": len(group),
                    "table_rows": table_rows,
                    "table_header_flags": [True] * len(headers) + [False] * len(structured_rows),
                },
                warnings=warnings,
            )
        )
    return drafts


def _atomic_drafts(unit: _Unit, config: FrozenChunkConfig, counter: _CounterAdapter) -> list[_Draft]:
    if unit.content_type == "table":
        return _table_drafts(unit, config, counter)
    limit = config.atomic_content_hard_max_tokens
    if unit.content_type == "formula" and _embedding_count(counter, unit.body, unit.heading_path) > limit:
        raise ValueError(
            f"Formula exceeds the atomic hard limit without a safe LaTeX boundary: {unit.source_block_ids[0]}"
        )
    pieces = _split_text_to_limit(unit.body, heading=unit.heading_path, limit=limit, counter=counter)
    return [
        _draft_from_units(
            [unit],
            body=piece,
            content_type=unit.content_type,
            config=config,
            metadata={"atomic_part": index + 1, "atomic_part_count": len(pieces), **unit.metadata},
            warnings=(f"atomic_{unit.content_type}_overflow_split",) if len(pieces) > 1 else (),
        )
        for index, piece in enumerate(pieces)
    ]


def _build_drafts(units: Sequence[_Unit], config: FrozenChunkConfig, counter: _CounterAdapter) -> list[_Draft]:
    units = _formula_context(units, config, counter)
    drafts: list[_Draft] = []
    normal_buffer: list[_Unit] = []

    def flush() -> None:
        nonlocal normal_buffer
        if normal_buffer:
            drafts.extend(_normal_drafts(normal_buffer, config, counter))
            normal_buffer = []

    previous: _Unit | None = None
    for unit in units:
        if unit.content_type == "paragraph":
            compatible = (
                previous is None
                or (
                    previous.content_type == "paragraph"
                    and previous.chapter_id == unit.chapter_id
                    and previous.heading_path == unit.heading_path
                    and previous.indexable_bucket == unit.indexable_bucket
                    and _office_locations_compatible(previous, unit)
                )
            )
            if not compatible:
                flush()
            normal_buffer.append(unit)
        else:
            flush()
            drafts.extend(_atomic_drafts(unit, config, counter))
        previous = unit
    flush()
    return sorted(drafts, key=lambda item: item.order)


def _stable_chunk_id(book_id: str, draft: _Draft, config: FrozenChunkConfig, embedding_text: str) -> str:
    identity = json.dumps(
        {
            "book_id": book_id,
            "chapter_id": draft.chapter_id,
            "chunk_version": config.chunk_version,
            "content_type": draft.content_type,
            "heading_path": list(draft.heading_path),
            "source_block_ids": list(draft.source_block_ids),
            "page_start": draft.page_start,
            "page_end": draft.page_end,
            "text": normalize_for_hash(embedding_text),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{book_id}_{draft.chapter_id}_{config.chunk_version}_{sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def _draft_to_chunk(book_id: str, draft: _Draft, config: FrozenChunkConfig, counter: _CounterAdapter) -> Chunk:
    embedding_text = render_embedding_text(draft.body, draft.heading_path, draft.overlap_text)
    source_ids = _unique([*draft.overlap_source_block_ids, *draft.source_block_ids])
    page_start = min(draft.page_start, draft.overlap_page_start or draft.page_start)
    metadata = {
        **draft.metadata,
        "body": draft.body,
        "overlap_text": draft.overlap_text,
        "quality_version": QUALITY_VERSION,
        "quality_formula_version": QUALITY_VERSION,
        "quality_threshold": config.quality_threshold,
        "tokenizer_model": config.tokenizer_model,
        "tokenizer_revision": config.tokenizer_revision,
    }
    if draft.warnings:
        metadata["warnings"] = list(draft.warnings)
    eligible = draft.metadata.get("indexable") is not False and is_chunk_indexable(
        draft.quality_score,
        config=config,
    )
    if not eligible:
        metadata.update({"indexable": False, "quarantined": True, "index_status": "quarantined"})
    else:
        metadata["indexable"] = True
    return Chunk(
        chunk_id=_stable_chunk_id(book_id, draft, config, embedding_text),
        book_id=book_id,
        chapter_id=draft.chapter_id,
        page_start=page_start,
        page_end=draft.page_end,
        content_type=draft.content_type,
        text=draft.body,
        asset_ids=list(draft.asset_ids),
        key_concepts=[],
        parser=draft.parser,
        parser_version=draft.parser_version,
        chunk_version=config.chunk_version,
        heading_path=list(draft.heading_path),
        source_block_ids=list(source_ids),
        quality_score=round(draft.quality_score, 6),
        token_count=counter.count(embedding_text),
        content_hash=sha256(normalize_for_hash(embedding_text).encode("utf-8")).hexdigest(),
        bbox=draft.bbox,
        metadata=metadata,
    )


def _merged_chunk_id(chunk: Chunk) -> str:
    identity = json.dumps(
        {
            "book_id": chunk.book_id,
            "chapter_id": chunk.chapter_id,
            "chunk_version": chunk.chunk_version,
            "content_type": chunk.content_type,
            "content_hash": chunk.content_hash,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "source_block_ids": chunk.source_block_ids,
            "asset_ids": chunk.asset_ids,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        f"{chunk.book_id}_{chunk.chapter_id}_{chunk.chunk_version or 'v2'}_"
        f"{sha256(identity.encode('utf-8')).hexdigest()[:20]}"
    )


def _merge_duplicate_chunks(first: Chunk, second: Chunk) -> Chunk:
    first_score = first.quality_score if first.quality_score is not None else -1.0
    second_score = second.quality_score if second.quality_score is not None else -1.0
    preferred = second if second_score > first_score else first
    source_ids = list(_unique([*first.source_block_ids, *second.source_block_ids]))
    asset_ids = list(_unique([*first.asset_ids, *second.asset_ids]))
    metadata = {**preferred.metadata}
    occurrence_pages = list(
        dict.fromkeys(
            [
                *list(first.metadata.get("occurrence_pages", range(first.page_start, first.page_end + 1))),
                *list(second.metadata.get("occurrence_pages", range(second.page_start, second.page_end + 1))),
            ]
        )
    )
    existing_locations = list(first.metadata.get("source_locations", []))
    source_locations = [
        *existing_locations,
        {
            "page_start": second.page_start,
            "page_end": second.page_end,
            "source_block_ids": list(second.source_block_ids),
            "asset_ids": list(second.asset_ids),
            "quality_score": second.quality_score,
        },
    ]
    if not existing_locations:
        source_locations.insert(
            0,
            {
                "page_start": first.page_start,
                "page_end": first.page_end,
                "source_block_ids": list(first.source_block_ids),
                "asset_ids": list(first.asset_ids),
                "quality_score": first.quality_score,
            },
        )
    indexable = preferred.metadata.get("indexable") is not False
    metadata.update(
        {
            "occurrence_pages": occurrence_pages,
            "source_locations": source_locations,
            "source_quality_scores": [location.get("quality_score") for location in source_locations],
            "deduplicated_occurrence_count": int(first.metadata.get("deduplicated_occurrence_count", 1))
            + int(second.metadata.get("deduplicated_occurrence_count", 1)),
            "duplicate_provenance_merged": True,
            "indexable": indexable,
        }
    )
    if indexable:
        metadata.pop("quarantined", None)
        metadata.pop("index_status", None)
    else:
        metadata.update({"quarantined": True, "index_status": "quarantined"})
    merged = first.model_copy(
        update={
            "page_start": min(first.page_start, second.page_start),
            "page_end": max(first.page_end, second.page_end),
            "asset_ids": asset_ids,
            "source_block_ids": source_ids,
            "quality_score": preferred.quality_score,
            "parser": preferred.parser,
            "parser_version": preferred.parser_version,
            "bbox": preferred.bbox if first.page_start == second.page_start else None,
            "metadata": metadata,
        }
    )
    return merged.model_copy(update={"chunk_id": _merged_chunk_id(merged)})


def build_chunks_v2(
    book_id: str,
    artifact_path: Path,
    chapters: list[Chapter],
    assets: list[Asset] | None = None,
    config: FrozenChunkConfig | Mapping[str, object] | object | None = None,
    token_counter: object | None = None,
) -> list[Chunk]:
    """Build deterministic, structure-aware Chunk V2 records.

    The normalized ``pages.json`` is the only document input. Every source
    block belongs to exactly one (the deepest matching) chapter. Low-quality
    semantic content remains auditable as quarantined chunks, while mock/OCR
    placeholders and page furniture never become retrieval text.
    """

    resolved = _resolve_config(config)
    counter = _CounterAdapter(token_counter or BgeM3TokenCounter())
    pages_path = Path(artifact_path) / "pages.json"
    if not pages_path.exists() or not chapters:
        return []
    raw_pages = json.loads(pages_path.read_text(encoding="utf-8"))
    if not isinstance(raw_pages, list):
        raise ValueError("pages.json must contain a JSON array")
    pages = [page for page in raw_pages if isinstance(page, Mapping)]
    asset_by_id = {asset.asset_id: asset for asset in (assets or [])}
    units = _collect_units(pages, chapters, asset_by_id, resolved)
    drafts = _build_drafts(units, resolved, counter)
    chunks = [_draft_to_chunk(book_id, draft, resolved, counter) for draft in drafts if draft.body.strip()]

    # A repeated exact chunk signals either duplicate source blocks or an
    # unstable boundary. Keep the earliest occurrence deterministically.
    deduplicated: list[Chunk] = []
    positions: dict[tuple[str, str, str], int] = {}
    for chunk in chunks:
        key = (chunk.chapter_id, chunk.content_type, chunk.content_hash or "")
        if key in positions:
            position = positions[key]
            deduplicated[position] = _merge_duplicate_chunks(deduplicated[position], chunk)
            continue
        positions[key] = len(deduplicated)
        deduplicated.append(chunk)
    for chunk in deduplicated:
        for asset_id in chunk.asset_ids:
            asset = asset_by_id.get(asset_id)
            if asset is None:
                continue
            if chunk.chunk_id not in asset.source_chunk_ids:
                asset.source_chunk_ids.append(chunk.chunk_id)
            if not asset.chapter_id:
                asset.chapter_id = chunk.chapter_id
    return deduplicated


__all__ = ["QUALITY_VERSION", "build_chunks_v2"]
