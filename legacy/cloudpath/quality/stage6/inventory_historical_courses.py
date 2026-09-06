from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
STAGE = Path(__file__).resolve().parent


def _json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _book_record(book_dir: Path) -> dict[str, Any]:
    artifacts = book_dir / "artifacts"
    report = _json(artifacts / "parser_report.json") or {}
    index = _json(artifacts / "rag_index_status.json") or {}
    chunks_path = artifacts / "chunks.jsonl"
    versions: set[str] = set()
    chunk_count = 0
    if chunks_path.exists():
        for line in chunks_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            chunk_count += 1
            try:
                value = json.loads(line)
            except ValueError:
                versions.add("invalid_json")
                continue
            versions.add(str(value.get("chunk_version") or "missing"))
    source_files = sorted(
        str(path.relative_to(book_dir))
        for path in book_dir.rglob("*")
        if path.is_file() and "artifacts" not in path.parts and "assets" not in path.parts
    )
    parser = report.get("final_parser") or report.get("parser")
    reasons: list[str] = []
    if not report:
        reasons.append("missing_parser_report")
    if parser not in {"mineru", "mixed"}:
        reasons.append("legacy_or_non_mineru_parser")
    if not versions or any(not version.startswith("v2") for version in versions):
        reasons.append("missing_or_legacy_chunk_version")
    if index.get("status") != "ready":
        reasons.append("index_not_ready")
    priority = "P0" if "index_not_ready" in reasons or "missing_parser_report" in reasons else "P1" if reasons else "none"
    return {
        "book_id": book_dir.name,
        "source_files": source_files,
        "parser": parser,
        "chunk_count": chunk_count,
        "chunk_versions": sorted(versions),
        "index_status": index.get("status"),
        "candidate": bool(reasons),
        "priority": priority,
        "reasons": reasons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only CloudPath historical course inventory")
    parser.add_argument("--storage-root", type=Path, default=ROOT / "backend" / "data")
    parser.add_argument("--output", type=Path, default=STAGE / "historical_reparse_inventory.json")
    args = parser.parse_args()
    storage_root = args.storage_root.resolve()
    books_root = storage_root / "books"
    books = sorted((_book_record(path) for path in books_root.iterdir() if path.is_dir()), key=lambda item: item["book_id"]) if books_root.exists() else []
    candidates = [book for book in books if book["candidate"]]
    payload = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_inventory",
        "storage_root": str(storage_root),
        "authoritative_for_production": False,
        "production_or_shared_storage_accessed": False,
        "course_count": len(books),
        "candidate_count": len(candidates),
        "courses": books,
        "decision": "not_executed",
        "requires_separate_approval": True,
        "note": "The workspace contains no production storage. Run this read-only inventory against the approved production snapshot before any migration decision.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    print(json.dumps({"courses": len(books), "candidates": len(candidates), "decision": "not_executed"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
