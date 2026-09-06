from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json


REPO = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent / "source_inventory.json"
ROOTS = [
    REPO / "backend" / "app",
    REPO / "frontend" / "src",
]
FILES = [
    REPO / ".gitignore",
    REPO / "README.md",
    REPO / "PRD-BookCourseAI.md",
    REPO / "MINERU_RAG_IMPROVEMENT_PLAN.md",
    REPO / "backend" / "pyproject.toml",
    REPO / "frontend" / "package.json",
    REPO / "frontend" / "package-lock.json",
    REPO / "frontend" / "vite.config.ts",
    REPO / "frontend" / "tsconfig.json",
    REPO / "frontend" / "tsconfig.app.json",
    REPO / "frontend" / "tsconfig.node.json",
]


def main() -> None:
    candidates = set(path for path in FILES if path.is_file())
    for root in ROOTS:
        candidates.update(path for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    entries = []
    for path in sorted(candidates, key=lambda item: item.as_posix().lower()):
        data = path.read_bytes()
        entries.append(
            {
                "path": path.relative_to(REPO).as_posix(),
                "bytes": len(data),
                "sha256": sha256(data).hexdigest(),
            }
        )
    payload = {
        "schema_version": 1,
        "captured_at": "2026-07-13",
        "git_metadata_available": (REPO / ".git").exists(),
        "file_count": len(entries),
        "entries": entries,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
