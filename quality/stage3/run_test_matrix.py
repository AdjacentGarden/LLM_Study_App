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


def _atomic_write(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _integer(pattern: str, text: str) -> int:
    matched = re.search(pattern, text, re.IGNORECASE)
    return int(matched.group(1)) if matched else 0


def _parse_counts(kind: str, output: str, exit_code: int) -> dict[str, int]:
    cleaned = _ANSI_RE.sub("", output)
    if kind == "pytest":
        return {
            "passed": _integer(r"(\d+)\s+passed", cleaned),
            "failed": _integer(r"(\d+)\s+failed", cleaned),
            "skipped": _integer(r"(\d+)\s+skipped", cleaned),
            "test_files_passed": 0,
        }
    if kind == "vitest":
        return {
            "passed": _integer(r"Tests\s+(\d+)\s+passed", cleaned),
            "failed": _integer(r"Tests\s+(\d+)\s+failed", cleaned),
            "skipped": _integer(r"Tests\s+.*?(\d+)\s+skipped", cleaned),
            "test_files_passed": _integer(r"Test Files\s+(\d+)\s+passed", cleaned),
        }
    return {
        "passed": 1 if exit_code == 0 else 0,
        "failed": 0 if exit_code == 0 else 1,
        "skipped": 0,
        "test_files_passed": 0,
    }


def _run(identifier: str, label: str, command: list[str], cwd: Path, kind: str) -> dict[str, object]:
    started_at = datetime.now(timezone.utc)
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
    elapsed = time.monotonic() - started
    combined = "\n".join(value for value in (completed.stdout, completed.stderr) if value)
    counts = _parse_counts(kind, combined, completed.returncode)
    return {
        "id": identifier,
        "label": label,
        "kind": kind,
        "command": command,
        "cwd": str(cwd),
        "started_at": started_at.isoformat(),
        "duration_seconds": round(elapsed, 3),
        "exit_code": completed.returncode,
        "passed": completed.returncode == 0,
        "counts": counts,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main() -> int:
    npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
    stage3_tests = sorted(BACKEND.glob("app/tests/test_stage3_*.py"))
    stage3_tests.extend(
        [
            BACKEND / "app/tests/test_chunk_protocol_stage3.py",
            BACKEND / "app/tests/test_chunker_v2_stage3.py",
        ]
    )
    relative_stage3 = [str(path.relative_to(BACKEND)) for path in stage3_tests if path.exists()]
    commands = [
        (
            "backend_full",
            "Backend full pytest suite",
            [sys.executable, "-m", "pytest", "app/tests", "-q"],
            BACKEND,
            "pytest",
        ),
        (
            "backend_stage3",
            "Stage 3 focused pytest matrix",
            [sys.executable, "-m", "pytest", *relative_stage3, "-q"],
            BACKEND,
            "pytest",
        ),
        ("frontend_test", "Frontend Vitest suite", [npm, "test"], FRONTEND, "vitest"),
        ("frontend_lint", "Frontend ESLint", [npm, "run", "lint"], FRONTEND, "check"),
        ("frontend_build", "Frontend TypeScript/Vite build", [npm, "run", "build"], FRONTEND, "check"),
    ]
    results = [_run(*command) for command in commands]
    by_id = {str(item["id"]): item for item in results}
    failures = [str(item["id"]) for item in results if not item["passed"]]
    payload = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "python_executable": sys.executable,
        "stage3_test_files": relative_stage3,
        "commands": results,
        "summary": {
            "command_count": len(results),
            "all_passed": not failures,
            "failed_commands": failures,
            "backend_full_passed": by_id["backend_full"]["counts"]["passed"],
            "backend_full_failed": by_id["backend_full"]["counts"]["failed"],
            "backend_full_skipped": by_id["backend_full"]["counts"]["skipped"],
            "stage3_passed": by_id["backend_stage3"]["counts"]["passed"],
            "stage3_failed": by_id["backend_stage3"]["counts"]["failed"],
            "frontend_test_files_passed": by_id["frontend_test"]["counts"]["test_files_passed"],
            "frontend_tests_passed": by_id["frontend_test"]["counts"]["passed"],
            "frontend_lint_passed": by_id["frontend_lint"]["passed"],
            "frontend_build_passed": by_id["frontend_build"]["passed"],
        },
    }
    _atomic_write(STAGE / "test_results.json", payload)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
