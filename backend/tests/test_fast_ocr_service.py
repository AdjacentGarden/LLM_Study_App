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
from PIL import Image

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


# Preserved earlier thread ownership, cancellation and path-isolation regressions.
SERVICE_PATH = Path(__file__).parents[2] / "deployment/server/fast_ocr_service.py"

@pytest.fixture
def legacy_service(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('fast_service_test', SERVICE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    for name in ('input', 'data', 'benchmarks'):
        (tmp_path / name).mkdir()
    return module

def legacy_source_pdf(legacy_service):
    source = legacy_service.ROOT / 'input/source.pdf'
    source.write_bytes(b'%PDF-1.7\nfixture\n')
    return source

def test_legacy_service_rejects_external_reads_and_writes_and_symlink_escape(legacy_service, tmp_path):
    source = legacy_source_pdf(legacy_service)
    outside = tmp_path.parent / (tmp_path.name + '-outside.pdf')
    outside.write_bytes(b'%PDF-1.7\n')
    try:
        with pytest.raises(ValueError, match='outside the runtime PDF'):
            legacy_service.validated_paths(str(outside), str(legacy_service.ROOT / 'data/new'))
        with pytest.raises(ValueError, match='outside the runtime artifact'):
            legacy_service.validated_paths(str(source), str(outside.parent / 'escape'))
        linked = legacy_service.ROOT / 'input/linked.pdf'
        linked.symlink_to(outside)
        with pytest.raises(ValueError, match='outside the runtime PDF'):
            legacy_service.validated_paths(str(linked), str(legacy_service.ROOT / 'data/new'))
        linkdir = legacy_service.ROOT / 'data/linked'
        linkdir.symlink_to(outside.parent, target_is_directory=True)
        with pytest.raises(ValueError, match='outside the runtime artifact'):
            legacy_service.validated_paths(str(source), str(linkdir / 'escape'))
    finally:
        outside.unlink(missing_ok=True)

def test_legacy_service_requires_pdf_header_and_fresh_output(legacy_service):
    source = legacy_source_pdf(legacy_service)
    output = legacy_service.ROOT / 'data/new'
    assert legacy_service.validated_paths(str(source), str(output)) == (source, output)
    output.mkdir()
    with pytest.raises(ValueError, match='fresh'):
        legacy_service.validated_paths(str(source), str(output))
    source.write_text('sensitive text is not a PDF')
    with pytest.raises(ValueError, match='PDF header'):
        legacy_service.validated_paths(str(source), str(legacy_service.ROOT / 'data/another'))

def legacy_fake_engine(legacy_service, *, fail_inference=False):
    closed_documents = []
    owner_threads = set()
    producer_fills_queue = threading.Event()

    class FakeBitmap:

        def to_pil(self):
            return Image.fromarray(np.full((32, 32, 3), 255, dtype=np.uint8))

        def close(self):
            owner_threads.add(threading.get_ident())

    class FakePage:

        def __init__(self, index):
            self.index = index

        def render(self, **kwargs):
            assert kwargs['scale'] == 200 / 72
            owner_threads.add(threading.get_ident())
            if self.index == 3:
                producer_fills_queue.set()
            return FakeBitmap()

        def close(self):
            owner_threads.add(threading.get_ident())

    class FakeDocument:

        def __init__(self, _source):
            owner_threads.add(threading.get_ident())

        def __len__(self):
            owner_threads.add(threading.get_ident())
            return 8

        def __getitem__(self, index):
            owner_threads.add(threading.get_ident())
            return FakePage(index)

        def close(self):
            owner_threads.add(threading.get_ident())
            closed_documents.append(True)
    engine = legacy_service.FastOCREngine.__new__(legacy_service.FastOCREngine)
    engine.np = np
    engine.cv2 = SimpleNamespace(COLOR_RGB2BGR=1, cvtColor=lambda pixels, _: pixels[..., ::-1].copy())
    engine.pdfium = SimpleNamespace(PdfDocument=FakeDocument)
    engine.completed_jobs = 0

    def predict(_image):
        if fail_inference:
            assert producer_fills_queue.wait(5), 'producer failed to fill its bounded queue'
            raise RuntimeError('GPU inference failed')
        return ([np.array([[1, 1], [30, 1], [30, 10], [1, 10]])], [('真实源文字', 0.93)])
    engine.predict = predict
    return (engine, closed_documents, owner_threads)

def test_legacy_render_and_gpu_pipeline_preserves_all_pages_and_owns_pdfium_in_one_thread(legacy_service):
    (engine, closed, owners) = legacy_fake_engine(legacy_service)
    result = engine.recognize(str(legacy_source_pdf(legacy_service)), str(legacy_service.ROOT / 'data/result'))
    assert result['status'] == 'complete'
    assert result['pages'] == 8
    assert len(closed) == 1
    assert len(owners) == 1
    assert threading.get_ident() not in owners
    assert engine.completed_jobs == 1
    assert result['capabilities']['table_structure'] is False
    assert result['capabilities']['formula_specialist'] is False
    raw = json.loads(Path(result['content_list']).read_text())
    assert [block['page_idx'] for block in raw] == list(range(8))
    assert all(block['confidence'] == 0.93 for block in raw)

def test_legacy_gpu_failure_cancels_renderer_even_when_prefetch_queue_is_full(legacy_service):
    (engine, closed, owners) = legacy_fake_engine(legacy_service, fail_inference=True)
    with pytest.raises(RuntimeError, match='GPU inference failed'):
        engine.recognize(str(legacy_source_pdf(legacy_service)), str(legacy_service.ROOT / 'data/failed'))
    assert closed == [True]
    assert len(owners) == 1
    assert not any(thread.name == 'fast-ocr-pdf-render' for thread in threading.enumerate())
    assert not (legacy_service.ROOT / 'data/failed/fast_ocr_metadata.json').exists()

def test_legacy_heading_inference_requires_toc_text_and_large_top_boxes(legacy_service):

    def line(page, text, bbox, index):
        return {'page_idx': page, 'text': text, 'bbox': bbox, 'type': 'text', 'ocr_line_id': str(index), 'ocr_backend': 'test'}
    toc = [line(0, '目录', [0, 0, 100, 30], 0), line(0, '第8章 测试章节', [100, 100, 300, 120], 1)]
    original = [line(7, '第', [100, 80, 130, 140], 2), line(7, '章测试章节', [250, 80, 550, 140], 3), line(7, '这是用于衡量正常正文高度的较长原始句子。', [50, 170, 900, 190], 4)]
    raw = toc + original
    result = legacy_service.infer_chapter_headings(raw)
    headings = [block for block in result if block.get('inferred')]
    assert len(headings) == 1
    assert headings[0]['text'] == '第8章 测试章节'
    assert headings[0]['toc_evidence'][0]['page_idx'] == 0
    assert headings[0]['source_ocr_line_ids'] == ['2', '3']
    assert [block for block in result if not block.get('inferred')] == raw
    assert not any(block.get('inferred') for block in legacy_service.infer_chapter_headings(original))
    footer = [dict(block, bbox=[100, 950, 400, 990]) for block in original]
    assert not any(block.get('inferred') for block in legacy_service.infer_chapter_headings(toc + footer))

def test_legacy_gpu_profile_does_not_copy_cloud_api_credentials(legacy_service, monkeypatch):
    calls = []
    config = legacy_service.ROOT / "config"
    config.mkdir()
    (config / "gpu.env").write_text("")
    (config / "ingestion-performance.env").write_text("")

    def environment(command):
        calls.append(command)
        return b'CUDA_VISIBLE_DEVICES=GPU-test\x00MINERU_DEVICE_MODE=cuda\x00MINIMAX_API_KEY=hidden\x00SMTP_PASSWORD=hidden\x00'
    monkeypatch.setattr(legacy_service.subprocess, 'check_output', environment)
    result = legacy_service.profile_environment()
    assert result['CUDA_VISIBLE_DEVICES'] == 'GPU-test'
    assert 'MINIMAX_API_KEY' not in result
    assert 'SMTP_PASSWORD' not in result
    assert str(legacy_service.ROOT / 'config/ingestion-performance.env') in calls[0]
    assert not any('secrets/' in part for part in calls[0])
