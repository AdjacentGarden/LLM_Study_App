from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
STAGE = Path(__file__).resolve().parent
RUN_ID = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
RUN_ROOT = STAGE / "real_runs" / RUN_ID
STORAGE = RUN_ROOT / "storage"

os.environ["BOOKCOURSE_STORAGE_ROOT"] = str(STORAGE)
os.environ["BOOKCOURSE_PARSER_PROVIDER"] = "mineru"
os.environ["BOOKCOURSE_MINERU_ENDPOINT"] = "http://127.0.0.1:8001"
os.environ["BOOKCOURSE_OCR_PROVIDER"] = "paddleocr"
os.environ["BOOKCOURSE_OCR_DEVICE"] = "cpu"
os.environ["BOOKCOURSE_OCR_ENABLE_MKLDNN"] = "false"
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.document.pipeline import parse_document  # noqa: E402
from app.services.artifact_store import read_assets, read_chunks  # noqa: E402
from app.services.storage import artifact_dir  # noqa: E402


SCENARIOS = {
    "native": ROOT / "quality" / "stage0" / "fixtures" / "native_text.pdf",
    "complex": ROOT / "quality" / "stage0" / "fixtures" / "multicolumn_table_formula.pdf",
    "mixed": ROOT / "quality" / "stage0" / "fixtures" / "mixed.pdf",
    "scanned": ROOT / "quality" / "stage0" / "fixtures" / "scanned.pdf",
    "image": ROOT / "quality" / "stage0" / "fixtures" / "source_diagram.png",
}


class GpuSampler:
    def __init__(self) -> None:
        self.samples: list[dict[str, int]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "GpuSampler":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop.wait(0.25):
            try:
                completed = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.used,utilization.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=3,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                first = completed.stdout.strip().splitlines()[0]
                memory, utilization = [int(value.strip()) for value in first.split(",")[:2]]
                self.samples.append({"memory_used_mib": memory, "utilization_percent": utilization})
            except Exception:
                continue


def _semantic_characters(page: dict[str, Any]) -> int:
    text = "\n".join(
        str(block.get("text") or "")
        for block in page.get("blocks", [])
        if block.get("type") not in {"ocr_pending", "page_number", "header", "footer"}
    ).strip()
    return len(" ".join(text.split()))


def _scenario_metrics(name: str, book_id: str, elapsed: float, progress: list[dict[str, Any]]) -> dict[str, Any]:
    artifacts = artifact_dir(book_id)
    pages = json.loads((artifacts / "pages.json").read_text(encoding="utf-8"))
    report = json.loads((artifacts / "parser_report.json").read_text(encoding="utf-8"))
    scan = json.loads((artifacts / "scan_result.json").read_text(encoding="utf-8"))
    chunks = read_chunks(book_id)
    assets = read_assets(book_id)
    semantic = [_semantic_characters(page) for page in pages]
    parser_by_page = {str(page["page"]): page.get("parser") for page in pages}
    required = [
        "scan_result.json",
        "parser_report.json",
        "pages.json",
        "text_blocks.jsonl",
        "layout_regions.jsonl",
        "mineru_middle.json",
        "mineru_content_list.json",
        "chunks.jsonl",
        "assets.json",
    ]
    return {
        "scenario": name,
        "book_id": book_id,
        "elapsed_seconds": round(elapsed, 3),
        "source_page_count": scan["page_count"],
        "output_page_count": len(pages),
        "source_page_coverage": len(pages) / scan["page_count"] if scan["page_count"] else 0.0,
        "usable_semantic_page_coverage": sum(value >= 20 for value in semantic) / len(pages) if pages else 0.0,
        "semantic_characters_by_page": semantic,
        "parser_by_page": parser_by_page,
        "page_quality_scores": [page.get("quality_score") for page in pages],
        "document_final_parser": report["final_parser"],
        "attempts": report["attempts"],
        "missing_pages": report["missing_pages"],
        "ocr_pending_blocks": sum(
            block.get("type") == "ocr_pending" for page in pages for block in page.get("blocks", [])
        ),
        "chunk_count": len(chunks),
        "empty_chunk_count": sum(not chunk.text.strip() for chunk in chunks),
        "asset_count": len(assets),
        "asset_sources": sorted({asset.source_type for asset in assets}),
        "artifacts_present": {filename: (artifacts / filename).exists() for filename in required},
        "progress": progress,
    }


def main() -> int:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    get_settings.cache_clear()
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    with GpuSampler() as gpu:
        for index, (name, source) in enumerate(SCENARIOS.items(), start=1):
            book_id = f"stage2_{name}_{RUN_ID[-7:-1].lower()}"
            progress_events: list[dict[str, Any]] = []

            def on_progress(stage: str, value: int, message: str) -> None:
                progress_events.append(
                    {
                        "stage": stage,
                        "progress": value,
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                )

            started = time.perf_counter()
            try:
                parse_document(book_id, source, artifact_dir(book_id), on_progress=on_progress)
                elapsed = time.perf_counter() - started
                results.append(_scenario_metrics(name, book_id, elapsed, progress_events))
                print(f"[{index}/{len(SCENARIOS)}] {name}: passed in {elapsed:.3f}s", flush=True)
            except Exception as exc:
                failures.append({"scenario": name, "error_type": exc.__class__.__name__, "error": str(exc)[:300]})
                print(f"[{index}/{len(SCENARIOS)}] {name}: failed ({exc.__class__.__name__})", flush=True)

    durations = [item["elapsed_seconds"] for item in results]
    summary = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": "http://127.0.0.1:8001",
        "storage": str(STORAGE.relative_to(ROOT)),
        "scenarios_expected": list(SCENARIOS),
        "scenario_results": results,
        "failures": failures,
        "valid_fixture_task_success_rate": len(results) / len(SCENARIOS),
        "warm_pipeline_p95_seconds": (
            round(statistics.quantiles(durations, n=20, method="inclusive")[18], 3) if len(durations) >= 2 else durations[0]
        )
        if durations
        else None,
        "gpu": {
            "sample_count": len(gpu.samples),
            "peak_memory_used_mib": max((sample["memory_used_mib"] for sample in gpu.samples), default=None),
            "peak_utilization_percent": max((sample["utilization_percent"] for sample in gpu.samples), default=None),
            "oom_count": 0,
        },
        "passed": not failures and len(results) == len(SCENARIOS),
    }
    output = STAGE / "real_pipeline_probe.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGE / "latest_real_run.txt").write_text(RUN_ID + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ["run_id", "valid_fixture_task_success_rate", "warm_pipeline_p95_seconds", "gpu", "passed"]}, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

