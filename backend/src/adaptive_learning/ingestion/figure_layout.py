"""Optional isolated PP-DocLayout figure extraction alongside unchanged text OCR.

The official ONNX model is local and checksum-pinned; no model downloads or
Paddle/Torch environment changes occur here. Whole-page rasters live only in
memory. The approved precision-first filter runs BEFORE writing any crop.
"""
from __future__ import annotations

import argparse
import hashlib
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections.abc import Callable
from typing import Any

MODEL_REVISION = "feb74619326f634e0e883218598096a3733ad9f7"
MODEL_SHA256 = "77afb2caa74dd13240d087d2eced91d7fcd2caebd16006a0a66162fc8707ff0e"
CONFIG_SHA256 = "d60f782a16f96afb27e8280399899a94c3e9ffc694ffb2f913ea00af1c522f1e"
FILTER_VERSION = "precision-first-geometry-caption-heading-v2"
FILTER_SHA256 = "1010401693facadd10c6afc954bc861704055a84dd9f7010d0d913a780d1fbd2"


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@lru_cache(maxsize=8)
def _checked_model(path: str, mtime: int, size: int, expected: str) -> bool:
    return file_digest(Path(path)) == expected


def layout_configuration() -> tuple[Path, Path]:
    interpreter = os.getenv("FIGURE_LAYOUT_PYTHON", "").strip()
    model = os.getenv("FIGURE_LAYOUT_MODEL_DIR", "").strip()
    if not interpreter or not model:
        raise RuntimeError("layout_not_configured")
    python = Path(interpreter)
    directory = Path(model)
    if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
        raise RuntimeError("layout_interpreter_unavailable")
    for filename, expected in (("inference.onnx", MODEL_SHA256), ("inference.yml", CONFIG_SHA256)):
        path = directory / filename
        try:
            stat = path.stat()
            if not _checked_model(str(path.resolve()), stat.st_mtime_ns, stat.st_size, expected):
                raise RuntimeError("layout_model_checksum_mismatch")
        except OSError as error:
            raise RuntimeError("layout_model_unavailable") from error
    return python, directory.resolve()


def layout_capabilities() -> dict[str, Any]:
    value = {"engine": "PP-DocLayout_plus-L", "model_revision": MODEL_REVISION,
             "model_sha256": MODEL_SHA256, "filter_version": FILTER_VERSION, "filter_sha256": FILTER_SHA256,
             "threshold": 0.5, "provider": "CPUExecutionProvider", "available": False}
    try:
        python, _ = layout_configuration()
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        check = subprocess.run([str(python), "-c",
            "import onnxruntime,numpy,cv2,pymupdf,PIL;print(','.join(onnxruntime.get_available_providers()))"],
            env=environment, capture_output=True, timeout=10, check=False, text=True)
        if check.returncode:
            raise RuntimeError("layout_dependencies_unavailable")
        providers = set(check.stdout.strip().split(","))
        value["provider"] = ("CUDAExecutionProvider" if "CUDAExecutionProvider" in providers
                             else "CPUExecutionProvider")
        value.update(available=True, status="ready")
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        value.update(status="unavailable", reason=str(error) if isinstance(error, RuntimeError) else "layout_runtime_unavailable")
    return value


def _service_url() -> str:
    return os.getenv("FIGURE_LAYOUT_SERVICE_URL", "").strip().rstrip("/")


