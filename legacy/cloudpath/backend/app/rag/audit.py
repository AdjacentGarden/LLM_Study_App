from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from app.services.storage import artifact_dir


def now_ms() -> float:
    return perf_counter() * 1000


def write_rag_audit(book_id: str, event: dict[str, Any]) -> None:
    path = artifact_dir(book_id) / "rag_audit.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
