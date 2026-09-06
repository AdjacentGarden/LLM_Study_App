from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
STAGE = Path(__file__).resolve().parent
RUN_ID = datetime.now(timezone.utc).strftime("fallback_%Y%m%dT%H%M%SZ")
STORAGE = STAGE / "fallback_runs" / RUN_ID / "storage"
os.environ["BOOKCOURSE_STORAGE_ROOT"] = str(STORAGE)
os.environ["BOOKCOURSE_PARSER_PROVIDER"] = "mineru"
os.environ["BOOKCOURSE_MINERU_ENDPOINT"] = "http://127.0.0.1:65534"
os.environ["BOOKCOURSE_MINERU_CONNECT_TIMEOUT_SECONDS"] = "0.2"
os.environ["BOOKCOURSE_MINERU_MAX_RETRIES"] = "0"
os.environ["BOOKCOURSE_OCR_PROVIDER"] = "paddleocr"
os.environ["BOOKCOURSE_OCR_DEVICE"] = "cpu"
os.environ["BOOKCOURSE_OCR_ENABLE_MKLDNN"] = "false"
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
sys.path.insert(0, str(ROOT / "backend"))

from app.document.pipeline import parse_document  # noqa: E402
from app.services.storage import artifact_dir  # noqa: E402


SCENARIOS = {
    "native": (ROOT / "quality" / "stage0" / "fixtures" / "native_text.pdf", ["pymupdf", "pymupdf"]),
    "scanned": (ROOT / "quality" / "stage0" / "fixtures" / "scanned.pdf", ["ocr"]),
    "image": (ROOT / "quality" / "stage0" / "fixtures" / "source_diagram.png", ["ocr"]),
}


def main() -> int:
    results = []
    failures = []
    for name, (source, expected_parsers) in SCENARIOS.items():
        book_id = f"stage2_fallback_{name}_{RUN_ID[-7:-1].lower()}"
        started = time.perf_counter()
        try:
            parse_document(book_id, source, artifact_dir(book_id))
            artifacts = artifact_dir(book_id)
            pages = json.loads((artifacts / "pages.json").read_text(encoding="utf-8"))
            report = json.loads((artifacts / "parser_report.json").read_text(encoding="utf-8"))
            parsers = [page["parser"] for page in pages]
            first_attempt = report["attempts"][0]
            passed = (
                parsers == expected_parsers
                and first_attempt["parser"] == "mineru"
                and first_attempt["status"] == "failed"
                and first_attempt["error_code"] in {"mineru_unavailable", "mineru_timeout"}
                and not any(block["type"] == "ocr_pending" for page in pages for block in page["blocks"])
            )
            results.append(
                {
                    "scenario": name,
                    "book_id": book_id,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "page_parsers": parsers,
                    "first_attempt": first_attempt,
                    "ocr_providers": [page.get("ocr_provider") for page in pages],
                    "semantic_characters": [len(" ".join(page["text"].split())) for page in pages],
                    "passed": passed,
                }
            )
            if not passed:
                failures.append({"scenario": name, "error": "fallback_contract_mismatch"})
        except Exception as exc:
            failures.append({"scenario": name, "error": exc.__class__.__name__, "detail": str(exc)[:200]})
    report = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "offline_endpoint": "http://127.0.0.1:65534",
        "results": results,
        "failures": failures,
        "passed": not failures and len(results) == len(SCENARIOS),
    }
    (STAGE / "fallback_probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"results": results, "failures": failures, "passed": report["passed"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

