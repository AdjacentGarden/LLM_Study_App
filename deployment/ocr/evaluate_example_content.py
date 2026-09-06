"""Audit three source pages per book; exercise chapter generation and reviewed flashcards.

OCR is full-book. Vision correction is explicitly a three-page sample, not full-book cleanup.
All model products are evaluation artifacts and are never published into the live catalog.
"""
from __future__ import annotations

import concurrent.futures
import argparse
import json
from pathlib import Path
import re
import time

from adaptive_learning.assessment.models import LearnerProfile
from adaptive_learning.config import get_settings
from adaptive_learning.ingestion.chaptering import ChapterReconstructor, load_normalized_pages
from adaptive_learning.llm.client import LLMConfig, OpenAICompatibleClient
from adaptive_learning.personalization.flashcard_quality import FlashcardQualityGate
from adaptive_learning.personalization.generator import ChapterCourseCompiler
from adaptive_learning.personalization.policy import PersonalizationPolicy

ROOT = Path('/data1/zhenghang/adaptive-book-ocr')
OUTPUT = ROOT/'output/examples-20260905'


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-page-review', action='store_true',
                        help='Reuse OCR unchanged; compare chapter/course generation only.')
    parser.add_argument('--run-tag', default='')
    args = parser.parse_args()
    inventories = json.loads((OUTPUT/'inventory.json').read_text())
    settings = get_settings()
    model_tag = re.sub(r'[^A-Za-z0-9_-]', '_', settings.text_model)
    if args.run_tag:
        model_tag += '-' + re.sub(r'[^A-Za-z0-9_-]', '_', args.run_tag)
    deadline = time.monotonic()+9000

    def run(row):
        target = OUTPUT/row['book_id']
        results = target/'model-evaluations'/model_tag
        results.mkdir(parents=True, exist_ok=True)
        result_file = results/'content-evaluation.json'
        previous = json.loads(result_file.read_text()) if result_file.is_file() else None
        if previous and previous.get('completed'):
            return
        while time.monotonic()<deadline:
            status_file = target/'status.json'
            if status_file.is_file():
                status = json.loads(status_file.read_text())
                if status['status'] in {'failed','time_limit'}:
                    save(result_file, {'filename':row['filename'],'error':'OCR failed'})
                    return
                if status['status']=='completed':
                    break
            time.sleep(10)
        else:
            return
        client = OpenAICompatibleClient(LLMConfig(settings.text_base_url,settings.text_api_key,
            settings.text_model,timeout_seconds=120))
        started = time.monotonic()
        report = {'filename':row['filename'], 'model':settings.text_model,
                  'cleanup_scope':('Not rerun; original full-book OCR input reused unchanged'
                                   if args.skip_page_review else
                                   'Three sampled PDF pages only, not complete-book LLM cleanup'),
                  'reasoning_effort':'low',
                  'page_reviews':[]}
        if previous:
            report['page_reviews'] = previous.get('page_reviews', [])
            report['resumed_from_model'] = previous.get('model')
            for item in report['page_reviews']:
                item.setdefault('model', previous.get('model'))
        pages = load_normalized_pages(target/'normalized/pages.jsonl')
        for sample in ([] if args.skip_page_review else row['samples']):
            n = sample['page']
            if any(item['page']==n and 'result' in item for item in report['page_reviews']):
                continue
            original = next(p.raw_text for p in pages if p.page_number==n)
            t = time.monotonic()
            try:
                response = client.structured(
                    system='你是扫描书籍的忠实校对员。必须逐项对照当前页图片校对 OCR。返回完整本页文本，不能改成总结，不能补写本页没有的知识。保留数字、否定词、条件、代码、数学公式和段落顺序。看不清的保留并标出。图片中结构式或复杂图形不能可靠转写时，用清晰的占位说明并在 unresolved 中标注，不能猜测。只返回 JSON：{"cleaned_text":"完整本页文本","edits":[{"before":"原OCR片段","after":"修复后的片段","reason":"依据"}],"unresolved":["需要人工核对的内容"]}。',
                    user=json.dumps({'pdf_page':n,'ocr_text':original},ensure_ascii=False),
                    images=[('image/png',(target/f'source-page-{n}.png').read_bytes())],
                    max_tokens=8192)
                cleaned = response.get('cleaned_text')
                if not isinstance(cleaned,str) or not cleaned.strip():
                    raise ValueError('Missing corrected full-page text')
                item = {'page':n,'model':settings.text_model,'seconds':round(time.monotonic()-t,2),'original_text':original,
                        'result':response,'length_ratio':round(len(cleaned)/max(1,len(original)),3)}
                if not .6 <= item['length_ratio'] <= 1.8:
                    item['review_required']='Large length change; not automatically accepted'
            except Exception as error:
                item = {'page':n,'model':settings.text_model,'error':type(error).__name__+': '+str(error)[:250]}
            report['page_reviews'].append(item)
            save(result_file,report)
        t = time.monotonic()
        try:
            structure = ChapterReconstructor(client).reconstruct_book(pages,row['filename'])
            save(results/'generated-structure.json',structure.model_dump(mode='json'))
            report['chapter_generation'] = {'success':True,'chapters':len(structure.chapters),
                'fallback':structure.used_fallback_chapter,'seconds':round(time.monotonic()-t,2)}
            chapter = max(structure.chapters,key=lambda c:len(c.knowledge_points))
            profile = LearnerProfile(user_id='isolated-book-evaluation',book_id=row['book_id'],
                goal='理解本书核心概念',focus_chapter_ids=[chapter.chapter_id])
            bundle = ChapterCourseCompiler().compile(chapter=chapter,profile=profile,
                decision=PersonalizationPolicy().decide(profile,chapter.chapter_id))
            reviewed = FlashcardQualityGate(client,results/'flashcard-quality.sqlite3',
                settings.text_base_url+'|'+settings.text_model).ensure(bundle)
            save(results/'reviewed-course.json',reviewed.model_dump(mode='json'))
            report['flashcards'] = {'success':True,'chapter':chapter.title,'count':len(reviewed.flashcards)}
        except Exception as error:
            report['generation_error'] = type(error).__name__+': '+str(error)[:500]
            if hasattr(error, 'review_feedback'):
                report['review_feedback'] = error.review_feedback
            cause = error.__cause__
            if cause is not None:
                report['error_cause_type'] = type(cause).__name__
                if hasattr(cause, 'errors'):
                    report['validation_errors'] = cause.errors(include_input=False, include_url=False)
        report['duration_seconds'] = round(time.monotonic()-started,2)
        report['new_token_usage'] = client.usage_totals()
        report['completed'] = True
        save(result_file,report)
        print(json.dumps({k:v for k,v in report.items() if k!='page_reviews'},ensure_ascii=False),flush=True)
        client.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run,inventories))


if __name__=='__main__':
    main()
