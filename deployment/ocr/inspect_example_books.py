"""Read-only source inventory; render representative source pages for human inspection."""
import hashlib
import json
from pathlib import Path

import pymupdf

ROOT = Path('/data1/zhenghang/adaptive-book-ocr')
OUTPUT = ROOT / 'output/examples-20260905'
inventory = []
for source in sorted((ROOT/'input/examples-20260905').glob('*.pdf')):
    book_id = hashlib.sha256(source.name.encode()).hexdigest()[:12]
    target = OUTPUT/book_id
    target.mkdir(exist_ok=True)
    with pymupdf.open(source) as doc:
        counts = [len(page.get_text().strip()) for page in doc]
        sample_numbers = sorted(set([min(len(doc), 12), max(1, len(doc)//3), max(1, 2*len(doc)//3)]))
        samples = []
        for n in sample_numbers:
            page = doc[n-1]
            page.get_pixmap(matrix=pymupdf.Matrix(1.2, 1.2)).save(target/f'source-page-{n}.png')
            samples.append({'page':n, 'native_text':page.get_text()})
        row = {'book_id':book_id, 'filename':source.name, 'pages':len(doc),
               'bytes':source.stat().st_size, 'native_text_pages_over_100_chars':sum(n>100 for n in counts),
               'native_text_characters':sum(counts), 'outline_entries':len(doc.get_toc()),
               'samples':samples}
        inventory.append(row)
        (target/'source-inventory.json').write_text(json.dumps(row, ensure_ascii=False, indent=2))
(OUTPUT/'inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2))
print(json.dumps([{k:v for k,v in row.items() if k != 'samples'} for row in inventory], ensure_ascii=False))
