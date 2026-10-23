#!/usr/bin/env python3
"""Manage one resident PP-OCRv5 detector/server recognizer on a loopback port.

This fast mode emits text lines and evidence-backed heading hints. It does not
perform VLM layout analysis, table reconstruction, or specialist formula OCR.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import queue
import re
import signal
import socket
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(os.getenv("RUNTIME_ROOT", "/data1/zhenghang/adaptive-book-ocr")).resolve()
APP = Path(os.getenv("APP_ROOT", str(ROOT / "app"))).resolve()
PORT = int(os.getenv("FAST_OCR_PORT", "18201"))
PID_FILE = ROOT / "run/fast-ocr.pid"
LOG_FILE = ROOT / "logs/fast-ocr.log"
SCRIPT = Path(__file__).resolve()
PYTHON = "/home/zhenghang/download/enter/bin/python"
CAPABILITIES = {
    "engine": "ppocr-v5-det-and-server-rec-pytorch",
    "resident_gpu": True,
    "render_dpi": 200,
    "det_max_side": 1280,
    "recognition_batch_size": 32,
    "render_gpu_overlap": True,
    "render_queue_max_pages": 2,
    "precision": "float16",
    "layout_detection": False,
    "table_structure": False,
    "formula_specialist": False,
    "reading_order": "detector line order; columns are not classified",
    "reading_order_quality_verified": False,
    "heading_inference": "exact TOC-title match plus top-of-page enlarged OCR boxes",
}
_CHAPTER = re.compile(r"^第\s*([一二三四五六七八九十百千万零〇两0-9]+)\s*章\s*(.+)$")
logger = logging.getLogger(__name__)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return path != parent
    except ValueError:
        return False


def validated_paths(source_value: object, output_value: object) -> tuple[Path, Path]:
    """Restrict PDF reads and new artifact writes to dedicated runtime trees."""
    if not isinstance(source_value, str) or not isinstance(output_value, str):
        raise TypeError("source and output must be absolute path strings")
    source_input, output_input = Path(source_value), Path(output_value)
    if not source_input.is_absolute() or not output_input.is_absolute():
        raise ValueError("source and output must be absolute paths")
    source = source_input.resolve(strict=True)
    output = output_input.resolve()
    if not any(_inside(source, ROOT / name) for name in ("input", "data", "benchmarks")):
        raise ValueError("source is outside the runtime PDF trees")
    if not any(_inside(output, ROOT / name) for name in ("data", "benchmarks")):
        raise ValueError("output is outside the runtime artifact trees")
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError("source must be a PDF file")
    with source.open("rb") as stream:
        if b"%PDF-" not in stream.read(1024):
            raise ValueError("source does not have a PDF header")
    if output.exists():
        raise ValueError("output must be a fresh, nonexistent artifact directory")
    if _inside(source, output):
        raise ValueError("output must not contain source")
    return source, output


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def infer_chapter_headings(blocks: list[dict]) -> list[dict]:
    """Recover split display headings using only OCR text, geometry, and TOC evidence."""
    by_page: dict[int, list[dict]] = {}
    for block in blocks:
        by_page.setdefault(block["page_idx"], []).append(block)
    toc: dict[str, list[dict]] = {}
    toc_pages: set[int] = set()
    for number, lines in by_page.items():
        candidates = []
        for line in lines:
            text = re.sub(r"(?:[.·…]{2,}|\s+)\d+\s*$", "", line["text"].strip())
            match = _CHAPTER.fullmatch(text)
            if match:
                ordinal, title = match.groups()
                candidates.append({"ordinal": ordinal, "title": title.strip(),
                                   "page_idx": number, "ocr_line_id": line["ocr_line_id"]})
        has_label = any(_compact(line["text"]) in {"目录", "目次"} for line in lines)
        if len(candidates) >= 3 or (has_label and candidates):
            toc_pages.add(number)
            for candidate in candidates:
                toc.setdefault(_compact(candidate["title"]), []).append(candidate)
    result: list[dict] = []
    for number, lines in by_page.items():
        inferred = None
        heights = [line["bbox"][3] - line["bbox"][1] for line in lines
                   if len(line["text"].strip()) >= 12 and 150 < line["bbox"][1] < 930]
        if heights and number not in toc_pages:
            body_height = statistics.median(heights)
            large = [line for line in lines if line["bbox"][1] < 260
                     and line["bbox"][3] - line["bbox"][1] >= 1.7 * body_height]
            joined = "".join(_compact(line["text"]) for line in sorted(large, key=lambda x: x["bbox"][0]))
            if "第" in joined and "章" in joined:
                matches = [(title, sources) for title, sources in toc.items() if title and title in joined]
                if len(matches) == 1 and len({x["ordinal"] for x in matches[0][1]}) == 1:
                    _, sources = matches[0]
                    reference = sources[0]
                    inferred = {
                        "type": "title", "text": "第" + reference["ordinal"] + "章 " + reference["title"],
                        "page_idx": number, "text_level": 1,
                        "bbox": [min(x["bbox"][0] for x in large), min(x["bbox"][1] for x in large),
                                 max(x["bbox"][2] for x in large), max(x["bbox"][3] for x in large)],
                        "ocr_backend": CAPABILITIES["engine"], "inferred": True,
                        "heading_inference_source": "toc_title_exact_match_and_large_font",
                        "source_ocr_line_ids": [x["ocr_line_id"] for x in large],
                        "toc_evidence": sources, "body_line_height": body_height,
                    }
        if inferred is not None:
            result.append(inferred)
        # Preserve original recognitions and geometry, including low-confidence
        # fragments. An inferred heading is separately labelled, never substituted.
        result.extend(lines)
    return result


class FastOCREngine:
    def __init__(self) -> None:
        started = time.monotonic()
        import cv2
        import numpy as np
        import pypdfium2 as pdfium
        import torch
        from mineru.model.ocr.pytorch_paddle import PytorchPaddleOCR

        self.cv2, self.np, self.pdfium, self.torch = cv2, np, pdfium, torch
        cv2.setNumThreads(2)
        torch.set_num_threads(4)
        if not torch.cuda.is_available():
            raise RuntimeError("Fast OCR requires CUDA; CPU fallback is disabled")
        self.model = PytorchPaddleOCR(lang="ch_server", enable_merge_det_boxes=False,
                                     det_limit_side_len=1280, det_limit_type="max",
                                     drop_score=0.0, show_log=False)
        self.model.text_recognizer.rec_batch_num = 32
        torch.cuda.synchronize()
        self.devices = {"detector": str(next(self.model.text_detector.net.parameters()).device),
                        "recognizer": str(next(self.model.text_recognizer.net.parameters()).device)}
        if not all(device.startswith("cuda") for device in self.devices.values()):
            raise RuntimeError("Both fast OCR models must be on GPU")
        # Warm kernels using generated pixels, retaining no PDF text or result.
        image = np.full((2400, 1700, 3), 255, dtype=np.uint8)
        for index in range(36):
            cv2.putText(image, "Resident OCR kernel warmup 0123456789", (70, 80 + index * 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
        self.predict(image)
        self.startup_seconds = time.monotonic() - started
        self.completed_jobs = 0
        self.lock = threading.Lock()

    def predict(self, image):
        torch = self.torch
        with torch.inference_mode(), torch.autocast(device_type="cuda", dtype=torch.float16):
            boxes, records = self.model(image)
        torch.cuda.synchronize()
        return boxes or [], records or []

    def recognize(self, source_value: object, output_value: object) -> dict:
        source, output = validated_paths(source_value, output_value)
        started = time.monotonic()
        output.mkdir(parents=True, exist_ok=False)
        blocks, page_rows = [], []
        render_seconds = ocr_seconds = 0.0
        rendered: queue.Queue = queue.Queue(maxsize=2)
        stop_rendering = threading.Event()

        def publish(value: tuple) -> bool:
            while not stop_rendering.is_set():
                try:
                    rendered.put(value, timeout=0.2)
                    return True
                except queue.Full:
                    pass
            return False

        def render_pages() -> None:
            # Every PDFium object belongs to this single producer thread. Only
            # owned BGR pixels cross into the consumer; queue size bounds RAM.
            document = None
            try:
                document = self.pdfium.PdfDocument(source)
                if not 1 <= len(document) <= 5000:
                    raise ValueError("PDF page count must be between 1 and 5000")
                if not publish(("count", len(document))):
                    return
                for index in range(len(document)):
                    if stop_rendering.is_set():
                        break
                    tick = time.monotonic()
                    page = document[index]
                    bitmap = image_pil = None
                    try:
                        bitmap = page.render(scale=200 / 72)
                        image_pil = bitmap.to_pil().convert("RGB")
                        pixels = self.np.array(image_pil, copy=True)
                        image = self.cv2.cvtColor(pixels, self.cv2.COLOR_RGB2BGR)
                    finally:
                        if image_pil is not None:
                            image_pil.close()
                        if bitmap is not None:
                            bitmap.close()
                        page.close()
                    if not publish(("page", index, image, time.monotonic() - tick)):
                        break
            except Exception as error:  # noqa: BLE001 - relay native renderer failures to the consumer
                publish(("error", error))
            finally:
                try:
                    if document is not None:
                        document.close()
                except Exception as error:  # noqa: BLE001 - closing must not strand the consumer
                    publish(("error", error))
                finally:
                    publish(("end",))

        producer = threading.Thread(target=render_pages, name="fast-ocr-pdf-render", daemon=True)
        producer.start()
        expected_pages = None
        try:
            with (output / "page-progress.jsonl").open("w", encoding="utf-8") as progress:
                while True:
                    item = rendered.get()
                    if item[0] == "error":
                        raise item[1]
                    if item[0] == "end":
                        break
                    if item[0] == "count":
                        expected_pages = item[1]
                        continue
                    _, index, image, rendering = item
                    render_seconds += rendering
                    height, width = image.shape[:2]
                    tick = time.monotonic()
                    boxes, records = self.predict(image)
                    inference = time.monotonic() - tick
                    ocr_seconds += inference
                    if len(boxes) != len(records):
                        raise RuntimeError("OCR detector/recognizer returned inconsistent lines")
                    lines = []
                    for line_number, (box, record) in enumerate(zip(boxes, records)):
                        text, confidence = record
                        polygon = self.np.array(box, dtype=float)
                        xs, ys = polygon[:, 0], polygon[:, 1]
                        lines.append({
                            "type": "text", "text": str(text), "page_idx": index,
                            "bbox": [float(xs.min()) / width * 1000, float(ys.min()) / height * 1000,
                                     float(xs.max()) / width * 1000, float(ys.max()) / height * 1000],
                            "polygon_pixels": polygon.tolist(), "confidence": float(confidence),
                            "render_width": width, "render_height": height,
                            "ocr_backend": CAPABILITIES["engine"], "semantic_role": "unclassified_text_line",
                            "ocr_line_id": "p" + str(index + 1) + "l" + str(line_number),
                        })
                    blocks.extend(lines)
                    row = {"page": index + 1, "render_seconds": rendering, "ocr_seconds": inference,
                           "lines": len(lines), "characters": sum(len(x["text"]) for x in lines),
                           "text": "\n".join(x["text"] for x in lines)}
                    page_rows.append(row)
                    progress.write(json.dumps({k: v for k, v in row.items() if k != "text"}) + "\n")
                    progress.flush()
        finally:
            stop_rendering.set()
            producer.join()
        if expected_pages != len(page_rows):
            raise RuntimeError("Fast OCR did not process every PDF page")
        blocks = infer_chapter_headings(blocks)
        content_list = output / "paddle_content_list.json"
        content_list.write_text(json.dumps(blocks, ensure_ascii=False), encoding="utf-8")
        markdown = output / "paddle.md"
        markdown.write_text("\n\n".join("<!-- PDF page " + str(row["page"]) + " -->\n" + row["text"]
                                         for row in page_rows) + "\n", encoding="utf-8")
        log_path = output / "paddle.log"
        log_path.write_text("Resident PP-OCRv5 GPU det+rec; no VLM/table/formula reconstruction.\n", encoding="utf-8")
        result = {"status": "complete", "pid": os.getpid(), "pages": len(page_rows),
                  "content_list": str(content_list), "markdown": str(markdown), "log_path": str(log_path),
                  "render_seconds": render_seconds, "ocr_seconds": ocr_seconds,
                  "duration_seconds": time.monotonic() - started, "capabilities": CAPABILITIES,
                  "inferred_headings": sum(bool(x.get("inferred")) for x in blocks),
                  "empty_pages": [x["page"] for x in page_rows if not x["text"].strip()]}
        (output / "fast_ocr_metadata.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.completed_jobs += 1
        logger.info("OCR completed: pages=%d seconds=%.3f", len(page_rows), result["duration_seconds"])
        return result


def serve_worker() -> None:
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = FastOCREngine()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status: int, value: dict) -> None:
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path != "/health":
                self.reply(404, {"error": "not found"})
                return
            self.reply(200, {"status": "healthy", "pid": os.getpid(), "max_concurrent_requests": 1,
                             "busy": engine.lock.locked(), "completed_jobs": engine.completed_jobs,
                             "devices": engine.devices, "startup_seconds": engine.startup_seconds,
                             "capabilities": CAPABILITIES})

        def do_POST(self) -> None:
            if self.path != "/ocr":
                self.reply(404, {"error": "not found"})
                return
            if self.headers.get("Origin") or self.headers.get_content_type() != "application/json":
                self.reply(400, {"error": "application/json without browser Origin is required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("request body must contain 1..4096 bytes")
                self.connection.settimeout(10)
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or set(payload) != {"source", "output"}:
                    raise ValueError("only source and output fields are allowed")
                validated_paths(payload["source"], payload["output"])
            except (TypeError, ValueError, OSError) as error:
                self.reply(400, {"error": str(error)})
                return
            if not engine.lock.acquire(blocking=False):
                self.reply(409, {"error": "fast OCR is busy; no second GPU task was started"})
                return
            try:
                result = engine.recognize(payload["source"], payload["output"])
                self.reply(200, result)
            except Exception:
                logger.exception("Fast OCR request failed")
                self.reply(500, {"error": "fast OCR failed; inspect fast-ocr.log"})
            finally:
                engine.lock.release()

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    logger.info("Resident GPU OCR ready on 127.0.0.1:%d pid=%d", PORT, os.getpid())
    server.serve_forever()


def profile_environment() -> dict[str, str]:
    raw = subprocess.check_output([
        "bash", "-c", 'set -ea; source "$1"; if [[ -r "$2" ]]; then source "$2"; fi; env -0',
        "fast-ocr-profile", str(ROOT / "config/gpu.env"), str(ROOT / "config/ingestion-performance.env"),
    ])
    environment = {}
    for item in raw.split(b"\0"):
        if b"=" in item:
            key, value = item.split(b"=", 1)
            name = os.fsdecode(key)
            if not any(marker in name.upper() for marker in ("API_KEY", "PASSWORD", "SECRET", "TOKEN")):
                environment[name] = os.fsdecode(value)
    environment.update(
        RUNTIME_ROOT=str(ROOT), APP_ROOT=str(APP), FAST_OCR_PORT=str(PORT),
        PYTHONPATH=":".join(str(p) for p in (APP / "backend/src", ROOT / "runtime-patches/lib/python3.12/site-packages", ROOT / "runtime/lib/python3.12/site-packages")),
        MINERU_MODEL_SOURCE="local", MINERU_TOOLS_CONFIG_JSON=str(ROOT / "config/mineru.json"),
        ORT_DISABLE_TELEMETRY="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="4", PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True",
    )
    if not environment.get("CUDA_VISIBLE_DEVICES") or environment.get("MINERU_DEVICE_MODE") != "cuda":
        raise RuntimeError("Fast OCR requires the existing CUDA GPU profile")
    return environment


def read_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text().strip())
        arguments = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
        if pid > 1 and os.fsencode(SCRIPT) in arguments and b"worker" in arguments and str(PORT).encode() in arguments:
            return pid
    except (OSError, ValueError):
        pass
    return None


def health() -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:" + str(PORT) + "/health", timeout=2) as response:
        value = json.load(response)
    if value.get("status") != "healthy" or value.get("max_concurrent_requests") != 1:
        raise RuntimeError("Unexpected fast OCR health response")
    return value


def start() -> None:
    pid = read_pid()
    if pid is not None:
        state = health()
        if state.get("pid") != pid:
            raise RuntimeError("Fast OCR PID and HTTP listener disagree")
        print(json.dumps({"running": True, "pid": pid, "health": state}))
        return
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            raise RuntimeError("Fast OCR port is occupied without our verified PID")
    started = time.monotonic()
    environment = profile_environment()
    with LOG_FILE.open("a") as stream:
        process = subprocess.Popen([PYTHON, str(SCRIPT), "worker", "--port", str(PORT)], env=environment,
                                   stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    PID_FILE.write_text(str(process.pid) + "\n")
    while time.monotonic() - started < 120:
        if process.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            raise RuntimeError("Fast OCR exited during startup; inspect " + str(LOG_FILE))
        try:
            state = health()
            if state.get("pid") == process.pid and read_pid() == process.pid:
                print(json.dumps({"running": True, "pid": process.pid, "health": state}))
                return
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
            logger.debug("Fast OCR is not healthy yet: %s", type(error).__name__)
        time.sleep(0.25)
    raise TimeoutError("Fast OCR is still starting; inspect its log and status")


def stop() -> None:
    if not PID_FILE.exists():
        print(json.dumps({"running": False}))
        return
    pid = read_pid()
    if pid is None:
        raise RuntimeError("Refusing to stop an unverified PID from " + str(PID_FILE))
    os.killpg(pid, signal.SIGTERM)
    for _ in range(200):
        if read_pid() is None:
            PID_FILE.unlink(missing_ok=True)
            print(json.dumps({"running": False, "stopped_pid": pid}))
            return
        time.sleep(0.1)
    os.killpg(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    print(json.dumps({"running": False, "stopped_pid": pid, "forced": True}))


def main() -> None:
    global PORT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "restart", "status", "worker"))
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    PORT = args.port
    if not 1 <= PORT <= 65535:
        raise ValueError("FAST_OCR_PORT must be a valid TCP port")
    for directory in (ROOT / "run", ROOT / "logs"):
        directory.mkdir(parents=True, exist_ok=True)
    if args.action == "worker":
        serve_worker()
        return
    with (ROOT / "run/fast-ocr.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action in ("stop", "restart"):
            stop()
        if args.action in ("start", "restart"):
            start()
        if args.action == "status":
            pid = read_pid()
            print(json.dumps({"running": pid is not None, "pid": pid,
                              "health": health() if pid is not None else None}))


if __name__ == "__main__":
    main()
