"""Review roles choose their provider credentials and preserve multimodal transport."""
from dataclasses import replace
from importlib import import_module
import os
import subprocess
import sys

import httpx
import pytest

from adaptive_learning.config import get_settings
from adaptive_learning.ingestion import ocr_review


@pytest.fixture
def configuration(monkeypatch):
    for key in ("TEACHING_REVIEW_PROVIDER", "TEACHING_REVIEW_MODEL", "OCR_REVIEW_PROVIDER",
                "OCR_REVIEW_MODEL", "DEEPSEEK_API_KEY", "PUCODING_API_KEY",
                "TEXT_LLM_THINKING_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield replace(get_settings(), deepseek_api_key="deepseek-fixture-key",
                  pucoding_api_key="pucoding-fixture-key", deepseek_model="deepseek-flash",
                  text_thinking_enabled=False)
    get_settings.cache_clear()


def test_deepseek_review_uses_own_key_endpoint_and_tracks_effective_cache_policy(configuration):
    configured = replace(configuration, teaching_review_provider="deepseek",
                         teaching_review_model="deepseek-flash")
    assert configured.teaching_review_configured
    assert configured.teaching_review_api_key == "deepseek-fixture-key"
    assert configured.teaching_review_base_url == configured.deepseek_base_url
    assert configured.teaching_review_identity == {
        "provider": "deepseek", "model": "deepseek-flash", "reasoning_effort": "low",
    }
    assert not replace(configured, deepseek_api_key="").teaching_review_configured
    assert replace(configured, pucoding_base_url="https://unused.invalid").teaching_review_revision == configured.teaching_review_revision
    for change in ({"deepseek_base_url": "https://deepseek-route.invalid/v1"},
                   {"teaching_review_model": "deepseek-other"},
                   {"text_thinking_enabled": True}, {"text_thinking_enabled": None}):
        assert replace(configured, **change).teaching_review_revision != configured.teaching_review_revision
    assert "fixture-key" not in configured.teaching_review_revision


def test_app_import_builds_flash_writer_and_reviewer_offline(configuration, tmp_path):
    environment = dict(os.environ)
    environment.update({
        "APP_DATA_DIR": str(tmp_path / "app-data"), "LLM_PROVIDER": "deepseek",
        "DEEPSEEK_API_KEY": "offline-app-fixture-key", "DEEPSEEK_MODEL": "deepseek-flash",
        "TEACHING_REVIEW_PROVIDER": "deepseek", "TEACHING_REVIEW_MODEL": "deepseek-flash",
        "TEXT_LLM_THINKING_ENABLED": "false", "TEXT_LLM_REASONING_EFFORT": "low",
        "RAG_INDEX_DIR": "", "RAG_EMBEDDING_MODEL_PATH": "", "RAG_RERANKER_MODEL_PATH": "",
        "OCR_WORKER_ENABLED": "false", "STUDIO_WORKER_ENABLED": "false",
    })
    result = subprocess.run([sys.executable, "-c", """
import importlib
import httpx
def offline(*args, **kwargs):
    raise AssertionError('app initialization must not send a model request')
httpx.Client.post = offline
app = importlib.import_module('adaptive_learning.api.app')
assert app.teaching_generator.review_identity == {
    'provider': 'deepseek', 'model': 'deepseek-flash', 'reasoning_effort': 'low'}
assert app.teaching_client is not app.teaching_review_client
assert app.item_generator is not None
assert app.item_generator.client is app.assessment_llm
assert app.item_generator.review_client is app.teaching_review_client
for client in (app.teaching_client, app.teaching_review_client):
    assert client.config.model == 'deepseek-flash'
    assert client.config.thinking_enabled is False
assert 'thinking=disabled' in app.settings.teaching_review_revision
"""], env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "independent diagnostic reviewer could not be initialized" not in result.stderr


def test_review_roles_can_use_same_flash_model_as_independent_clients(configuration, monkeypatch):
    app = import_module("adaptive_learning.api.app")
    configured = replace(configuration, teaching_review_provider="deepseek",
                         teaching_review_model="deepseek-flash")
    monkeypatch.setattr(ocr_review, "get_settings", lambda: configured)
    monkeypatch.setenv("OCR_REVIEW_PROVIDER", "deepseek")
    teaching = app._create_teaching_review_client(configured)
    visual = ocr_review.review_client()
    try:
        assert teaching is not visual
        for client in (teaching, visual):
            assert client.config.api_key == "deepseek-fixture-key"
            assert client.config.model == "deepseek-flash"
            assert client.config.base_url == configured.deepseek_base_url
            assert client.config.thinking_enabled is False
            assert client.config.reasoning_effort == "low"
    finally:
        teaching.close()
        visual.close()


@pytest.mark.parametrize("provider,missing_key", [("deepseek", "deepseek_api_key"),
                                                 ("pucoding", "pucoding_api_key")])
def test_ocr_review_never_falls_back_to_other_provider_key(configuration, monkeypatch, provider, missing_key):
    monkeypatch.setattr(ocr_review, "get_settings", lambda: replace(configuration, **{missing_key: ""}))
    monkeypatch.setenv("OCR_REVIEW_PROVIDER", provider)
    with pytest.raises(ocr_review.OCRReviewUnavailable, match="not configured"):
        ocr_review.review_client()


@pytest.mark.parametrize("provider,model", [("unknown", "deepseek-flash"),
                                          ("deepseek", "gpt-5.6-terra"),
                                          ("deepseek", "sk-invalid-secret")])
def test_ocr_review_rejects_invalid_provider_model_before_transport(configuration, monkeypatch, provider, model):
    monkeypatch.setattr(ocr_review, "get_settings", lambda: configuration)
    monkeypatch.setenv("OCR_REVIEW_PROVIDER", provider)
    monkeypatch.setenv("OCR_REVIEW_MODEL", model)
    with pytest.raises(ocr_review.OCRReviewUnavailable):
        ocr_review.review_client()


def test_ocr_review_defaults_to_pucoding_provider(configuration, monkeypatch):
    monkeypatch.setattr(ocr_review, "get_settings", lambda: configuration)
    client = ocr_review.review_client()
    try:
        assert client.config.api_key == "pucoding-fixture-key"
        assert client.config.base_url == configuration.pucoding_base_url
        assert client.config.model == configuration.pucoding_vision_model
    finally:
        client.close()


def test_deepseek_ocr_client_sends_image_json_with_disabled_thinking(configuration, monkeypatch):
    monkeypatch.setattr(ocr_review, "get_settings", lambda: configuration)
    monkeypatch.setenv("OCR_REVIEW_PROVIDER", "deepseek")
    calls = []

    def post(self, url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "model": "deepseek-flash", "choices": [{"finish_reason": "stop",
            "message": {"content": '{"verified":true}'}}],
        })

    monkeypatch.setattr(httpx.Client, "post", post)
    client = ocr_review.review_client()
    try:
        assert client.structured(system="Return JSON.", user="Read source image.",
                                 images=[("image/png", b"image-fixture")]) == {"verified": True}
    finally:
        client.close()
    endpoint, request = calls[0]
    assert endpoint == configuration.deepseek_base_url.rstrip("/") + "/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer deepseek-fixture-key"
    payload = request["json"]
    assert payload["model"] == "deepseek-flash"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["reasoning_effort"] == "low"
    assert payload["messages"][1]["content"][0] == {
        "type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2UtZml4dHVyZQ=="},
    }
    assert payload["response_format"] == {"type": "json_object"}
