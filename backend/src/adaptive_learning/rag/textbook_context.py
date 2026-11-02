"""Small server-owned textbook hints for routing, never citation evidence."""
from __future__ import annotations

import json
from typing import Any

from ..ingestion.models import BookStructure


def _short(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


def textbook_context(
    structure: BookStructure, chapter_id: str | None = None, *, subject: str = "",
) -> dict[str, Any]:
    current = next((c for c in structure.chapters if c.chapter_id == chapter_id), None)
    book: dict[str, Any] = {
        "title": _short(structure.title, 120),
        "summary": _short(structure.summary, 320),
        "total_chapters": len(structure.chapters),
    }
    if subject:
        book["subject"] = _short(subject, 60)
    outline: list[dict[str, Any]] = []
    used = 0
    # Preserve the selected chapter even near the end of a very large book.
    candidates = ([current] if current else []) + [c for c in structure.chapters if c is not current]
    for chapter in candidates:
        item = {"title": _short(chapter.title, 96),
                "knowledge_points": [_short(p, 48) for p in chapter.knowledge_points[:2]]}
        size = len(json.dumps(item, ensure_ascii=False))
        if len(outline) >= 32 or used + size > 3200:
            break
        outline.append(item)
        used += size
    book["outline"] = outline
    book["outline_truncated"] = len(outline) < len(structure.chapters)
    context: dict[str, Any] = {"textbook": book}
    if current:
        context["chapter"] = {
            "id": _short(current.chapter_id, 100), "title": _short(current.title, 120),
            "start_page": current.start_page, "end_page": current.end_page,
            "summary": _short(current.summary, 256),
            "knowledge_points": [_short(p, 72) for p in current.knowledge_points[:10]],
        }
    return context
