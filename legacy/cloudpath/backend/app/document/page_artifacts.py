from __future__ import annotations

from collections.abc import Callable, Iterable
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Any

from app.document.mineru.exceptions import MinerUStaleResultError
if TYPE_CHECKING:
    from app.document.parsers.base import ParsedDocument, ParsedPage


GenerationGuard = Callable[[], bool]


def _guard_current(guard: GenerationGuard | None) -> None:
    if guard is not None and not guard():
        raise MinerUStaleResultError("Parse generation became obsolete before artifact commit")


def _atomic_write_text(path: Path, content: str, *, guard: GenerationGuard | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _guard_current(guard)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _guard_current(guard)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _page_payload(page: ParsedPage) -> dict[str, Any]:
    return {
        "page": page.page_number,
        "pdf_page_index": page.page_number - 1,
        "text": page.text,
        "needs_ocr": page.needs_ocr,
        "blocks": [block.model_dump(mode="json") for block in page.text_blocks],
        "ocr_provider": page.ocr_provider,
        "ocr_confidence": page.ocr_confidence,
        "quality_warnings": [warning.model_dump(mode="json") for warning in page.quality_warnings],
        "preprocessed_image_url": None,
        "layout_regions": [region.model_dump(mode="json") for region in page.layout_regions],
        "parser": page.parser,
        "quality_score": page.quality_score,
        "metadata": page.metadata,
    }


def _jsonl(items: Iterable[dict[str, Any]]) -> str:
    return "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in items)


def write_parsed_document_artifacts(
    document: ParsedDocument,
    artifact_path: Path,
    *,
    generation_guard: GenerationGuard | None = None,
) -> None:
    """Publish the normalized page artifacts once, after all fallbacks merge.

    Individual parsers intentionally do not write ``pages.json``.  This avoids
    PyMuPDF or OCR replacing good MinerU pages while a mixed document is being
    assembled.  Every file is written through a sibling temporary file and an
    atomic replace.
    """

    pages = sorted(document.pages, key=lambda item: item.page_number)
    payloads = [_page_payload(page) for page in pages]
    _atomic_write_text(
        artifact_path / "pages.json",
        json.dumps(payloads, ensure_ascii=False, indent=2),
        guard=generation_guard,
    )
    _atomic_write_text(
        artifact_path / "text_blocks.jsonl",
        _jsonl(
            {**block.model_dump(mode="json"), "book_id": document.book_id, "parser": page.parser}
            for page in pages
            for block in page.text_blocks
        ),
        guard=generation_guard,
    )
    _atomic_write_text(
        artifact_path / "layout_regions.jsonl",
        _jsonl(
            {**region.model_dump(mode="json"), "book_id": document.book_id, "parser": page.parser}
            for page in pages
            for region in page.layout_regions
        ),
        guard=generation_guard,
    )

    raw_middle = document.metadata.get("mineru_middle_json")
    raw_content = document.metadata.get("mineru_content_list")
    if isinstance(raw_middle, dict):
        _atomic_write_text(
            artifact_path / "mineru_middle.json",
            json.dumps(raw_middle, ensure_ascii=False, indent=2),
            guard=generation_guard,
        )
    if isinstance(raw_content, list):
        _atomic_write_text(
            artifact_path / "mineru_content_list.json",
            json.dumps(raw_content, ensure_ascii=False, indent=2),
            guard=generation_guard,
        )


def atomic_write_json(path: Path, payload: Any, *, generation_guard: GenerationGuard | None = None) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2), guard=generation_guard)
