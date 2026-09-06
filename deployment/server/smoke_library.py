"""Exercise the deployed catalog, structures and book-scoped answers."""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path('/data1/zhenghang/adaptive-book-ocr')
BASE = 'http://127.0.0.1:8100'


def get(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = Request(BASE+path,data=data,headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=150) as response:
        return json.load(response)


def main():
    catalog = get('/api/books')
    assert len(catalog) == 10
    output = ROOT/'output/library-publish-20260906'
    output.mkdir(exist_ok=True)
    results = []

    def run(book):
        book_id = book['book_id']
        t = time.monotonic()
        result = {'book_id':book_id,'title':book['title']}
        try:
            structure = get(f'/api/books/{book_id}/structure')
            result['chapters'] = len(structure['chapters'])
            assert result['chapters'] == book['chapter_count']
            artifact_id = '6b0c596886a5' if book_id == 'biology-required-2' else book_id
            previous = json.loads((ROOT/'output/examples-20260905'/artifact_id/'answer-evaluation-grok-4_3-repaired-final.json').read_text())
            question = previous['queries'][0]['question']
            result['question'] = question
            answer = get(f'/api/books/{book_id}/qa',{'question':question})
            assert answer['book_id'] == book_id
            result['answer'] = answer
            result['passed'] = answer['status'] == 'supported' and answer['semantic_checked']
        except Exception as error:
            result['passed'] = False
            result['error'] = str(error)
        result['seconds'] = round(time.monotonic()-t,2)
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(run,book) for book in catalog]):
            result = future.result()
            results.append(result)
            (output/'live-smoke.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
            print(json.dumps({k:v for k,v in result.items() if k!='answer'},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
