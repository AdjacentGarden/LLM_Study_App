from __future__ import annotations

import argparse
import json
from pathlib import Path

from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository
from adaptive_learning.ingestion.models import BookStructure
from adaptive_learning.ingestion.ocr_job import sha256_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register an already verified source PDF and its evidence-checked structure"
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--book-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--structure", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = args.source.resolve(strict=True)
    structure_path = args.structure.resolve(strict=True)
    structure = BookStructure.model_validate_json(structure_path.read_text(encoding="utf-8"))
    repository = SQLiteOCRJobRepository(args.database)
    existing = repository.get_book(args.book_id)
    if existing is None:
        repository.register_book(
            book_id=args.book_id,
            original_name=args.title,
            file_path=source,
            source_sha256=sha256_file(source),
        )
    elif existing.file_path.resolve() != source:
        raise SystemExit("book id already points to a different source file")
    repository.save_structure(args.book_id, structure)
    print(
        json.dumps(
            {
                "book_id": args.book_id,
                "source": str(source),
                "pages": structure.source_page_count,
                "chapters": len(structure.chapters),
                "knowledge_points": sum(
                    len(chapter.knowledge_points) for chapter in structure.chapters
                ),
                "status": "structured",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
