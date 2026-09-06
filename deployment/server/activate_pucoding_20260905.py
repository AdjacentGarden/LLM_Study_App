"""Activate the separately supplied credential after provider adapter validation."""
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

root = Path('/data1/zhenghang/adaptive-book-ocr')
app = root/'app'
source = app/'tmp/provider-stage-20260905/backend/src/adaptive_learning'
active = root/'secrets/adaptive-book.env'
backup = root/'secrets/pre-pucoding-20260905.env'
if backup.exists():
    raise RuntimeError('Existing activation backup; inspect state instead of overwriting it')
incoming = dict(line.split('=',1) for line in (root/'secrets/pucoding-20260905.env').read_text().splitlines()
                if line and not line.startswith('#') and '=' in line)
key = incoming['PUCODING_API_KEY']
if not key.strip():
    raise RuntimeError('Empty incoming credential')
files = ['config.py', 'llm/client.py', 'api/app.py', 'api/qa_dependency.py', 'api/chapter_dependency.py']
with tarfile.open(root/'release-backups/pre-pucoding-source-20260905.tar.gz', 'w:gz') as archive:
    for name in files:
        archive.add(app/'backend/src/adaptive_learning'/name, arcname=name)
shutil.copy2(active, backup)
os.chmod(backup, 0o600)
updates = {'LLM_PROVIDER':'pucoding', 'PUCODING_BASE_URL':'https://pucoding.com/v1',
           'PUCODING_TEXT_MODEL':'claude-haiku-4-5-20251001',
           'PUCODING_VISION_MODEL':'claude-haiku-4-5-20251001', 'PUCODING_API_KEY':key}
lines = [line for line in active.read_text().splitlines() if line.split('=',1)[0] not in updates]
lines.extend(f'{name}={value}' for name,value in updates.items())
temporary = active.with_suffix('.new')
fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write('\n'.join(lines)+'\n')
for name in files:
    shutil.copy2(source/name, app/'backend/src/adaptive_learning'/name)
temporary.replace(active)
runner = str(app/'deployment/server/run_backend.sh')
try:
    subprocess.run([runner, 'restart'], check=True)
except subprocess.CalledProcessError:
    subprocess.run([runner, 'stop'], check=False)
    shutil.copy2(backup, active)
    with tarfile.open(root/'release-backups/pre-pucoding-source-20260905.tar.gz') as archive:
        for name in files:
            member = archive.extractfile(name)
            if member is None:
                raise RuntimeError('Incomplete source backup')
            with (app/'backend/src/adaptive_learning'/name).open('wb') as output:
                shutil.copyfileobj(member, output)
    subprocess.run([runner, 'start'], check=True)
    raise
print('Activated PUCODING claude-haiku-4-5-20251001; original credentials and source backed up.')
