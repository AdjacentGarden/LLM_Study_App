"""macOS Vision text OCR through a small, locally compiled Swift helper."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path


def native_helper() -> Path:
    if platform.system() != "Darwin":
        raise RuntimeError("OCR_BACKEND=apple-vision requires macOS")
    import fcntl

    configured = os.getenv("APPLE_VISION_OCR_BINARY", "").strip()
    if configured:
        binary = Path(configured)
        if not binary.is_absolute() or not binary.is_file() or not os.access(binary, os.X_OK):
            raise RuntimeError("APPLE_VISION_OCR_BINARY must name an absolute executable file")
        return binary
    from ..config import get_settings

    source = Path(__file__).with_name("apple_vision_ocr.swift")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    cache = get_settings().data_dir / "runtime" / "apple-vision"
    cache.mkdir(parents=True, exist_ok=True)
    binary = cache / f"apple-vision-ocr-{platform.machine()}-{source_hash}"
    with (cache / "compile.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not binary.is_file():
            temporary = binary.with_suffix(".building")
            result = subprocess.run(
                ["/usr/bin/xcrun", "swiftc", "-O", str(source), "-o", str(temporary)],
                capture_output=True, text=True, timeout=120, check=False,
            )
            if result.returncode:
                temporary.unlink(missing_ok=True)
                raise RuntimeError(f"Cannot compile Apple Vision OCR helper: {result.stderr[-4000:]}")
            temporary.replace(binary)
    return binary


def run_apple_vision(source: Path, raw_dir: Path) -> tuple[float, Path]:
    binary = native_helper()
    dpi = int(os.getenv("APPLE_VISION_OCR_DPI", "200"))
    if not 72 <= dpi <= 420:
        raise ValueError("APPLE_VISION_OCR_DPI must be between 72 and 420")
    result = subprocess.run(
        [str(binary), "--source", str(source.resolve()), "--output", str(raw_dir.resolve()),
         "--dpi", str(dpi)],
        capture_output=True, text=True, timeout=4 * 60 * 60, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Apple Vision OCR failed: {result.stderr[-4000:]}")
    metadata = json.loads((raw_dir / "fast_ocr_metadata.json").read_text(encoding="utf-8"))
    return float(metadata["duration_seconds"]), raw_dir / "vision.log"
