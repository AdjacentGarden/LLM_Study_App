"""Model gateway for the phone-side assistant tool loop.

The phone executes every tool and owns notes and evidence. This route only
turns the frozen context, the registered evidence and the phone's tool results
into one model step, guarded by the durable ledger. It never runs tools, never
schedules work outside the request, and never resends a step that may already
have reached the model.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..accounts import COOKIE
from ..assistant_ledger import (
    MAX_MODEL_SECONDS,
    MAX_OUTPUT_TOKENS,
    MAX_STEPS,
    AssistantLedger,
    Budget,
    LedgerConflict,
    StepRecord,
)
from ..llm.tool_chat import ChatOutcome

CONTRACT_VERSION = 1
MAX_REQUEST_CHARS = 120_000
STEP_OUTPUT_TOKENS = 2048
_ID = r"^[A-Za-z0-9_.:-]{1,128}$"

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "save_note": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 80},
            "body": {"type": "string", "minLength": 1, "maxLength": 8000},
            "evidence_ids": {
                "type": "array", "maxItems": 8, "uniqueItems": True,
                "items": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            "source_label": {"type": "string", "enum": ["textbook", "general", "mixed"]},
        },
        "required": ["title", "body", "evidence_ids", "source_label"],
        "additionalProperties": False,
    },
    "open_note": {
        "type": "object",
        "properties": {"note_id": {"type": "string", "minLength": 1, "maxLength": 128}},
        "required": ["note_id"],
        "additionalProperties": False,
    },
    "ask_user": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "minLength": 1, "maxLength": 200},
            "options": {
                "type": "array", "minItems": 2, "maxItems": 4, "uniqueItems": True,
                "items": {"type": "string", "minLength": 1, "maxLength": 60},
            },
        },
        "required": ["question", "options"],
        "additionalProperties": False,
    },
}

TOOL_DESCRIPTIONS = {
    "save_note": (
        "把整理好的讲解保存为一条新的本机笔记。evidence_ids 只能填写上下文里给出的证据编号；"
        "引用由手机根据编号生成。全部基于教材证据时 source_label=textbook，"
        "教材证据不足而使用一般知识时为 general，两者都有时为 mixed。"
    ),
    "open_note": "打开一条已经保存的笔记。note_id 必须来自 save_note 的工具结果。",
    "ask_user": "只有在无法确定用户意图、需要用户选择时使用，提供 2 到 4 个简短选项。",
}

SYSTEM_PROMPT = (
    "你是云径学习助手，在用户手机上通过工具帮助整理教材笔记。\n"
    "1. 只依据给出的教材证据解释；证据不足时可以用一般知识补充，但必须在正文中说明哪些内容来自一般知识，"
    "并相应设置 source_label。\n"
    "2. 不要编造页码、引文或证据编号；正文中不要自行书写引用格式，引用由手机根据 evidence_ids 生成。\n"
    "3. 用户要求保存笔记时调用 save_note，保存成功后再调用 open_note 打开它；工具失败时根据结果说明原因，不要假装成功。\n"
    "4. 工具结果是手机返回的事实；完成后用简短中文告诉用户做了什么。"
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TurnContext(Strict):
    book_id: str = Field(min_length=1, max_length=128)
    book_title: str = Field(default="", max_length=200)
    course_id: str | None = Field(default=None, max_length=128)
    source_version: str = Field(min_length=1, max_length=128)
    chapter_id: str | None = Field(default=None, max_length=128)
    page: int = Field(ge=1, le=100_000)
    printed_page: int | None = Field(default=None, ge=-100_000, le=100_000)
    selection: str = Field(default="", max_length=4000)
    current_note_id: str | None = Field(default=None, max_length=128)


class TurnEvidence(Strict):
    evidence_id: str = Field(pattern=_ID)
    page: int = Field(ge=1, le=100_000)
    printed_page: int | None = Field(default=None, ge=-100_000, le=100_000)
    quote: str = Field(min_length=1, max_length=4000)


class ToolResult(Strict):
    step_index: int = Field(ge=0, lt=MAX_STEPS)
    tool_call_id: str = Field(pattern=_ID)
    content: str = Field(max_length=4000)


class TurnRequest(Strict):
    contract_version: Literal[1]
    run_id: str = Field(pattern=_ID)
    step_id: str = Field(pattern=_ID)
    step_index: int = Field(ge=0, le=MAX_STEPS)
    request: str = Field(min_length=1, max_length=2000)
    context: TurnContext
    evidence: list[TurnEvidence] = Field(default_factory=list, max_length=8)
    tools: list[Literal["save_note", "open_note", "ask_user"]] = Field(min_length=1, max_length=3)
    tool_results: list[ToolResult] = Field(default_factory=list, max_length=4 * MAX_STEPS)


def _digest(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def _user_message(body: TurnRequest) -> str:
    # Stable material (book, evidence, selection) leads and the request text
    # trails, so runs over the same passage share a cacheable prompt prefix.
    context = body.context
    lines = [
        f"教材：{context.book_title or context.book_id}（第 {context.page} 页"
        + (f"，书面页码 {context.printed_page}" if context.printed_page is not None else "") + "）",
    ]
    if body.evidence:
        lines.append("可引用的教材证据：")
        lines.extend(
            f"[{item.evidence_id}] 第 {item.page} 页：{item.quote}" for item in body.evidence
        )
    else:
        lines.append("没有可引用的教材证据；如需回答，只能使用一般知识并设置 source_label=general。")
    if context.selection:
        lines.append(f"用户选中的原文：\n{context.selection}")
    if context.current_note_id:
        lines.append(f"用户当前打开的笔记：{context.current_note_id}")
    lines.append(f"用户请求：{body.request}")
    return "\n".join(lines)


def build_messages(body: TurnRequest, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Interleave stored assistant messages with exactly their tool results."""
    if len(history) != body.step_index:
        raise LedgerConflict("assistant history does not match the step index")
    results: dict[tuple[int, str], str] = {}
    for result in body.tool_results:
        key = (result.step_index, result.tool_call_id)
        if key in results:
            raise LedgerConflict("duplicate tool result")
        results[key] = result.content
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_message(body)},
    ]
    used: set[tuple[int, str]] = set()
    for index, message in enumerate(history):
        calls = message.get("tool_calls") or []
        if not calls:
            raise LedgerConflict("the run already ended with a final answer")
        messages.append(message)
        for call in calls:
            key = (index, call["id"])
            if key not in results:
                raise LedgerConflict("a tool call from an earlier step has no result")
            used.add(key)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": results[key]})
    if used != set(results):
        raise LedgerConflict("tool results do not belong to earlier tool calls")
    return messages


