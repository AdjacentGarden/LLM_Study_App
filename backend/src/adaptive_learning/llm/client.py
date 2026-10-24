from __future__ import annotations

import base64
import json
import math
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from .model_admission import AdmissionDenied, admission, overload_wait, route_key


class LLMError(RuntimeError):
    pass


class LLMCooldownError(LLMError):
    """No inference was sent because this configured route is cooling down."""

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__("configured model route is temporarily cooling down")
        self.retry_after_seconds = retry_after_seconds


class LLMRefusalError(LLMError):
    """The provider explicitly refused the content, not an authentication failure."""


class LLMTimeoutError(LLMError):
    """An upstream timeout or the shared generation deadline was exhausted."""

    _draining_future: Future[dict[str, Any]] | None = None


class LLMOutputLimitError(LLMError):
    """The provider exhausted the output budget before completing the response."""


# A timed-out synchronous transport can still be draining a socket. Keep its slot
# until it exits so deadlines never create an unbounded pool of abandoned calls.
_transport_slots = threading.BoundedSemaphore(8)


def _request_before_deadline(call: Callable[[], dict[str, Any]], until: float | None) -> dict[str, Any]:
    slots = _transport_slots
    if until is None:
        # The same process-wide cap applies without a whole-operation deadline.
        with slots:
            return call()
    remaining = until - time.monotonic()
    if remaining <= 0 or not slots.acquire(timeout=max(0, remaining)):
        raise LLMTimeoutError("model time budget exceeded")
    if time.monotonic() >= until:
        slots.release()
        raise LLMTimeoutError("model time budget exceeded")
    future: Future[dict[str, Any]] = Future()

    def run() -> None:
        try:
            if time.monotonic() >= until:
                raise LLMTimeoutError("model time budget exceeded")
            future.set_result(call())
        except BaseException as error:
            future.set_exception(error)
        finally:
            slots.release()

    try:
        threading.Thread(target=run, name="bounded-model-request", daemon=True).start()
    except BaseException:
        slots.release()
        raise
    try:
        return future.result(timeout=max(0, until - time.monotonic()))
    except FutureTimeout as error:
        timeout_error = LLMTimeoutError("model time budget exceeded")
        timeout_error._draining_future = future
        raise timeout_error from error


_deadline: ContextVar[float | None] = ContextVar("model_deadline", default=None)
_block_ambiguous_retries: ContextVar[bool] = ContextVar("block_ambiguous_model_retries", default=False)


@contextmanager
def no_ambiguous_model_retries() -> Iterator[None]:
    """Let a durable submission owner reconcile an uncertain call before replay.

    This is scoped to the calling task. Explicit retryable HTTP replies retain
    their existing handling; a lost transport or unusable body cannot initiate
    another paid submission inside this context.
    """
    token = _block_ambiguous_retries.set(True)
    try:
        yield
    finally:
        _block_ambiguous_retries.reset(token)


def remaining_model_time() -> float | None:
    """Seconds left in the shared deadline, including time spent in a queue."""
    until = _deadline.get()
    return None if until is None else max(0.0, until - time.monotonic())


@contextmanager
def model_time_budget(seconds: float) -> Iterator[None]:
    """Carry one remaining-time budget across planning, answering, review and retries."""
    existing = _deadline.get()
    until = time.monotonic() + seconds
    token = _deadline.set(min(existing, until) if existing is not None else until)
    try:
        yield
    finally:
        _deadline.reset(token)


def _retry_after_delay(headers: Mapping[str, str], body: object) -> float | None:
    """Read a bounded server delay only after an existing retryable HTTP error."""
    def seconds(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        try:
            delay = float(value)
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(delay) or delay < 0:
            return None
        return min(delay, 30.0)

    header = headers.get("Retry-After", headers.get("retry-after"))
    if isinstance(header, str):
        delay = seconds(header)
        if delay is not None:
            return delay
        try:
            date = parsedate_to_datetime(header)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            return seconds(max(0.0, date.timestamp() - time.time()))
        except (OverflowError, TypeError, ValueError):
            pass
    if isinstance(body, dict):
        error = body.get("error")
        values = [error.get("retry_after_seconds")] if isinstance(error, dict) else []
        values.append(body.get("retry_after_seconds"))
        for value in values:
            delay = seconds(value)
            if delay is not None:
                return delay
    return None


def _known_overload_wait(error: BaseException) -> float | None:
    """Classify only an actual final HTTP 429/503, never a timeout or parse error."""
    cause: BaseException | None = error
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        response = getattr(cause, "response", None)
        if isinstance(cause, (httpx.HTTPStatusError, APIStatusError)):
            status = response.status_code if response is not None else None
            if status not in {429, 503}:
                return None
            body = getattr(cause, "body", None)
            if body is None and response is not None:
                try:
                    body = response.json()
                except (ValueError, TypeError):
                    pass
            headers = response.headers if response is not None else {}
            return overload_wait(headers, body, time.time())
        cause = cause.__cause__
    return None


def _draining_request(error: BaseException) -> Future[dict[str, Any]] | None:
    cause: BaseException | None = error
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, LLMTimeoutError) and cause._draining_future is not None:
            return cause._draining_future
        cause = cause.__cause__
    return None


