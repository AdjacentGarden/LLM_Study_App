"""Extraction cache identity must track renderer code, DPI, and custom binaries."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from test_ocr_jobs import enqueue, make_repository, register

from adaptive_learning import config
from adaptive_learning.ingestion import apple_vision, ocr_review
from adaptive_learning.ingestion.jobs import OCRRunResult, SubprocessOCRRunner
from adaptive_learning.ingestion.ocr_job import write_report


@pytest.fixture
def extraction(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLE_VISION_OCR_BINARY", raising=False)
    monkeypatch.setenv("APPLE_VISION_OCR_DPI", "200")
    monkeypatch.setattr(
        config, "get_settings", lambda: SimpleNamespace(vision_rescue_enabled=False)
    )
    repository = make_repository(tmp_path)
    register(repository, tmp_path)
    enqueue(repository, tmp_path)
    job = repository.claim_next(owner="cache-test", lease_seconds=60, now=3)
    job.output_dir.mkdir(parents=True)
    return job, SubprocessOCRRunner(backend="apple-vision")


def artifact(directory, job, identity=None, *, backend="apple-vision"):
    directory.mkdir(parents=True, exist_ok=True)
    report = {
        "source_sha256": hashlib.sha256(job.source_path.read_bytes()).hexdigest(),
        "backend": backend,
        "quality_band_counts": {"accepted": 1},
        "mean_heuristic_score": 0.95,
        "page_count_normalized": 1,
    }
    if identity is not None:
        report["extraction_identity"] = identity
        report["ocr_capabilities"] = {"extraction_identity": identity}
    (directory / "quality_report.json").write_text(json.dumps(report))
    for name in ("quality_report.md", "pages.jsonl", "full_text.md"):
        (directory / name).write_text("fixture")
    return directory


def checkpoint(job, directory, identity=None, *, backend="apple-vision"):
    value = {
        "source_hash": hashlib.sha256(job.source_path.read_bytes()).hexdigest(),
        "backend": backend,
        "normalized": str(directory.resolve()),
    }
    if identity is not None:
        value["extraction_identity"] = identity
    (job.output_dir / "extraction-checkpoint.json").write_text(json.dumps(value))


@pytest.mark.parametrize("old_kind", ["unversioned", "old-helper", "old-dpi", "unversioned-report"])
def test_apple_reextracts_old_checkpoint_and_preserves_original_attempt(
    extraction, monkeypatch, old_kind
):
    job, runner = extraction
    current = apple_vision.extraction_identity()
    previous = dict(current)
    if old_kind == "old-helper":
        previous["helper_source_sha256"] = "0" * 64
    elif old_kind == "old-dpi":
        previous["render_dpi"] = 72
    elif old_kind == "unversioned":
        previous = None
    original = artifact(
        job.output_dir / "attempt-01-original" / "normalized",
        job,
        None if old_kind == "unversioned-report" else previous,
    )
    checkpoint(job, original, previous)
    before = (original / "quality_report.json").read_bytes()
    new = artifact(job.output_dir / "attempt-02-current" / "normalized", job, current)
    extracted = []

    def fresh(*args):
        extracted.append(True)
        return OCRRunResult(0.95, False, 1, new)

    monkeypatch.setattr(runner, "_extract", fresh)
    result = runner.run(job, lambda *_: True)
    assert extracted == [True] and result.artifact_dir == new
    assert (original / "quality_report.json").read_bytes() == before
    saved = json.loads((job.output_dir / "extraction-checkpoint.json").read_text())
    assert saved["extraction_identity"] == current
    assert saved["normalized"] == str(new.resolve())


def test_apple_legacy_normalized_without_identity_cannot_bypass_checkpoint_guard(
    extraction, monkeypatch
):
    job, runner = extraction
    original = artifact(job.output_dir / "normalized", job)
    new = artifact(
        job.output_dir / "attempt-02-current" / "normalized",
        job,
        apple_vision.extraction_identity(),
    )
    monkeypatch.setattr(runner, "_extract", lambda *_: OCRRunResult(0.95, False, 1, new))
    assert runner.run(job, lambda *_: True).artifact_dir == new
    assert (original / "quality_report.json").is_file()


def test_same_apple_identity_resumes_review_without_new_extraction(extraction, monkeypatch):
    job, runner = extraction
    current = apple_vision.extraction_identity()
    original = artifact(job.output_dir / "attempt-01-current" / "normalized", job, current)
    checkpoint(job, original, current)
    reviewed = []
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(vision_rescue_enabled=True))

    def unexpected(*args):
        raise AssertionError("unchanged extraction must resume review without running OCR")

    def review(source, normalized, *args, **kwargs):
        reviewed.append(normalized)
        return normalized

    monkeypatch.setattr(runner, "_extract", unexpected)
    monkeypatch.setattr(ocr_review, "review_pages", review)
    assert runner.run(job, lambda *_: True).artifact_dir == original
    assert reviewed == [original]


def test_changed_effective_dpi_invalidates_checkpoint_and_legacy_fallback(extraction, monkeypatch):
    job, runner = extraction
    previous = apple_vision.extraction_identity()
    original = artifact(job.output_dir / "normalized", job, previous)
    checkpoint(job, original, previous)
    monkeypatch.setenv("APPLE_VISION_OCR_DPI", "300")
    current = apple_vision.extraction_identity()
    assert current["render_dpi"] == 300 and current != previous
    new = artifact(job.output_dir / "attempt-02-current" / "normalized", job, current)
    monkeypatch.setattr(runner, "_extract", lambda *_: OCRRunResult(0.95, False, 1, new))
    assert runner.run(job, lambda *_: True).artifact_dir == new


def test_other_backend_keeps_unversioned_checkpoint_compatibility(extraction, monkeypatch):
    job, _ = extraction
    runner = SubprocessOCRRunner(backend="vlm-auto-engine")
    original = artifact(
        job.output_dir / "attempt-01-mineru" / "normalized", job, backend=runner.backend
    )
    checkpoint(job, original, backend=runner.backend)

    def unexpected(*args):
        raise AssertionError("existing non-Apple cache contract must remain compatible")

    monkeypatch.setattr(runner, "_extract", unexpected)
    assert runner.run(job, lambda *_: True).artifact_dir == original


def test_identity_tracks_helper_source_and_override_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(apple_vision, "__file__", str(tmp_path / "apple_vision.py"))
    source = tmp_path / "apple_vision_ocr.swift"
    source.write_text("renderer v1")
    monkeypatch.delenv("APPLE_VISION_OCR_BINARY", raising=False)
    monkeypatch.delenv("APPLE_VISION_OCR_DPI", raising=False)
    first = apple_vision.extraction_identity()
    assert first["render_dpi"] == 200
    source.write_text("renderer v2")
    assert apple_vision.extraction_identity() != first
    binary = tmp_path / "custom-ocr"
    binary.write_bytes(b"binary v1")
    binary.chmod(0o700)
    monkeypatch.setenv("APPLE_VISION_OCR_BINARY", str(binary))
    overridden = apple_vision.extraction_identity()
    assert overridden["override_binary_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    binary.write_bytes(b"binary v2")
    assert apple_vision.extraction_identity() != overridden


def test_actual_apple_run_propagates_identity_to_metadata_and_quality_report(tmp_path, monkeypatch):
    import pymupdf

    monkeypatch.delenv("APPLE_VISION_OCR_BINARY", raising=False)
    monkeypatch.setenv("APPLE_VISION_OCR_DPI", "200")
    source = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        document.new_page()
        document.save(source)
    raw = tmp_path / "raw"
    binary = tmp_path / "fake-helper"
    monkeypatch.setattr(apple_vision, "native_helper", lambda: binary)

    def run(command, **kwargs):
        assert command[-2:] == ["--dpi", "200"]
        raw.mkdir()
        (raw / "fast_ocr_metadata.json").write_text(
            json.dumps(
                {
                    "status": "complete",
                    "pages": 1,
                    "page_numbers": [1],
                    "empty_pages": [],
                    "duration_seconds": 0.3,
                    "capabilities": {
                        "engine": "apple-vision",
                        "framework": "Vision",
                        "resident_gpu": False,
                    },
                }
            )
        )
        (raw / "source_content_list.json").write_text(
            json.dumps(
                [
                    {
                        "page_idx": 0,
                        "type": "text",
                        "text": "Text recognized from the actual source page.",
                    },
                ]
            )
        )
        (raw / "source.md").write_text("Text recognized from the actual source page.")
        (raw / "vision.log").write_text("fixture")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(apple_vision.subprocess, "run", run)
    duration, log = apple_vision.run_apple_vision(source, raw)
    identity = apple_vision.extraction_identity()
    metadata = json.loads((raw / "fast_ocr_metadata.json").read_text())
    assert (
        metadata["extraction_identity"]
        == metadata["capabilities"]["extraction_identity"]
        == identity
    )
    report = write_report(
        source=source,
        output_dir=tmp_path / "normalized",
        raw_dir=raw,
        backend="apple-vision",
        duration_seconds=duration,
        log_path=log,
    )
    assert (
        report["extraction_identity"]
        == report["ocr_capabilities"]["extraction_identity"]
        == identity
    )
