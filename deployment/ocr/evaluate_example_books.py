"""Run all supplied PDFs on two isolated GPU workers, retaining per-book failures."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import time

ROOT = Path('/data1/zhenghang/adaptive-book-ocr')
OUTPUT = ROOT / 'output/examples-20260905'


def main() -> None:
    files = sorted((ROOT / 'input/examples-20260905').glob('*.pdf'))
    jobs: queue.Queue[Path] = queue.Queue()
    for source in sorted(files, key=lambda p: p.stat().st_size, reverse=True):
        jobs.put(source)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 3.5 * 3600

    def worker(gpu: int) -> None:
        while not jobs.empty() and time.monotonic() < deadline:
            try:
                source = jobs.get_nowait()
            except queue.Empty:
                return
            book_id = hashlib.sha256(source.name.encode()).hexdigest()[:12]
            target = OUTPUT / book_id
            target.mkdir(exist_ok=True)
            state = {'book_id': book_id, 'filename': source.name, 'gpu': gpu,
                     'status': 'running', 'started_at': time.time()}
            status_path = target / 'status.json'
            status_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
            environment = os.environ.copy()
            environment.update(CUDA_VISIBLE_DEVICES=str(gpu), MINERU_DEVICE_MODE='cuda',
                MINERU_MODEL_SOURCE='local', MINERU_TOOLS_CONFIG_JSON=str(ROOT/'config/mineru.json'),
                PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True', TOKENIZERS_PARALLELISM='false',
                HF_HOME=str(ROOT/'cache/huggingface'), MODELSCOPE_CACHE=str(ROOT/'cache/modelscope'),
                TORCH_HOME=str(ROOT/'cache/torch'), XDG_CACHE_HOME=str(ROOT/'cache/xdg'), TMPDIR=str(ROOT/'tmp'))
            command = [sys.executable, '-m', 'adaptive_learning.ingestion.ocr_job',
                       '--source', str(source), '--output', str(target), '--language', 'ch']
            try:
                with (target/'runner.log').open('w') as log:
                    process = subprocess.Popen(command, env=environment, stdout=log,
                        stderr=subprocess.STDOUT, start_new_session=True)
                    try:
                        code = process.wait(timeout=max(1, deadline-time.monotonic()))
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                        raise
                state['status'] = 'completed' if code == 0 else 'failed'
                state['returncode'] = code
            except subprocess.TimeoutExpired:
                state['status'] = 'time_limit'
            state['duration_seconds'] = round(time.time()-state['started_at'], 2)
            status_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
            print(json.dumps(state, ensure_ascii=False), flush=True)
            jobs.task_done()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(worker, [2, 3]))


if __name__ == '__main__':
    main()
