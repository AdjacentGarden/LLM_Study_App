"""Activate the probed PUCODING model without changing source, OCR, or learning data."""
import os
from pathlib import Path
import shutil
import subprocess

root = Path('/data1/zhenghang/adaptive-book-ocr')
active = root/'secrets/adaptive-book.env'
backup = root/'secrets/pre-grok43-20260905.env'
if backup.exists():
    raise RuntimeError('Backup already exists; refusing duplicate activation')
shutil.copy2(active, backup)
os.chmod(backup, 0o600)
updates = {'LLM_PROVIDER':'pucoding', 'PUCODING_TEXT_MODEL':'grok-4.3',
           'PUCODING_VISION_MODEL':'grok-4.3'}
lines = [line for line in active.read_text().splitlines() if line.split('=', 1)[0] not in updates]
lines.extend(f'{key}={value}' for key, value in updates.items())
temporary = active.with_suffix('.grok43-new')
with os.fdopen(os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600), 'w') as stream:
    stream.write('\n'.join(lines)+'\n')
temporary.replace(active)
runner = str(root/'app/deployment/server/run_backend.sh')
try:
    subprocess.run([runner, 'restart'], check=True)
except subprocess.CalledProcessError:
    subprocess.run([runner, 'stop'], check=False)
    shutil.copy2(backup, active)
    subprocess.run([runner, 'start'], check=True)
    raise
print('Activated grok-4.3 for text and vision; previous configuration retained privately.')
