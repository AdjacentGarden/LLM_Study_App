"""Register verified example artifacts without replacing existing learner records."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository
from adaptive_learning.ingestion.models import BookStructure
from adaptive_learning.ingestion.ocr_job import sha256_file

ROOT = Path('/data1/zhenghang/adaptive-book-ocr')
OUTPUT = ROOT/'output/examples-20260905'


def main():
    rows = json.loads((OUTPUT/'inventory.json').read_text())
    assert len(rows) == 10
    prepared = []
    for row in rows:
        source = ROOT/'input/examples-20260905'/row['filename']
        artifact = OUTPUT/row['book_id']
        result = artifact/'model-evaluations/grok-4_3-repaired-v1'
        report = json.loads((result/'content-evaluation.json').read_text())
        assert report['completed'] and report['chapter_generation']['success']
        structure = BookStructure.model_validate_json((result/'generated-structure.json').read_text())
        assert structure.source_page_count == row['pages'] and source.is_file()
        index = artifact/'index-repaired-final'
        assert all((index/name).is_file() for name in ('chunks.json','embeddings.npy','index_manifest.json'))
        prepared.append((row, source, structure, index))
    db_path = ROOT/'data/state/ocr_jobs.sqlite3'
    backup = ROOT/'release-backups/library-20260906'
    backup.mkdir(parents=True, exist_ok=True)
    if not (backup/'ocr_jobs.sqlite3').exists():
        with sqlite3.connect(db_path) as src, sqlite3.connect(backup/'ocr_jobs.sqlite3') as dst:
            src.backup(dst)
    repository = SQLiteOCRJobRepository(db_path)
    published = ['biology-required-2']
    indexes = {}
    for row, source, structure, index in prepared:
        if row['book_id'] == '6b0c596886a5':
            # The same biology PDF already has live sessions and chapter IDs.
            assert repository.get_structure('biology-required-2') is not None
            continue
        book_id = row['book_id']
        if repository.get_book(book_id) is None:
            repository.register_book(book_id=book_id, original_name=row['filename'],
                                     file_path=source, source_sha256=sha256_file(source))
            repository.save_structure(book_id, structure)
        else:
            assert repository.get_structure(book_id) is not None
        published.append(book_id)
        indexes[book_id] = str(index)
    manifest = ROOT/'config/published-book-indexes.json'
    temp = manifest.with_suffix('.tmp')
    temp.write_text(json.dumps(indexes, ensure_ascii=False, indent=2))
    temp.replace(manifest)
    env = ROOT/'config/published-books.env'
    temp = env.with_suffix('.tmp')
    temp.write_text('PUBLISHED_BOOK_IDS='+','.join(published)+'\nRAG_BOOK_INDEX_MANIFEST='+str(manifest)+'\n')
    temp.replace(env)
    print(json.dumps({'published_books':len(published),'new_books':len(indexes),'backup':str(backup)}))


if __name__ == '__main__':
    main()
