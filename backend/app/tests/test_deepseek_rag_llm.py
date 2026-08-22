from __future__ import annotations

import json

import app.core.ai_runtime as ai_runtime_module
from app.core.config import get_settings
from app.rag.llm import DeepSeekRagAnswerAdapter, build_grounded_prompt, get_rag_answer_adapter
from app.schemas.books import Citation


def _citation() -> Citation:
    return Citation(
        chapter_id="c1",
        chapter_title="Chapter 1",
        page=3,
        chunk_id="chunk_001",
        quote="Concept A is explained by this textbook passage.",
        score=0.9,
    )


def test_deepseek_rag_adapter_uses_v4_flash_payload(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return json.dumps({"choices": [{"message": {"content": "Grounded answer with citation."}}]}).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(ai_runtime_module._NO_REDIRECT_OPENER, "open", fake_urlopen)
    adapter = DeepSeekRagAnswerAdapter(
        "https://api.deepseek.com/chat/completions",
        "fake-key",
        "deepseek-v4-flash",
        timeout_seconds=9,
        extra_payload={"thinking": {"type": "disabled"}},
    )

    answer, prompt = adapter.answer("What is Concept A?", [_citation()])

    payload = captured["payload"]
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["thinking"] == {"type": "disabled"}
    assert captured["timeout"] == 9
    assert "chunk_001" in prompt
    assert answer == "Grounded answer with citation."


def test_deepseek_rag_provider_defaults_to_v4_flash(monkeypatch) -> None:
    monkeypatch.setenv("BOOKCOURSE_RAG_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("BOOKCOURSE_DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.delenv("BOOKCOURSE_DEEPSEEK_MODEL", raising=False)
    get_settings.cache_clear()

    adapter = get_rag_answer_adapter()

    assert isinstance(adapter, DeepSeekRagAnswerAdapter)
    assert adapter.model == "deepseek-v4-flash"
    assert adapter.extra_payload == {"thinking": {"type": "disabled"}}
    get_settings.cache_clear()


def test_deepseek_rag_model_is_independent_from_lesson_model(monkeypatch) -> None:
    monkeypatch.setenv("BOOKCOURSE_RAG_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("BOOKCOURSE_DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setenv("BOOKCOURSE_DEEPSEEK_LESSON_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("BOOKCOURSE_DEEPSEEK_RAG_MODEL", "deepseek-v4-flash")
    get_settings.cache_clear()

    adapter = get_rag_answer_adapter()

    assert isinstance(adapter, DeepSeekRagAnswerAdapter)
    assert adapter.model == "deepseek-v4-flash"


def test_grounded_rag_prompt_obeys_hard_input_budget() -> None:
    citation = _citation().model_copy(update={"quote": "evidence " * 500})

    prompt = build_grounded_prompt("What is Concept A?", [citation], max_input_chars=400)

    assert len(prompt) <= 400
    assert "Question: What is Concept A?" in prompt
