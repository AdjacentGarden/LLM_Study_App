from __future__ import annotations

from typing import Any

from app.core.ai_runtime import get_ai_runtime
from app.core.config import get_settings
from app.core.deepseek import deepseek_payload_extras
from app.core.errors import AppError
from app.schemas.common import AssistantChatRequest, AssistantChatResponse


_SYSTEM_PROMPT = (
    "You are CloudPath, a careful and encouraging study assistant. "
    "Answer in the student's language. Be concise but useful, explain reasoning step by step when appropriate, "
    "and distinguish facts from uncertainty. Never invent textbook quotations, page numbers, or citations. "
    "If the student asks for medical, legal, or financial decisions, provide only general educational information "
    "and recommend a qualified professional."
)


def _provider_configuration() -> tuple[str, str, str, str, dict[str, Any]]:
    settings = get_settings()
    if settings.llm_provider == "deepseek":
        if not settings.deepseek_api_key:
            raise AppError(
                "assistant_llm_not_configured",
                "DeepSeek 云端模型尚未配置",
                status_code=503,
            )
        return (
            "deepseek",
            settings.deepseek_api_url,
            settings.deepseek_api_key,
            settings.deepseek_model,
            deepseek_payload_extras(settings.deepseek_thinking),
        )
    if settings.llm_provider == "openai_compatible":
        if not settings.llm_api_url or not settings.llm_api_key:
            raise AppError(
                "assistant_llm_not_configured",
                "云端大模型尚未配置",
                status_code=503,
            )
        return (
            "openai_compatible",
            settings.llm_api_url,
            settings.llm_api_key,
            settings.llm_model,
            {},
        )
    raise AppError(
        "assistant_llm_not_configured",
        "当前是模板模式，未连接真实云端大模型",
        status_code=503,
    )


def chat_with_assistant(payload: AssistantChatRequest) -> AssistantChatResponse:
    settings = get_settings()
    provider, api_url, api_key, model, extra_payload = _provider_configuration()
    messages: list[dict[str, str]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if payload.context_title or payload.context_description:
        context = "\n".join(
            part
            for part in (
                f"Current app context: {payload.context_title}" if payload.context_title else "",
                payload.context_description or "",
            )
            if part
        )
        messages.append({"role": "system", "content": context})
    messages.extend({"role": item.role, "content": item.content} for item in payload.history[-8:])
    messages.append({"role": "user", "content": payload.message})
    request_payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": min(settings.llm_max_output_tokens, 1600),
        "temperature": 0.3,
        "stream": False,
    }
    request_payload.update(extra_payload)
    body = get_ai_runtime(provider).post_json(
        api_url=api_url,
        api_key=api_key,
        payload=request_payload,
        timeout_seconds=settings.llm_timeout_seconds,
        error_prefix="assistant_llm",
    )
    choices = body.get("choices")
    content = (
        choices[0].get("message", {}).get("content")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict)
        else None
    )
    if not isinstance(content, str) or not content.strip():
        raise AppError(
            "assistant_llm_empty_response",
            "云端大模型返回了空回答",
            status_code=502,
        )
    return AssistantChatResponse(answer=content.strip(), provider=provider, model=model)
