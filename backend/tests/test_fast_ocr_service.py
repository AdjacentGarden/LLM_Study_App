from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

SERVICE_PATH = Path(__file__).parents[2] / "deployment/server/fast_ocr_service.py"


@pytest.fixture
def service(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("fast_service_test", SERVICE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    for name in ("input", "data", "benchmarks"):
        (tmp_path / name).mkdir()
    return module


def source_pdf(service):
    source = service.ROOT / "input/source.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture\n")
    return source


def test_service_rejects_external_reads_and_writes_and_symlink_escape(service, tmp_path):
    source = source_pdf(service)
    outside = tmp_path.parent / (tmp_path.name + "-outside.pdf")
    outside.write_bytes(b"%PDF-1.7\n")
    try:
        with pytest.raises(ValueError, match="outside the runtime PDF"):
            service.validated_paths(str(outside), str(service.ROOT / "data/new"))
        with pytest.raises(ValueError, match="outside the runtime artifact"):
            service.validated_paths(str(source), str(outside.parent / "escape"))
        linked = service.ROOT / "input/linked.pdf"
        linked.symlink_to(outside)
        with pytest.raises(ValueError, match="outside the runtime PDF"):
            service.validated_paths(str(linked), str(service.ROOT / "data/new"))
        linkdir = service.ROOT / "data/linked"
        linkdir.symlink_to(outside.parent, target_is_directory=True)
        with pytest.raises(ValueError, match="outside the runtime artifact"):
            service.validated_paths(str(source), str(linkdir / "escape"))
    finally:
        outside.unlink(missing_ok=True)


def test_service_requires_pdf_header_and_fresh_output(service):
    source = source_pdf(service)
    output = service.ROOT / "data/new"
    assert service.validated_paths(str(source), str(output)) == (source, output)
    output.mkdir()
    with pytest.raises(ValueError, match="fresh"):
        service.validated_paths(str(source), str(output))
    source.write_text("sensitive text is not a PDF")
    with pytest.raises(ValueError, match="PDF header"):
        service.validated_paths(str(source), str(service.ROOT / "data/another"))


def fake_engine(service, *, fail_inference=False):
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
            assert kwargs["scale"] == 200 / 72
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

    engine = service.FastOCREngine.__new__(service.FastOCREngine)
    engine.np = np
    engine.cv2 = SimpleNamespace(COLOR_RGB2BGR=1, cvtColor=lambda pixels, _: pixels[..., ::-1].copy())
    engine.pdfium = SimpleNamespace(PdfDocument=FakeDocument)
    engine.completed_jobs = 0

    def predict(_image):
        if fail_inference:
            assert producer_fills_queue.wait(5), "producer failed to fill its bounded queue"
            raise RuntimeError("GPU inference failed")
        return [np.array([[1, 1], [30, 1], [30, 10], [1, 10]])], [("真实源文字", 0.93)]

    engine.predict = predict
    return engine, closed_documents, owner_threads


def test_render_and_gpu_pipeline_preserves_all_pages_and_owns_pdfium_in_one_thread(service):
    engine, closed, owners = fake_engine(service)
    result = engine.recognize(str(source_pdf(service)), str(service.ROOT / "data/result"))
    assert result["status"] == "complete"
    assert result["pages"] == 8
    assert len(closed) == 1
    assert len(owners) == 1
    assert threading.get_ident() not in owners
    assert engine.completed_jobs == 1
    assert result["capabilities"]["table_structure"] is False
    assert result["capabilities"]["formula_specialist"] is False
    raw = json.loads(Path(result["content_list"]).read_text())
    assert [block["page_idx"] for block in raw] == list(range(8))
    assert all(block["confidence"] == 0.93 for block in raw)


def test_gpu_failure_cancels_renderer_even_when_prefetch_queue_is_full(service):
    engine, closed, owners = fake_engine(service, fail_inference=True)
    with pytest.raises(RuntimeError, match="GPU inference failed"):
        engine.recognize(str(source_pdf(service)), str(service.ROOT / "data/failed"))
    assert closed == [True]
    assert len(owners) == 1
    assert not any(thread.name == "fast-ocr-pdf-render" for thread in threading.enumerate())
    assert not (service.ROOT / "data/failed/fast_ocr_metadata.json").exists()


def test_heading_inference_requires_toc_text_and_large_top_boxes(service):
    def line(page, text, bbox, index):
        return {"page_idx": page, "text": text, "bbox": bbox, "type": "text",
                "ocr_line_id": str(index), "ocr_backend": "test"}

    toc = [line(0, "目录", [0, 0, 100, 30], 0),
           line(0, "第8章 测试章节", [100, 100, 300, 120], 1)]
    original = [line(7, "第", [100, 80, 130, 140], 2),
                line(7, "章测试章节", [250, 80, 550, 140], 3),
                line(7, "这是用于衡量正常正文高度的较长原始句子。", [50, 170, 900, 190], 4)]
    raw = toc + original
    result = service.infer_chapter_headings(raw)
    headings = [block for block in result if block.get("inferred")]
    assert len(headings) == 1
    assert headings[0]["text"] == "第8章 测试章节"
    assert headings[0]["toc_evidence"][0]["page_idx"] == 0
    assert headings[0]["source_ocr_line_ids"] == ["2", "3"]
    assert [block for block in result if not block.get("inferred")] == raw
    assert not any(block.get("inferred") for block in service.infer_chapter_headings(original))
    footer = [dict(block, bbox=[100, 950, 400, 990]) for block in original]
    assert not any(block.get("inferred") for block in service.infer_chapter_headings(toc + footer))


def test_gpu_profile_does_not_copy_cloud_api_credentials(service, monkeypatch):
    calls = []

    def environment(command):
        calls.append(command)
        return b"CUDA_VISIBLE_DEVICES=GPU-test\0MINERU_DEVICE_MODE=cuda\0MINIMAX_API_KEY=hidden\0SMTP_PASSWORD=hidden\0"

    monkeypatch.setattr(service.subprocess, "check_output", environment)
    result = service.profile_environment()
    assert result["CUDA_VISIBLE_DEVICES"] == "GPU-test"
    assert "MINIMAX_API_KEY" not in result
    assert "SMTP_PASSWORD" not in result
    assert str(service.ROOT / "config/ingestion-performance.env") in calls[0]
    assert not any("secrets/" in part for part in calls[0])
