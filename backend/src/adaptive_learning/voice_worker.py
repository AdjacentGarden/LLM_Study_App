"""Bounded reusable offline ASR subprocess (the ASR wheel uses its own Python)."""
from __future__ import annotations

import json
import queue
import subprocess
import threading


class VoiceWorker:
    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.output = None
        self.signature = None

    def close(self):
        with self.lock:
            if self.process is not None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
                for stream in (self.process.stdin, self.process.stdout):
                    if stream:
                        stream.close()
            self.process = None

    def transcribe(self, command, env, payload, timeout=240):
        with self.lock:
            signature = (tuple(command), env.get("PYTHONPATH"))
            if self.process is None or self.process.poll() is not None or signature != self.signature:
                self.close()
                self.process = subprocess.Popen(command, env=env, stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                                text=True, bufsize=1)
                self.signature = signature
                self.output = queue.Queue(maxsize=2)
                process, output = self.process, self.output
                def read():
                    for line in process.stdout:
                        output.put(line)
                    output.put(None)
                threading.Thread(target=read, daemon=True, name="voice-response").start()
            try:
                self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
                line = self.output.get(timeout=timeout)
                if line is None:
                    raise RuntimeError("voice worker stopped")
                value = json.loads(line)
                if "error" in value:
                    raise RuntimeError("voice transcription failed")
                return value
            except (OSError, ValueError, RuntimeError, queue.Empty):
                self.close()
                raise