def _extract_via_service(source: Path, normalized: Path, model: Path,
                         heartbeat: Callable[[float, str], bool] | None) -> dict[str, Any] | None:
    url = _service_url()
    if not url:
        return None
    import urllib.request

    timeout = float(os.getenv("FIGURE_LAYOUT_TIMEOUT_SECONDS", "3600"))
    if not math.isfinite(timeout) or not 1 <= timeout <= 14400:
        raise RuntimeError("layout_timeout_configuration_invalid")
    payload = json.dumps({"source": str(source.resolve()), "output": str(normalized.resolve()),
                          "model_dir": str(model.resolve())}).encode()
    request = urllib.request.Request(url + "/layout", data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
    result: dict[str, Any] = {}
    failure: list[BaseException] = []

    def call() -> None:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result["value"] = json.loads(response.read())
        except BaseException as error:  # pragma: no cover - transport failure
            failure.append(error)

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    deadline = time.monotonic() + timeout
    while worker.is_alive():
        if heartbeat is not None and not heartbeat(0.94, "正在整理教材插图"):
            raise RuntimeError("layout_cancelled")
        if time.monotonic() >= deadline:
            raise RuntimeError("layout_timeout")
        worker.join(timeout=5)
    if failure:
        raise RuntimeError("layout_service_failed") from failure[0]
    value = result.get("value")
    if not isinstance(value, dict) or value.get("status") not in {"completed", "partial"}:
        raise RuntimeError("layout_service_failed")
    saved = json.loads((normalized / "figure_manifest.json").read_text())
    if saved.get("source_sha256") != file_digest(source):
        raise RuntimeError("layout_source_mismatch")
    return saved


def extract_figures(source: Path, normalized: Path,
                    heartbeat: Callable[[float, str], bool] | None = None) -> dict[str, Any]:
    """Best effort for a whole book; honest failure is never successful empty data."""
    destination = normalized / "figure_manifest.json"
    fingerprint = file_digest(source)
    try:
        python, model = layout_configuration()
    except RuntimeError as error:
        result = {"schema_version": 1, "status": "unavailable", "reason": str(error),
                  "source_sha256": fingerprint, "model_revision": MODEL_REVISION,
                  "model_sha256": MODEL_SHA256, "filter_version": FILTER_VERSION, "filter_sha256": FILTER_SHA256, "figures": []}
        _write_json(destination, result)
        return result
    try:
        saved = json.loads(destination.read_text())
        if (saved.get("status") in {"completed", "partial"} and saved.get("source_sha256") == fingerprint
                and saved.get("model_sha256") == MODEL_SHA256 and saved.get("filter_version") == FILTER_VERSION and saved.get("filter_sha256") == FILTER_SHA256
                and all((normalized / item["file"]).is_file() for item in saved.get("figures", []))):
            return saved
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        service_result = _extract_via_service(source, normalized, model, heartbeat)
        if service_result is not None:
            return service_result
    except (OSError, ValueError, RuntimeError):
        # The resident service is an optimization; the isolated subprocess is the fallback.
        pass
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment.update(OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2")
    try:
        timeout = float(os.getenv("FIGURE_LAYOUT_TIMEOUT_SECONDS", "3600"))
        if not math.isfinite(timeout) or not 1 <= timeout <= 14400:
            raise RuntimeError("layout_timeout_configuration_invalid")
        process = subprocess.Popen([str(python), str(Path(__file__).resolve()), "--source", str(source.resolve()),
            "--output", str(normalized.resolve()), "--model-dir", str(model)],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + timeout
        try:
            while True:
                if heartbeat is not None and not heartbeat(0.94, "正在整理教材插图"):
                    raise RuntimeError("layout_cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("layout_timeout")
                try:
                    _, stderr = process.communicate(timeout=min(5, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode:
                # Internal-only bounded diagnostics; not part of public material allowlist.
                _write_json(normalized / "figure_runtime_error.json", {
                    "reason": "layout_inference_failed", "exit_code": process.returncode,
                    "stderr_tail": stderr[-8000:]})
                raise RuntimeError("layout_inference_failed")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        result = json.loads(destination.read_text())
        if result.get("source_sha256") != fingerprint:
            raise RuntimeError("layout_source_mismatch")
        return result
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as error:
        result = {"schema_version": 1, "status": "unavailable", "reason":
                  str(error) if isinstance(error, RuntimeError) else "layout_runtime_failed",
                  "source_sha256": fingerprint, "model_revision": MODEL_REVISION,
                  "model_sha256": MODEL_SHA256, "filter_version": FILTER_VERSION, "filter_sha256": FILTER_SHA256, "figures": []}
        _write_json(destination, result)
        return result


def figure_identity(source_sha256: str, page_number: int, bbox: list[float], image_sha256: str) -> str:
    # Semantic location identity is separate from deduplicated image bytes.
    payload = json.dumps([source_sha256, page_number, bbox, image_sha256], separators=(",", ":"), allow_nan=False)
    return "figure_" + hashlib.sha256(payload.encode()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _labels(config: Path) -> list[str]:
    raw = config.read_text()
    if hashlib.sha256(config.read_bytes()).hexdigest() != CONFIG_SHA256:
        raise ValueError("layout_config_checksum_mismatch")
    labels = [line[2:].strip() for line in raw.split("label_list:\n", 1)[1].split("Hpi:", 1)[0].splitlines()
              if line.startswith("- ")]
    if len(labels) != 20 or labels[1] != "image" or labels[16] != "chart":
        raise ValueError("layout_label_contract_mismatch")
    return labels


def _nms(rows: list[list[float]]) -> list[list[float]]:
    ordered = sorted(rows, key=lambda row: row[1], reverse=True)
    kept = []
    while ordered:
        current = ordered.pop(0)
        kept.append(current)
        remaining = []
        a = current[2:]
        for row in ordered:
            b = row[2:]
            overlap = max(0, min(a[2], b[2])-max(a[0], b[0])+1)*max(0, min(a[3], b[3])-max(a[1], b[1])+1)
            aa = max(0, a[2]-a[0]+1)*max(0, a[3]-a[1]+1)
            bb = max(0, b[2]-b[0]+1)*max(0, b[3]-b[1]+1)
            if overlap/max(aa+bb-overlap, 1e-12) < (0.6 if int(row[0]) == int(current[0]) else 0.98):
                remaining.append(row)
        ordered = remaining
    return kept


_ENGINE_CACHE: dict[str, dict[str, Any]] = {}
_ENGINE_LOCK = threading.Lock()
_SERVICE_LOCK = threading.Lock()


def _engine(model_dir: Path) -> dict[str, Any]:
    import onnxruntime as ort

    key = str(model_dir.resolve())
    with _ENGINE_LOCK:
        cached = _ENGINE_CACHE.get(key)
        if cached is not None:
            return cached
        labels = _labels(model_dir / "inference.yml")
        if file_digest(model_dir / "inference.onnx") != MODEL_SHA256:
            raise ValueError("layout_model_checksum_mismatch")
        options = ort.SessionOptions()
        force_cpu = os.getenv("FIGURE_LAYOUT_FORCE_CPU", "").strip().lower() in {"1", "true", "yes", "on"}
        available_providers = set(ort.get_available_providers())
        providers = ["CPUExecutionProvider"]
        if not force_cpu and "CUDAExecutionProvider" in available_providers:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        if providers[0] == "CUDAExecutionProvider":
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
        else:
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
        session = ort.InferenceSession(str(model_dir / "inference.onnx"), sess_options=options, providers=providers)
        if {item.name for item in session.get_inputs()} != {"image", "im_shape", "scale_factor"}:
            raise ValueError("layout_input_contract_mismatch")
        if any(item.type != "tensor(float)" for item in session.get_inputs()):
            raise ValueError("layout_input_dtype_mismatch")
        cached = {"session": session, "labels": labels, "provider": session.get_providers()[0],
                  "model_dir": key}
        _ENGINE_CACHE[key] = cached
        return cached


def run_layout(source: Path, output: Path, model_dir: Path) -> dict[str, Any]:
    import numpy as np
    import cv2
    import pymupdf
    from PIL import Image
    from adaptive_learning.ingestion.figure_filter import filter_manifest, pdf_text_context, checked_box, area

    if file_digest(Path(__file__).with_name("figure_filter.py")) != FILTER_SHA256:
        raise ValueError("approved_figure_filter_checksum_mismatch")
    engine = _engine(model_dir)
    labels = engine["labels"]
    session = engine["session"]
    source_sha256 = file_digest(source)
    baseline: dict[str, Any] = {"threshold": 0.5, "provider": session.get_providers()[0], "pages": []}
    failures: list[dict[str, Any]] = []
    # First pass retains only detections. No crops are saved until filtering all pages.
    with pymupdf.open(source) as document:
        for page_number, page in enumerate(document, start=1):
            entry: dict[str, Any] = {"page_number": page_number, "width": 1, "height": 1, "boxes": [], "figures": []}
            try:
                pix = page.get_pixmap(matrix=pymupdf.Matrix(200/72, 200/72), alpha=False)
                rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
                resized = cv2.resize(rgb, (800, 800), interpolation=cv2.INTER_CUBIC)
                feed = {"image": np.ascontiguousarray((resized.astype(np.float32)/np.float32(255)).transpose(2, 0, 1)[None]),
                        "im_shape": np.array([[800, 800]], dtype=np.float32),
                        "scale_factor": np.array([[800/pix.height, 800/pix.width]], dtype=np.float32)}
                predictions = session.run(None, feed)
                boxes = [value for value in predictions if value.ndim == 2 and value.shape[1] == 6]
                if len(boxes) != 1 or not np.isfinite(boxes[0]).all():
                    raise ValueError("layout_output_contract_mismatch")
                rows = _nms([row.tolist() for row in boxes[0] if 0 <= int(row[0]) < len(labels) and row[1] > 0.2])
                entry.update(width=pix.width, height=pix.height)
                candidates = []
                exclusions = []
                for index, row in enumerate(rows):
                    bounds = checked_box(row[2:])
                    if bounds is None:
                        continue
                    bounds = [max(0, math.floor(bounds[0])), max(0, math.floor(bounds[1])),
                              min(pix.width, math.ceil(bounds[2])), min(pix.height, math.ceil(bounds[3]))]
                    if area(bounds) <= 0:
                        continue
                    label = labels[int(row[0])]
                    entry["boxes"].append({"label": label, "score": row[1], "coordinate": row[2:]})
                    if row[1] < 0.5:
                        continue
                    if label in {"table", "formula"}:
                        exclusions.append(bounds)
                    if label in {"image", "chart"}:
                        candidates.append((index, row, bounds))
                for index, row, bounds in candidates:
                    if area(bounds)/(pix.width*pix.height) >= 0.9:
                        continue
                    from adaptive_learning.ingestion.figure_filter import overlap_1d
                    def overlap(a: list[float], b: list[float]) -> float:
                        return overlap_1d(a[0], a[2], b[0], b[2])*overlap_1d(a[1], a[3], b[1], b[3])
                    if any(overlap(bounds, other)/area(bounds) >= 0.5 for other in exclusions):
                        continue
                    if any(other_index != index and area(other) > area(bounds) and overlap(bounds, other)/area(bounds) >= 0.95
                           for other_index, _, other in candidates):
                        continue
                    entry["figures"].append({"file": f"page-{page_number}-candidate-{index}", "label": labels[int(row[0])],
                                             "score": row[1], "bbox_pixels": bounds})
            except Exception as error:
                failures.append({"page_number": page_number, "reason": type(error).__name__})
            baseline["pages"].append(entry)
        try:
            text_context = pdf_text_context(source)
        except Exception:
            text_context = {}  # Text-layer absence does not turn into invented captions.
        filtered, audit = filter_manifest(baseline, text_context)
        figures = []
        figure_dir = output / "figures"
        figure_dir.mkdir(parents=True, exist_ok=True)
        for entry in filtered["pages"]:
            if not entry["figures"]:
                continue
            page = document[entry["page_number"]-1]
            try:
                pix = page.get_pixmap(matrix=pymupdf.Matrix(200/72, 200/72), alpha=False)
                rgb = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            except Exception as error:
                failures.append({"page_number": entry["page_number"], "reason": type(error).__name__})
                continue
            for candidate in entry["figures"]:
                try:
                    import io
                    content = io.BytesIO()
                    rgb.crop(candidate["bbox_pixels"]).save(content, format="PNG")
                    data = content.getvalue()
                    digest = hashlib.sha256(data).hexdigest()
                    path = figure_dir / (digest + ".png")
                    if not path.exists():
                        path.write_bytes(data)
                    x0, y0, x1, y1 = candidate["bbox_pixels"]
                    pdf_bbox = [x0*page.rect.width/pix.width, y0*page.rect.height/pix.height,
                                x1*page.rect.width/pix.width, y1*page.rect.height/pix.height]
                    figures.append({"asset_id": figure_identity(source_sha256, entry["page_number"], pdf_bbox, digest),
                        "file_asset_id": digest, "file": "figures/"+path.name,
                        "page_number": entry["page_number"], "bbox": pdf_bbox,
                        "caption": "", "label": candidate["label"], "score": candidate["score"],
                        "sha256": digest, "bytes": len(data), "media_type": "image/png"})
                except Exception as error:
                    failures.append({"page_number": entry["page_number"], "candidate": candidate["file"], "reason": type(error).__name__})
    result = {"schema_version": 1, "status": "partial" if failures else "completed", "source_sha256": file_digest(source),
              "model_revision": MODEL_REVISION, "model_sha256": MODEL_SHA256, "filter_version": FILTER_VERSION, "filter_sha256": FILTER_SHA256,
              "provider": session.get_providers()[0],
              "figures": figures, "failures": failures, "skipped_candidates": audit["skipped"]}
    if failures and len(failures) >= len(baseline["pages"]) and not figures:
        result.update(status="unavailable", reason="layout_all_pages_failed")
    _write_json(output / "figure-extraction-audit.json", audit)
    _write_json(output / "figure_manifest.json", result)
    return result


def _service_health(model_dir: Path) -> dict[str, Any]:
    engine = _engine(model_dir)
    return {"status": "ready", "provider": engine["provider"], "model_dir": str(model_dir),
            "model_sha256": MODEL_SHA256, "filter_sha256": FILTER_SHA256, "pid": os.getpid()}


def serve(host: str, port: int) -> None:
    configured = os.getenv("FIGURE_LAYOUT_MODEL_DIR", "").strip()
    if not configured:
        raise SystemExit("FIGURE_LAYOUT_MODEL_DIR is not configured")
    model_dir = Path(configured).resolve()
    engine = _engine(model_dir)
    print(json.dumps({"event": "layout_service_ready", "host": host, "port": port,
                      "provider": engine["provider"], "model_dir": str(model_dir)}), flush=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            print(json.dumps({"event": "http", "message": format % args}), flush=True)

        def _send(self, status: int, value: Any) -> None:
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] == "/health":
                self._send(200, _service_health(model_dir))
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != "/layout":
                self._send(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                source = Path(str(body["source"])).resolve()
                output = Path(str(body["output"])).resolve()
                requested_model = Path(str(body.get("model_dir", model_dir))).resolve()
                if requested_model != model_dir:
                    raise ValueError("model_dir_mismatch")
                with _SERVICE_LOCK:
                    result = run_layout(source, output, model_dir)
                self._send(200, {"status": result.get("status"), "provider": result.get("provider"),
                                 "figures": len(result.get("figures", [])),
                                 "failures": len(result.get("failures", [])),
                                 "source_sha256": result.get("source_sha256")})
            except Exception as error:
                self._send(500, {"error": type(error).__name__, "detail": str(error)[:500]})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    # Isolated interpreter loads repository code, not production dependency paths.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18202)
    arguments = parser.parse_args()
    if arguments.serve:
        serve(arguments.host, arguments.port)
    else:
        if arguments.source is None or arguments.output is None or arguments.model_dir is None:
            parser.error("--source, --output and --model-dir are required unless --serve is used")
        run_layout(arguments.source, arguments.output, arguments.model_dir)
