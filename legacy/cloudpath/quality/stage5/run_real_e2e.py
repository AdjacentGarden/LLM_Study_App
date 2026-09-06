from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import asdict, is_dataclass
from io import BytesIO
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time
from typing import Any

from PIL import Image


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
os.environ["BOOKCOURSE_EMBEDDING_PROVIDER"] = "hashing"
os.environ["BOOKCOURSE_DATABASE_URL"] = ""
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.document.pipeline import parse_document  # noqa: E402
from app.rag.indexing import read_index_status  # noqa: E402
from app.rag.service import answer_query  # noqa: E402
from app.schemas.books import RagQuery  # noqa: E402
from app.services.artifact_store import read_assets, read_chunks  # noqa: E402
from app.services.storage import artifact_dir  # noqa: E402
from app.services.upload_validation import validate_saved_upload  # noqa: E402


FIXTURES = ROOT / "quality" / "stage0" / "fixtures"


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


def _single_image(path: Path, image_format: str) -> Path:
    buffer = BytesIO()
    source = Image.open(FIXTURES / "source_diagram.png").convert("RGB")
    source.save(buffer, format=image_format)
    source.close()
    path.write_bytes(buffer.getvalue())
    return path


def _semantic_characters(pages: list[dict[str, Any]]) -> list[int]:
    return [
        len(
            " ".join(
                str(block.get("text") or "").strip()
                for block in page.get("blocks", [])
                if block.get("type") not in {"ocr_pending", "page_number", "header", "footer"}
            ).strip()
        )
        for page in pages
    ]


