from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def test_runtime_capabilities_require_auth_and_do_not_expose_secrets(monkeypatch) -> None:
    monkeypatch.setenv("BOOKCOURSE_AUTH_MODE", "strict")
    monkeypatch.setenv("BOOKCOURSE_API_KEY", "never-return-this")
    monkeypatch.setenv("BOOKCOURSE_PARSER_PROVIDER", "pymupdf")
    monkeypatch.setenv("BOOKCOURSE_RAG_INDEX_PROVIDER", "artifact")
    get_settings.cache_clear()
    client = TestClient(create_app())
    blocked = client.get("/api/runtime-capabilities")
    response = client.get(
        "/api/runtime-capabilities",
        headers={"X-BookCourse-Api-Key": "never-return-this"},
    )

    assert blocked.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert payload["auth_mode"] == "strict"
    assert payload["parser_provider"] == "pymupdf"
    assert payload["ocr_provider"] == "paddleocr-vl"
    assert payload["ocr_model"] == "PaddleOCR-VL-1.6"
    assert payload["ocr_quality_profile"] == "balanced"
    assert payload["ocr_cache_enabled"] is True
    assert payload["ocr_adaptive_retry"] is True
    assert payload["ocr_text_fallback_enabled"] is True
    assert payload["mineru_backend"] == "pipeline"
    assert payload["rag_index_provider"] == "artifact"
    assert payload["reranker_provider"] == "heuristic"
    assert payload["reranker_model"] == "BAAI/bge-reranker-v2-m3"
    assert len(payload["reranker_revision"]) == 40
    assert payload["reranker_device"] == "auto"
    assert payload["reranker_fail_open"] is True
    assert payload["ai_max_concurrent"] == 4
    assert payload["ai_max_retries"] == 2
    assert isinstance(payload["ai_runtime"], list)
    assert "never-return-this" not in response.text


def test_large_responses_are_gzip_compressed() -> None:
    response = TestClient(create_app()).get(
        "/openapi.json",
        headers={"Accept-Encoding": "gzip"},
    )

    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"


def test_text_ocr_provider_reports_its_actual_default_model(monkeypatch) -> None:
    monkeypatch.setenv("BOOKCOURSE_OCR_PROVIDER", "paddleocr")
    monkeypatch.delenv("BOOKCOURSE_OCR_MODEL", raising=False)
    get_settings.cache_clear()
    try:
        response = TestClient(create_app()).get("/api/runtime-capabilities")
        assert response.status_code == 200
        payload = response.json()
        assert payload["ocr_provider"] == "paddleocr"
        assert payload["ocr_model"] == "PP-OCRv6-medium"
    finally:
        get_settings.cache_clear()
