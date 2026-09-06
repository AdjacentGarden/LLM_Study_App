"""Generate and review chapter choice banks; checkpoint valid chapters for safe resume."""
from __future__ import annotations

import json
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from adaptive_learning.assessment.item_generation import DiagnosticItemGenerator, structure_fingerprint
from adaptive_learning.assessment.models import DiagnosticItem
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.config import get_settings
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository
from adaptive_learning.llm.client import LLMConfig, OpenAICompatibleClient

ROOT = Path('/data1/zhenghang/adaptive-book-ocr')
CHECKPOINT = ROOT/'output/library-publish-20260906'


def main():
    CHECKPOINT.mkdir(exist_ok=True)
    settings = get_settings()
    books = SQLiteOCRJobRepository(ROOT/'data/state/ocr_jobs.sqlite3')
    assessments = SQLiteAssessmentRepository(ROOT/'data/state/assessments.sqlite3')
    pending = {}
    reports = {}

    def generate(book_id, structure, chapter, destination):
        if destination.is_file():
            return [DiagnosticItem.model_validate(x) for x in json.loads(destination.read_text())]
        client = OpenAICompatibleClient(LLMConfig(settings.text_base_url, settings.text_api_key,
                                                settings.text_model, timeout_seconds=120))
        generator = DiagnosticItemGenerator(client, allow_grounded_answer_rewrite=True)
        one = structure.model_copy(update={'chapters':[chapter]})
        items = generator.generate(one)
        destination.write_text(json.dumps([x.model_dump(mode='json') for x in items], ensure_ascii=False))
        return items

    with ThreadPoolExecutor(max_workers=4) as pool:
        for book_id in sorted(settings.published_book_ids,
                              key=lambda value: len(books.get_structure(value).chapters)):
            structure = books.get_structure(book_id)
            assert structure is not None
            fingerprint = structure_fingerprint(structure)
            existing = assessments.get_bank(book_id, expected_fingerprint=fingerprint)
            if existing:
                reports[book_id] = {'ready':True, 'items':len(existing), 'cached':True}
                continue
            reports[book_id] = {'ready':False,'chapters':len(structure.chapters),'completed':0,'errors':[]}
            folder = CHECKPOINT/book_id/fingerprint
            folder.mkdir(parents=True, exist_ok=True)
            batches = {}
            for chapter in structure.chapters:
                name = hashlib.sha256(chapter.chapter_id.encode()).hexdigest()[:20]+'.json'
                future = pool.submit(generate,book_id,structure,chapter,folder/name)
                pending[future] = (book_id,structure,chapter,batches)
        for future in as_completed(pending):
            book_id, structure, chapter, batches = pending[future]
            report = reports[book_id]
            try:
                batches[chapter.chapter_id] = future.result()
                report['completed'] += 1
                if len(batches) == len(structure.chapters):
                    items = [item for ch in structure.chapters for item in batches[ch.chapter_id]]
                    assessments.save_bank(book_id=book_id,structure_fingerprint=structure_fingerprint(structure),items=items)
                    report.update(ready=True,items=len(items))
            except Exception as error:
                report['errors'].append({'chapter':chapter.title,'error':type(error).__name__+': '+str(error)[:400]})
            (CHECKPOINT/'diagnostics-report.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2))
            print(json.dumps({'book':book_id,**report},ensure_ascii=False),flush=True)
    (CHECKPOINT/'diagnostics-report.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
