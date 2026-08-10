from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
STAGE = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _count(pattern: str, value: str) -> int:
    matched = re.search(pattern, _ANSI_RE.sub("", value), re.IGNORECASE)
    return int(matched.group(1)) if matched else 0


def _run(identifier: str, command: list[str], cwd: Path, kind: str) -> dict[str, object]:
    started = time.monotonic()
    environment = dict(os.environ)
    environment.setdefault("PYTHONUTF8", "1")
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = "\n".join((completed.stdout, completed.stderr))
    if kind == "pytest":
        counts = {
            "passed": _count(r"(\d+)\s+passed", output),
            "failed": _count(r"(\d+)\s+failed", output),
            "skipped": _count(r"(\d+)\s+skipped", output),
        }
    elif kind == "vitest":
        counts = {
            "passed": _count(r"Tests\s+(\d+)\s+passed", output),
            "failed": _count(r"Tests\s+(\d+)\s+failed", output),
            "skipped": _count(r"Tests\s+.*?(\d+)\s+skipped", output),
        }
    else:
        counts = {"passed": int(completed.returncode == 0), "failed": int(completed.returncode != 0), "skipped": 0}
    return {
        "id": identifier,
        "command": command,
        "cwd": str(cwd),
        "duration_seconds": round(time.monotonic() - started, 3),
        "exit_code": completed.returncode,
        "passed": completed.returncode == 0,
        "counts": counts,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main() -> int:
    npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
    failure_files = [
        "app/tests/test_mineru_http_client.py",
        "app/tests/test_mineru_task_store.py",
        "app/tests/test_parser_router_stage2.py",
        "app/tests/test_stage4_index_coordinator.py",
        "app/tests/test_stage5_office_semantics.py",
    ]
    commands = [
        ("backend_full", [sys.executable, "-m", "pytest", "app/tests", "-q", "-rs"], BACKEND, "pytest"),
        ("failure_matrix", [sys.executable, "-m", "pytest", *failure_files, "-q"], BACKEND, "pytest"),
        ("frontend_test", [npm, "test"], FRONTEND, "vitest"),
        ("frontend_lint", [npm, "run", "lint"], FRONTEND, "check"),
        ("frontend_build", [npm, "run", "build"], FRONTEND, "check"),
    ]
    results = [_run(*item) for item in commands]
    by_id = {str(item["id"]): item for item in results}
    failures = [str(item["id"]) for item in results if not item["passed"]]
    summary = {
        "all_passed": not failures,
        "failed_commands": failures,
        "backend_full_passed": by_id["backend_full"]["counts"]["passed"],
        "backend_full_failed": by_id["backend_full"]["counts"]["failed"],
        "backend_full_skipped": by_id["backend_full"]["counts"]["skipped"],
        "failure_matrix_passed": by_id["failure_matrix"]["counts"]["passed"],
        "failure_matrix_failed": by_id["failure_matrix"]["counts"]["failed"],
        "frontend_tests_passed": by_id["frontend_test"]["counts"]["passed"],
        "frontend_lint_passed": by_id["frontend_lint"]["passed"],
        "frontend_build_passed": by_id["frontend_build"]["passed"],
    }
    payload = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "python_executable": sys.executable,
        "failure_matrix_test_files": failure_files,
        "expected_skip_explanation": {
            "count": 9,
            "reason": "Seven pgvector atomicity tests and two business lifecycle tests require the explicitly isolated Stage-4 loopback PostgreSQL/pgvector DSN.",
            "independent_evidence": "quality/stage4/test_results.json backend_with_isolated_pgvector: 296 passed, 0 failed, 0 skipped",
        },
        "commands": results,
        "summary": summary,
    }
    temporary = STAGE / "test_results.json.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, STAGE / "test_results.json")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
