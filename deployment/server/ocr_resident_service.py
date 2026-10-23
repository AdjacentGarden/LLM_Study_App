#!/usr/bin/env python3
"""Manage one loopback MinerU API process whose CUDA model survives requests."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(os.getenv('RUNTIME_ROOT', '/data1/zhenghang/adaptive-book-ocr'))
APP = Path(os.getenv('APP_ROOT', str(ROOT / 'app')))
PORT = int(os.getenv('OCR_RESIDENT_PORT', '18200'))
PID_FILE = ROOT / 'run/ocr-resident.pid'
LOG_FILE = ROOT / 'logs/ocr-resident.log'
URL = 'http://127.0.0.1:' + str(PORT)


def health():
    # This service is local; outbound model proxies must never see OCR requests.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(URL + '/health', timeout=2) as response:
        value = json.load(response)
    if value.get('status') != 'healthy' or value.get('max_concurrent_requests') != 1:
        raise RuntimeError('Unexpected MinerU service health/configuration')
    return value


def read_pid():
    try:
        pid = int(PID_FILE.read_text().strip())
        if pid <= 1:
            return None
        arguments = (Path('/proc') / str(pid) / 'cmdline').read_bytes().split(b'\0')
        if b'mineru.cli.fast_api' in arguments and str(PORT).encode() in arguments:
            return pid
    except (OSError, ValueError):
        pass
    return None


def profile_environment():
    profile = ROOT / 'config/gpu.env'
    raw = subprocess.check_output(['bash', '-c', 'set -a; source "$1"; env -0', 'ocr-profile', str(profile)])
    env = {}
    for item in raw.split(b'\0'):
        if b'=' in item:
            key, value = item.split(b'=', 1)
            env[os.fsdecode(key)] = os.fsdecode(value)
    env.update(
        PYTHONPATH=':'.join(str(path) for path in (APP / 'backend/src', ROOT / 'runtime-patches/lib/python3.12/site-packages', ROOT / 'runtime/lib/python3.12/site-packages')),
        MINERU_MODEL_SOURCE='local', MINERU_TOOLS_CONFIG_JSON=str(ROOT / 'config/mineru.json'),
        ORT_DISABLE_TELEMETRY='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        MINERU_API_MAX_CONCURRENT_REQUESTS='1', MINERU_API_DISABLE_ACCESS_LOG='1',
        MINERU_API_ENABLE_FASTAPI_DOCS='0', MINERU_API_OUTPUT_ROOT=str(ROOT / 'data/tmp/mineru-resident'),
        MINERU_API_TASK_RETENTION_SECONDS='86400', MINERU_API_TASK_CLEANUP_INTERVAL_SECONDS='300',
        TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='4',
        PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True',
    )
    env.pop('MINERU_API_SHUTDOWN_ON_STDIN_EOF', None)
    if not env.get('CUDA_VISIBLE_DEVICES') or env.get('MINERU_DEVICE_MODE') != 'cuda':
        raise RuntimeError('A GPU OCR profile is required')
    return env


def serve():
    env = profile_environment()
    python = '/home/zhenghang/download/enter/bin/python'
    # Validate and use the same bounded engine options as the application.
    arguments = json.loads(subprocess.check_output([
        python, '-c', 'from adaptive_learning.ingestion.ocr_job import mineru_engine_arguments; import json; print(json.dumps(mineru_engine_arguments()))'
    ], env=env, text=True))
    os.execve(python, [python, '-m', 'mineru.cli.fast_api', '--host', '127.0.0.1',
                       '--port', str(PORT), '--enable-vlm-preload', 'true', *arguments], env)


def start():
    if read_pid() is not None:
        print(json.dumps({'running': True, 'pid': read_pid(), 'url': URL, 'health': health()}))
        return
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(('127.0.0.1', PORT)) == 0:
            raise RuntimeError('Port is already in use without our verified PID; inspect it first')
    started = time.monotonic()
    with LOG_FILE.open('a') as stream:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'serve'],
                                   stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    PID_FILE.write_text(str(process.pid) + '\n')
    while time.monotonic() - started < 180:
        if process.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            raise RuntimeError('OCR API exited during startup; inspect ' + str(LOG_FILE))
        try:
            state = health()
        except Exception:
            time.sleep(0.5)
            continue
        if read_pid() != process.pid:
            raise RuntimeError('Healthy service does not match the launched process')
        print(json.dumps({'running': True, 'pid': process.pid, 'url': URL,
                          'startup_seconds': time.monotonic() - started, 'health': state}))
        return
    raise TimeoutError('OCR API is still starting; inspect its log and status')


def stop():
    if not PID_FILE.exists():
        print(json.dumps({'running': False}))
        return
    pid = read_pid()
    if pid is None:
        raise RuntimeError('Refusing to stop an unverified process from ' + str(PID_FILE))
    os.killpg(pid, signal.SIGTERM)
    for _ in range(300):
        if read_pid() is None:
            PID_FILE.unlink(missing_ok=True)
            print(json.dumps({'running': False, 'stopped_pid': pid}))
            return
        time.sleep(0.1)
    os.killpg(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    print(json.dumps({'running': False, 'stopped_pid': pid, 'forced': True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'stop', 'restart', 'status', 'serve'))
    action = parser.parse_args().action
    if not 1 <= PORT <= 65535:
        raise ValueError('OCR_RESIDENT_PORT must be a valid TCP port')
    for directory in (ROOT / 'run', ROOT / 'logs'):
        directory.mkdir(parents=True, exist_ok=True)
    if action == 'serve':
        serve()
        return
    with (ROOT / 'run/ocr-resident.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action in ('stop', 'restart'):
            stop()
        if action in ('start', 'restart'):
            start()
        if action == 'status':
            pid = read_pid()
            print(json.dumps({'running': pid is not None, 'pid': pid, 'url': URL,
                              'health': health() if pid is not None else None}))


if __name__ == '__main__':
    main()