@dataclass(frozen=True, slots=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int = 60
    max_retries: int = 1
    proxy_url: str | None = None
    thinking_enabled: bool | None = None
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative")


class OpenAICompatibleClient:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._usage_lock = threading.Lock()
        self._usage = {"responses": 0, "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
        self._http = httpx.Client(
            timeout=httpx.Timeout(config.timeout_seconds, connect=10, pool=10),
            # Each idle CONNECT tunnel occupies a Windows relay slot. Book
            # analysis and teaching use distinct clients, so retain only a small
            # idle pool while preserving eight concurrent active requests.
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=2, keepalive_expiry=5),
            proxy=config.proxy_url,
            # Model traffic follows LLM_HTTPS_PROXY, independently of shell/git proxies.
            trust_env=False,
        )
        self._responses_sdk = (
            OpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                http_client=self._http,
                max_retries=0,
            )
            if "pucoding.com" in config.base_url.rstrip("/")
            else None
        )

    def close(self) -> None:
        self._http.close()

    def usage_totals(self) -> dict[str, int]:
        with self._usage_lock:
            return dict(self._usage)

    def _record_usage(self, body: dict[str, Any]) -> None:
        usage = body.get("usage")
        if not isinstance(usage, dict):
            return
        details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        values = {
            "responses": 1,
            "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)),
            "output_tokens": usage.get("output_tokens", usage.get("completion_tokens", 0)),
            "cached_input_tokens": usage.get("cache_read_input_tokens", details.get("cached_tokens", 0)),
        }
        with self._usage_lock:
            for key, value in values.items():
                if isinstance(value, int) and value >= 0:
                    self._usage[key] += value

    def check_admission(self) -> None:
        """No-cost prerequisite check; never a paid health probe."""
        try:
            admission.check(route_key(self.config.base_url, self.config.model, self.config.api_key))
        except AdmissionDenied as error:
            raise LLMCooldownError(error.retry_after_seconds) from error

    def structured(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0,
        max_tokens: int = 4096,
        images: list[tuple[str, bytes]] | None = None,
    ) -> dict[str, Any]:
        key = route_key(self.config.base_url, self.config.model, self.config.api_key)
        gate = admission
        until = _deadline.get()
        admission_wait = min(
            self.config.timeout_seconds,
            max(0.0, until - time.monotonic()) if until is not None else self.config.timeout_seconds,
        )
        try:
            lease = gate.begin(key, admission_wait)
        except AdmissionDenied as error:
            raise LLMCooldownError(error.retry_after_seconds) from error
        try:
            result = self._structured_transport(
                system=system, user=user, temperature=temperature, max_tokens=max_tokens, images=images
            )
        except BaseException as error:
            draining = _draining_request(error)
            if draining is not None:
                def finish_drained(done: Future[dict[str, Any]]) -> None:
                    try:
                        late_error = done.exception()
                    except BaseException as completion_error:
                        late_error = completion_error
                    # A late body is never parsed, returned, or marked healthy.
                    # A late explicit 429/503 may update known cooldown only.
                    wait_for = _known_overload_wait(late_error) if late_error else None
                    gate.finish(lease, overload=wait_for)

                draining.add_done_callback(finish_drained)
            else:
                gate.finish(lease, overload=_known_overload_wait(error))
            raise
        gate.finish(lease, completed=True)
        return result

    def _structured_transport(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0,
        max_tokens: int = 4096,
        images: list[tuple[str, bytes]] | None = None,
    ) -> dict[str, Any]:
        if not self.config.api_key:
            raise LLMError("LLM API key is not configured")
        payload: dict[str, Any] = {
            "model": self.config.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        base_url = self.config.base_url.rstrip("/")
        if (
            urlsplit(base_url).hostname == "api.deepseek.com"
            and self.config.model.startswith("deepseek-")
        ):
            # Keep reasoning enabled at the requested low effort. Thinking shares
            # max_tokens with the final JSON, so callers must budget for both.
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = "low"
        endpoint = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        anthropic = self.config.model.startswith("claude-")
        # PUCODING exposes its current GPT/Codex catalogue through the Responses
        # API only. Keep ordinary OpenAI-compatible relays on chat/completions,
        # while routing this gateway (and native Grok response models) correctly.
        responses = self.config.model.startswith("grok-") or (
            "pucoding.com" in base_url
            and self.config.model.startswith(("gpt-", "codex-"))
        )
        if anthropic:
            endpoint = f"{base_url}/messages"
            headers.update({"x-api-key": self.config.api_key, "anthropic-version": "2023-06-01"})
            parts: list[dict[str, Any]] = [
                {"type": "image", "source": {"type": "base64", "media_type": mime,
                 "data": base64.b64encode(data).decode("ascii")}}
                for mime, data in images or []
            ]
            parts.append({"type": "text", "text": user})
            payload = {
                "model": self.config.model, "temperature": temperature,
                "max_tokens": max_tokens, "stream": False,
                "system": system + "\nReturn exactly one JSON object. No commentary or Markdown fences.",
                "messages": [{"role": "user", "content": parts}],
            }
        elif responses:
            endpoint = f"{base_url}/responses"
            response_parts: list[dict[str, Any]] = [{"type": "input_text", "text": user}]
            response_parts.extend(
                {"type": "input_image", "detail": "high", "image_url":
                    f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}
                for mime, data in images or []
            )
            payload = {
                "model": self.config.model,
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": system}]},
                    {"role": "user", "content": response_parts},
                ],
                "max_output_tokens": max_tokens, "stream": False, "store": False,
                "reasoning": {"effort": self.config.reasoning_effort or "low"},
                "text": {"format": {"type": "json_object"}},
            }
        elif images:
            payload["messages"][1]["content"] = [
                {"type": "image_url", "image_url": {
                    "url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}}
                for mime, data in images
            ] + [{"type": "text", "text": user}]
        if not anthropic and not responses:
            # These are explicit Chat Completions settings: providers differ in
            # supported values, so do not infer them from a provider/model name.
            if self.config.thinking_enabled is not None:
                payload["thinking"] = {
                    "type": "enabled" if self.config.thinking_enabled else "disabled"
                }
            if self.config.reasoning_effort is not None:
                payload["reasoning_effort"] = self.config.reasoning_effort
        last_error: Exception | None = None
        upstream_detail = ""
        for attempt in range(self.config.max_retries + 1):
            until = _deadline.get()
            remaining = (
                min(self.config.timeout_seconds, until - time.monotonic())
                if until is not None
                else self.config.timeout_seconds
            )
            if remaining <= 0:
                raise LLMTimeoutError("model time budget exceeded")
            retry_after: float | None = None
            try:
                timeout = httpx.Timeout(
                    remaining, connect=min(10, remaining), pool=min(10, remaining)
                )
                def send(timeout: httpx.Timeout = timeout) -> dict[str, Any]:
                    if responses and self._responses_sdk is not None:
                        # This gateway explicitly accepts the official OpenAI SDK for
                        # Responses calls. Its result is normalised back to the same
                        # dictionary contract used by the other compatible providers.
                        sdk_response = self._responses_sdk.responses.create(
                            **payload, timeout=timeout
                        )
                        body = sdk_response.model_dump(mode="json")
                    else:
                        response = self._http.post(
                            endpoint,
                            headers=headers,
                            json=payload,
                            timeout=timeout,
                        )
                        response.raise_for_status()
                        body = response.json()
                    if not isinstance(body, dict):
                        raise ValueError("model response must be a JSON object")
                    return body

                body = _request_before_deadline(send, until)
                if until is not None and time.monotonic() >= until:
                    raise LLMTimeoutError("model time budget exceeded")
                self._record_usage(body)
                if anthropic:
                    if body.get("stop_reason") == "refusal":
                        raise LLMRefusalError("model refused the request")
                    if body.get("stop_reason") == "max_tokens":
                        raise ValueError("model output is incomplete or refused")
                    content = "".join(
                        part["text"] for part in body["content"] if part.get("type") == "text"
                    )
                elif responses:
                    if any(
                        part.get("type") == "refusal"
                        for item in body.get("output", []) if item.get("type") == "message"
                        for part in item.get("content", [])
                    ):
                        raise LLMRefusalError("model refused the request")
                    if body.get("status") != "completed" or body.get("error"):
                        raise ValueError("model response is incomplete or failed")
                    content = "".join(
                        part["text"] for item in body["output"] if item.get("type") == "message"
                        for part in item.get("content", []) if part.get("type") == "output_text"
                    )
                else:
                    choice = body["choices"][0]
                    if choice.get("finish_reason") == "content_filter" or choice["message"].get("refusal"):
                        raise LLMRefusalError("model refused the request")
                    if choice.get("finish_reason") == "length":
                        # Even syntactically valid JSON can describe a partial
                        # answer. Do not accept it or replay the same capped call.
                        raise LLMOutputLimitError("model output reached max_tokens before completion")
                    content = choice["message"]["content"]
                return self._parse_object(content)
            except httpx.HTTPStatusError as error:
                last_error = error
                status = error.response.status_code
                raw_error: object = {}
                error_body: object = None
                try:
                    error_body = error.response.json()
                    raw_error = error_body.get("error", {}) if isinstance(error_body, dict) else {}
                    message = raw_error.get("message", "") if isinstance(raw_error, dict) else ""
                    if isinstance(message, str):
                        upstream_detail = message.replace(self.config.api_key, "[redacted]")[:240]
                except (ValueError, AttributeError, TypeError):
                    upstream_detail = ""
                if status in {400, 403, 422} and self._content_refusal(raw_error):
                    raise LLMRefusalError("provider declined this content") from error
                retryable = status == 429 or status >= 500
                if not retryable or attempt >= self.config.max_retries:
                    break
                retry_after = _retry_after_delay(
                    getattr(error.response, "headers", {}), error_body
                )
            except APIStatusError as error:
                last_error = error
                status = error.status_code
                raw_error = error.body.get("error", error.body) if isinstance(error.body, dict) else {}
                message = raw_error.get("message", "") if isinstance(raw_error, dict) else ""
                if isinstance(message, str):
                    upstream_detail = message.replace(self.config.api_key, "[redacted]")[:240]
                if status in {400, 403, 422} and self._content_refusal(raw_error):
                    raise LLMRefusalError("provider declined this content") from error
                retryable = status == 429 or status >= 500
                if not retryable or attempt >= self.config.max_retries:
                    break
                retry_after = _retry_after_delay(error.response.headers, error.body)
            except (
                APITimeoutError,
                APIConnectionError,
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.ProxyError,
                httpx.RemoteProtocolError,
                httpx.PoolTimeout,
                KeyError,
                IndexError,
                AttributeError,
                TypeError,
                ValueError,
            ) as error:
                last_error = error
                if _block_ambiguous_retries.get() or attempt >= self.config.max_retries:
                    break
            delay = retry_after if retry_after is not None else 0.25 * (attempt + 1)
            if until is not None:
                remaining = until - time.monotonic()
                if remaining <= delay:
                    raise LLMTimeoutError("model time budget exceeded") from last_error
            time.sleep(delay)
        assert last_error is not None
        if isinstance(last_error, (httpx.TimeoutException, APITimeoutError)):
            raise LLMTimeoutError("model request timed out") from last_error
        detail = f" ({upstream_detail})" if upstream_detail else ""
        raise LLMError(
            f"structured model call failed: {type(last_error).__name__}{detail}"
        ) from last_error

    @staticmethod
    def _content_refusal(error: object) -> bool:
        if not isinstance(error, dict):
            return False
        code = str(error.get("code", "")).lower()
        if code in {"content_filter", "content_policy_violation", "safety_violation", "moderation_blocked"}:
            return True
        message = str(error.get("message", "")).lower()
        return any(marker in message for marker in (
            "content policy", "content safety", "content filter", "safety policy",
            "内容安全", "内容审核", "内容政策",
        ))

    @staticmethod
    def _parse_object(content: str) -> dict[str, Any]:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("model returned empty or non-text content")
        text = content.strip()
        # Reasoning-capable gateways may expose a private-thought prelude even when
        # JSON mode is requested. It is never returned to callers or treated as data.
        text = re.sub(r"^<think>.*?</think>\s*", "", text, flags=re.DOTALL | re.IGNORECASE)
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1)
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("expected JSON object")
        return value
