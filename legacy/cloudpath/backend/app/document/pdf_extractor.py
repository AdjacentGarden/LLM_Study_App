from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import json

import fitz

from app.schemas.books import PageResult, TextBlock


def _font_size_from_lines(block: dict) -> float | None:
    sizes: list[float] = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            size = span.get("size")
            if isinstance(size, (int, float)):
                sizes.append(float(size))
    return round(sum(sizes) / len(sizes), 2) if sizes else None


def extract_pages(file_path: Path, page_numbers: Iterable[int] | None = None) -> list[PageResult]:
    selected = set(page_numbers) if page_numbers is not None else None
    pages: list[PageResult] = []
    with fitz.open(file_path) as doc:
        for index in range(doc.page_count):
            page_number = index + 1
            if selected is not None and page_number not in selected:
                continue
            page = doc.load_page(index)
            text = page.get_text("text")
            raw = page.get_text("dict")
            blocks: list[TextBlock] = []
            for block_index, block in enumerate(raw.get("blocks", [])):
                if block.get("type") != 0:
                    continue
                block_text = "\n".join(
                    "".join(span.get("text", "") for span in line.get("spans", []))
                    for line in block.get("lines", [])
                ).strip()
                if not block_text:
                    continue
                bbox = [float(value) for value in block.get("bbox", [0, 0, 0, 0])]
                blocks.append(
                    TextBlock(
                        block_id=f"pymupdf_p{page_number:04d}_b{block_index + 1:04d}",
                        page=page_number,
                        type="paragraph",
                        text=block_text,
                        bbox=bbox,
                        font_size=_font_size_from_lines(block),
                    )
                )
            pages.append(
                PageResult(
                    page=page_number,
                    pdf_page_index=index,
                    text=text,
                    needs_ocr=not bool(text.strip()),
                    blocks=blocks,
                    parser="pymupdf",
                    quality_score=1.0 if text.strip() else 0.0,
                )
            )
    return pages


def write_page_artifacts(book_id: str, file_path: Path, artifact_path: Path) -> None:
    pages = extract_pages(file_path)
    pages_json = artifact_path / "pages.json"
    blocks_jsonl = artifact_path / "text_blocks.jsonl"
    pages_json.write_text(
        json.dumps([page.model_dump() for page in pages], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with blocks_jsonl.open("w", encoding="utf-8") as handle:
        for page in pages:
            for block in page.blocks:
                payload = block.model_dump()
                payload["book_id"] = book_id
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
