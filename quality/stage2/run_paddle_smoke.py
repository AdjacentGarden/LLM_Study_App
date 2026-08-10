from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("BOOKCOURSE_OCR_PROVIDER", "paddleocr")
os.environ.setdefault("BOOKCOURSE_OCR_DEVICE", "cpu")

from app.core.config import get_settings  # noqa: E402
from app.document.ocr import _paddle_adapter, get_ocr_adapter  # noqa: E402


def main() -> int:
    get_settings.cache_clear()
    _paddle_adapter.cache_clear()
    image = BACKEND / "paddle_probe_2.png"
    started = time.perf_counter()
    adapter = get_ocr_adapter()
    initialized = time.perf_counter()
    blocks, warnings = adapter.recognize(image, 1)
    finished = time.perf_counter()
    import paddle
    import paddleocr

    payload = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "provider": adapter.name,
        "device": get_settings().ocr_device,
        "paddle_version": paddle.__version__,
        "paddleocr_version": getattr(paddleocr, "__version__", "unknown"),
        "paddle_cuda_compiled": bool(paddle.device.is_compiled_with_cuda()),
        "initialization_seconds": round(initialized - started, 3),
        "recognition_seconds": round(finished - initialized, 3),
        "block_count": len(blocks),
        "semantic_characters": sum(len(block.text.strip()) for block in blocks),
        "mean_confidence": round(
            sum(block.confidence or 0.0 for block in blocks) / len(blocks), 4
        )
        if blocks
        else None,
        "recognized_text": [block.text for block in blocks],
        "warnings": [warning.code for warning in warnings],
        "passed": adapter.name == "paddleocr" and bool(blocks),
    }
    output = Path(__file__).with_name("paddle_smoke.json")
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

