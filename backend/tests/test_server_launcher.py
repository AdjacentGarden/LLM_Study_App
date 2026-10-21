"""Run the real launcher with temporary profiles and explicit interpreter doubles."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / "deployment/server/run_backend.sh"
FAKE_INTERPRETER = r"""
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer

def record(event, **extra):
    data = {"event": event, "args": sys.argv[1:], "interpreter": __file__, **extra}
    names = ("APP_PORT", "APP_HOST", "RUNTIME_ROOT", "PYTHONPATH", "FORWARDED_ALLOW_IPS",
             "FAST_OCR_API_URL", "FAST_OCR_PORT", "MINERU_API_URL", "OCR_RESIDENT_PORT",
             "FAST_OCR_SOURCE_ROOTS", "FAST_OCR_OUTPUT_ROOTS",
             "MINERU_DEVICE_MODE", "BOOK_LLM_CONCURRENCY", "CUDA_VISIBLE_DEVICES")
    data["environment"] = {name: os.getenv(name) for name in names}
    with open(os.environ["LAUNCHER_TEST_EVENTS"], "a") as stream:
        stream.write(json.dumps(data) + "\n")

args = sys.argv[1:]
if args[:1] == ["-c"]:
    os.execv(sys.executable, [sys.executable, *args])
elif args[:2] == ["-m", "uvicorn"]:
    record("application")
    if os.getenv("LAUNCHER_TEST_SERVE") == "1":
        time.sleep(float(os.getenv("LAUNCHER_TEST_START_DELAY", "0")))
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                record("health", path=self.path)
                time.sleep(float(os.getenv("LAUNCHER_TEST_HEALTH_DELAY", "0")))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"healthy"}')
            def log_message(self, *_args): pass
        HTTPServer(("127.0.0.1", int(os.environ["APP_PORT"])), Handler).serve_forever()
elif args and args[0].endswith(("fast_ocr_service.py", "ocr_resident_service.py")):
    record("ocr-service")
else:
    raise SystemExit("Unexpected fake interpreter invocation: " + repr(args))