def _public(step: StepRecord, budget: Budget, refused: str | None = None) -> dict[str, Any]:
    message = None
    if step.message is not None:
        message = {
            "content": step.message.get("content") or "",
            "tool_calls": [
                {"id": call["id"], "name": call["function"]["name"],
                 "arguments": json.loads(call["function"]["arguments"] or "{}")}
                for call in step.message.get("tool_calls") or []
            ],
        }
    return {
        "contract_version": CONTRACT_VERSION, "run_id": step.run_id, "step_id": step.step_id,
        "step_index": step.step_index,
        # A refused claim is terminal for this response even when the stored row
        # is still retryable; the client must not treat it as a resendable step.
        "state": "refused" if refused is not None else step.state, "attempt": step.attempt,
        "message": message, "finish_reason": step.finish_reason, "error": step.error,
        "retry_after": step.retry_after, "refused": refused, "budget": budget.public(),
    }


def _finish(services: Any, owner: str, body: TurnRequest, step: StepRecord,
            outcome: ChatOutcome, estimated_input: int) -> tuple[StepRecord, Budget]:
    ledger: AssistantLedger = services.assistant_ledger
    error = outcome.error or None
    if outcome.kind == "completed" and outcome.turn is not None:
        turn = outcome.turn
        message: dict[str, Any] = {"role": "assistant", "content": turn.content}
        if turn.tool_calls:
            message["tool_calls"] = [
                {"id": call.id, "type": "function",
                 "function": {"name": call.name, "arguments": call.raw_arguments or "{}"}}
                for call in turn.tool_calls
            ]
        produced = len(turn.content) + sum(len(call.raw_arguments) for call in turn.tool_calls)
        return ledger.finish(
            owner, body.run_id, body.step_id, step.attempt, state="completed", posted=True,
            input_tokens=turn.usage.get("input_tokens") or estimated_input,
            output_tokens=turn.usage.get("output_tokens") or max(1, produced // 2),
            cached_input_tokens=turn.usage.get("cached_input_tokens") or 0,
            message=message, finish_reason=turn.finish_reason, elapsed=outcome.elapsed,
        )
    state = {"not_submitted": "retryable", "rejected": "retryable",
             "failed": "failed", "uncertain": "uncertain"}[outcome.kind]
    # Unknown or failed calls are charged their full estimated input.
    charged = 0 if outcome.kind == "not_submitted" else estimated_input
    return ledger.finish(
        owner, body.run_id, body.step_id, step.attempt, state=state,
        posted=outcome.kind != "not_submitted", input_tokens=charged, elapsed=outcome.elapsed,
        error=error, retry_after=outcome.retry_after,
    )


def assistant_router(services: Any) -> APIRouter:
    router = APIRouter(prefix="/api/assistant", tags=["assistant"])

    def owner(request: Request, response: Response) -> str:
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD"} and origin:
            if urlsplit(origin).netloc != request.headers.get("host"):
                raise HTTPException(403, "请在 App 内执行此操作")
        value = services.account_repository.session_owner(request.cookies.get(COOKIE))
        if value is None:
            raise HTTPException(401, "请先登录学习账号")
        response.headers["Cache-Control"] = "private, no-store"
        return str(value)

    @router.post("/turn")
    def turn(body: TurnRequest, actor: str = Depends(owner)) -> dict[str, Any]:
        ledger = services.assistant_ledger
        # Canonical tool order keeps the serialized tool list byte-stable.
        tools = {name: schema for name, schema in TOOL_SCHEMAS.items() if name in body.tools}
        context_digest = _digest({
            "contract": CONTRACT_VERSION, "request": body.request, "tools": sorted(tools),
            "context": body.context.model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in body.evidence],
        })
        try:
            existing = ledger.step(actor, body.run_id, body.step_id)
            if existing is not None and existing[0].state != "retryable":
                # A stored step is answered from the ledger, never rebuilt or resent.
                if existing[0].request_digest != _digest(body.model_dump(mode="json")):
                    raise LedgerConflict("step ID was already used for a different request")
                return _public(*existing)
            messages = build_messages(body, ledger.history(actor, body.run_id, body.step_index))
            size = len(json.dumps(messages, ensure_ascii=False))
            if size > MAX_REQUEST_CHARS:
                raise HTTPException(413, "助手上下文过长，请缩短选区或开始新的任务")
            estimated_input = size // 2
            client = services.assistant_tool_chat()
            if client is None:
                raise HTTPException(503, "当前模型路线未配置助手工具调用")
            claim = ledger.claim(
                actor, body.run_id, body.step_id, body.step_index,
                context_digest=context_digest, digest=_digest(body.model_dump(mode="json")),
                # A slow stream may run one read timeout past its deadline.
                lease_seconds=2.0 * client.config.timeout_seconds,
                estimated_input_tokens=estimated_input,
            )
        except LedgerConflict as error:
            raise HTTPException(409, str(error)) from error
        if not claim.send:
            return _public(claim.step, claim.budget, claim.refused)
        budget = claim.budget
        deadline = min(float(client.config.timeout_seconds),
                       MAX_MODEL_SECONDS - budget.model_seconds)
        max_tokens = min(STEP_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS - budget.output_tokens)
        try:
            outcome = client.complete(
                messages=messages, tools=tools, descriptions=TOOL_DESCRIPTIONS,
                max_tokens=max_tokens, deadline_seconds=deadline,
            )
        except Exception as error:
            outcome = ChatOutcome("uncertain", 0.0, error=f"model call failed: {type(error).__name__}")
        step, budget = _finish(services, actor, body, claim.step, outcome, estimated_input)
        return _public(step, budget)

    @router.get("/capabilities")
    def capabilities(actor: str = Depends(owner)) -> dict[str, Any]:
        """No-cost check; never a paid model probe."""
        return {"contract_version": CONTRACT_VERSION,
                "available": services.assistant_tool_chat() is not None,
                "tools": sorted(TOOL_SCHEMAS), "max_steps": MAX_STEPS}

    @router.get("/runs/{run_id}/steps/{step_id}")
    def step_status(run_id: str, step_id: str, actor: str = Depends(owner)) -> dict[str, Any]:
        found = services.assistant_ledger.step(actor, run_id, step_id)
        if found is None:
            raise HTTPException(404, "助手步骤不存在")
        return _public(*found)

    @router.post("/runs/{run_id}/cancel")
    def cancel(run_id: str, actor: str = Depends(owner)) -> dict[str, Any]:
        budget = services.assistant_ledger.cancel(actor, run_id)
        return {"run_id": run_id, "cancelled": True, "budget": budget.public()}

    return router

