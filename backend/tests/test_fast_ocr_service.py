"""Exercise real PDF rendering, queueing and HTTP; recognition is an explicit test double."""

from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import numpy as np
import pytest

from adaptive_learning.ingestion.ocr_job import run_paddle_gpu, write_report


@pytest.fixture
def service(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "deployment/server/fast_ocr_service.py"
    spec = importlib.util.spec_from_file_location("tested_fast_ocr_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.delenv("FAST_OCR_SOURCE_ROOTS", raising=False)
    monkeypatch.delenv("FAST_OCR_OUTPUT_ROOTS", raising=False)
    return module


def source_pdf(tmp_path: Path, pages: int = 2) -> Path:
    import pymupdf

    source = tmp_path / "input/book.pdf"
    source.parent.mkdir(exist_ok=True)
    with pymupdf.open() as document:
        for _ in range(pages):
            page = document.new_page(width=200, height=240)
            page.insert_text((10, 30), "Real synthetic PDF pixels")
        document.save(source)
    return source


def fake_engine(service, *, failure=False):
    pdfium = pytest.importorskip("pypdfium2")
    engine = service.FastOCREngine.__new__(service.FastOCREngine)
    engine.pdfium, engine.np = pdfium, np
    engine.cv2 = SimpleNamespace(COLOR_RGB2BGR=1, cvtColor=lambda pixels, _: pixels[:, :, ::-1])
    engine.lock = threading.Lock()
    engine.completed_jobs = 0
    engine.devices = {"detector": "test double", "recognizer": "test double"}
    engine.startup_seconds = 0
    calls = 0

    def predict(image):
        nonlocal calls
        calls += 1
        assert image.shape[0] > 240 and image.shape[1] > 200
        if failure:
            raise RuntimeError("explicit recognizer failure")
        if calls == 2:
            return [], []
        return [[[10, 10], [100, 10], [100, 30], [10, 30]]], [
            ("这是测试识别器返回的教材原始文本。", 0.96)
        ]

    engine.predict = predict
    return engine


def test_real_pdf_pipeline_preserves_empty_page_and_capability_metadata(service, tmp_path):
    source = source_pdf(tmp_path)
    output = tmp_path / "data/attempt/raw"
    result = fake_engine(service).recognize(str(source), str(output))

    assert result["pages"] == 2
    assert result["page_numbers"] == [1, 2]
    assert result["empty_pages"] == [2]
    assert result["capabilities"]["table_structure"] is False
    blocks = json.loads(Path(result["content_list"]).read_text())
    assert len(blocks) == 1 and blocks[0]["confidence"] == 0.96
    assert blocks[0]["polygon_pixels"][0] == [10.0, 10.0]
    report = write_report(
        source=source,
        output_dir=output.parent / "normalized",
        raw_dir=output,
        backend="paddle-gpu",
        duration_seconds=result["duration_seconds"],
        log_path=Path(result["log_path"]),
    )
    assert report["quality_band_counts"]["missing"] == 1
    assert report["reading_order_quality_verified"] is False
    normalized = [
        json.loads(row)
        for row in (output.parent / "normalized/pages.jsonl").read_text().splitlines()
    ]
    assert normalized[0]["extraction_method"] == "paddle_gpu"
    assert normalized[1]["text"] == ""


def test_recognizer_failure_never_publishes_completed_metadata(service, tmp_path):
    source = source_pdf(tmp_path, pages=6)
    output = tmp_path / "data/failed/raw"
    engine = fake_engine(service, failure=True)
    with pytest.raises(RuntimeError, match="explicit recognizer failure"):
        engine.recognize(str(source), str(output))
    assert not (output / "fast_ocr_metadata.json").exists()
    assert engine.completed_jobs == 0
    assert not any(thread.name == "fast-ocr-pdf-render" for thread in threading.enumerate())


def test_allowed_paths_reject_escape_and_existing_output(service, tmp_path):
    source = source_pdf(tmp_path)
    allowed = tmp_path / "data/new/raw"
    assert service.validated_paths(str(source), str(allowed)) == (source, allowed)
    with pytest.raises(ValueError, match="absolute"):
        service.validated_paths("relative.pdf", str(allowed))
    with pytest.raises(ValueError, match="outside"):
        service.validated_paths(str(source), str(tmp_path / "data/../../escape"))
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data/link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        service.validated_paths(str(source), str(tmp_path / "data/link/result"))
    allowed.mkdir(parents=True)
    with pytest.raises(ValueError, match="fresh"):
        service.validated_paths(str(source), str(allowed))


def test_explicit_roots_and_port_state_are_isolated(service, tmp_path, monkeypatch):
    source = source_pdf(tmp_path)
    monkeypatch.setenv("FAST_OCR_SOURCE_ROOTS", str(source.parent))
    monkeypatch.setenv("FAST_OCR_OUTPUT_ROOTS", str(tmp_path / "results"))
    output = tmp_path / "results/run/raw"
    assert service.validated_paths(str(source), str(output)) == (source, output)
    monkeypatch.setattr(service, "PORT", 18202)
    assert service.state_file("pid").name == "fast-ocr-18202.pid"
    assert service.log_file().name == "fast-ocr-18202.log"
    monkeypatch.setenv("FAST_OCR_SOURCE_ROOTS", "relative")
    with pytest.raises(ValueError, match="absolute"):
        service.validated_paths(str(source), str(output))


def test_loopback_http_client_and_busy_request_do_not_spawn_second_job(
    service, tmp_path, monkeypatch
):
    source = source_pdf(tmp_path)
    engine = fake_engine(service)
    server = service.create_server(engine, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("FAST_OCR_API_URL", base)
    # A broken outbound proxy must never intercept the loopback OCR request.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    opener = build_opener(ProxyHandler({}))
    try:
        payload = json.dumps(
            {"source": str(source), "output": str(tmp_path / "data/busy/raw")}
        ).encode()
        request = Request(base + "/ocr", data=payload, headers={"Content-Type": "application/json"})
        engine.lock.acquire()
        with pytest.raises(HTTPError) as error:
            opener.open(request)
        assert error.value.code == 409
        assert not (tmp_path / "data/busy/raw").exists()
        engine.lock.release()
        duration, log = run_paddle_gpu(source, tmp_path / "data/success/raw")
        assert duration >= 0 and log.is_file()
        assert engine.completed_jobs == 1
        with opener.open(base + "/health") as response:
            assert json.load(response)["completed_jobs"] == 1
    finally:
        if engine.lock.locked():
            engine.lock.release()
        server.shutdown()
        server.server_close()
        thread.join()


def test_heading_inference_requires_toc_and_retains_original_lines(service):
    def line(text, page, bbox, line_id):
        return {
            "type": "text",
            "text": text,
            "page_idx": page,
            "bbox": bbox,
            "ocr_line_id": line_id,
        }

    lines = [
        line("目录", 0, [0, 0, 20, 15], "toc"),
        line("第一章 遗传基础 5", 0, [0, 30, 100, 45], "toc1"),
        line("第一章", 2, [0, 20, 60, 50], "h1"),
        line("遗传基础", 2, [70, 20, 160, 50], "h2"),
        line("这是一行具有足够长度的教材正文。", 2, [0, 200, 160, 210], "b1"),
    ]
    result = service.infer_chapter_headings(lines)
    inferred = [block for block in result if block.get("inferred")]
    assert len(inferred) == 1
    assert inferred[0]["text"] == "第一章 遗传基础"
    assert inferred[0]["source_ocr_line_ids"] == ["h1", "h2"]
    assert all(block in result for block in lines)
    assert not any(block.get("inferred") for block in service.infer_chapter_headings(lines[2:]))


@pytest.mark.parametrize(
    "headers,body",
    [
        ({"Origin": "http://127.0.0.1"}, b"{}"),
        ({"Content-Type": "text/plain"}, b"{}"),
        ({}, b"[]"),
        ({}, b"{bad"),
    ],
)
def test_http_rejects_browser_origin_and_invalid_request_before_engine_work(
    service, tmp_path, headers, body
):
    engine = fake_engine(service)
    server = service.create_server(engine, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = build_opener(ProxyHandler({}))
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/ocr",
            data=body,
            headers={"Content-Type": "application/json", **headers},
        )
        with pytest.raises(HTTPError) as error:
            opener.open(request)
        assert error.value.code == 400
        assert engine.completed_jobs == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_resident_mineru_environment_uses_current_checkout_and_per_port_output(
    tmp_path, monkeypatch
):
    path = Path(__file__).resolve().parents[2] / "deployment/server/ocr_resident_service.py"
    spec = importlib.util.spec_from_file_location("tested_ocr_resident_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "PORT", 18203)
    monkeypatch.setenv("PYTHONPATH", "/explicit/runtime-overlay")
    monkeypatch.setenv("MINERU_DEVICE_MODE", "cuda")
    environment = module.profile_environment()
    assert str(module.APP / "backend/src") in environment["PYTHONPATH"]
    assert "/explicit/runtime-overlay" in environment["PYTHONPATH"]
    assert environment["MINERU_API_OUTPUT_ROOT"] == str(tmp_path / "data/tmp/mineru-resident-18203")
    assert environment["MINERU_API_MAX_CONCURRENT_REQUESTS"] == "1"
    assert module.state_file("pid").name == "ocr-resident-18203.pid"
    assert module.url() == "http://127.0.0.1:18203"
