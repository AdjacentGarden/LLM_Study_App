import json
from pathlib import Path

import pytest

from adaptive_learning.ingestion.ocr_job import (
    evaluate_page,
    infer_log_duration,
    load_pages,
    mineru_subprocess_environment,
)


def test_load_pages_preserves_page_boundaries_and_blocks(tmp_path: Path) -> None:
    source = tmp_path / "book_content_list.json"
    source.write_text(
        json.dumps(
            [
                {"page_idx": 0, "type": "title", "text": "第一章 基础"},
                {"page_idx": 0, "type": "text", "text": "这是清晰、连续的教材正文。"},
                {"page_idx": 2, "type": "table", "table_body": "| A | B |"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    pages = load_pages(source, expected_pages=3)

    assert len(pages) == 3
    assert len(pages[0]["blocks"]) == 2
    assert pages[1]["blocks"] == []
    assert "A" in pages[2]["text"]


def test_evaluate_page_flags_missing_output() -> None:
    report = evaluate_page({"page_number": 4, "blocks": [], "text": ""})

    assert report["band"] == "missing"
    assert report["heuristic_score"] < 0.2


def test_load_pages_quarantines_foreign_generation_runaway(tmp_path: Path) -> None:
    source = tmp_path / "book_content_list.json"
    source.write_text(
        json.dumps(
            [
                {"page_idx": 0, "type": "title", "text": "第三章 基因的本质"},
                {"page_idx": 0, "type": "text", "text": "这是应保留的中文正文。"},
                {"page_idx": 0, "type": "text", "text": "small, " * 2_000},
                {"page_idx": 0, "type": "text", "text": "invented English background " * 80},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    page = load_pages(source, expected_pages=1)[0]

    assert "应保留" in page["text"]
    assert "invented English" not in page["text"]
    assert len(page["discarded_blocks"]) == 2


def test_infer_log_duration_uses_first_and_last_timestamps(tmp_path: Path) -> None:
    log = tmp_path / "mineru.log"
    log.write_text(
        "2026-08-29 12:35:44.923 | start\n"
        "progress without timestamp\n"
        "2026-08-29 12:38:39.676 | completed\n",
        encoding="utf-8",
    )

    assert infer_log_duration(log) == 174.753


def test_load_pages_removes_vlm_refusal_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "book_content_list.json"
    source.write_text(
        json.dumps(
            [
                {"page_idx": 0, "type": "text", "text": "可核验的教材正文。"},
                {
                    "page_idx": 0,
                    "type": "header",
                    "text": "The image is too blurry to recognize any text content.",
                },
                {"page_idx": 0, "type": "header", "text": "No"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    page = load_pages(source, expected_pages=1)[0]

    assert page["text"] == "可核验的教材正文。"
    assert len(page["discarded_blocks"]) == 2


def test_mineru_environment_bypasses_proxy_for_loopback(monkeypatch) -> None:
    monkeypatch.setenv("NO_PROXY", "internal.example")
    monkeypatch.setenv("no_proxy", "stale.example")

    environment = mineru_subprocess_environment()

    assert environment["NO_PROXY"] == environment["no_proxy"]
    entries = set(environment["NO_PROXY"].split(","))
    assert {"internal.example", "localhost", "127.0.0.1", "::1"} <= entries


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1:18200",
        "http://example.com",
        "http://u:p@localhost",
        "http://localhost/?x=1",
    ],
)
def test_mineru_api_rejects_non_loopback_or_ambiguous_url(monkeypatch, value):
    from adaptive_learning.ingestion.ocr_job import mineru_api_url

    monkeypatch.setenv("MINERU_API_URL", value)
    with pytest.raises(ValueError, match="loopback HTTP"):
        mineru_api_url()


def test_mineru_resident_call_never_adds_local_engine_options(tmp_path, monkeypatch):
    import subprocess

    from adaptive_learning.ingestion.ocr_job import run_mineru

    calls = []
    monkeypatch.setenv("MINERU_API_URL", "http://127.0.0.1:18200")
    monkeypatch.setenv("MINERU_ENGINE_ARGS_JSON", "invalid ignored resident settings")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: (
            calls.append((command, kwargs)) or subprocess.CompletedProcess(command, 0)
        ),
    )
    run_mineru(tmp_path / "book.pdf", tmp_path / "raw", "vlm-auto-engine", "ch")
    assert calls[0][0][-2:] == ["--api-url", "http://127.0.0.1:18200"]
    assert calls[0][1]["env"]["ORT_DISABLE_TELEMETRY"] == "1"


@pytest.mark.parametrize(
    "value",
    [
        '["--gpu-memory-utilization", 1.1]',
        '["--session-len", true]',
        '["--session-len", 0]',
        '["--unknown", 1]',
        '["--session-len", 1, "--session-len", 2]',
        "{}",
    ],
)
def test_mineru_engine_options_validate_numeric_bounds(monkeypatch, value):
    from adaptive_learning.ingestion.ocr_job import mineru_engine_arguments

    monkeypatch.setenv("MINERU_ENGINE_ARGS_JSON", value)
    with pytest.raises(ValueError):
        mineru_engine_arguments()


@pytest.mark.parametrize(
    "value",
    ["http://example.com", "http://localhost/path", "http://u:p@localhost", "https://localhost"],
)
def test_fast_client_rejects_non_loopback_origin(monkeypatch, value, tmp_path):
    from adaptive_learning.ingestion.ocr_job import run_paddle_gpu

    monkeypatch.setenv("FAST_OCR_API_URL", value)
    with pytest.raises(ValueError, match="loopback HTTP origin"):
        run_paddle_gpu(tmp_path / "source.pdf", tmp_path / "raw")


@pytest.mark.parametrize("tamper", ["page_numbers", "empty_pages", "page_idx"])
def test_fast_metadata_cannot_hide_missing_or_invalid_pages(tmp_path, monkeypatch, tamper):
    import adaptive_learning.ingestion.ocr_job as ocr

    source = tmp_path / "book.pdf"
    source.write_bytes(b"%PDF-test")
    monkeypatch.setattr(ocr, "pdf_page_count", lambda _: 2)
    raw = tmp_path / "raw"
    raw.mkdir()
    metadata = {
        "status": "complete",
        "pages": 2,
        "page_numbers": [1, 2],
        "empty_pages": [2],
        "capabilities": {"engine": "test"},
    }
    blocks = [{"page_idx": 0, "type": "text", "text": "可识别原文"}]
    if tamper == "page_numbers":
        metadata["page_numbers"] = [1, 1]
    elif tamper == "empty_pages":
        metadata["empty_pages"] = []
    else:
        blocks[0]["page_idx"] = 2
    (raw / "test_content_list.json").write_text(json.dumps(blocks))
    (raw / "test.md").write_text("original")
    (raw / "fast_ocr_metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(RuntimeError):
        ocr.write_report(
            source=source,
            output_dir=tmp_path / "normalized",
            raw_dir=raw,
            backend="paddle-gpu",
            duration_seconds=1,
            log_path=raw / "test.log",
        )
    assert not (tmp_path / "normalized/quality_report.json").exists()


def test_failed_mineru_resident_request_is_not_retried_locally(tmp_path, monkeypatch):
    import subprocess

    from adaptive_learning.ingestion.ocr_job import run_mineru

    calls = []
    monkeypatch.setenv("MINERU_API_URL", "http://127.0.0.1:18200")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 2),
    )
    with pytest.raises(RuntimeError, match="MinerU exited with 2"):
        run_mineru(tmp_path / "book.pdf", tmp_path / "raw", "vlm-auto-engine", "ch")
    assert len(calls) == 1 and "--api-url" in calls[0]


@pytest.mark.parametrize("case", ["outside", "not_complete", "nonfinite", "missing_artifact"])
def test_fast_client_validates_completed_response_artifacts(tmp_path, monkeypatch, case):
    import io

    import adaptive_learning.ingestion.ocr_job as ocr

    output = tmp_path / "raw"
    output.mkdir()
    artifacts = {}
    for name, filename in (
        ("content_list", "paddle_content_list.json"),
        ("markdown", "paddle.md"),
        ("log_path", "paddle.log"),
    ):
        path = output / filename
        path.write_text("fixture")
        artifacts[name] = str(path)
    result = {"status": "complete", "duration_seconds": 1, **artifacts}
    if case == "outside":
        outside = tmp_path / "outside.md"
        outside.write_text("outside")
        result["markdown"] = str(outside)
    elif case == "not_complete":
        result["status"] = "failed"
    elif case == "nonfinite":
        result["duration_seconds"] = float("nan")
    else:
        del result["log_path"]
    opener = type(
        "Opener", (), {"open": lambda *args, **kwargs: io.BytesIO(json.dumps(result).encode())}
    )()
    monkeypatch.setattr(ocr, "build_opener", lambda *_: opener)
    monkeypatch.setenv("FAST_OCR_API_URL", "http://127.0.0.1:18201")
    with pytest.raises(RuntimeError):
        ocr.run_paddle_gpu(tmp_path / "book.pdf", output)


def test_mineru_uses_explicit_ocr_interpreter(tmp_path, monkeypatch):
    import subprocess

    from adaptive_learning.ingestion.ocr_job import run_mineru

    calls = []
    monkeypatch.setenv("OCR_PYTHON", "/configured/ocr/bin/python")
    monkeypatch.delenv("MINERU_API_URL", raising=False)
    monkeypatch.delenv("MINERU_ENGINE_ARGS_JSON", raising=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 0),
    )
    run_mineru(tmp_path / "book.pdf", tmp_path / "raw", "vlm-auto-engine", "ch")
    assert calls[0][0] == "/configured/ocr/bin/python"
