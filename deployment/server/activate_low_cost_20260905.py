"""Switch the existing PUCODING credential to the verified lowest-price available model."""
import os
from pathlib import Path
import shutil
import subprocess

root=Path('/data1/zhenghang/adaptive-book-ocr')
app=root/'app'
active=root/'secrets/adaptive-book.env'
backup=root/'secrets/pre-grok-cost-20260905.env'
source_backup=root/'release-backups/pre-grok-cost-20260905'
if backup.exists() or source_backup.exists():
    raise RuntimeError('Backup already exists; refusing duplicate activation')
source_backup.mkdir()
files=['config.py','llm/client.py']
for name in files:
    target=source_backup/name
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(app/'backend/src/adaptive_learning'/name,target)
shutil.copy2(active,backup)
os.chmod(backup,0o600)
updates={'LLM_PROVIDER':'pucoding','PUCODING_TEXT_MODEL':'grok-4-fast','PUCODING_VISION_MODEL':'grok-4-fast'}
lines=[line for line in active.read_text().splitlines() if line.split('=',1)[0] not in updates]
lines.extend(f'{k}={v}' for k,v in updates.items())
temporary=active.with_suffix('.grok-new')
with os.fdopen(os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:
    stream.write('\n'.join(lines)+'\n')
for name in files:
    shutil.copy2(app/'tmp/provider-stage-20260905/backend/src/adaptive_learning'/name,
                 app/'backend/src/adaptive_learning'/name)
temporary.replace(active)
runner=str(app/'deployment/server/run_backend.sh')
try:
    subprocess.run([runner,'restart'],check=True)
except subprocess.CalledProcessError:
    subprocess.run([runner,'stop'],check=False)
    shutil.copy2(backup,active)
    for name in files:
        shutil.copy2(source_backup/name,app/'backend/src/adaptive_learning'/name)
    subprocess.run([runner,'start'],check=True)
    raise
print('Activated grok-4-fast through PUCODING Responses API; low reasoning enabled.')
