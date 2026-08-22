from __future__ import annotations

import pytest

from app.core.config import Settings


def _valid_production_env(monkeypatch) -> None:
    values = {
        "BOOKCOURSE_ENVIRONMENT": "production",
        "BOOKCOURSE_AUTH_MODE": "strict",
        "BOOKCOURSE_API_KEY": "api-key-abcdefghijklmnopqrstuvwxyz",
        "BOOKCOURSE_TRUSTED_PROXY_TOKEN": "proxy-token-abcdefghijklmnopqrstuvwxyz",
        "BOOKCOURSE_ALLOWED_ORIGINS": "https://bookcourse.example.com",
        "BOOKCOURSE_ALLOW_CREDENTIALS": "false",
        "BOOKCOURSE_USE_WORKER": "true",
        "BOOKCOURSE_PARSER_PROVIDER": "mineru",
        "BOOKCOURSE_EMBEDDING_PROVIDER": "bge_m3",
        "BOOKCOURSE_EMBEDDING_DEVICE": "cuda:0",
        "BOOKCOURSE_RERANKER_PROVIDER": "bge",
        "BOOKCOURSE_RERANKER_DEVICE": "cuda:0",
        "BOOKCOURSE_RERANKER_FAIL_OPEN": "false",
        "BOOKCOURSE_RAG_INDEX_PROVIDER": "pgvector",
        "BOOKCOURSE_LLM_PROVIDER": "deepseek",
        "BOOKCOURSE_RAG_LLM_PROVIDER": "deepseek",
        "BOOKCOURSE_DEEPSEEK_API_KEY": "deepseek-key-abcdefghijklmnopqrstuvwxyz",
        "BOOKCOURSE_IMAGE_PROVIDER": "openai_compatible",
        "BOOKCOURSE_IMAGE_API_URL": "https://images.example.com/v1/generate",
        "BOOKCOURSE_IMAGE_API_KEY": "image-key-abcdefghijklmnopqrstuvwxyz",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_production_rejects_wildcard_credentialed_cors(monkeypatch) -> None:
    _valid_production_env(monkeypatch)
    monkeypatch.setenv("BOOKCOURSE_ALLOWED_ORIGINS", "*")
    monkeypatch.setenv("BOOKCOURSE_ALLOW_CREDENTIALS", "true")

    with pytest.raises(ValueError, match="wildcard origins"):
        Settings()


@pytest.mark.parametrize("admin_token", ["short", "REQUIRED_ADMIN_SECRET"])
def test_production_rejects_weak_admin_token(monkeypatch, admin_token: str) -> None:
    _valid_production_env(monkeypatch)
    monkeypatch.setenv("BOOKCOURSE_ADMIN_TOKEN", admin_token)

    with pytest.raises(ValueError, match="ADMIN_TOKEN"):
        Settings()


def test_production_rejects_reused_admin_token(monkeypatch) -> None:
    _valid_production_env(monkeypatch)
    monkeypatch.setenv("BOOKCOURSE_ADMIN_TOKEN", "api-key-abcdefghijklmnopqrstuvwxyz")

    with pytest.raises(ValueError, match="must be distinct"):
        Settings()
