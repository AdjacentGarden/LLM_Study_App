from __future__ import annotations

from fastapi.testclient import TestClient

from app.assistant import service
from app.core.config import get_settings
from app.main import app


class _RuntimeStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def post_json(self, **kwargs):
        self.calls.append(kwargs)
        return {"choices": [{"message": {"content": "减数分裂会使染色体数目减半。"}}]}


def test_global_assistant_calls_real_provider_adapter(monkeypatch) -> None:
    runtime = _RuntimeStub()
    monkeypatch.setenv("BOOKCOURSE_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("BOOKCOURSE_LLM_API_URL", "https://provider.example/v1/chat/completions")
    monkeypatch.setenv("BOOKCOURSE_LLM_API_KEY", "provider-key")
    monkeypatch.setenv("BOOKCOURSE_LLM_MODEL", "cloud-model")
    get_settings.cache_clear()
    monkeypatch.setattr(service, "get_ai_runtime", lambda _provider: runtime)

    response = TestClient(app).post(
        "/api/assistant/chat",
        json={
            "message": "什么是减数分裂？",
            "history": [{"role": "user", "content": "我正在复习生物"}],
            "context_title": "高中生物",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "answer": "减数分裂会使染色体数目减半。",
        "provider": "openai_compatible",
        "model": "cloud-model",
    }
    assert runtime.calls[0]["api_url"] == "https://provider.example/v1/chat/completions"
    sent = runtime.calls[0]["payload"]
    assert isinstance(sent, dict)
    assert sent["stream"] is False
    assert sent["messages"][-1] == {"role": "user", "content": "什么是减数分裂？"}


def test_global_assistant_refuses_template_mode(monkeypatch) -> None:
    monkeypatch.setenv("BOOKCOURSE_LLM_PROVIDER", "template")
    get_settings.cache_clear()

    response = TestClient(app).post("/api/assistant/chat", json={"message": "你好"})

    assert response.status_code == 503
    assert response.json()["code"] == "assistant_llm_not_configured"
