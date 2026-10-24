from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from adaptive_learning.config import get_settings
from adaptive_learning.llm.client import LLMConfig, LLMOutputLimitError, OpenAICompatibleClient


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("deepseek-flash", {"thinking": {"type": "disabled"}, "reasoning_effort": "high"}),
        ("grok-test", {"reasoning": {"effort": "high"}}),
        ("claude-test", {}),
    ],
)
def test_explicit_controls_follow_provider_protocol(
    monkeypatch: pytest.MonkeyPatch, model: str, expected: dict[str, Any]
) -> None:
    captured: dict[str, Any] = {}

    def post(self: httpx.Client, url: str, **kwargs: Any) -> httpx.Response:
        captured.update(kwargs["json"])
        if model.startswith("claude-"):
            body = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "{}"}]}
        elif model.startswith("grok-"):
            body = {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": "{}"},
            ]}]}
        else:
            body = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
        return httpx.Response(200, request=httpx.Request("POST", url), json=body)

    monkeypatch.setattr(httpx.Client, "post", post)
    client = OpenAICompatibleClient(LLMConfig(
        "https://example.test/v1", "test-key", model,
        thinking_enabled=False, reasoning_effort="high",
    ))
    try:
        assert client.structured(system="s", user="u") == {}
    finally:
        client.close()
    for name in ("thinking", "reasoning_effort", "reasoning"):
        if name in expected:
            assert captured[name] == expected[name]
        else:
            assert name not in captured


def test_default_chat_payload_does_not_change_provider_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def post(self: httpx.Client, url: str, **kwargs: Any) -> httpx.Response:
        captured.update(kwargs["json"])
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": "{}"}}],
        })

    monkeypatch.setattr(httpx.Client, "post", post)
    client = OpenAICompatibleClient(LLMConfig("https://example.test/v1", "test-key", "test"))
    try:
        assert client.structured(system="s", user="u") == {}
    finally:
        client.close()
    assert "thinking" not in captured
    assert "reasoning_effort" not in captured


@pytest.mark.parametrize("retries", [0, 1])
def test_length_stopped_response_is_rejected_even_when_json_is_valid(
    monkeypatch: pytest.MonkeyPatch, retries: int,
) -> None:
    reasons = iter(["length", "stop"])
    calls = 0

    def post(self: httpx.Client, url: str, **kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"finish_reason": next(reasons), "message": {"content": '{"items":[]}'}}],
        })

    monkeypatch.setattr(httpx.Client, "post", post)
    client = OpenAICompatibleClient(LLMConfig(
        "https://example.test/v1", "test-key", "test", max_retries=retries,
    ))
    try:
        # A known truncated answer needs a different output budget, not an
        # identical automatic paid replay, even if ordinary retries are enabled.
        with pytest.raises(LLMOutputLimitError):
            client.structured(system="s", user="u")
        assert calls == 1
    finally:
        client.close()


@pytest.fixture
def uncached_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("uncached_settings")
@pytest.mark.parametrize("value,expected", [("false", False), (" TRUE ", True), ("", None)])
def test_settings_capture_explicit_controls(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool | None,
) -> None:
    monkeypatch.setenv("TEXT_LLM_THINKING_ENABLED", value)
    monkeypatch.setenv("TEXT_LLM_REASONING_EFFORT", " high ")
    settings = get_settings()
    assert settings.text_thinking_enabled is expected
    assert settings.text_reasoning_effort == "high"
    # Settings are a snapshot, just like provider credentials and model names.
    monkeypatch.setenv("TEXT_LLM_THINKING_ENABLED", "false")
    assert settings.text_thinking_enabled is expected


@pytest.mark.usefixtures("uncached_settings")
def test_invalid_thinking_setting_fails_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT_LLM_THINKING_ENABLED", "fasle")
    with pytest.raises(ValueError, match="TEXT_LLM_THINKING_ENABLED"):
        get_settings()


@pytest.mark.usefixtures("uncached_settings")
@pytest.mark.parametrize("value,expected", [(None, True), ("0", False), ("1", True), ("false", False), ("true", True)])
def test_grounded_diagnostic_rewrite_is_default_with_explicit_legacy_opt_out(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool,
) -> None:
    if value is None:
        monkeypatch.delenv("DIAGNOSTIC_ALLOW_GROUNDED_ANSWER_REWRITE", raising=False)
    else:
        monkeypatch.setenv("DIAGNOSTIC_ALLOW_GROUNDED_ANSWER_REWRITE", value)
    assert get_settings().diagnostic_allow_grounded_answer_rewrite is expected


@pytest.mark.parametrize("value,expected", [(None, True), ("0", False), ("1", True), ("false", False), ("true", True)])
def test_api_wires_grounded_diagnostic_rewrite_to_generator(
    tmp_path, value: str | None, expected: bool,
) -> None:
    environment = {
        **os.environ,
        "APP_DATA_DIR": str(tmp_path),
        "LLM_PROVIDER": "deepseek",
        "DEEPSEEK_API_KEY": "test-key",
    }
    if value is None:
        environment.pop("DIAGNOSTIC_ALLOW_GROUNDED_ANSWER_REWRITE", None)
    else:
        environment["DIAGNOSTIC_ALLOW_GROUNDED_ANSWER_REWRITE"] = value
    subprocess.run(
        [sys.executable, "-c", (
            "from adaptive_learning.api.app import item_generator; "
            "assert item_generator is not None; "
            f"assert item_generator.allow_grounded_answer_rewrite is {expected}; "
            "assert item_generator.verify_items"
        )],
        env=environment,
        check=True,
        timeout=20,
        capture_output=True,
    )
