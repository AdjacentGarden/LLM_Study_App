#!/usr/bin/env python3
"""Manage one loopback MinerU API process whose CUDA model survives requests.

Export model, CUDA, and MINERU_ENGINE_ARGS_JSON settings before launching. The
interpreter defaults to the one running this script; OCR_PYTHON can override it.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT = Path(__file__).resolve()
APP = Path(os.getenv("APP_ROOT", str(SCRIPT.parents[2]))).resolve()
ROOT = Path(os.getenv("RUNTIME_ROOT", str(APP / "data/ocr-runtime"))).resolve()
PORT = int(os.getenv("OCR_RESIDENT_PORT", "18200"))
PYTHON = os.getenv("OCR_PYTHON", sys.executable)


def state_file(suffix: str) -> Path:
    return ROOT / "run" / f"ocr-resident-{PORT}.{suffix}"


def log_file() -> Path:
    return ROOT / "logs" / f"ocr-resident-{PORT}.log"


def url() -> str:
    return f"http://127.0.0.1:{PORT}"


def health() -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url() + "/health", timeout=2) as response:
        value = json.load(response)
    if (
        not isinstance(value, dict)
        or value.get("status") != "healthy"
        or value.get("max_concurrent_requests") != 1
    ):
        raise RuntimeError("Unexpected MinerU service health/configuration")
    return value


def read_pid() -> int | None:
    try:
        pid = int(state_file("pid").read_text().strip())
        arguments = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
        if (
            pid > 1
            and b"mineru.cli.fast_api" in arguments
            and str(PORT).encode() in arguments
        ):
            return pid
    except (OSError, ValueError):
        pass
    return None


def profile_environment() -> dict[str, str]:
    environment = os.environ.copy()
    source = str(APP / "backend/src")
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [source, environment.get("PYTHONPATH")])
    )
    environment.update(
        RUNTIME_ROOT=str(ROOT),
        APP_ROOT=str(APP),
        OCR_RESIDENT_PORT=str(PORT),
        ORT_DISABLE_TELEMETRY="1",
        MINERU_API_MAX_CONCURRENT_REQUESTS="1",
        MINERU_API_DISABLE_ACCESS_LOG="1",
        MINERU_API_ENABLE_FASTAPI_DOCS="0",
        MINERU_API_OUTPUT_ROOT=str(ROOT / "data/tmp" / f"mineru-resident-{PORT}"),
    )
    environment.setdefault("MINERU_DEVICE_MODE", "cuda")
    environment.setdefault("MINERU_API_TASK_RETENTION_SECONDS", "86400")
    environment.setdefault("MINERU_API_TASK_CLEANUP_INTERVAL_SECONDS", "300")
    environment.setdefault("TOKENIZERS_PARALLELISM", "false")
    environment.setdefault("OMP_NUM_THREADS", "4")
    environment.pop("MINERU_API_SHUTDOWN_ON_STDIN_EOF", None)
    if environment["MINERU_DEVICE_MODE"] != "cuda":
        raise RuntimeError("Resident GPU OCR requires MINERU_DEVICE_MODE=cuda")
    return environment


def serve() -> None:
    environment = profile_environment()
    arguments = json.loads(
        subprocess.check_output(
            [
                PYTHON,
                "-c",
                "from adaptive_learning.ingestion.ocr_job import mineru_engine_arguments; import json; print(json.dumps(mineru_engine_arguments()))",
            ],
            env=environment,
            text=True,
        )
    )
    os.execve(
        PYTHON,
        [
            PYTHON,
            "-m",
            "mineru.cli.fast_api",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--enable-vlm-preload",
            "true",
            *arguments,
        ],
        environment,
    )


def start() -> None:
    pid = read_pid()
    if pid is not None:
        print(
            json.dumps({"running": True, "pid": pid, "url": url(), "health": health()})
        )
        return
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            raise RuntimeError("Port is occupied without our verified PID")
    started = time.monotonic()
    with log_file().open("a") as stream:
        process = subprocess.Popen(
            [sys.executable, str(SCRIPT), "serve", "--port", str(PORT)],
            env=profile_environment(),
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    state_file("pid").write_text(str(process.pid) + "\n")
    while time.monotonic() - started < 180:
        if process.poll() is not None:
            state_file("pid").unlink(missing_ok=True)
            raise RuntimeError(
                "OCR API exited during startup; inspect " + str(log_file())
            )
        try:
            state = health()
        except (OSError, ValueError, RuntimeError, urllib.error.URLError):
            time.sleep(0.5)
            continue
        if read_pid() != process.pid:
            raise RuntimeError("Healthy service does not match the launched process")
        print(
            json.dumps(
                {
                    "running": True,
                    "pid": process.pid,
                    "url": url(),
                    "startup_seconds": time.monotonic() - started,
                    "health": state,
                }
            )
        )
        return
    raise TimeoutError("OCR API is still starting; inspect its log and status")


def stop() -> None:
    if not state_file("pid").exists():
        print(json.dumps({"running": False}))
        return
    pid = read_pid()
    if pid is None:
        raise RuntimeError(
            "Refusing to stop an unverified process from " + str(state_file("pid"))
        )
    os.killpg(pid, signal.SIGTERM)
    for _ in range(300):
        if read_pid() is None:
            state_file("pid").unlink(missing_ok=True)
            print(json.dumps({"running": False, "stopped_pid": pid}))
            return
        time.sleep(0.1)
    os.killpg(pid, signal.SIGKILL)
    state_file("pid").unlink(missing_ok=True)
    print(json.dumps({"running": False, "stopped_pid": pid, "forced": True}))


def main() -> None:
    global PORT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("start", "stop", "restart", "status", "serve")
    )
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    PORT = args.port
    if not 1 <= PORT <= 65535:
        raise ValueError("OCR_RESIDENT_PORT must be a valid TCP port")
    for directory in (ROOT / "run", ROOT / "logs"):
        directory.mkdir(parents=True, exist_ok=True)
    if args.action == "serve":
        serve()
        return
    with state_file("lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action in ("stop", "restart"):
            stop()
        if args.action in ("start", "restart"):
            start()
        if args.action == "status":
            pid = read_pid()
            print(
                json.dumps(
                    {
                        "running": pid is not None,
                        "pid": pid,
                        "url": url(),
                        "health": health() if pid is not None else None,
                    }
                )
            )


if __name__ == "__main__":
    main()
