"""/api/assistant/turn: durable model steps, budgets and unknown outcomes.

Only the model transport is replaced (httpx.MockTransport); the ledger, router,
stream assembler and account sessions are the production implementations.
"""

import json
import sqlite3
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from native_harness import api, configure

from adaptive_learning import assistant_ledger as ledger_module
from adaptive_learning.api.assistant_routes import assistant_router
from adaptive_learning.assistant_ledger import AssistantLedger
from adaptive_learning.llm import tool_chat as tool_chat_module
from adaptive_learning.llm.client import LLMConfig
from adaptive_learning.llm.model_admission import admission
from adaptive_learning.llm.tool_chat import ToolChatClient


def sse(*chunks):
    return "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"


SAVE_ARGS = {"title": "减数分裂", "body": "染色体复制一次，细胞分裂两次。",
             "evidence_ids": ["ev1"], "source_label": "textbook"}


def save_call_stream(call_id="call_1"):
    raw = json.dumps(SAVE_ARGS, ensure_ascii=False)
    half = len(raw) // 2
    return sse(
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": call_id, "type": "function",
            "function": {"name": "save_note", "arguments": raw[:half]}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": raw[half:]}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 30}},
    )


def final_stream(text="已保存并打开笔记。"):
    return sse(
        {"choices": [{"index": 0, "delta": {"content": text}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 200, "completion_tokens": 10}},
    )


class Model:
    """Scripted provider. Each entry is a response text or a callable(request)."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []

    def __call__(self, request):
        self.requests.append(json.loads(request.content))
        item = self.script.pop(0)
        if callable(item):
            return item(request)
        return httpx.Response(200, text=item, headers={"content-type": "text/event-stream"})


def body(step_index=0, step_id=None, run_id="run_1", tool_results=(), **context):
    return {
        "contract_version": 1, "run_id": run_id, "step_id": step_id or f"step_{step_index}",
        "step_index": step_index, "request": "把选中的内容整理成笔记",
        "context": {"book_id": "book_1", "book_title": "生物", "source_version": "v1", "page": 3,
                    "selection": "染色体只复制一次，而细胞分裂两次。", **context},
        "evidence": [{"evidence_id": "ev1", "page": 3, "quote": "染色体只复制一次，而细胞分裂两次。"}],
        "tools": ["save_note", "open_note"],
        "tool_results": list(tool_results),
    }


def login(client, email):
    challenge = client.post("/api/auth/code", json={"email": email, "purpose": "register"}).json()
    response = client.post("/api/auth/verify", json={
        "challenge_id": challenge["challenge_id"], "code": challenge["demo_code"],
        "registration": {"nickname": "测试读者", "stage": "university", "interests": [], "goal": "interest"}})
    assert response.status_code == 200, response.text


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    app = configure(tmp_path, monkeypatch)
    app.include_router(assistant_router(api))
    monkeypatch.setattr(api, "assistant_ledger", AssistantLedger(tmp_path / "state" / "assistant.sqlite3"))
    admission._states.clear()
    holder = {}

    def install(model):
        holder["client"] = ToolChatClient(
            LLMConfig("https://api.deepseek.com/v1", "test-key", "deepseek-chat", timeout_seconds=30,
                      max_retries=0),
            transport=httpx.MockTransport(model))
        return model

    monkeypatch.setattr(api, "assistant_tool_chat", lambda: holder.get("client"))
    with TestClient(app) as a, TestClient(app) as b:
        login(a, "reader@zhiwo.test")
        login(b, "xiaolin@zhiwo.test")
        yield a, b, install, tmp_path
    admission._states.clear()


def test_streamed_tool_call_is_assembled_validated_and_persisted(gateway):
    a, _, install, root = gateway
    model = install(Model(save_call_stream()))
    response = a.post("/api/assistant/turn", json=body())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["state"] == "completed"
    assert result["message"]["tool_calls"] == [{"id": "call_1", "name": "save_note", "arguments": SAVE_ARGS}]
    assert result["budget"]["posts"] == 1 and result["budget"]["input_tokens"] == 120
    sent = model.requests[0]
    assert sent["stream"] is True and sent["thinking"] == {"type": "disabled"}
    assert {tool["function"]["name"] for tool in sent["tools"]} == {"save_note", "open_note"}
    assert "[ev1] 第 3 页" in sent["messages"][1]["content"]
    assert result["budget"]["cached_input_tokens"] == 0
    # A fresh process reads the same stored step and final model message.
    reopened = AssistantLedger(root / "state" / "assistant.sqlite3")
    owner = api.account_repository.session_owner(a.cookies.get("zhiwo_account"))
    step, _ = reopened.step(owner, "run_1", "step_0")
    assert step.state == "completed" and step.message["tool_calls"][0]["id"] == "call_1"


def test_completed_step_replays_without_a_second_post(gateway):
    a, _, install, _ = gateway
    model = install(Model(save_call_stream()))
    first = a.post("/api/assistant/turn", json=body()).json()
    again = a.post("/api/assistant/turn", json=body()).json()
    assert again["message"] == first["message"] and len(model.requests) == 1
    assert a.get("/api/assistant/runs/run_1/steps/step_0").json()["message"] == first["message"]
    changed = body()
    changed["request"] = "换一个问题"
    assert a.post("/api/assistant/turn", json=changed).status_code == 409
    assert len(model.requests) == 1


def test_next_step_carries_stored_assistant_message_and_phone_tool_result(gateway):
    a, _, install, _ = gateway
    model = install(Model(save_call_stream(), final_stream()))
    a.post("/api/assistant/turn", json=body())
    result_text = json.dumps({"ok": True, "note_id": "note_9", "revision": 1})
    second = a.post("/api/assistant/turn", json=body(1, tool_results=[
        {"step_index": 0, "tool_call_id": "call_1", "content": result_text}])).json()
    assert second["state"] == "completed" and second["message"]["content"] == "已保存并打开笔记。"
    messages = model.requests[1]["messages"]
    assert messages[2]["role"] == "assistant" and messages[2]["tool_calls"][0]["id"] == "call_1"
    assert messages[3] == {"role": "tool", "tool_call_id": "call_1", "content": result_text}
    assert second["budget"]["posts"] == 2 and second["budget"]["input_tokens"] == 320


def test_missing_or_foreign_tool_results_are_rejected_before_any_post(gateway):
    a, _, install, _ = gateway
    model = install(Model(save_call_stream()))
    a.post("/api/assistant/turn", json=body())
    assert a.post("/api/assistant/turn", json=body(1)).status_code == 409
    forged = [{"step_index": 0, "tool_call_id": "call_1", "content": "{}"},
              {"step_index": 0, "tool_call_id": "call_2", "content": "{}"}]
    assert a.post("/api/assistant/turn", json=body(1, tool_results=forged)).status_code == 409
    assert a.post("/api/assistant/turn", json=body(2, tool_results=forged[:1])).status_code == 409
    assert len(model.requests) == 1


def test_invalid_streamed_arguments_fail_the_step_without_returning_a_tool_call(gateway):
    a, _, install, _ = gateway
    bad = sse(
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
            "function": {"name": "save_note", "arguments": "{\"title\": \"x\", \"body\": \"y\"}"}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
    model = install(Model(bad))
    result = a.post("/api/assistant/turn", json=body()).json()
    assert result["state"] == "failed" and result["message"] is None
    assert "required property" in result["error"]
    assert a.post("/api/assistant/turn", json=body()).json()["state"] == "failed"
    assert len(model.requests) == 1


def test_dropped_stream_is_uncertain_and_never_resent(gateway):
    a, _, install, _ = gateway
    truncated = sse({"choices": [{"index": 0, "delta": {"content": "已经"}}]})
    model = install(Model(truncated, save_call_stream()))
    result = a.post("/api/assistant/turn", json=body()).json()
    assert result["state"] == "uncertain" and result["budget"]["posts"] == 1
    assert a.post("/api/assistant/turn", json=body()).json()["state"] == "uncertain"
    assert a.get("/api/assistant/runs/run_1/steps/step_0").json()["state"] == "uncertain"
    assert len(model.requests) == 1
    # A different step ID cannot bypass the unresolved step either.
    assert a.post("/api/assistant/turn", json=body(1, step_id="step_x")).status_code == 409


def test_read_timeout_after_submission_is_uncertain(gateway):
    a, _, install, _ = gateway

    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    model = install(Model(timeout))
    assert a.post("/api/assistant/turn", json=body()).json()["state"] == "uncertain"
    assert a.post("/api/assistant/turn", json=body()).json()["state"] == "uncertain"
    assert len(model.requests) == 1


def test_connect_failure_before_submission_may_be_resent_once_with_same_step(gateway):
    a, _, install, _ = gateway

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    model = install(Model(refused, save_call_stream()))
    first = a.post("/api/assistant/turn", json=body()).json()
    assert first["state"] == "retryable" and first["budget"]["posts"] == 0
    second = a.post("/api/assistant/turn", json=body()).json()
    assert second["state"] == "completed" and second["attempt"] == 2
    assert second["budget"]["posts"] == 1 and len(model.requests) == 2


def test_explicit_overload_is_retryable_only_after_retry_after(gateway):
    a, _, install, _ = gateway
    busy = lambda request: httpx.Response(429, json={"error": {"message": "busy"}},  # noqa: E731
                                          headers={"Retry-After": "30"})
    model = install(Model(busy, save_call_stream()))
    first = a.post("/api/assistant/turn", json=body()).json()
    assert first["state"] == "retryable" and first["retry_after"] == 30
    assert first["budget"]["posts"] == 1
    waiting = a.post("/api/assistant/turn", json=body()).json()
    assert waiting["state"] == "retryable" and len(model.requests) == 1


def test_cost_accounting_persists_across_restart_without_old_post_limit(gateway, monkeypatch):
    a, _, install, root = gateway
    monkeypatch.setattr(ledger_module, "MAX_POSTS", 2, raising=False)
    model = install(Model(save_call_stream(), save_call_stream("call_2"), final_stream()))
    a.post("/api/assistant/turn", json=body())
    a.post("/api/assistant/turn", json=body(1, tool_results=[
        {"step_index": 0, "tool_call_id": "call_1", "content": "{}"}]))
    monkeypatch.setattr(api, "assistant_ledger", AssistantLedger(root / "state" / "assistant.sqlite3"))
    third = a.post("/api/assistant/turn", json=body(2, tool_results=[
        {"step_index": 0, "tool_call_id": "call_1", "content": "{}"},
        {"step_index": 1, "tool_call_id": "call_2", "content": "{}"}])).json()
    assert third["refused"] is None and third["budget"]["posts"] == 3
    assert len(model.requests) == 3


def test_retryable_step_is_not_blocked_by_old_post_budget(gateway, monkeypatch):
    a, _, install, _ = gateway
    monkeypatch.setattr(ledger_module, "MAX_POSTS", 1, raising=False)
    monkeypatch.setattr(tool_chat_module, "overload_wait", lambda *args: 0.0)
    model = install(Model(lambda request: httpx.Response(429,json={"error":{"message":"busy"}}),save_call_stream()))
    assert a.post("/api/assistant/turn",json=body()).json()["state"] == "retryable"
    assert a.post("/api/assistant/turn",json=body()).json()["state"] == "completed"
    assert len(model.requests) == 2


def test_old_run_time_budget_does_not_refuse_a_model_request(gateway, monkeypatch):
    a, _, install, _ = gateway
    model = install(Model(save_call_stream()))
    monkeypatch.setattr(ledger_module, "MAX_MODEL_SECONDS", 0.5, raising=False)
    result = a.post("/api/assistant/turn", json=body()).json()
    assert result["state"] == "completed" and len(model.requests) == 1


def test_old_step_limit_does_not_stop_a_valid_next_step(gateway, monkeypatch):
    a, _, install, _ = gateway
    monkeypatch.setattr(ledger_module, "MAX_STEPS", 1, raising=False)
    model = install(Model(save_call_stream(),final_stream()))
    a.post("/api/assistant/turn", json=body())
    result = a.post("/api/assistant/turn", json=body(1, tool_results=[
        {"step_index":0,"tool_call_id":"call_1","content":"{}"}])).json()
    assert result["state"] == "completed" and len(model.requests) == 2


def test_cancel_fences_later_steps_but_keeps_completed_results(gateway):
    a, _, install, _ = gateway
    model = install(Model(save_call_stream()))
    a.post("/api/assistant/turn", json=body())
    assert a.post("/api/assistant/runs/run_1/cancel").json()["cancelled"] is True
    later = a.post("/api/assistant/turn", json=body(1, tool_results=[
        {"step_index": 0, "tool_call_id": "call_1", "content": "{}"}])).json()
    assert later["refused"] == "cancelled" and len(model.requests) == 1
    assert a.post("/api/assistant/turn", json=body()).json()["state"] == "completed"
    # Cancel before the first step also fences it.
    assert a.post("/api/assistant/runs/run_2/cancel").status_code == 200
    assert a.post("/api/assistant/turn", json=body(run_id="run_2")).json()["refused"] == "cancelled"


def test_runs_are_isolated_per_account_and_context_is_frozen(gateway):
    a, b, install, _ = gateway
    model = install(Model(save_call_stream(), save_call_stream()))
    a.post("/api/assistant/turn", json=body())
    assert b.get("/api/assistant/runs/run_1/steps/step_0").status_code == 404
    assert b.post("/api/assistant/turn", json=body()).json()["state"] == "completed"
    assert len(model.requests) == 2
    moved = body(1, tool_results=[{"step_index": 0, "tool_call_id": "call_1", "content": "{}"}], page=9)
    assert a.post("/api/assistant/turn", json=moved).status_code == 409


def test_requires_login_and_same_origin(gateway):
    a, _, install, root = gateway
    install(Model())
    assert a.post("/api/assistant/turn", json=body(),
                  headers={"Origin": "https://elsewhere.example"}).status_code == 403
    a.cookies.clear()
    assert a.post("/api/assistant/turn", json=body()).status_code == 401


def test_concurrent_identical_requests_post_once(gateway):
    a, _, install, _ = gateway
    gate = threading.Event()

    def slow(request):
        gate.wait(5)
        return httpx.Response(200, text=save_call_stream(), headers={"content-type": "text/event-stream"})

    model = install(Model(slow))
    results = []
    first = threading.Thread(target=lambda: results.append(a.post("/api/assistant/turn", json=body()).json()))
    first.start()
    deadline = time.monotonic() + 5
    while not model.requests and time.monotonic() < deadline:
        time.sleep(0.01)
    concurrent = a.post("/api/assistant/turn", json=body()).json()
    assert concurrent["state"] == "submitting"
    gate.set()
    first.join(5)
    assert results[0]["state"] == "completed" and len(model.requests) == 1


def test_expired_owner_lease_becomes_uncertain_and_late_result_still_lands(tmp_path, monkeypatch):
    ledger = AssistantLedger(tmp_path / "assistant.sqlite3")
    monkeypatch.setattr(ledger_module, "LEASE_GRACE_SECONDS", -1.0)
    claim = ledger.claim("o", "r", "s0", 0, context_digest="c", digest="d", lease_seconds=0,
                         estimated_input_tokens=10)
    assert claim.send
    step, budget = ledger.step("o", "r", "s0")
    assert step.state == "uncertain" and budget.posts == 1
    assert not ledger.claim("o", "r", "s0", 0, context_digest="c", digest="d", lease_seconds=0,
                            estimated_input_tokens=10).send
    step, budget = ledger.finish("o", "r", "s0", claim.step.attempt, state="completed", posted=True,
                                 elapsed=1.0, input_tokens=10, output_tokens=5,
                                 message={"role": "assistant", "content": "ok"}, finish_reason="stop")
    assert step.state == "completed" and budget.posts == 1 and budget.output_tokens == 5


def test_expired_owner_lease_charges_estimated_input_and_lease_seconds(tmp_path):
    path = tmp_path / "assistant.sqlite3"
    ledger = AssistantLedger(path)
    claim = ledger.claim("o", "r", "s0", 0, context_digest="c", digest="d", lease_seconds=30,
                         estimated_input_tokens=321)
    assert claim.send
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE assistant_steps SET updated=?,lease_until=? "
            "WHERE owner='o' AND run_id='r' AND step_id='s0'",
            (time.time() - 7.0, time.time() - 1.0),
        )
    step, budget = ledger.step("o", "r", "s0")
    assert step.state == "uncertain"
    assert budget.posts == 1 and budget.input_tokens == 321
    assert 6.5 <= budget.model_seconds < 60


def test_late_finish_after_expiry_does_not_double_charge(tmp_path):
    path = tmp_path / "assistant.sqlite3"
    ledger = AssistantLedger(path)
    claim = ledger.claim("o", "r", "s0", 0, context_digest="c", digest="d", lease_seconds=30,
                         estimated_input_tokens=321)
    assert claim.send
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE assistant_steps SET updated=?,lease_until=? "
            "WHERE owner='o' AND run_id='r' AND step_id='s0'",
            (time.time() - 7.0, time.time() - 1.0),
        )
    step, budget = ledger.step("o", "r", "s0")
    assert step.state == "uncertain"
    assert budget.posts == 1 and budget.input_tokens == 321
    assert 6.5 <= budget.model_seconds < 60

    step, budget = ledger.finish("o", "r", "s0", claim.step.attempt, state="completed", posted=True,
                                 elapsed=2.0, input_tokens=100, output_tokens=5,
                                 message={"role": "assistant", "content": "ok"}, finish_reason="stop")
    assert step.state == "completed"
    assert budget.posts == 1
    assert budget.input_tokens == 100
    assert budget.output_tokens == 5
    assert 1.5 <= budget.model_seconds <= 2.5


def test_late_finish_after_expiry_without_a_post_refunds_the_conservative_charge(tmp_path):
    path = tmp_path / "assistant.sqlite3"
    ledger = AssistantLedger(path)
    claim = ledger.claim("o", "r", "s0", 0, context_digest="c", digest="d", lease_seconds=30,
                         estimated_input_tokens=321)
    assert claim.send
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE assistant_steps SET updated=?,lease_until=? "
            "WHERE owner='o' AND run_id='r' AND step_id='s0'",
            (time.time() - 7.0, time.time() - 1.0),
        )
    step, budget = ledger.step("o", "r", "s0")
    assert step.state == "uncertain"
    assert budget.posts == 1 and budget.input_tokens == 321
    assert 6.5 <= budget.model_seconds < 60

    # The owner came back and reported that no request reached the provider.
    step, budget = ledger.finish("o", "r", "s0", claim.step.attempt, state="failed",
                                 posted=False, elapsed=0.0)
    assert step.state == "failed"
    assert budget.posts == 0
    assert budget.input_tokens == 0
    assert budget.model_seconds == pytest.approx(0.0, abs=1e-6)


def test_unsupported_route_reports_unavailable_without_claiming(gateway, monkeypatch):
    a, _, _, _ = gateway
    monkeypatch.setattr(api, "assistant_tool_chat", lambda: None)
    assert a.get("/api/assistant/capabilities").json()["available"] is False
    assert a.post("/api/assistant/turn", json=body()).status_code == 503
    assert a.get("/api/assistant/runs/run_1/steps/step_0").status_code == 404


def test_provider_cache_hits_are_persisted_with_the_run_budget(gateway):
    a, _, install, _ = gateway
    install(Model(sse(
        {"choices": [{"index": 0, "delta": {"content": "解释"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 1000, "completion_tokens": 8,
                                  "prompt_cache_hit_tokens": 896, "prompt_cache_miss_tokens": 104}},
    )))
    result = a.post("/api/assistant/turn", json=body()).json()
    assert result["state"] == "completed"
    assert result["budget"]["input_tokens"] == 1000
    assert result["budget"]["cached_input_tokens"] == 896
    status = a.get(f"/api/assistant/runs/{result['run_id']}/steps/{result['step_id']}").json()
    assert status["budget"]["cached_input_tokens"] == 896


def test_existing_ledger_without_cached_column_is_migrated(tmp_path):
    path = tmp_path / "assistant.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE assistant_runs (
                owner TEXT NOT NULL, run_id TEXT NOT NULL, context_digest TEXT NOT NULL,
                state TEXT NOT NULL, posts INTEGER NOT NULL,
                input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                model_seconds REAL NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                PRIMARY KEY(owner,run_id));
            CREATE TABLE assistant_steps (
                owner TEXT NOT NULL, run_id TEXT NOT NULL, step_id TEXT NOT NULL,
                step_index INTEGER NOT NULL, request_digest TEXT NOT NULL,
                state TEXT NOT NULL, attempt INTEGER NOT NULL, lease_until REAL NOT NULL,
                message_json TEXT, finish_reason TEXT, error TEXT, retry_after REAL,
                created REAL NOT NULL, updated REAL NOT NULL,
                PRIMARY KEY(owner,run_id,step_id),
                UNIQUE(owner,run_id,step_index),
                FOREIGN KEY(owner,run_id) REFERENCES assistant_runs(owner,run_id));
        """)
    ledger = AssistantLedger(path)
    with sqlite3.connect(path) as db:
        step_columns = {row[1] for row in db.execute("PRAGMA table_info(assistant_steps)")}
    assert "estimated_input_tokens" in step_columns
    assert "expire_charged_input_tokens" in step_columns
    assert "expire_charged_seconds" in step_columns
    assert "expire_charged_posts" in step_columns
    claim = ledger.claim("o", "r", "s0", 0, context_digest="c", digest="d", lease_seconds=30,
                         estimated_input_tokens=10)
    assert claim.send
    step, budget = ledger.finish("o", "r", "s0", claim.step.attempt, state="completed",
                                 posted=True, elapsed=0.5, input_tokens=100, output_tokens=5,
                                 cached_input_tokens=80,
                                 message={"role": "assistant", "content": "ok"})
    assert step.state == "completed"
    assert budget.cached_input_tokens == 80
    # A fresh open reads the migrated column back.
    reopened = AssistantLedger(path)
    step, budget = reopened.step("o", "r", "s0")
    assert step.state == "completed" and budget.cached_input_tokens == 80
