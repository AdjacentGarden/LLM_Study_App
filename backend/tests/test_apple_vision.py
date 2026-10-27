from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaptive_learning.ingestion import apple_vision, ocr_job
from adaptive_learning.ingestion.chaptering import load_normalized_pages
from adaptive_learning.ingestion.models import ExtractionMethod


def test_apple_vision_refuses_other_platforms(monkeypatch):
    monkeypatch.setattr(apple_vision.platform, "system", lambda: "Linux")
    with pytest.raises(RuntimeError, match="requires macOS"):
        apple_vision.native_helper()


def test_apple_vision_requires_an_explicit_executable_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(apple_vision.platform, "system", lambda: "Darwin")
    binary = tmp_path / "not-executable"
    binary.write_text("fixture")
    monkeypatch.setenv("APPLE_VISION_OCR_BINARY", str(binary))
    with pytest.raises(RuntimeError, match="absolute executable"):
        apple_vision.native_helper()


@pytest.mark.parametrize("tamper", [None, "missing_page", "wrong_engine"])
def test_vision_report_retains_source_confidence_and_rejects_incomplete_provenance(
    monkeypatch, tmp_path: Path, tamper
):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-test")
    monkeypatch.setattr(ocr_job, "pdf_page_count", lambda _: 2)
    raw = tmp_path / "raw"
    raw.mkdir()
    metadata = {
        "status": "complete", "pages": 2, "page_numbers": [1, 2], "empty_pages": [2],
        "capabilities": {"engine": "apple-vision", "framework": "Vision", "resident_gpu": False},
    }
    if tamper == "missing_page":
        metadata["page_numbers"] = [1]
    if tamper == "wrong_engine":
        metadata["capabilities"]["engine"] = "unknown"
    (raw / "fast_ocr_metadata.json").write_text(json.dumps(metadata))
    (raw / "vision_content_list.json").write_text(json.dumps([
        {"page_idx": 0, "type": "text", "text": "可核验的教材原文与对应文字位置。",
         "confidence": 0.71, "bbox": [10, 20, 800, 90], "ocr_backend": "apple-vision"}
    ]))
    (raw / "vision.md").write_text("Original text")
    options = dict(source=source, raw_dir=raw, output_dir=tmp_path / "normalized",
                   backend="apple-vision", duration_seconds=1, log_path=raw / "vision.log")
    if tamper:
        with pytest.raises(RuntimeError):
            ocr_job.write_report(**options)
        assert not (tmp_path / "normalized/quality_report.json").exists()
        return
    report = ocr_job.write_report(**options)
    assert report["quality_band_counts"]["missing"] == 1
    pages = load_normalized_pages(tmp_path / "normalized/pages.jsonl")
    assert pages[0].method == ExtractionMethod.APPLE_VISION
    assert pages[0].blocks[0].confidence == 0.71
    assert pages[0].blocks[0].metadata["ocr_capabilities"]["framework"] == "Vision"
    assert pages[1].raw_text == ""
