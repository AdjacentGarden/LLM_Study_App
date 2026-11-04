"""Model gateway for the phone-side assistant tool loop.

The phone executes every tool and owns notes and evidence. This route only
turns the frozen context, the registered evidence and the phone's tool results
into one model step, guarded by the durable ledger. It never runs tools, never
schedules work outside the request, and never resends a step that may already
have reached the model.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import threading
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .assistant_query_tools import TOOL_SCHEMAS as QUERY_TOOL_SCHEMAS
from .assistant_query_tools import TOOL_DESCRIPTIONS as QUERY_TOOL_DESCRIPTIONS

from ..accounts import COOKIE
from ..assistant_ledger import (
    AssistantLedger,
    Budget,
    LedgerConflict,
    StepRecord,
)
from ..assistant_sessions import WINDOW, AssistantSessions
from ..llm.tool_chat import ChatOutcome
from ..native import NativeConflict

CONTRACT_VERSION = 2
STEP_OUTPUT_TOKENS = 2048
_ID = r"^[A-Za-z0-9_.:-]{1,128}$"

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "save_note": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 80},
            "body": {"type": "string", "minLength": 1, "maxLength": 8000},
            "evidence_ids": {
                "type": "array",
                "maxItems": 8,
                "uniqueItems": True,
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
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "uniqueItems": True,
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
    book_id: str | None = Field(default=None, max_length=128)
    book_title: str = Field(default="", max_length=200)
    course_id: str | None = Field(default=None, max_length=128)
    source_version: str = Field(default="", max_length=128)
    chapter_id: str | None = Field(default=None, max_length=128)
    page: int | None = Field(default=None, ge=1, le=100_000)
    printed_page: int | None = Field(default=None, ge=-100_000, le=100_000)
    selection: str = Field(default="", max_length=4000)
    current_note_id: str | None = Field(default=None, max_length=128)
    course_resource_id: str | None = Field(default=None, max_length=128)
    page_type: str | None = Field(default=None, max_length=128)
    practice_state: str | None = Field(default=None, max_length=128)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    snapshot: dict[str, Any] = Field(default_factory=dict)


class TurnEvidence(Strict):
    evidence_id: str = Field(pattern=_ID)
    page: int | None = Field(default=None, ge=1, le=100_000)
    printed_page: int | None = Field(default=None, ge=-100_000, le=100_000)
    quote: str = Field(default="")
    source_type: str = "textbook"
    object_id: str | None = None
    source_version: str | None = None
    book_id: str | None = None
    course_id: str | None = None
    course_resource_id: str | None = None


class ToolResult(Strict):
    step_index: int = Field(ge=0)
    tool_call_id: str = Field(pattern=_ID)
    content: str
    complete: bool = True
    continuation: str | None = None


    def model_content(self) -> str:
        if self.complete:
            return self.content
        return json.dumps(
            {"content": self.content, "complete": False, "continuation": self.continuation},
            ensure_ascii=False,
        )


class ReconcileRequest(Strict):
    run_id: str = Field(pattern=_ID)
    tool_results: list[ToolResult]
    ephemeral: bool = False


class CompleteLearningTaskRequest(Strict):
    operation_id: str = Field(pattern=_ID)
    task_id: str | None = None


class ReviewLearningCardRequest(Strict):
    operation_id: str | None = Field(default=None, pattern=_ID)
    event_id: str | None = Field(default=None, pattern=_ID)
    rating: Literal["mastered", "again", "good", "hard", "easy"]
    seconds: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)
    response_seconds: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)


class LegacyMessage(Strict):
    role: Literal["user", "assistant"]
    text: str


class LegacyConversation(Strict):
    session_id: str = Field(pattern=_ID)
    title: str = Field(max_length=200)
    state: Literal["open", "archived", "read_only", "finishing"] = "open"
    input_tokens: int = Field(default=0, ge=0)
    estimated: bool = True
    active_run_id: str | None = Field(default=None, pattern=_ID)
    created: float | None = None
    messages: list[LegacyMessage] = Field(default_factory=list)


class LegacyHistoryRequest(Strict):
    sessions: list[LegacyConversation]


class OperationRequest(Strict):
    operation_id: str = Field(pattern=_ID)
    arguments: dict[str, Any]


class TurnRequest(Strict):
    contract_version: Literal[1, 2, 3]
    run_id: str = Field(pattern=_ID)
    step_id: str = Field(pattern=_ID)
    step_index: int = Field(ge=0)
    request: str = Field(min_length=1)
    session_id: str | None = Field(default=None, pattern=_ID)
    message_id: str | None = Field(default=None, pattern=_ID)
    ephemeral: bool = False
    history: list[dict[str, Any]] | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    context: TurnContext = Field(default_factory=TurnContext)
    evidence: list[TurnEvidence] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=lambda: list(TOOL_SCHEMAS))
    tool_results: list[ToolResult] = Field(default_factory=list)
    client_messages: list[dict[str, Any]] = Field(default_factory=list)


UNIFIED_SYSTEM_PROMPT = (
    "你是云径统一学习助手。可从任何页面理解请求、读取资料、讨论附件并操作学习数据。"
    "仅能读取当前会话的聊天和附件，禁止跨会话搜索或从笔记的 conversation 字段恢复聊天。"
    "先取得真实业务 ID 再操作。所有结果据实际工具回执报告，部分读取不能声称全部。"
    "工具结果 complete=false 时必须继续读取或明确标出缺失。截图只代表实际捕获时的可见页面。"
    "当前待作答的章节题不得提供答案、代填或提交，截图和附件同样遵循；只复盘已提交记录。"
    "删除须先向用户展示真实对象、版本和各类关联数量并获得明确确认；用户取消不能视为同意。"
    "有原文锚点的笔记必须有明确课程和 course_resource_id，无指定课程时原样说明"
    "「无指定课程，无法进行笔记撰写」。不要按教材文件自动推断课程。"
    "保存笔记应附可核验来源；保存后可用返回的 ID 打开。一般知识与引文分开。"
    "最终回复写给学习者，第一句直接回答问题。用课程名、章节标题、笔记名指代对象。"
    "默认不输出内部编号、工具名、版本号、原始状态码、JSON、分页参数或执行流水；"
    "用户明确要求技术信息时可提供相关字段。保留教材本身的章节号、必要的引用页码、失败原因及删除影响。"
    "跨教材找知识点可直接用 search_learning_materials，不要求先列课程；要比较指定课程时分别限定范围取得双方证据。"
    "当前页面只是指代线索，不限制其他课程查询。图像描述未读取前不能声称看过图片。"
    "问有哪些课程就列课程名；问有哪些章节时用 read_course_catalog(kind=outline)，默认只列主要章名；"
    "点名某章或明确要求完整目录才展开小节。目录来源的不确定性集中说明一次。"
    "问解析、生成诊断题或教材准备进度时用 read_learning_data(kind=preparation,course_id=...)。"
    "只有查到任务记录才说明阶段、完成或失败；若只能看到空目录，说明当前查不到解析任务进度。"
    "不能从目录为空推断失败、需要重新上传或稍后会完成。区分最新任务和上次记录。"
    "诊断题生成期间总数未确定，只报告已通过的题数及当前阶段；"
    "diagnostics_finalized=true 时才报告最终总题数，已放弃知识点不计入题库。"
    "保存成功通常只说已保存《标题》；用户要求打开时再执行打开。不要把按钮写成正文。"
    "知识讲解按问题需要展开。避免重复结论、逐项汇报工具执行、例行追问和许诺后台跟踪。"
    "重复相同失败且没有新信息时，解释具体原因并请用户选择。不要展示内部推理。"
)

# Tool execution stays on the phone so local and cloud courses share one registry.
# Cloud mutations have an account-scoped endpoint and durable operation receipts.
for _name in (
    "list_courses",
    "read_course_catalog",
    "search_discovery",
    "acquire_course",
    "read_material",
    "read_attachment",
    "read_learning_data",
    "navigate",
    "capture_screen",
    "edit_note",
    "delete_note",
    "create_flashcards",
    "create_review_task",
    "update_study_plan",
    "remove_course",
):
    TOOL_SCHEMAS[_name] = {
        "type": "object",
        "properties": {
            key: {"type": kind}
            for key, kind in {
                "course_id": "string",
                "book_id": "string",
                "course_resource_id": "string",
                "chapter_id": "string",
                "object_id": "string",
                "page": "integer",
                "keyword": "string",
                "offset": "integer",
                "limit": "integer",
                "kind": "string",
                "date": "string",
                "mode": "string",
                "asset_id": "string",
                "attachment_id": "string",
                "target": "string",
                "title": "string",
                "body": "string",
                "revision": "integer",
                "confirmed": "boolean",
                "front": "string",
                "back": "string",
                "cards": "array",
                "tasks": "array",
                "operation_id": "string",
            }.items()
        },
        "additionalProperties": False,
    }
    TOOL_DESCRIPTIONS[_name] = {
        "list_courses": "分页查看当前账号的全部课程，包括准备中课程；用真实 ID 去重。",
        "read_course_catalog": "按课程和资源 ID 读取完整章节目录，明确完整性和未就绪状态。用户索要章节列表时传 kind=outline 以提供可展开目录。",
        "search_discovery": "按现有目录字段检索发现页，比较已保存课程，返回真实可打开目录 ID。",
        "acquire_course": "按已确认目录 ID 获取课程，加入课程和文件下载分别返回真实状态。",
        "read_material": "定位任意已保存课程的文字或原图，mode=text|image，保留来源 ID/版本/页码。",
        "read_attachment": "仅按当前会话 attachment_id 读取附件文字或图像及页码。",
        "read_learning_data": "读取当前账号的笔记、计划、复习、闪卡、已提交作答和学习进度。新版手机支持 kind=lessons 查询本机讲义、kind=media 查询已保存的图解和视频、kind=notes 查询含语音转写和整理结果的笔记，可按 course_id 和 keyword 筛选。kind=preparation 专查课程的解析任务，不要用目录推断进度；禁止返回其他聊天。",
        "navigate": "打开已确定业务 ID 对应的界面，必须核对页面和对象实际已到达。",
        "capture_screen": "捕获实际当前页面，target=business（业务页）或 full（包括助手）。",
        "edit_note": "按笔记 ID 和 revision 编辑，防止覆盖用户更新。",
        "delete_note": "先显示名称取得用户明确确认，再按 ID 和版本删除。",
        "create_flashcards": "用户明确要求时写入闪卡，返回卡片 ID 和版本。",
        "create_review_task": "用户明确要求时写入复习任务，返回任务 ID 和日期。",
        "update_study_plan": "按指定日期调整真实计划，只变更用户指定的范围。",
        "remove_course": "先取得影响清单并显示名称与版本，确认后按稳定课程 ID 删除其关联数据。",
    }[_name]


# Shared argument vocabulary stays strict at the model boundary. Mutations apply
# stronger object, permission, version and confirmation checks at execution.
for _schema in TOOL_SCHEMAS.values():
    if _schema is TOOL_SCHEMAS["ask_user"]:
        continue
    _schema["properties"].update(
        {
            key: {"type": "string"}
            for key in (
                "course_id",
                "course_resource_id",
                "book_id",
                "chapter_id",
                "note_id",
                "catalog_id",
                "page_type",
                "subject",
                "grade",
                "version",
                "impact_version",
                "local_version",
            )
        }
    )
    _schema["properties"].update(
        {
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "replace_all": {"type": "boolean"},
            "minutes": {"type": "integer"},
            "page": {"type": "integer"},
            "expected_revision": {"type": "integer"},
            "frozen_resource_ids": {"type": "array", "items": {"type": "string"}},
        }
    )
    for _field in ("cards", "tasks"):
        if _field in _schema["properties"]:
            _schema["properties"][_field]["items"] = {
                "type": "object",
                "properties": {
                    key: {"type": "string"}
                    for key in (
                        "id",
                        "title",
                        "front",
                        "back",
                        "date",
                        "course_id",
                        "chapter_id",
                        "kind",
                        "book_id",
                        "status",
                        "lesson_id",
                        "card_id",
                        "resource_id",
                    )
                },
                "additionalProperties": False,
            }
            _schema["properties"][_field]["items"]["properties"]["minutes"] = {"type": "integer"}


# Reuse the strict read-only search contract; execution remains an Agent tool.
TOOL_SCHEMAS["search_learning_materials"] = QUERY_TOOL_SCHEMAS["search_learning_materials"]
TOOL_DESCRIPTIONS["search_learning_materials"] = QUERY_TOOL_DESCRIPTIONS["search_learning_materials"]


def _multimodal_message(body: TurnRequest) -> str | list[dict[str, Any]]:
    text = _user_message(body)
    if not body.attachments:
        return text
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for item in body.attachments:
        data = item.get("data_base64")
        if data is None:
            continue
        media = item.get("media_type", "image/png")
        if media not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            raise HTTPException(422, "不支持的图像格式")
        try:
            decoded = base64.b64decode(data, validate=True)
            from io import BytesIO

            from PIL import Image

            with Image.open(BytesIO(decoded)) as picture:
                picture.verify()
        except Exception as error:
            raise HTTPException(422, "图像内容无效") from error
        content.append(
            {
                "type": "text",
                "text": "附件来源："
                + json.dumps(
                    {k: v for k, v in item.items() if k != "data_base64"}, ensure_ascii=False
                ),
            }
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media};base64,{data}", "detail": "high"},
            }
        )
    return content


def _new_attachments(
    repository: AssistantSessions, actor: str, session: str, body: TurnRequest, ephemeral: bool
) -> TurnRequest:
    registered: dict[str, str] = {}
    for event in repository.events(actor, session, ephemeral):
        parts = event.get("content")
        if not isinstance(parts, list):
            continue
        attachment = None
        for part in parts:
            if part.get("type") == "text" and part.get("text", "").startswith("附件来源："):
                attachment = json.loads(part["text"][5:]).get("attachment_id")
            elif part.get("type") == "image_url" and attachment:
                registered[attachment] = part["image_url"]["url"].split(",", 1)[1]
    fresh = []
    for item in body.attachments:
        key = item.get("attachment_id")
        if not isinstance(key, str) or not key:
            raise HTTPException(422, "图像缺少会话内附件 ID")
        if key in registered:
            if item.get("data_base64") and item["data_base64"] != registered[key]:
                raise LedgerConflict("附件 ID 已绑定另一版本的图像")
            continue
        if not item.get("data_base64"):
            raise HTTPException(404, "附件不属于当前会话或尚未上传")
        registered[key] = item["data_base64"]
        fresh.append(item)
    return body.model_copy(update={"attachments": fresh})


def estimate_context(messages: list[dict[str, Any]], tools: dict[str, Any]) -> int:
    # Conservative UTF-8 estimate includes system, tools, history and results.
    # Image bytes are not text tokens; reserve tiles separately and correct from
    # provider input usage after every response (cached tokens remain included).
    total = 0
    for message in messages:
        copy = dict(message)
        if isinstance(copy.get("content"), list):
            parts = []
            for part in copy["content"]:
                if part.get("type") == "image_url":
                    from io import BytesIO

                    from PIL import Image

                    data = part["image_url"]["url"].split(",", 1)[1]
                    with Image.open(BytesIO(base64.b64decode(data))) as picture:
                        total += max(
                            4096,
                            85
                            + 170
                            * math.ceil(picture.width / 512)
                            * math.ceil(picture.height / 512),
                        )
                else:
                    parts.append(part)
            copy["content"] = parts
        total += math.ceil(len(json.dumps(copy, ensure_ascii=False).encode()) / 2)
    return (
        total
        + math.ceil(
            len(
                json.dumps(
                    {
                        "tools": tools,
                        "descriptions": {name: TOOL_DESCRIPTIONS[name] for name in tools},
                    },
                    ensure_ascii=False,
                ).encode()
            )
            / 2
        )
        + 64
    )


def _validate_messages(messages: list[dict[str, Any]]) -> None:
    pending: set[str] = set()
    for message in messages:
        if message["role"] == "tool":
            call = message["tool_call_id"]
            if call not in pending:
                raise LedgerConflict("工具回执不属于当前会话")
            pending.remove(call)
        else:
            if pending:
                raise LedgerConflict("上一模型步骤仍缺少工具结果")
            pending = {c["id"] for c in message.get("tool_calls", [])}
    if pending:
        raise LedgerConflict("上一模型步骤仍缺少工具结果")


def _validate_context(services: Any, actor: str, body: TurnRequest) -> None:
    context = body.context
    if context.course_resource_id and context.course_resource_id.startswith("resource_"):
        if not context.course_id:
            raise HTTPException(422, "资料缺少课程归属")
        try:
            row = services.native_repository.resource(
                actor, context.course_id, context.course_resource_id
            )
            if context.book_id and row["book_id"] != context.book_id:
                raise KeyError(context.book_id)
        except KeyError as error:
            raise HTTPException(404, "课程资料不存在或无权访问") from error


def _with_capacity(result: dict[str, Any], value: dict[str, Any]) -> None:
    usage = dict(
        input_tokens=value["input_tokens"],
        estimated=bool(value["estimated"]),
        window_tokens=WINDOW,
        new_turn_threshold=900_000,
        state=value["state"],
        active_run_id=value.get("active_run_id"),
        reason=value.get("reason"),
    )
    if usage["state"] == "archived" and result.get("message"):
        message = dict(result["message"])
        if message.get("tool_calls"):
            result["withheld_tool_calls"] = message["tool_calls"]
            message["tool_calls"] = []
            result["message"] = message
    result.update(
        context_tokens=usage["input_tokens"],
        context_window=WINDOW,
        capacity_state=usage["state"],
        context_estimated=usage["estimated"],
        context_usage=usage,
    )


def _digest(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def _user_message(body: TurnRequest) -> str:
    # Stable material (book, evidence, selection) leads and the request text
    # trails, so runs over the same passage share a cacheable prompt prefix.
    context = body.context
    if body.contract_version == 2:
        return (
            "发送时页面快照："
            + json.dumps(context.model_dump(mode="json"), ensure_ascii=False)
            + "\n可用来源："
            + json.dumps([e.model_dump(mode="json") for e in body.evidence], ensure_ascii=False)
            + "\n用户请求："
            + body.request
        )
    lines = [
        f"教材：{context.book_title or context.book_id}（第 {context.page} 页"
        + (f"，书面页码 {context.printed_page}" if context.printed_page is not None else "")
        + "）",
    ]
    if body.evidence:
        lines.append("可引用的教材证据：")
        lines.extend(
            f"[{item.evidence_id}] 第 {item.page} 页：{item.quote}" for item in body.evidence
        )
    else:
        lines.append(
            "没有可引用的教材证据；如需回答，只能使用一般知识并设置 source_label=general。"
        )
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
                {
                    "id": call["id"],
                    "name": call["function"]["name"],
                    "arguments": json.loads(call["function"]["arguments"] or "{}"),
                }
                for call in step.message.get("tool_calls") or []
            ],
        }
    return {
        "contract_version": CONTRACT_VERSION,
        "run_id": step.run_id,
        "step_id": step.step_id,
        "step_index": step.step_index,
        # A refused claim is terminal for this response even when the stored row
        # is still retryable; the client must not treat it as a resendable step.
        "state": "refused" if refused is not None else step.state,
        "attempt": step.attempt,
        "message": message,
        "finish_reason": step.finish_reason,
        "error": step.error,
        "retry_after": step.retry_after,
        "refused": refused,
        "budget": budget.public(),
    }


def _finish(
    services: Any,
    owner: str,
    body: TurnRequest,
    step: StepRecord,
    outcome: ChatOutcome,
    estimated_input: int,
) -> tuple[StepRecord, Budget]:
    ledger: AssistantLedger = (
        services if isinstance(services, AssistantLedger) else services.assistant_ledger
    )
    error = outcome.error or None
    if outcome.kind == "completed" and outcome.turn is not None:
        turn = outcome.turn
        message: dict[str, Any] = {"role": "assistant", "content": turn.content}
        if turn.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.raw_arguments or "{}"},
                }
                for call in turn.tool_calls
            ]
        produced = len(turn.content) + sum(len(call.raw_arguments) for call in turn.tool_calls)
        return ledger.finish(
            owner,
            body.run_id,
            body.step_id,
            step.attempt,
            state="completed",
            posted=True,
            input_tokens=turn.usage.get("input_tokens", estimated_input),
            output_tokens=turn.usage.get("output_tokens") or max(1, produced // 2),
            cached_input_tokens=turn.usage.get("cached_input_tokens") or 0,
            message=message,
            finish_reason=turn.finish_reason,
            elapsed=outcome.elapsed,
        )
    state = {
        "not_submitted": "retryable",
        "rejected": "retryable",
        "failed": "failed",
        "uncertain": "uncertain",
    }[outcome.kind]
    # Unknown or failed calls are charged their full estimated input.
    charged = 0 if outcome.kind == "not_submitted" else estimated_input
    return ledger.finish(
        owner,
        body.run_id,
        body.step_id,
        step.attempt,
        state=state,
        posted=outcome.kind != "not_submitted",
        input_tokens=charged,
        elapsed=outcome.elapsed,
        error=error,
        retry_after=outcome.retry_after,
    )


def assistant_router(services: Any) -> APIRouter:
    router = APIRouter(prefix="/api/assistant", tags=["assistant"])
    # Resolve lazily because tests and runtime setup can replace the ledger.
    repositories: dict[int, AssistantSessions] = {}
    repository_lock = threading.RLock()

    def sessions() -> AssistantSessions:
        ledger = services.assistant_ledger
        with repository_lock:
            if id(ledger) not in repositories:
                repositories[id(ledger)] = AssistantSessions(ledger)
            return repositories[id(ledger)]

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
        # Query runs have a separate ACL-aware replay contract.
        if body.run_id.startswith("query:"):
            raise HTTPException(409, "此助手查询请通过普通对话继续")
        if body.contract_version == 3:
            from .private_forwarding import private_turn
            return private_turn(services, actor, body)
        repository = sessions()
        ephemeral = body.contract_version == 2 and body.ephemeral
        session = body.session_id or body.run_id
        ledger = services.assistant_ledger
        if ephemeral and (actor, session) in repository.memory_ledgers:
            ledger = repository.memory_ledgers[(actor, session)]
        try:
            existing = ledger.step(actor, body.run_id, body.step_id)
            if existing is not None and existing[0].state != "retryable":
                if existing[0].request_digest != _digest(body.model_dump(mode="json")):
                    raise LedgerConflict("step ID was already used for a different request")
                result = _public(*existing)
                if body.contract_version == 2:
                    value = repository.get(actor, session, ephemeral)
                    _with_capacity(result, value)
                return result
            unknown = set(body.tools) - TOOL_SCHEMAS.keys()
            if unknown:
                raise HTTPException(422, "未知助手工具：" + ", ".join(sorted(unknown)))
            tools = {name: schema for name, schema in TOOL_SCHEMAS.items() if name in body.tools}
            client = services.assistant_tool_chat()
            if client is None:
                raise HTTPException(503, "当前模型路线未配置助手工具调用")
            if client.config.context_window_tokens < WINDOW:
                raise HTTPException(422, "API 错误：模型上下文窗口不足 1M")
            if body.contract_version == 2:
                if not body.session_id or not body.message_id:
                    raise HTTPException(422, "v2 请求必须提供 session_id 和 message_id")
                _validate_context(services, actor, body)
                if body.history and not ephemeral:
                    for previous in body.history:
                        if previous.get("role") not in {"user", "assistant"} or not isinstance(
                            previous.get("text"), str
                        ):
                            raise HTTPException(422, "旧会话迁移只接受当前会话的用户与助手文本")
                title = (
                    next(
                        (
                            previous["text"]
                            for previous in body.history or []
                            if previous.get("role") == "user"
                            and isinstance(previous.get("text"), str)
                        ),
                        body.request,
                    )
                    if not ephemeral
                    else body.request
                )
                repository.begin(actor, session, body.run_id, title, ephemeral)
                if (
                    body.step_index == 0
                    and existing is None
                    and not ephemeral
                    and body.history
                    and not repository.events(actor, session, False)
                ):
                    for index, previous in enumerate(body.history):
                        payload = {"role": previous["role"], "content": previous["text"]}
                        if previous["role"] == "user":
                            payload["display_text"] = previous["text"]
                        repository.append(
                            actor, session, f"migration:{session}:{index}", payload, False
                        )
                if ephemeral:
                    ledger = repository.memory_ledgers[(actor, session)]
                    with services.assistant_ledger.connect() as db:
                        cancelled = services.assistant_ledger._run(db, actor, body.run_id)
                    if cancelled is not None and cancelled["state"] == "cancelled":
                        ledger.cancel(actor, body.run_id)
                image_body = _new_attachments(repository, actor, session, body, ephemeral)
                if body.step_index == 0 and existing is None:
                    repository.append(
                        actor,
                        session,
                        body.message_id,
                        {
                            "role": "user",
                            "content": _multimodal_message(image_body),
                            "display_text": body.request,
                        },
                        ephemeral,
                    )
                # Incremental receipts are matched against the stored model calls;
                # all earlier receipts are reconstructed from the current session.
                history = ledger.history(actor, body.run_id, body.step_index)
                registered = {
                    (i, c["id"]) for i, m in enumerate(history) for c in m.get("tool_calls", [])
                }
                for receipt in body.tool_results:
                    if (receipt.step_index, receipt.tool_call_id) not in registered:
                        raise LedgerConflict("tool result does not belong to this run")
                    content = receipt.model_content()
                    repository.append(
                        actor,
                        session,
                        f"{body.run_id}:tool:{receipt.tool_call_id}",
                        {"role": "tool", "tool_call_id": receipt.tool_call_id, "content": content},
                        ephemeral,
                    )
                if body.step_index > 0 and image_body.attachments:
                    repository.append(
                        actor,
                        session,
                        f"{body.run_id}:images:{body.step_index}",
                        {"role": "user", "content": _multimodal_message(image_body)},
                        ephemeral,
                    )
                messages = [
                    {"role": "system", "content": UNIFIED_SYSTEM_PROMPT}
                ] + repository.model_history(actor, session, ephemeral, ledger, services.native_repository.assistant_operation)
                _validate_messages(messages)
            else:
                messages = build_messages(body, ledger.history(actor, body.run_id, body.step_index))
            messages = [
                {
                    k: v
                    for k, v in message.items()
                    if k in {"role", "content", "tool_calls", "tool_call_id", "name"}
                }
                for message in messages
            ]
            estimated_input = estimate_context(messages, tools)
            if body.contract_version == 2:
                repository.capacity(actor, session, estimated_input, True, body.run_id, ephemeral)
                if estimated_input + STEP_OUTPUT_TOKENS > WINDOW:
                    # An over-capacity estimate is a monotonic stop, including
                    # calls which cross both thresholds in one request.
                    value = repository.capacity(
                        actor, session, estimated_input, True, body.run_id, ephemeral, done=True
                    )
                    # Reserving the response can stop a request just below 1M.
                    # Such a refused request still returns durable capacity to
                    # let every client show the same read-only state.
                    with ledger.connect() as db:
                        budget = ledger._budget(db, actor, body.run_id)
                    result = _public(
                        ledger._absent(
                            body.run_id,
                            body.step_id,
                            body.step_index,
                            _digest(body.model_dump(mode="json")),
                        ),
                        budget,
                        "context_capacity",
                    )
                    _with_capacity(result, value)
                    return result
            elif estimated_input + STEP_OUTPUT_TOKENS > WINDOW:
                raise HTTPException(413, "助手上下文已达到 1M，请新建对话")
            context_digest = _digest(
                {
                    "contract": body.contract_version,
                    "request": body.request,
                    "tools": sorted(tools),
                    "context": body.context.model_dump(mode="json"),
                    # v2 freezes the origin, not evidence subsequently obtained by tools.
                    "evidence": [i.model_dump(mode="json") for i in body.evidence]
                    if body.contract_version == 1 else None,
                }
            )
            claim = ledger.claim(
                actor,
                body.run_id,
                body.step_id,
                body.step_index,
                context_digest=context_digest,
                digest=_digest(body.model_dump(mode="json")),
                lease_seconds=2.0 * client.config.timeout_seconds,
                estimated_input_tokens=estimated_input,
            )
        except LedgerConflict as error:
            raise HTTPException(409, str(error)) from error
        if not claim.send:
            return _public(claim.step, claim.budget, claim.refused)
        try:
            outcome = client.complete(
                messages=messages,
                tools=tools,
                descriptions=TOOL_DESCRIPTIONS,
                max_tokens=STEP_OUTPUT_TOKENS,
                deadline_seconds=float(client.config.timeout_seconds),
            )
        except Exception as error:
            outcome = ChatOutcome(
                "uncertain", 0.0, error=f"model call failed: {type(error).__name__}"
            )
        step, budget = _finish(ledger, actor, body, claim.step, outcome, estimated_input)
        result = _public(step, budget)
        if body.contract_version == 2:
            actual = outcome.turn.usage.get("input_tokens") if outcome.turn else None
            done = step.state in {"failed", "uncertain"} or bool(
                step.message and not step.message.get("tool_calls")
            )
            with ledger.connect() as db:
                run_state = ledger._run(db, actor, body.run_id)
            if run_state is not None and run_state["state"] == "cancelled":
                done = True
                result["refused"] = "cancelled"
                if result.get("message"):
                    result["message"] = {**result["message"], "tool_calls": []}

                _with_capacity(result, repository.get(actor, session, ephemeral))
                return result

            if step.message:
                repository.append(
                    actor,
                    session,
                    f"{body.run_id}:assistant:{body.step_index}",
                    step.message,
                    ephemeral,
                )
            value = repository.capacity(
                actor,
                session,
                actual if actual is not None else estimated_input,
                actual is None,
                body.run_id,
                ephemeral,
                done=done,
            )
            _with_capacity(result, value)
        return result

    @router.get("/capabilities")
    def capabilities(actor: str = Depends(owner)) -> dict[str, Any]:
        """No-cost check; never a paid model probe."""
        return {
            "contract_version": CONTRACT_VERSION,
            "available": services.assistant_tool_chat() is not None,
            "tools": sorted(TOOL_SCHEMAS),
            "tool_registry": [
                {
                    "name": name,
                    "parameters": schema,
                    "execution": "client",
                    "permission": "current_account_and_explicit_course_resource",
                    "confirmation_required": name in {"delete_note", "remove_course"},
                    "result_contract": {
                        "business_ids": "stable",
                        "pagination": "explicit_complete_and_continuation",
                        "writes": "operation_id_and_revision",
                    },
                    "failure_states": [
                        "not_found",
                        "permission_denied",
                        "not_ready",
                        "conflict",
                        "needs_confirmation",
                        "not_submitted",
                        "uncertain",
                        "partial_failure",
                    ],
                }
                for name, schema in TOOL_SCHEMAS.items()
            ],
            "context_window": WINDOW,
            "new_turn_threshold": 900_000,
            "history_limit": 6,
        }

    @router.get("/runs/{run_id}/steps/{step_id}")
    def step_status(run_id: str, step_id: str, actor: str = Depends(owner)) -> dict[str, Any]:
        if run_id.startswith("query:"):
            raise HTTPException(404, "助手步骤不存在")
        from .private_forwarding import private_ledger, PRIVATE_PREFIX, wire
        private = private_ledger(services).step(actor, PRIVATE_PREFIX + run_id, PRIVATE_PREFIX + step_id)
        if private is not None:
            return wire(*private, run=run_id, step_id=step_id)
        sessions()
        found = services.assistant_ledger.step(actor, run_id, step_id)
        if found is None:
            for key, memory in sessions().memory_ledgers.items():
                if key[0] != actor:
                    continue
                found = memory.step(actor, run_id, step_id)
                if found:
                    result = _public(*found)
                    _with_capacity(result, sessions().get(actor, key[1], True))
                    return result
            raise HTTPException(404, "助手步骤不存在")
        result = _public(*found)
        with services.assistant_ledger.connect() as db:
            row = db.execute(
                "SELECT session_id FROM assistant_conversation_runs WHERE owner=? AND run_id=?",
                (actor, run_id),
            ).fetchone()
        if row:
            _with_capacity(result, sessions().get(actor, row[0]))
        return result

    @router.post("/runs/{run_id}/cancel")
    def cancel(run_id: str, actor: str = Depends(owner)) -> dict[str, Any]:
        from .private_forwarding import private_ledger, PRIVATE_PREFIX
        private = private_ledger(services)
        with private.connect() as db:
            private_run = private._run(db, actor, PRIVATE_PREFIX + run_id)
        private.cancel(actor, PRIVATE_PREFIX + run_id)
        if private_run is not None:
            budget = private.cancel(actor, PRIVATE_PREFIX + run_id)
            return {"run_id": run_id, "cancelled": True, "budget": budget.public()}
        target = services.assistant_ledger
        for key, memory in sessions().memory_ledgers.items():
            if key[0] == actor:
                with memory.connect() as db:
                    found = memory._run(db, actor, run_id)
                if found is not None:
                    target = memory
                    value = sessions().get(actor, key[1], True)
                    sessions().capacity(
                        actor,
                        key[1],
                        value["input_tokens"],
                        bool(value["estimated"]),
                        run_id,
                        True,
                        done=True,
                    )
                    break
        budget = target.cancel(actor, run_id)
        for value in sessions().list(actor):
            if value["active_run_id"] == run_id:
                sessions().capacity(
                    actor,
                    value["session_id"],
                    value["input_tokens"],
                    bool(value["estimated"]),
                    run_id,
                    False,
                    done=True,
                )
        return {"run_id": run_id, "cancelled": True, "budget": budget.public()}

    @router.post("/tools/{tool}")
    def execute_tool(
        tool: str, body: OperationRequest, actor: str = Depends(owner)
    ) -> dict[str, Any]:
        try:
            return services.native_repository.execute_assistant_operation(
                actor, body.operation_id, tool, body.arguments
            )
        except KeyError as error:
            raise HTTPException(404, "课程或资料不存在") from error
        except NativeConflict as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/operations/{operation_id}")
    def operation(operation_id: str, actor: str = Depends(owner)) -> dict[str, Any]:
        receipt = services.native_repository.assistant_operation(actor, operation_id)
        return receipt or {"operation_id": operation_id, "state": "not_submitted"}

    @router.post("/learning-data/{object_id}/complete")
    def complete_learning_task(
        object_id: str, body: CompleteLearningTaskRequest, actor: str = Depends(owner)
    ) -> dict[str, Any]:
        try:
            value = services.native_repository.assistant_object(actor, object_id)
            return services.native_repository.execute_assistant_operation(
                actor,
                body.operation_id,
                "complete_learning_task",
                {
                    "course_id": value["workspace_id"],
                    "object_id": object_id,
                    "task_id": body.task_id,
                },
            )
        except KeyError as error:
            raise HTTPException(404, "学习任务不存在") from error
        except NativeConflict as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/learning-data/{object_id}/review")
    def review_learning_card(
        object_id: str, body: ReviewLearningCardRequest, actor: str = Depends(owner)
    ) -> dict[str, Any]:
        operation_id = body.operation_id or body.event_id
        if not operation_id:
            raise HTTPException(422, "复习必须提供稳定操作 ID")
        try:
            value = services.native_repository.assistant_object(actor, object_id)
            prior = services.native_repository.assistant_operation(actor, operation_id)
            result = services.native_repository.execute_assistant_operation(
                actor,
                operation_id,
                "review_flashcard",
                {
                    "course_id": value["workspace_id"],
                    "object_id": object_id,
                    "rating": "good" if body.rating == "mastered" else body.rating,
                    "seconds": body.seconds
                    if body.seconds is not None
                    else body.response_seconds or 0,
                },
            )
            return {**result, "duplicate": prior is not None}
        except KeyError as error:
            raise HTTPException(404, "闪卡不存在") from error
        except NativeConflict as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/learning-data")
    def learning_data(
        course_id: str | None = None, kind: str | None = None, actor: str = Depends(owner)
    ) -> dict[str, Any]:
        objects = services.native_repository.assistant_objects(actor, course_id, kind)
        return {"objects": objects, "total": len(objects), "complete": True}

    @router.get("/courses/{course_id}/deletion-impact")
    def deletion_impact(course_id: str, actor: str = Depends(owner)) -> dict[str, Any]:
        try:
            return services.native_repository.deletion_impact(actor, course_id)
        except KeyError as error:
            raise HTTPException(404, "课程不存在") from error

    @router.post("/conversations/{session_id}/receipts")
    def reconcile_receipts(
        session_id: str, body: ReconcileRequest, actor: str = Depends(owner)
    ) -> dict[str, Any]:
        repository = sessions()
        try:
            value = repository.get(actor, session_id, body.ephemeral)
            target = (
                repository.memory_ledgers[(actor, session_id)]
                if body.ephemeral
                else services.assistant_ledger
            )
            if not body.ephemeral:
                with target.connect() as db:
                    bound = db.execute(
                        "SELECT session_id FROM assistant_conversation_runs WHERE owner=? AND run_id=?",
                        (actor, body.run_id),
                    ).fetchone()
                if bound is None or bound[0] != session_id:
                    raise LedgerConflict("任务不属于当前会话")
            for receipt in body.tool_results:
                with target.connect() as db:
                    step = db.execute(
                        "SELECT message_json FROM assistant_steps WHERE owner=? AND run_id=? AND step_index=?",
                        (actor, body.run_id, receipt.step_index),
                    ).fetchone()
                if (
                    not step
                    or not step[0]
                    or receipt.tool_call_id
                    not in {c["id"] for c in json.loads(step[0]).get("tool_calls", [])}
                ):
                    raise LedgerConflict("回执不属于当前会话的工具调用")
                repository.append(
                    actor,
                    session_id,
                    f"{body.run_id}:tool:{receipt.tool_call_id}",
                    {
                        "role": "tool",
                        "tool_call_id": receipt.tool_call_id,
                        "content": receipt.model_content(),
                    },
                    body.ephemeral,
                )
            return {"recorded": True, "state": value["state"]}
        except KeyError as error:
            raise HTTPException(404, "会话不存在") from error
        except LedgerConflict as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/conversations/migrate")
    def migrate_history(body: LegacyHistoryRequest, actor: str = Depends(owner)) -> dict[str, Any]:
        ids = [session.session_id for session in body.sessions]
        if len(set(ids)) != len(ids):
            raise HTTPException(422, "旧会话 ID 重复")
        imported = sessions().migrate_history(
            actor, [session.model_dump(mode="json") for session in body.sessions]
        )
        return {"imported": imported, "sessions": sessions().list(actor)}

    @router.get("/conversations")
    def conversations(actor: str = Depends(owner)) -> list[dict[str, Any]]:
        return sessions().list(actor)

    @router.get("/conversations/{session_id}")
    def conversation(session_id: str, actor: str = Depends(owner)) -> dict[str, Any]:
        try:
            return {
                **sessions().public(sessions().get(actor, session_id)),
                "messages": sessions().events(actor, session_id, False),
            }
        except KeyError as error:
            raise HTTPException(404, "会话不存在") from error

    @router.delete("/conversations/{session_id}")
    def delete_conversation(session_id: str, actor: str = Depends(owner)) -> dict[str, bool]:
        sessions().delete(actor, session_id)
        return {"deleted": True}

    @router.post("/conversations/{session_id}/close")
    def close_conversation(session_id: str, actor: str = Depends(owner)) -> dict[str, bool]:
        if (actor, session_id) in sessions().temporary:
            sessions().close_temporary(actor, session_id)
        return {"closed": True}

    return router
