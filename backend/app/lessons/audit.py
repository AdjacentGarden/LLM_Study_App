from __future__ import annotations

from datetime import UTC, datetime
import json

from app.schemas.books import ChapterSourcePackage
from app.services.storage import artifact_dir


def write_lesson_generation_audit(
    source: ChapterSourcePackage,
    *,
    provider: str,
    status: str,
    duration_ms: int,
    error_code: str | None = None,
) -> None:
    record = {
        "created_at": datetime.now(UTC).isoformat(),
        "book_id": source.book_id,
        "chapter_id": source.chapter_id,
        "provider": provider,
        "status": status,
        "duration_ms": duration_ms,
        "error_code": error_code,
        "source_summary": {
            "page_start": source.page_start,
            "page_end": source.page_end,
            "chunk_count": source.chunk_count,
            "text_length": source.text_length,
            "window_count": len(source.windows),
            "warnings": source.warnings,
        },
    }
    path = artifact_dir(source.book_id) / "lesson_generation_audit.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
