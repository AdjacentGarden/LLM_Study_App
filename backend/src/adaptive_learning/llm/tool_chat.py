"""One streaming tool-call Chat Completions POST for the assistant gateway.

A call is exactly one POST and never retries. The durable step ledger decides
whether a step may be sent again, and it only does so when the outcome proves
that no model input left this process or the provider explicitly declined it.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from .client import (
    LLMConfig,
    LLMTimeoutError,
    OpenAICompatibleClient,
    _connection_not_submitted,
    _context_limit_error,
    _transport_slots,
)
from .model_admission import AdmissionDenied, admission, overload_wait, route_key
from .tool_stream import (
    AssembledTurn,
    ToolStreamError,
    ToolStreamIncomplete,
    ToolStreamOutputLimit,
    ToolStreamRefused,
    assemble_chat_stream,
    iter_sse_data,
)

OutcomeKind = Literal["completed", "not_submitted", "rejected", "failed", "uncertain"]


@dataclass(frozen=True, slots=True)
class ChatOutcome:
    """``not_submitted`` and ``rejected`` are the only resendable kinds."""

    kind: OutcomeKind
    elapsed: float
    turn: AssembledTurn | None = None
    error: str = ""
    http_status: int | None = None
    retry_after: float | None = None


def supports_tool_chat(config: LLMConfig) -> bool:
    """Only OpenAI-compatible Chat Completions routes stream tool calls here."""
    base_url = config.base_url.rstrip("/")
    return bool(config.api_key) and not (
        config.model.startswith(("claude-", "grok-"))
        or "pucoding.com" in base_url and config.model.startswith(("gpt-", "codex-"))
    )


class ToolChatClient:
    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None) -> None:
        if not supports_tool_chat(config):
            raise ValueError("configured model route does not support streamed tool calls")
        self.config = config
        self._http = httpx.Client(
            timeout=httpx.Timeout(config.timeout_seconds, connect=10, pool=10),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=1, keepalive_expiry=5),
            proxy=config.proxy_url if transport is None else None,
            transport=transport,
            trust_env=False,
        )

    def close(self) -> None:
        self._http.close()

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: Mapping[str, Mapping[str, Any]],
        descriptions: Mapping[str, str],
        max_tokens: int,
        deadline_seconds: float,
    ) -> ChatOutcome:
        started = time.monotonic()
        until = started + deadline_seconds

        def outcome(kind: OutcomeKind, **values: Any) -> ChatOutcome:
            return ChatOutcome(kind, time.monotonic() - started, **values)

        base_url = self.config.base_url.rstrip("/")
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "tools": [
                {"type": "function", "function": {
                    "name": name, "description": descriptions.get(name, ""), "parameters": schema}}
                for name, schema in tools.items()
            ],
            "tool_choice": "auto",
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        if not tools:
            payload.pop("tools", None)
            payload.pop("tool_choice", None)
        if urlsplit(base_url).hostname == "api.deepseek.com":
            # Thinking mode requires replaying private reasoning between tool
            # steps. The gateway never stores it, so keep tool turns nonthinking.
            payload["thinking"] = {"type": "disabled"}
        key = route_key(self.config.base_url, self.config.model, self.config.api_key)
        try:
            lease = admission.begin(key, max(0.0, min(10.0, deadline_seconds / 4)))
        except AdmissionDenied as error:
            return outcome("not_submitted", error="model route is cooling down",
                           retry_after=error.retry_after_seconds)
        if not _transport_slots.acquire(timeout=max(0.0, min(10.0, until - time.monotonic()))):
            admission.finish(lease)
            return outcome("not_submitted", error="model transport is busy")
        overload: float | None = None
        completed = False
        try:
            remaining = max(0.05, until - time.monotonic())
            timeout = httpx.Timeout(remaining, connect=min(10.0, remaining), pool=min(10.0, remaining))
            with self._http.stream(
                "POST", f"{base_url}/chat/completions", json=payload, timeout=timeout,
                headers={"Authorization": f"Bearer {self.config.api_key}"},
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    try:
                        body: object = response.json()
                    except ValueError:
                        body = None
                    status = response.status_code
                    if status in {429, 503}:
                        overload = overload_wait(response.headers, body, time.time())
                        return outcome("rejected", error=f"provider returned HTTP {status}",
                                       http_status=status, retry_after=overload)
                    detail = body.get("error", body) if isinstance(body, dict) else {}
                    if status in {400, 413, 422} and _context_limit_error(body):
                        reason = "model input exceeds context window"
                    elif status in {400, 403, 422} and OpenAICompatibleClient._content_refusal(detail):
                        reason = "provider declined this content"
                    else:
                        reason = f"provider returned HTTP {status}"
                    return outcome("failed", error=reason, http_status=status)

                def bounded() -> Iterator[str]:
                    for line in response.iter_lines():
                        if time.monotonic() >= until:
                            raise LLMTimeoutError("model time budget exceeded")
                        yield line

                turn = assemble_chat_stream(iter_sse_data(bounded()), tools)
                completed = True
                return outcome("completed", turn=turn)
        except ToolStreamOutputLimit:
            return outcome("failed", error="model output reached max_tokens")
        except ToolStreamRefused:
            return outcome("failed", error="model refused the request")
        except ToolStreamIncomplete:
            # The provider processed the input; a dropped stream is not a refusal.
            return outcome("uncertain", error="model stream ended before completion")
        except ToolStreamError as error:
            return outcome("failed", error=f"invalid model output: {error}"[:300])
        except LLMTimeoutError:
            return outcome("uncertain", error="model time budget exceeded")
        except Exception as error:
            if _connection_not_submitted(error):
                return outcome("not_submitted", error="model connection failed before submission")
            return outcome("uncertain", error=f"model request outcome unknown: {type(error).__name__}")
        finally:
            _transport_slots.release()
            admission.finish(lease, overload=overload, completed=completed)
