from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4
import argparse
import json
import subprocess
import time

from app.document.mineru.client import MinerUClient
from app.document.mineru.exceptions import MinerUError
from app.document.mineru.models import MinerUClientConfig, MinerUParseOptions, sha256_file
from app.document.mineru.task_store import MinerUTaskStore


def _gpu_sample() -> dict[str, int] | None:
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        ).strip()
        memory, utilization = [int(value.strip()) for value in output.splitlines()[0].split(",")]
        return {"memory_used_mib": memory, "utilization_percent": utilization}
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _sample_gpu(stop: Event, samples: list[dict[str, int]]) -> None:
    while not stop.is_set():
        sample = _gpu_sample()
        if sample is not None:
            samples.append(sample)
        stop.wait(0.2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8001")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    source = args.file.resolve()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    options = MinerUParseOptions(return_images=True)
    config = MinerUClientConfig(
        endpoint=args.endpoint,
        total_timeout_seconds=args.timeout,
        request_timeout_seconds=60.0,
        connect_timeout_seconds=10.0,
        poll_interval_seconds=0.5,
        max_retries=2,
        retry_backoff_seconds=0.5,
        retry_max_backoff_seconds=5.0,
    )
    store = MinerUTaskStore(namespace=f"mineru_stage1_probe_{uuid4().hex}")
    samples: list[dict[str, int]] = []
    stop = Event()
    sampler = Thread(target=_sample_gpu, args=(stop, samples), daemon=True)
    sampler.start()
    started = time.monotonic()

    report: dict[str, object] = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": config.endpoint,
        "input_fixture": source.name,
        "input_bytes": source.stat().st_size,
        "input_sha256": sha256_file(source),
        "options_sha256": options.fingerprint(),
        "return_images": options.return_images,
    }
    try:
        with MinerUClient(config, task_store=store) as client:
            health = client.health()
            report["health"] = {
                "status": health.status,
                "version": health.version,
                "protocol_version": health.protocol_version,
                "max_concurrent_requests": health.max_concurrent_requests,
                "task_retention_seconds": health.task_retention_seconds,
                "task_cleanup_interval_seconds": health.task_cleanup_interval_seconds,
            }
            execution = client.execute(
                book_id=f"stage1_probe_{report['input_sha256'][:12]}",
                file_path=source,
                options=options,
            )

        document = next(iter(execution.result.results.values()))
        content = document.content_list or []
        middle = document.middle_json or {}
        pages = middle.get("pdf_info", []) if isinstance(middle, dict) else []
        content_types = Counter(
            str(item.get("type") or "unknown")
            for item in content
            if isinstance(item, dict)
        )
        images = document.images if isinstance(document.images, dict) else {}
        image_uri_chars = sum(len(value) for value in images.values() if isinstance(value, str))
        record = store.get_current(f"stage1_probe_{report['input_sha256'][:12]}")
        report.update(
            {
                "outcome": "completed",
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "task": {
                    "task_id": execution.task.task_id,
                    "status": execution.task.status,
                    "parse_generation": execution.parse_generation,
                    "resumed": execution.resumed,
                    "queued_ahead": execution.task.queued_ahead,
                },
                "result": {
                    "backend": execution.result.backend,
                    "version": execution.result.version,
                    "document_count": len(execution.result.results),
                    "markdown_chars": len(document.md_content or ""),
                    "middle_page_count": len(pages) if isinstance(pages, list) else 0,
                    "content_item_count": len(content),
                    "content_type_counts": dict(sorted(content_types.items())),
                    "image_count": len(images),
                    "image_data_uri_chars": image_uri_chars,
                    "result_digest": record.result_digest if record else None,
                },
            }
        )
        exit_code = 0
    except MinerUError as exc:
        report.update(
            {
                "outcome": "failed",
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error_code": exc.code,
                "error_type": type(exc).__name__,
                "status_code": exc.status_code,
            }
        )
        exit_code = 2
    finally:
        stop.set()
        sampler.join(timeout=5)

    if samples:
        report["gpu"] = {
            "sample_count": len(samples),
            "peak_memory_used_mib": max(sample["memory_used_mib"] for sample in samples),
            "peak_utilization_percent": max(sample["utilization_percent"] for sample in samples),
        }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
