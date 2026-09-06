"""Deploy tested source + new live-book index, retaining an explicit rollback snapshot."""
from pathlib import Path
import shutil
import sqlite3
import subprocess

ROOT = Path('/data1/zhenghang/adaptive-book-ocr')
APP = ROOT/'app'
STAGE = APP/'tmp/repair-20260905'
BACKUP = ROOT/'release-backups/repair-20260905'
FILES = ['backend/src/adaptive_learning/ingestion/chaptering.py',
         'backend/src/adaptive_learning/rag/index.py',
         'backend/src/adaptive_learning/rag/service.py',
         'backend/src/adaptive_learning/rag/grounded_qa.py',
         'backend/src/adaptive_learning/personalization/flashcard_quality.py',
         'deployment/rag/rag_eval.py']


def main():
    if BACKUP.exists():
        raise RuntimeError('Release backup already exists; refusing duplicate deployment')
    with sqlite3.connect('file:'+str(ROOT/'data/state/ocr_jobs.sqlite3')+'?mode=ro', uri=True) as db:
        states = dict(db.execute('SELECT status,count(*) FROM ocr_jobs GROUP BY status'))
    if any(count and status not in {'failed','ocr_ready','completed','cancelled'} for status,count in states.items()):
        raise RuntimeError('OCR jobs are active; postpone deployment')
    new_index = ROOT/'rag-eval/index-repaired-20260905'
    old_index = ROOT/'rag-eval/index'
    if not all((new_index/name).is_file() for name in ['index_manifest.json','chunks.json','embeddings.npy']):
        raise RuntimeError('Validated new index missing')
    BACKUP.mkdir(parents=True)
    for name in FILES:
        target = BACKUP/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(APP/name, target)
    runner = str(APP/'deployment/server/run_backend.sh')
    subprocess.run([runner,'stop'],check=True)
    moved_old = False
    moved_new = False
    try:
        old_index.rename(BACKUP/'index')
        moved_old = True
        new_index.rename(old_index)
        moved_new = True
        for name in FILES:
            shutil.copy2(STAGE/name, APP/name)
        subprocess.run([runner,'start'],check=True)
    except Exception:
        subprocess.run([runner,'stop'],check=False)
        for name in FILES:
            shutil.copy2(BACKUP/name, APP/name)
        if moved_new:
            old_index.rename(new_index)
        if moved_old:
            (BACKUP/'index').rename(old_index)
        subprocess.run([runner,'start'],check=True)
        raise
    print('Repair release activated; source and previous index retained for rollback. User data untouched.')


if __name__ == '__main__':
    main()
