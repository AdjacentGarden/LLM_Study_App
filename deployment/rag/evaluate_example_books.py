"""Cross-book evaluation with source-image-authored questions and explicit page targets.

Does not publish books or modify application learning state. Retrieval can run without an LLM.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import re
import sys
import time

import numpy as np

from adaptive_learning.config import get_settings
from adaptive_learning.ingestion.chaptering import ChapterReconstructor, load_normalized_pages
from adaptive_learning.llm.client import LLMConfig, OpenAICompatibleClient
from adaptive_learning.rag.backends import TransformerPairReranker, TransformerQueryEncoder
from adaptive_learning.rag.grounded_qa import GroundedAnswerGenerator
from adaptive_learning.rag.index import PersistentRAGIndex
from adaptive_learning.rag.service import TextbookQAService
from rag_eval import build_chunks, embed, load_embedding_model

ROOT = Path('/data1/zhenghang/adaptive-book-ocr')
OUTPUT = ROOT/'output/examples-20260905'


def save(path: Path, value: object) -> None:
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--answers', action='store_true')
    parser.add_argument('--wait-seconds', type=int, default=9000)
    parser.add_argument('--run-tag', default='')
    parser.add_argument('--index-tag', default=None)
    parser.add_argument('--book-id', action='append', default=[])
    args = parser.parse_args()
    suffix = '-' + re.sub(r'[^A-Za-z0-9_-]', '_', args.run_tag) if args.run_tag else ''
    index_suffix = ('-' + re.sub(r'[^A-Za-z0-9_-]', '_', args.index_tag)) if args.index_tag else suffix
    cases = json.loads(Path(__file__).with_name('example_book_cases.json').read_text())
    if args.book_id:
        cases = [case for case in cases if case['book_id'] in args.book_id]
    model_dir = ROOT/'rag-eval/models'
    encoder = TransformerQueryEncoder(model_dir/'bge-small-zh-v1.5', device='cuda')
    reranker = TransformerPairReranker(model_dir/'bge-reranker-base', device='cuda')
    tokenizer, model = load_embedding_model(model_dir/'bge-small-zh-v1.5', 'cuda')
    settings = get_settings()
    client = None
    if args.answers:
        client = OpenAICompatibleClient(LLMConfig(base_url=settings.text_base_url,
            api_key=settings.text_api_key, model=settings.text_model, timeout_seconds=120))
        client.structured(system='Return JSON only.', user='Return {"ok":true}.', max_tokens=100)
    pending = {case['book_id']:case for case in cases}
    deadline = time.monotonic()+args.wait_seconds
    while pending and time.monotonic() < deadline:
        progressed = False
        for book_id, case in list(pending.items()):
            target = OUTPUT/book_id
            pages_file = target/'normalized/pages.jsonl'
            model_tag = re.sub(r'[^A-Za-z0-9_-]', '_', settings.text_model)
            report_path = target/(f'answer-evaluation-{model_tag}{suffix}.json' if args.answers else f'retrieval-evaluation{suffix}.json')
            if report_path.is_file():
                del pending[book_id]
                continue
            status_file = target/'status.json'
            if not status_file.is_file():
                continue
            status = json.loads(status_file.read_text())
            if status['status'] == 'running':
                continue
            if not pages_file.is_file():
                save(report_path, {'book_id':book_id, 'error':'OCR incomplete', 'ocr_status':status})
                del pending[book_id]
                continue
            started = time.monotonic()
            try:
                chunks = build_chunks(pages_file, limit=900, child_limit=360)
                index_dir = target/('index'+index_suffix)
                if not (index_dir/'embeddings.npy').is_file():
                    vectors = embed([c.text for c in chunks], tokenizer, model, 'cuda', query=False)
                    index_dir.mkdir(exist_ok=True)
                    save(index_dir/'chunks.json', [asdict(c) for c in chunks])
                    np.save(index_dir/'embeddings.npy', vectors)
                vectors = np.load(index_dir/'embeddings.npy', mmap_mode='r')
                save(index_dir/'index_manifest.json', {'chunk_count':len(chunks),
                     'embedding_dimensions':int(vectors.shape[1]),
                     'embedding_model':str(model_dir/'bge-small-zh-v1.5')})
                index = PersistentRAGIndex.load(index_dir, encoder=encoder, reranker=reranker)
                pages = load_normalized_pages(pages_file)
                structure = ChapterReconstructor().reconstruct_book(pages, status['filename'])
                save(target/f'detected-structure{suffix}.json', structure.model_dump(mode='json'))
                report = {'book_id':book_id, 'filename':status['filename'], 'pages':len(pages),
                          'chunk_count':len(chunks), 'chapter_count':len(structure.chapters),
                          'fallback_chapter':structure.used_fallback_chapter, 'queries':[],
                          'reference_page':case['page'], 'model':settings.text_model if args.answers else None}
                sample = next(p for p in pages if p.page_number == case['page'])
                compact = lambda s: re.sub(r'\s+', '', s)
                report['reference_text'] = sample.raw_text
                report['visual_reference_quote'] = case['reference']
                report['reference_exact_match'] = compact(case['reference']) in compact(sample.raw_text)
                service = TextbookQAService(book_id=book_id, index=index,
                    generator=GroundedAnswerGenerator(client, use_evidence_planner=True,
                        use_semantic_review=True)) if client else None
                for question in case['questions']:
                    t = time.monotonic()
                    result = index.search(question['question'], top_pages=5, max_evidence=10)
                    row = {**question, 'target_page':case['page'], 'retrieved_pages':list(result.pages),
                           'target_page_hit':case['page'] in result.pages, 'score':result.score,
                           'retrieval_seconds':round(time.monotonic()-t, 3),
                           'evidence':[asdict(item) for item in result.evidence]}
                    if service:
                        try:
                            row['answer'] = service.answer(question['question']).model_dump(mode='json')
                        except Exception as error:
                            row['answer_error'] = type(error).__name__+': '+str(error)[:200]
                    report['queries'].append(row)
                    save(report_path, report)
                report['duration_seconds'] = round(time.monotonic()-started, 3)
                if client:
                    report['runner_cumulative_token_usage'] = client.usage_totals()
                save(report_path, report)
                print(json.dumps({k:v for k,v in report.items() if k not in {'queries','reference_text'}}, ensure_ascii=False), flush=True)
            except Exception as error:
                save(report_path, {'book_id':book_id,'error':type(error).__name__+': '+str(error)[:500]})
                print(book_id, type(error).__name__, flush=True)
            del pending[book_id]
            progressed = True
        if pending and not progressed:
            time.sleep(15)
    if pending:
        print('Incomplete books:', sorted(pending), flush=True)
        sys.exit(2)


if __name__ == '__main__':
    main()
