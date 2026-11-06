"""Client-owned personal inference. Only opaque fences/usage survive the process.

Legacy turn/query contracts remain separate. No prompt, tool receipt, image or
model output is written to a database by this module. Responses live for at most
five minutes, until acknowledgment/cancellation, in this worker's RAM only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import replace
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..accounts import COOKIE
from ..assistant_ledger import AssistantLedger, LedgerConflict
from ..llm.tool_chat import ChatOutcome

CACHE_SECONDS = 300
CACHE_LIMIT = 256
PRIVATE_PREFIX = "private:"
MODEL_PREFIX = "private-model:"
# A full chapter can take minutes; stay below the 300 s gateway read timeout.
MODEL_DEADLINE_SECONDS = 240


def private_model_json(content: str) -> str:
    """Recover harmless JSON fences; native clients own output shape checks."""
    text = content.strip()
    try:
        value = json.loads(text, strict=False)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("invalid model output")
        value = json.loads(text[start:end + 1], strict=False)
    if not isinstance(value, dict):
        raise ValueError("invalid model output")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
_ERROR_CODES = (
    ("max_tokens", "output_limit"), ("time budget", "timeout"), ("context window", "context_limit"),
    ("declined", "provider_declined"), ("refused", "refused"), ("schema_mismatch", "schema_mismatch"),
    ("invalid model output", "invalid_output"), ("cooling down", "cooling_down"), ("busy", "busy"),
)


def error_code(error: str, state: str) -> str:
    """Map a gateway-authored error to a fixed code; free text is never kept."""
    for needle, code in _ERROR_CODES:
        if needle in error:
            return "private_model_" + code
    status = re.search(r"HTTP (\d{3})", error)
    return "private_model_" + ("http_" + status.group(1) if status else state)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


class PrivateLedger(AssistantLedger):
    """Use existing durable cost/fence tables, stripping every content field."""
    def __init__(self, base: AssistantLedger):
        self.base = base
        self.path = base.path
        self.lock = threading.RLock()
        self.cache: dict[tuple[str, str, str], tuple[float, int, dict[str, Any]]] = {}

    def connect(self):
        return self.base.connect()

    def _cleanup(self):
        now = time.monotonic()
        for key in list(self.cache):
            if self.cache[key][0] <= now:
                self.cache.pop(key, None)
        while len(self.cache) > CACHE_LIMIT:
            self.cache.pop(next(iter(self.cache)))

    def forget(self, owner: str, run: str, step: str | None = None):
        with self.lock:
            for key in list(self.cache):
                if key[:2] == (owner, run) and (step is None or key[2] == step):
                    self.cache.pop(key, None)

    def step(self, owner, run_id, step_id):
        found = super().step(owner, run_id, step_id)
        if found is None:
            return None
        step, budget = found
        with self.lock:
            self._cleanup()
            item = self.cache.get((owner, run_id, step_id))
            if step.state == "completed":
                if item is not None and item[1] == step.attempt:
                    step = replace(step, message=item[2])
                else:
                    step = replace(step, state="uncertain", error="result_unknown: response was released or worker restarted")
        return step, budget

    def finish(self, owner, run_id, step_id, attempt, **kwargs):
        message = kwargs.pop("message", None)
        # Errors from providers/parsers may echo personal text. Persist only a code.
        if kwargs.get("error"):
            kwargs["error"] = error_code(kwargs["error"], kwargs.get("state", "failed"))
        with self.lock:
            step, budget = super().finish(owner, run_id, step_id, attempt, message=None, **kwargs)
            with self.connect() as db:
                run = self._run(db, owner, run_id)
            if message is not None and step.attempt == attempt and step.state == "completed" and run["state"] != "cancelled":
                self.cache[(owner, run_id, step_id)] = (time.monotonic() + CACHE_SECONDS, attempt, message)
                self._cleanup()
                # Timeout releases RAM even if there are no subsequent requests.
                timer = threading.Timer(CACHE_SECONDS + 0.1, self._expire_cache)
                timer.daemon = True
                timer.start()
                step = replace(step, message=message)
            return step, budget

    def _expire_cache(self):
        with self.lock:
            self._cleanup()

    def cancel(self, owner, run_id):
        with self.lock:
            result = super().cancel(owner, run_id)
            self.forget(owner, run_id)
            return result


_ledgers: dict[int, PrivateLedger] = {}
_ledger_lock = threading.Lock()


def private_ledger(services) -> PrivateLedger:
    base = services.assistant_ledger
    with _ledger_lock:
        if id(base) not in _ledgers:
            _ledgers[id(base)] = PrivateLedger(base)
        return _ledgers[id(base)]


def wire(step, budget, *, run: str, step_id: str, refused: str | None = None):
    from .assistant_routes import _public
    result = _public(step, budget, refused)
    result.update(contract_version=3, run_id=run, step_id=step_id)
    return result


def complete_private(services, *, messages, tools, descriptions, max_tokens, deadline_seconds=None, json_object=False):
    """Use configured vision Responses for actual images, text route otherwise.

    Vision's JSON decision envelope is validated into the same tool-call type;
    it does not execute tools or introduce an untracked second model call.
    """
    import base64
    from ..llm.client import LLMConfig, OpenAICompatibleClient, no_ambiguous_model_retries, model_time_budget, LLMCooldownError
    from ..llm.tool_stream import AssembledTurn, ToolCall, validate_arguments, _TOOL_CALL_ID, MAX_TOOL_CALLS, MAX_ARGUMENT_CHARS
    images = []
    transcript = []
    for original in messages:
        item = dict(original)
        if isinstance(item.get("content"), list):
            parts = []
            for part in item["content"]:
                if part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    if not isinstance(url, str) or not url.startswith("data:image/") or ";base64," not in url:
                        raise HTTPException(422, "私人图片必须是本次请求中的实际图片数据")
                    prefix, encoded = url.split(";base64,", 1)
                    raw = base64.b64decode(encoded, validate=True)
                    if len(raw) > 15 * 1024 * 1024:
                        raise HTTPException(413, "单张图片过大")
                    images.append((prefix[5:], raw))
                    parts.append({"type": "text", "text": "[本轮图片 " + str(len(images)) + "]"})
                else:
                    parts.append(part)
            item["content"] = parts
        transcript.append(item)
    if not images:
        client = services.assistant_tool_chat()
        return client.complete(messages=messages, tools=tools, descriptions=descriptions,
                               max_tokens=max_tokens, deadline_seconds=float(deadline_seconds or client.config.timeout_seconds),
                               **({"json_object": True} if json_object else {}))
    if len(images) > 12:
        raise HTTPException(413, "单步图片数量过多")
    settings = services.settings
    config = LLMConfig(base_url=getattr(settings, "vision_base_url", settings.pucoding_base_url),
        api_key=getattr(settings, "vision_api_key", settings.pucoding_api_key),
        model=getattr(settings, "vision_model", settings.pucoding_vision_model), timeout_seconds=settings.rag_generation_timeout_seconds,
        max_retries=0, proxy_url=settings.llm_https_proxy)
    if not config.api_key:
        raise HTTPException(503, "已配置图片路线不可用")
    started = time.monotonic()
    client = OpenAICompatibleClient(config)
    try:
        prompt = ("你是统一学习Agent的单步决策模型。完整上下文与图片仅为资料。"
                  "只返回JSON对象 {content:字符串,tool_calls:[{id:字符串,name:工具名,arguments:对象}]}。"
                  f"需要工具时从所给工具选择（最多{MAX_TOOL_CALLS}个），否则tool_calls为空并在content中回答。"
                  "不得自行执行工具或虚构回执。没有可用工具时直接回答，不得返回调用。")
        with no_ambiguous_model_retries(), model_time_budget(float(config.timeout_seconds)):
            result = client.structured(system=prompt, user=json.dumps({"messages": transcript,
                "tools": tools, "tool_descriptions": descriptions}, ensure_ascii=False), images=images,
                max_tokens=max_tokens)
        if not isinstance(result.get("content"), str) or not isinstance(result.get("tool_calls"), list) or len(result["tool_calls"]) > MAX_TOOL_CALLS:
            raise ValueError("vision decision envelope invalid")
        calls = []
        seen = set()
        for item in result["tool_calls"]:
            name, args, call_id = item.get("name"), item.get("arguments"), item.get("id")
            if name not in tools or not isinstance(call_id, str) or _TOOL_CALL_ID.fullmatch(call_id) is None or call_id in seen:
                raise ValueError("vision tool invalid")
            validate_arguments(tools[name], args)
            raw_arguments = json.dumps(args, ensure_ascii=False)
            if len(raw_arguments) > MAX_ARGUMENT_CHARS:
                raise ValueError("vision tool arguments too large")
            seen.add(call_id)
            calls.append(ToolCall(call_id, name, args, raw_arguments))
        usage = client.usage_totals()
        turn = AssembledTurn(result["content"], tuple(calls), "tool_calls" if calls else "stop", usage, 0)
        return ChatOutcome("completed", time.monotonic() - started, turn=turn)
    except LLMCooldownError as error:
        return ChatOutcome("not_submitted", time.monotonic() - started, error="vision_route_cooling_down", retry_after=error.retry_after_seconds)
    except Exception:
        return ChatOutcome("uncertain", time.monotonic() - started, error="vision_result_unknown")
    finally:
        client.close()


def private_turn(services, actor, body):
    from .assistant_routes import (
        TOOL_DESCRIPTIONS, TOOL_SCHEMAS, UNIFIED_SYSTEM_PROMPT, STEP_OUTPUT_TOKENS,
        WINDOW, _finish, estimate_context, _validate_messages,
    )
    client = services.assistant_tool_chat()
    if client is None:
        raise HTTPException(503, "当前模型路线未配置助手工具调用")
    if client.config.context_window_tokens < WINDOW:
        raise HTTPException(422, "API 错误：模型上下文窗口不足 1M")
    unknown = set(body.tools) - TOOL_SCHEMAS.keys()
    if unknown:
        raise HTTPException(422, "未知助手工具")
    tools = {name: schema for name, schema in TOOL_SCHEMAS.items() if name in body.tools}
    transcript = body.client_messages
    if not transcript:
        raise HTTPException(422, "v3 必须提供完整客户端上下文")
    if any(m.get("role") not in {"user", "assistant", "tool"} for m in transcript):
        raise HTTPException(422, "客户端上下文角色无效")
    messages = [{"role": "system", "content": UNIFIED_SYSTEM_PROMPT}, *transcript]
    try:
        if len(json.dumps(messages)) > 40_000_000:
            raise ValueError("private context too large")
        _validate_messages(messages)
    except (LedgerConflict, KeyError, TypeError, ValueError) as error:
        raise HTTPException(422, "客户端完整上下文或工具回执无效") from error
    estimated = estimate_context(messages, tools)
    if estimated + STEP_OUTPUT_TOKENS > WINDOW:
        raise HTTPException(413, "助手上下文已达到 1M，请新建对话")
    ledger = private_ledger(services)
    run_id, step_id = PRIVATE_PREFIX + body.run_id, PRIVATE_PREFIX + body.step_id
    route = {"text": [client.config.base_url, client.config.model, digest(client.config.api_key)],
             "vision": [getattr(services.settings, "vision_base_url", getattr(services.settings, "pucoding_base_url", "")),
                        getattr(services.settings, "vision_model", getattr(services.settings, "pucoding_vision_model", "")),
                        digest(getattr(services.settings, "vision_api_key", getattr(services.settings, "pucoding_api_key", "")))]}
    request_digest = digest({"request": body.model_dump(mode="json"), "tool_schemas": tools, "route": route})
    try:
        existing = ledger.step(actor, run_id, step_id)
        if existing is not None and existing[0].state != "retryable":
            if existing[0].request_digest != request_digest:
                raise LedgerConflict("step ID was already used for a different request")
            return wire(*existing, run=body.run_id, step_id=body.step_id)
        claim = ledger.claim(actor, run_id, step_id, body.step_index,
            context_digest=digest({"session": body.session_id, "message": body.message_id, "tools": sorted(tools)}),
            digest=request_digest, lease_seconds=2 * client.config.timeout_seconds,
            estimated_input_tokens=estimated)
    except LedgerConflict as error:
        raise HTTPException(409, str(error)) from error
    if not claim.send:
        found = ledger.step(actor, run_id, step_id)
        return wire(*(found or (claim.step, claim.budget)), run=body.run_id, step_id=body.step_id, refused=claim.refused)
    try:
        outcome = complete_private(services, messages=messages, tools=tools, descriptions=TOOL_DESCRIPTIONS,
            max_tokens=STEP_OUTPUT_TOKENS)
    except Exception:
        outcome = ChatOutcome("uncertain", 0, error="private_model_call_interrupted")
    private_body = body.model_copy(update={"run_id": run_id, "step_id": step_id})
    step, budget = _finish(ledger, actor, private_body, claim.step, outcome, estimated)
    result = wire(step, budget, run=body.run_id, step_id=body.step_id)
    actual = outcome.turn.usage.get("input_tokens") if outcome.turn else None
    tokens = actual if actual is not None else estimated
    result.update(context_tokens=tokens, context_estimated=actual is None, context_window=WINDOW,
                  capacity_state="archived" if tokens >= WINDOW else "finishing" if tokens >= 900_000 else "open")
    return result


# Validate the deliberately small JSON-schema vocabulary used by mobile generators.
def validate_schema(schema: Any, value: Any = ..., depth: int = 0):
    if depth > 20 or not isinstance(schema, dict):
        raise ValueError("invalid response schema")
    allowed = {"type", "properties", "required", "items", "additionalProperties", "enum", "description", "title",
               "minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum"}
    kind = schema.get("type")
    kinds = [kind] if isinstance(kind, str) else kind
    supported = {"object", "array", "string", "number", "integer", "boolean", "null"}
    if (set(schema) - allowed or not isinstance(kinds, list) or not kinds
        or any(not isinstance(item, str) or item not in supported for item in kinds)
        or len(set(kinds)) != len(kinds)):
        raise ValueError("unsupported response schema")
    for child in schema.get("properties", {}).values():
        validate_schema(child, depth=depth + 1)
    if "items" in schema:
        validate_schema(schema["items"], depth=depth + 1)
    if value is ...:
        return
    # Select the actual value's branch without revisiting child schemas for
    # every union member; nested nullable fields stay a bounded traversal.
    if value is None:
        actual = "null"
    elif isinstance(value, bool):
        actual = "boolean"
    elif isinstance(value, dict):
        actual = "object"
    elif isinstance(value, list):
        actual = "array"
    elif isinstance(value, str):
        actual = "string"
    elif isinstance(value, int):
        actual = "integer"
    elif isinstance(value, float):
        actual = "number"
    else:
        raise ValueError("response type mismatch")
    kind = "number" if actual == "integer" and "integer" not in kinds and "number" in kinds else actual
    if kind not in kinds:
        raise ValueError("response type mismatch")
    if kind == "object":
        if not isinstance(value, dict) or any(k not in value for k in schema.get("required", [])):
            raise ValueError("response object mismatch")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError("unexpected response fields")
        for key, child in properties.items():
            if key in value:
                validate_schema(child, value[key], depth + 1)
    elif kind == "array":
        if not isinstance(value, list) or not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 10000):
            raise ValueError("response array mismatch")
        for item in value:
            validate_schema(schema.get("items", {}), item, depth + 1)
    elif kind == "string":
        if not isinstance(value, str) or not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 2_000_000):
            raise ValueError("response string mismatch")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError("response boolean mismatch")
    elif kind == "null":
        if value is not None:
            raise ValueError("response null mismatch")
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or kind == "integer" and not isinstance(value, int):
            raise ValueError("response number mismatch")
        if not schema.get("minimum", float("-inf")) <= value <= schema.get("maximum", float("inf")):
            raise ValueError("response number out of range")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("response enum mismatch")


class PrivateModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    operation: Literal["lecture", "plan", "diagnostic"]
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    response_schema: dict[str, Any]
    max_tokens: int = Field(default=8192, ge=256, le=65536)


def private_forwarding_router(services):
    router = APIRouter(prefix="/api/assistant", tags=["private-forwarding"])
    def owner(request: Request, response: Response):
        actor = services.account_repository.session_owner(request.cookies.get(COOKIE))
        if actor is None:
            raise HTTPException(401, "请先登录学习账号")
        response.headers["Cache-Control"] = "private, no-store"
        return str(actor)

    def model_result(request_id, found):
        if found is None:
            return {"request_id": request_id, "state": "not_started", "content": None}
        step, budget = found
        state = "not_started" if step.state == "retryable" and budget.posts == 0 else step.state
        return {"request_id": request_id, "state": state, "content": (step.message or {}).get("content"),
                "error": step.error, "retry_after": step.retry_after, "usage": budget.public()}

    @router.post("/private-model")
    def generate(body: PrivateModelInput, actor: str = Depends(owner)):
        from .assistant_routes import _finish, estimate_context
        try:
            validate_schema(body.response_schema)
            if len(json.dumps(body.model_dump())) > 4_000_000:
                raise ValueError("private request too large")
            if any(set(m) - {"role", "content"} or m.get("role") not in {"system", "user", "assistant"}
                   or not isinstance(m.get("content"), (str, list)) for m in body.messages):
                raise ValueError("private message invalid")
        except (ValueError, TypeError, AttributeError) as error:
            raise HTTPException(422, "个人生成请求或输出格式无效") from error
        client = services.assistant_tool_chat()
        if client is None:
            raise HTTPException(503, "当前模型路线未配置助手工具调用")
        # Indented JSON can double the output tokens of a full chapter.
        messages = [{"role": "system", "content": "只返回符合以下JSON schema的JSON对象，不要代码围栏；输出紧凑的单行JSON，不要缩进或换行排版：" + json.dumps(body.response_schema, ensure_ascii=False)}, *body.messages]
        estimated = estimate_context(messages, {})
        if estimated + body.max_tokens > client.config.context_window_tokens:
            raise HTTPException(413, "个人生成上下文过长")
        ledger = private_ledger(services)
        run, step_id = MODEL_PREFIX + body.request_id, MODEL_PREFIX + body.request_id + ".s0"
        request_digest = digest({"request": body.model_dump(mode="json"), "route": [client.config.base_url, client.config.model, digest(client.config.api_key)]})
        try:
            found = ledger.step(actor, run, step_id)
            if found is not None:
                if found[0].request_digest != request_digest:
                    raise LedgerConflict("request ID reused with different content")
                # No model input left this process: an explicit repeat may
                # reclaim the same fenced request. Posted calls stay immutable.
                if found[0].state != "retryable" or found[1].posts != 0:
                    return model_result(body.request_id, found)
            claim = ledger.claim(actor, run, step_id, 0, context_digest=digest({"operation": body.operation}),
                digest=request_digest, lease_seconds=2 * MODEL_DEADLINE_SECONDS, estimated_input_tokens=estimated)
        except LedgerConflict as error:
            raise HTTPException(409, str(error)) from error
        if not claim.send:
            return model_result(body.request_id, ledger.step(actor, run, step_id))
        try:
            outcome = complete_private(services, messages=messages, tools={}, descriptions={}, max_tokens=body.max_tokens,
                deadline_seconds=MODEL_DEADLINE_SECONDS, json_object=True)
        except Exception:
            outcome = ChatOutcome("uncertain", 0, error="private_model_call_interrupted")
        schema_failed = False
        if outcome.kind == "completed" and outcome.turn:
            try:
                content = private_model_json(outcome.turn.content)
                outcome = replace(outcome, turn=replace(outcome.turn, content=content))
                if os.getenv("CLOUDPATH_PRIVATE_MODEL_SCHEMA_REVIEW", "0") == "1":
                    validate_schema(body.response_schema, json.loads(content))
            except (ValueError, TypeError, AttributeError):
                schema_failed = True
        from types import SimpleNamespace
        if schema_failed:
            ledger.finish(actor, run, step_id, claim.step.attempt, state="failed", posted=True, elapsed=outcome.elapsed,
                input_tokens=outcome.turn.usage.get("input_tokens", estimated), output_tokens=outcome.turn.usage.get("output_tokens", 0),
                cached_input_tokens=outcome.turn.usage.get("cached_input_tokens", 0), error="schema_mismatch")
        else:
            _finish(ledger, actor, SimpleNamespace(run_id=run, step_id=step_id), claim.step, outcome, estimated)
        return model_result(body.request_id, ledger.step(actor, run, step_id))

    @router.get("/private-model/{request_id}")
    def status(request_id: str, actor: str = Depends(owner)):
        return model_result(request_id, private_ledger(services).step(actor, MODEL_PREFIX + request_id, MODEL_PREFIX + request_id + ".s0"))

    @router.post("/private-model/{request_id}/cancel")
    def cancel_model(request_id: str, actor: str = Depends(owner)):
        private_ledger(services).cancel(actor, MODEL_PREFIX + request_id)
        return {"request_id": request_id, "cancelled": True}

    @router.post("/private-model/{request_id}/ack")
    def acknowledge(request_id: str, actor: str = Depends(owner)):
        private_ledger(services).forget(actor, MODEL_PREFIX + request_id)
        return {"request_id": request_id, "released": True}

    @router.post("/runs/{run_id}/steps/{step_id}/ack")
    def acknowledge_agent(run_id: str, step_id: str, actor: str = Depends(owner)):
        private_ledger(services).forget(actor, PRIVATE_PREFIX + run_id, PRIVATE_PREFIX + step_id)
        return {"released": True}
    return router
