from __future__ import annotations

from typing import Any, Protocol
import re

from app.core.ai_runtime import AIProviderRuntime, get_ai_runtime, policy_from_settings
from app.core.config import get_settings
from app.core.deepseek import deepseek_payload_extras
from app.core.errors import AppError
from app.schemas.books import Citation, RagHistoryMessage


class RagAnswerAdapter(Protocol):
    name: str

    def answer(
        self,
        question: str,
        citations: list[Citation],
        history: list[RagHistoryMessage] | None = None,
    ) -> tuple[str, str]:
        ...


class TemplateRagAnswerAdapter:
    name = "template"

    def answer(
        self,
        question: str,
        citations: list[Citation],
        history: list[RagHistoryMessage] | None = None,
    ) -> tuple[str, str]:
        prompt = build_grounded_prompt(question, citations, history=history)
        if not citations:
            return (
                "\u539f\u4e66\u4e2d\u672a\u627e\u5230\u660e\u786e\u8bf4\u660e\u3002\u5efa\u8bae\u6269\u5927\u7ae0\u8282\u8303\u56f4\uff0c\u6216\u5728 OCR \u5b8c\u6210\u540e\u518d\u6b21\u68c0\u7d22\u3002",
                prompt,
            )
        sentences = [
            sentence.strip()
            for citation in citations
            for sentence in re.split(r"(?<=[。！？.!?])\s+|\n+", citation.quote)
            if sentence.strip()
        ]
        query_terms = set(re.findall(r"[A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", question.lower()))
        best = max(
            sentences,
            key=lambda sentence: len(
                query_terms.intersection(
                    set(re.findall(r"[A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", sentence.lower()))
                )
            ),
            default=citations[0].quote,
        )
        location = citations[0].location_label or f"第 {citations[0].page} 页"
        answer = f"教材原文指出：{best}（{citations[0].chapter_title}，{location}）"
        return answer, prompt


def _render_history(history: list[RagHistoryMessage] | None, max_chars: int = 2400) -> str:
    if not history:
        return ""
    lines: list[str] = []
    remaining = max_chars
    for item in history[-6:]:
        label = "Student" if item.role == "user" else "Tutor"
        content = " ".join(item.content.split())
        rendered = f"{label}: {content}"
        if len(rendered) > remaining:
            rendered = rendered[:remaining]
        if rendered:
            lines.append(rendered)
            remaining -= len(rendered) + 1
        if remaining <= 0:
            break
    return "\n".join(lines)


def build_grounded_prompt(
    question: str,
    citations: list[Citation],
    *,
    history: list[RagHistoryMessage] | None = None,
    max_input_chars: int | None = None,
) -> str:
    rendered_history = _render_history(history)
    history_section = f"Conversation so far:\n{rendered_history}\n" if rendered_history else ""
    prefix = (
        "Answer the student only with the retrieved textbook contexts below. "
        "If the contexts do not contain the answer, say the textbook source is insufficient. "
        "Always cite chapter, page and chunk id.\n"
        f"{history_section}Question: {question}\nContexts:\n"
    )
    remaining = max_input_chars - len(prefix) if max_input_chars is not None else None
    contexts: list[str] = []
    for index, item in enumerate(citations):
        rendered = (
            f"[{index + 1}] chapter={item.chapter_id} page={item.page} "
            f"chunk={item.chunk_id} score={item.score:.4f}: {item.quote}"
        )
        separator_cost = 1 if contexts else 0
        if remaining is not None and remaining <= separator_cost:
            break
        if remaining is not None and len(rendered) + separator_cost > remaining:
            rendered = rendered[: max(0, remaining - separator_cost)]
        contexts.append(rendered)
        if remaining is not None:
            remaining -= len(rendered) + separator_cost
            if remaining <= 0:
                break
    prompt = prefix + "\n".join(contexts)
    return prompt[:max_input_chars] if max_input_chars is not None else prompt


class OpenAICompatibleRagAnswerAdapter:
    name = "openai_compatible"

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60,
        extra_payload: dict[str, Any] | None = None,
        name: str | None = None,
        runtime: AIProviderRuntime | None = None,
    ) -> None:
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.extra_payload = extra_payload or {}
        if name:
            self.name = name
        self.runtime = runtime or AIProviderRuntime(self.name, policy_from_settings())

    def answer(
        self,
        question: str,
        citations: list[Citation],
        history: list[RagHistoryMessage] | None = None,
    ) -> tuple[str, str]:
        settings = get_settings()
        prompt = build_grounded_prompt(
            question,
            citations,
            history=history,
            max_input_chars=settings.llm_max_input_chars,
        )
        if not citations:
            return TemplateRagAnswerAdapter().answer(question, citations, history)
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a careful textbook tutor. Answer in the user's language. "
                        "Use only the supplied textbook contexts and cite chapter, page, and chunk id."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": settings.llm_max_output_tokens,
            "stream": False,
        }
        payload.update(self.extra_payload)
        cache_key = self.runtime.fingerprint(self.api_url, self.model, payload)
        body = self.runtime.post_json(
            api_url=self.api_url,
            api_key=self.api_key,
            payload=payload,
            timeout_seconds=self.timeout_seconds,
            error_prefix="rag_llm",
            cache_key=cache_key,
        )

        content = body.get("choices", [{}])[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise AppError("rag_llm_empty_response", "RAG LLM provider returned an empty answer", status_code=502)
        return content.strip(), prompt


class DeepSeekRagAnswerAdapter(OpenAICompatibleRagAnswerAdapter):
    name = "deepseek"


def get_rag_answer_adapter() -> RagAnswerAdapter:
    settings = get_settings()
    provider = getattr(settings, "rag_llm_provider", "template")
    if provider == "openai_compatible":
        if not settings.llm_api_url or not settings.llm_api_key:
            raise AppError("rag_llm_not_configured", "RAG LLM API is not configured", status_code=500)
        return OpenAICompatibleRagAnswerAdapter(
            settings.llm_api_url,
            settings.llm_api_key,
            settings.rag_llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            runtime=get_ai_runtime("openai_compatible"),
        )
    if provider == "deepseek":
        if not settings.deepseek_api_key:
            raise AppError("rag_llm_not_configured", "DeepSeek API key is not configured", status_code=500)
        return DeepSeekRagAnswerAdapter(
            settings.deepseek_api_url,
            settings.deepseek_api_key,
            settings.deepseek_rag_model,
            timeout_seconds=settings.llm_timeout_seconds,
            extra_payload=deepseek_payload_extras(settings.deepseek_thinking),
            runtime=get_ai_runtime("deepseek"),
        )
    return TemplateRagAnswerAdapter()
