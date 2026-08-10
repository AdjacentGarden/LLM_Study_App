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
    focused = sorted(BACKEND.glob("app/tests/test_stage5_*.py"))
    focused.append(BACKEND / "app/tests/test_phase5_ocr.py")
    relative = [str(path.relative_to(BACKEND)) for path in focused]
    commands = [
        ("backend_full", [sys.executable, "-m", "pytest", "app/tests", "-q"], BACKEND, "pytest"),
        ("backend_stage5", [sys.executable, "-m", "pytest", *relative, "-q"], BACKEND, "pytest"),
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
        "stage5_passed": by_id["backend_stage5"]["counts"]["passed"],
        "stage5_failed": by_id["backend_stage5"]["counts"]["failed"],
        "frontend_tests_passed": by_id["frontend_test"]["counts"]["passed"],
        "frontend_lint_passed": by_id["frontend_lint"]["passed"],
        "frontend_build_passed": by_id["frontend_build"]["passed"],
    }
    payload = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "python_executable": sys.executable,
        "stage5_test_files": relative,
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