def _jsonable_status(status: object | None) -> object | None:
    if status is None:
        return None
    model_dump = getattr(status, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if is_dataclass(status):
        return asdict(status)
    return dict(vars(status))


def main() -> int:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    generated = RUN_ROOT / "generated_inputs"
    generated.mkdir(parents=True, exist_ok=True)
    scenarios: dict[str, tuple[Path, str]] = {
        "pdf": (FIXTURES / "native_text.pdf", "cell membrane exchange"),
        "png": (FIXTURES / "source_diagram.png", "CELL DIAGRAM"),
        "jpg": (FIXTURES / "sample.jpg", "CELL DIAGRAM"),
        "jpeg": (FIXTURES / "sample.jpeg", "CELL DIAGRAM"),
        "jp2": (FIXTURES / "sample.jp2", "CELL DIAGRAM"),
        "webp": (FIXTURES / "sample.webp", "CELL DIAGRAM"),
        "gif": (_single_image(generated / "single.gif", "GIF"), "CELL DIAGRAM"),
        "bmp": (FIXTURES / "sample.bmp", "CELL DIAGRAM"),
        "tif": (FIXTURES / "sample.tif", "CELL DIAGRAM"),
        "tiff": (_single_image(generated / "single.tiff", "TIFF"), "CELL DIAGRAM"),
        "docx": (FIXTURES / "sample.docx", "cell membrane exchange"),
        "pptx": (FIXTURES / "sample.pptx", "ATP production"),
        "xlsx": (FIXTURES / "sample.xlsx", "Synthetic Plant Growth"),
    }
    get_settings.cache_clear()
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    with GpuSampler() as gpu:
        for position, (extension, (source, question)) in enumerate(scenarios.items(), start=1):
            book_id = f"stage5_{extension}_{RUN_ID[-7:-1].lower()}"
            progress: list[dict[str, object]] = []
            started = time.perf_counter()

            def on_progress(stage: str, value: int, message: str) -> None:
                progress.append(
                    {
                        "stage": stage,
                        "progress": value,
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                )

            try:
                inspection = validate_saved_upload(source, source.name)
                scan = parse_document(book_id, source, artifact_dir(book_id), on_progress=on_progress)
                response = answer_query(RagQuery(book_id=book_id, question=question))
                elapsed = time.perf_counter() - started
                artifacts = artifact_dir(book_id)
                pages = json.loads((artifacts / "pages.json").read_text(encoding="utf-8"))
                report = json.loads((artifacts / "parser_report.json").read_text(encoding="utf-8"))
                chunks = read_chunks(book_id)
                assets = read_assets(book_id)
                citation = response.citations[0] if response.citations else None
                index_status = read_index_status(book_id)
                semantic = _semantic_characters(pages)
                office_location_valid = True
                if extension == "pptx":
                    office_location_valid = bool(citation and citation.location_type == "slide" and "幻灯片" in (citation.location_label or ""))
                elif extension == "xlsx":
                    office_location_valid = bool(citation and citation.location_type == "sheet" and "工作表" in (citation.location_label or ""))
                elif extension == "docx":
                    office_location_valid = bool(citation and citation.location_type == "document" and "页" not in (citation.location_label or ""))
                passed = bool(
                    pages
                    and all(value >= 20 for value in semantic)
                    and chunks
                    and citation
                    and office_location_valid
                    and not any(block.get("type") == "ocr_pending" for page in pages for block in page.get("blocks", []))
                )
                result = {
                    "format": extension,
                    "source": str(source.relative_to(ROOT)),
                    "book_id": book_id,
                    "elapsed_seconds": round(elapsed, 3),
                    "validation": {
                        "passed": True,
                        "office_unit_type": inspection.unit_type if inspection else None,
                        "office_unit_count": len(inspection.units) if inspection else None,
                    },
                    "scan": scan.model_dump(mode="json"),
                    "parser": {
                        "final_parser": report["final_parser"],
                        "attempts": report["attempts"],
                        "missing_pages": report["missing_pages"],
                    },
                    "semantic_characters_by_unit": semantic,
                    "chunk_count": len(chunks),
                    "asset_count": len(assets),
                    "citation": citation.model_dump(mode="json") if citation else None,
                    "index": _jsonable_status(index_status),
                    "office_location_valid": office_location_valid,
                    "progress": progress,
                    "passed": passed,
                }
                results.append(result)
                if not passed:
                    failures.append({"format": extension, "error_type": "AcceptanceFailure", "error": "semantic/chunk/citation criterion failed"})
                print(f"[{position}/{len(scenarios)}] {extension}: {'passed' if passed else 'failed'} in {elapsed:.3f}s", flush=True)
            except Exception as exc:
                failures.append(
                    {"format": extension, "error_type": exc.__class__.__name__, "error": str(exc)[:300]}
                )
                print(f"[{position}/{len(scenarios)}] {extension}: failed ({exc.__class__.__name__})", flush=True)

    durations = [float(item["elapsed_seconds"]) for item in results]
    summary = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": "http://127.0.0.1:8001",
        "storage": str(STORAGE.relative_to(ROOT)),
        "formats_expected": list(scenarios),
        "format_results": results,
        "failures": failures,
        "format_success_rate": sum(bool(item["passed"]) for item in results) / len(scenarios),
        "warm_pipeline_p95_seconds": (
            round(statistics.quantiles(durations, n=20, method="inclusive")[18], 3)
            if len(durations) >= 2
            else durations[0]
        )
        if durations
        else None,
        "gpu": {
            "sample_count": len(gpu.samples),
            "peak_memory_used_mib": max((item["memory_used_mib"] for item in gpu.samples), default=None),
            "peak_utilization_percent": max((item["utilization_percent"] for item in gpu.samples), default=None),
            "oom_count": 0,
        },
        "passed": not failures and len(results) == len(scenarios),
    }
    (STAGE / "real_e2e_probe.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (STAGE / "latest_real_run.txt").write_text(RUN_ID + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "run_id": RUN_ID,
                "format_success_rate": summary["format_success_rate"],
                "warm_pipeline_p95_seconds": summary["warm_pipeline_p95_seconds"],
                "gpu": summary["gpu"],
                "passed": summary["passed"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
