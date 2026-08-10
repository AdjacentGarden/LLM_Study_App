from __future__ import annotations

from datetime import datetime, UTC
import json

from app.schemas.books import ImageGenerationRequest
from app.services.storage import artifact_dir


def write_generation_audit(
    payload: ImageGenerationRequest,
    *,
    provider: str,
    status: str,
    duration_ms: int,
    error_code: str | None = None,
) -> None:
    record = {
        "created_at": datetime.now(UTC).isoformat(),
        "book_id": payload.book_id,
        "lesson_id": payload.lesson_id,
        "chapter_id": payload.chapter_id,
        "provider": provider,
        "status": status,
        "duration_ms": duration_ms,
        "error_code": error_code,
        "request_summary": {
            "concepts": payload.concepts,
            "style": payload.style,
            "purpose": payload.purpose[:160],
            "source_chunk_ids": payload.source_chunk_ids,
        },
    }
    path = artifact_dir(payload.book_id) / "image_generation_audit.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
