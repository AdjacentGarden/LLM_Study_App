from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
STAGE = Path(__file__).resolve().parent


def main() -> int:
    endpoint = os.environ.get("BOOKCOURSE_MINERU_ENDPOINT", "http://127.0.0.1:8001").rstrip("/")
    failures: list[str] = []
    try:
        with urllib.request.urlopen(f"{endpoint}/health", timeout=10) as response:  # noqa: S310 - loopback endpoint is intentional
            health = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # pragma: no cover - recorded for operational diagnosis
        health = {"error_type": type(exc).__name__}
        failures.append("mineru_health")

    gpu: dict[str, object]
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        name, total, used, utilization = [item.strip() for item in completed.stdout.strip().split(",", 3)]
        gpu = {
            "name": name,
            "memory_total_mib": int(total),
            "memory_used_mib": int(used),
            "utilization_percent": int(utilization),
        }
    except Exception as exc:  # pragma: no cover - recorded for operational diagnosis
        gpu = {"error_type": type(exc).__name__}
        failures.append("gpu_probe")

    expected_health = {
        "status": "healthy",
        "protocol_version": 2,
        "max_concurrent_requests": 1,
        "task_retention_seconds": 86400,
    }
    for field, expected in expected_health.items():
        if health.get(field) != expected:
            failures.append(f"mineru_{field}")

    payload = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "mineru_health": health,
        "expected_health": expected_health,
        "gpu": gpu,
        "failures": sorted(set(failures)),
        "passed": not failures,
    }
    output = STAGE / "operational_probe.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    print(json.dumps({"passed": payload["passed"], "failures": payload["failures"], "health": health}, ensure_ascii=False))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