"""


@pytest.fixture
def setup_launcher(tmp_path):
    environment = os.environ.copy()
    for name in list(environment):
        if name.startswith(("APP_", "OCR_", "FAST_OCR_", "MINERU_", "RUNTIME_", "CLOUDPATH_")):
            environment.pop(name)
    for name in (
        "SECRET_FILE",
        "ENV_FILE",
        "GPU_PROFILE_FILE",
        "INGESTION_PROFILE_FILE",
        "FORWARDED_ALLOW_IPS",
    ):
        environment.pop(name, None)
    app_python = tmp_path / "app python"
    ocr_python = tmp_path / "ocr python"
    for path in (app_python, ocr_python):
        path.write_text(f"#!{sys.executable}\n" + FAKE_INTERPRETER)
        path.chmod(0o755)
    config = tmp_path / "app.env"
    config.write_text("")
    events = tmp_path / "events.jsonl"
    environment.update(
        APP_PYTHON=str(app_python),
        OCR_PYTHON=str(ocr_python),
        RUNTIME_ROOT=str(tmp_path / "runtime"),
        ENV_FILE=str(config),
        LAUNCHER_TEST_EVENTS=str(events),
        PYTHONPATH="/explicit/prior",
    )

    def run(action="serve", content="", **extra):
        config.write_text(content)
        return subprocess.run(
            [str(LAUNCHER), action],
            env={**environment, **extra},
            capture_output=True,
            text=True,
            timeout=15,
        )

    def read_events():
        return (
            [json.loads(line) for line in events.read_text().splitlines()]
            if events.exists()
            else []
        )

    yield run, read_events, environment, tmp_path
    pid_file = tmp_path / "runtime/run/backend.pid"
    if pid_file.exists():
        try:
            os.killpg(int(pid_file.read_text()), signal.SIGTERM)
        except ProcessLookupError:
            pass


def test_default_launcher_is_portable_loopback_and_preserves_pythonpath(setup_launcher):
    run, events, environment, _ = setup_launcher
    result = run()
    assert result.returncode == 0, result.stderr
    assert len(events()) == 1
    application = events()[0]
    assert application["interpreter"] == environment["APP_PYTHON"]
    assert application["environment"]["APP_HOST"] == "127.0.0.1"
    assert application["environment"]["FORWARDED_ALLOW_IPS"] == "127.0.0.1"
    assert application["environment"]["PYTHONPATH"].endswith(":/explicit/prior")
    assert "--forwarded-allow-ips=127.0.0.1" in application["args"]
    assert "--workers" in application["args"]


def test_explicit_fast_backend_manages_configured_url_with_separate_ocr_python(setup_launcher):
    run, events, environment, _ = setup_launcher
    result = run(
        content="OCR_WORKER_ENABLED=true\nOCR_BACKEND=paddle-gpu\nFAST_OCR_API_URL=http://127.0.0.1:18303\n"
    )
    assert result.returncode == 0, result.stderr
    service, app = events()
    assert service["event"] == "ocr-service"
    assert service["interpreter"] == environment["OCR_PYTHON"]
    assert service["args"][-3:] == ["start", "--port", "18303"]
    assert service["args"][0].endswith("fast_ocr_service.py")
    assert app["environment"]["FAST_OCR_API_URL"] == "http://127.0.0.1:18303"


@pytest.mark.parametrize(
    "content",
    [
        "OCR_WORKER_ENABLED=false\nOCR_BACKEND=paddle-gpu\n",
        "OCR_WORKER_ENABLED=true\nOCR_BACKEND=paddle-gpu\nFAST_OCR_MANAGED=false\nFAST_OCR_API_URL=http://127.0.0.1:19444\n",
        "OCR_WORKER_ENABLED=true\nOCR_BACKEND=vlm-auto-engine\n",
    ],
)
def test_unselected_or_externally_managed_services_are_left_alone(setup_launcher, content):
    run, events, _, _ = setup_launcher
    result = run(content=content)
    assert result.returncode == 0, result.stderr
    assert [event["event"] for event in events()] == ["application"]


def test_mineru_resident_is_opt_in_and_uses_supplied_port(setup_launcher):
    run, events, _, _ = setup_launcher
    result = run(
        content="OCR_WORKER_ENABLED=true\nMINERU_RESIDENT_ENABLED=true\nMINERU_API_URL=http://127.0.0.1:18304\n"
    )
    assert result.returncode == 0, result.stderr
    service, app = events()
    assert service["args"][0].endswith("ocr_resident_service.py")
    assert service["args"][-1] == "18304"
    assert app["environment"]["MINERU_API_URL"] == "http://127.0.0.1:18304"


def test_url_port_conflict_fails_before_starting_either_service(setup_launcher):
    run, events, _, _ = setup_launcher
    result = run(
        content="OCR_WORKER_ENABLED=true\nOCR_BACKEND=paddle-gpu\nFAST_OCR_API_URL=http://127.0.0.1:18303\nFAST_OCR_PORT=18304\n"
    )
    assert result.returncode != 0
    assert "disagree" in result.stderr
    assert events() == []


def test_optional_gpu_and_performance_profiles_are_loaded(setup_launcher):
    run, events, _, tmp_path = setup_launcher
    config = tmp_path / "runtime/config"
    config.mkdir(parents=True)
    (config / "gpu.env").write_text("MINERU_DEVICE_MODE=cuda\nCUDA_VISIBLE_DEVICES=3\n")
    (config / "ingestion-performance.env").write_text("BOOK_LLM_CONCURRENCY=4\nAPP_PORT=18305\n")
    result = run(content="OCR_WORKER_ENABLED=false\n")
    assert result.returncode == 0, result.stderr
    env = events()[0]["environment"]
    assert env["MINERU_DEVICE_MODE"] == "cuda"
    assert env["CUDA_VISIBLE_DEVICES"] == "3"
    assert env["BOOK_LLM_CONCURRENCY"] == "4"
    assert env["APP_PORT"] == "18305"


def test_start_probes_custom_port_loaded_from_environment_file(setup_launcher):
    run, events, _, _ = setup_launcher
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    content = f"APP_PORT={port}\nOCR_WORKER_ENABLED=false\n"
    started = run("start", content, LAUNCHER_TEST_SERVE="1")
    assert started.returncode == 0, started.stderr
    assert any(
        event["event"] == "health" and event["environment"]["APP_PORT"] == str(port)
        for event in events()
    )
    assert run("status", content).returncode == 0
    stopped = run("stop", content)
    assert stopped.returncode == 0, stopped.stderr


def test_restart_replaces_stale_pid_with_a_healthy_backend(setup_launcher):
    run, events, _, tmp_path = setup_launcher
    # Use the PID of an actual exited/reaped process, as left by a crashed server.
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    exited.wait(timeout=5)
    pid_file = tmp_path / "runtime/run/backend.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text(str(exited.pid))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    content = f"APP_PORT={port}\nOCR_WORKER_ENABLED=false\n"

    restarted = run("restart", content, LAUNCHER_TEST_SERVE="1")

    assert restarted.returncode == 0, restarted.stderr
    assert "removed stale pid file" in restarted.stdout
    assert int(pid_file.read_text()) != exited.pid
    assert any(event["event"] == "health" for event in events())
    assert run("status", content).returncode == 0
    assert run("stop", content).returncode == 0


def test_restart_refuses_a_live_unrelated_process(setup_launcher):
    run, events, _, tmp_path = setup_launcher
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    pid_file = tmp_path / "runtime/run/backend.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text(str(unrelated.pid))
    try:
        result = run("restart", "OCR_WORKER_ENABLED=false\n")

        assert result.returncode != 0
        assert "refusing to stop unverified process" in result.stderr
        assert unrelated.poll() is None
        assert pid_file.read_text() == str(unrelated.pid)
        assert events() == []
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
        pid_file.unlink(missing_ok=True)


def test_custom_app_data_directory_is_inside_managed_fast_ocr_roots(setup_launcher):
    run, events, _, tmp_path = setup_launcher
    custom_data = tmp_path / "custom app data"
    result = run(content=f"APP_DATA_DIR='{custom_data}'\nOCR_BACKEND=paddle-gpu\n")
    assert result.returncode == 0, result.stderr
    service = events()[0]
    assert str(custom_data) in service["environment"]["FAST_OCR_SOURCE_ROOTS"].split(os.pathsep)
    assert str(custom_data) in service["environment"]["FAST_OCR_OUTPUT_ROOTS"].split(os.pathsep)


def test_ingestion_profile_is_last_configuration_override(setup_launcher):
    run, events, _, tmp_path = setup_launcher
    config = tmp_path / "runtime/config"
    secrets = tmp_path / "runtime/secrets"
    config.mkdir(parents=True)
    secrets.mkdir()
    for path, value in (
        (config / "published-books.env", 2),
        (config / "accounts.env", 3),
        (secrets / "learning-studio.env", 4),
        (config / "gpu.env", 5),
        (config / "ingestion-performance.env", 6),
    ):
        path.write_text(f"BOOK_LLM_CONCURRENCY={value}\n")
    result = run(content="BOOK_LLM_CONCURRENCY=1\nOCR_WORKER_ENABLED=false\n")
    assert result.returncode == 0, result.stderr
    assert events()[0]["environment"]["BOOK_LLM_CONCURRENCY"] == "6"


@pytest.mark.parametrize(
    "start_delay,health_delay,timeout,succeeds",
    [
        (0.4, 0, 3, True),
        (2, 0, 0.2, False),
        (0, 1, 0.2, False),
    ],
)
def test_startup_timeout_uses_elapsed_time_with_delayed_http(
    setup_launcher, start_delay, health_delay, timeout, succeeds
):
    run, _, _, _ = setup_launcher
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    content = (
        f"APP_PORT={port}\nOCR_WORKER_ENABLED=false\nBACKEND_STARTUP_TIMEOUT_SECONDS={timeout}\n"
    )
    result = run(
        "start",
        content,
        LAUNCHER_TEST_SERVE="1",
        LAUNCHER_TEST_START_DELAY=str(start_delay),
        LAUNCHER_TEST_HEALTH_DELAY=str(health_delay),
    )
    assert (result.returncode == 0) is succeeds, result.stderr
    if not succeeds:
        assert "did not become healthy" in result.stderr
    assert run("stop", content).returncode == 0
