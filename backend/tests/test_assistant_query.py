"""Unified chat uses one model and durable raw read-tool continuations."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from native_harness import api, configure

from adaptive_learning.api import assistant_query_routes as routes
from adaptive_learning.api.assistant_routes import assistant_router
from adaptive_learning.assistant_ledger import AssistantLedger
from adaptive_learning.llm.client import LLMConfig
from adaptive_learning.llm.model_admission import admission
from adaptive_learning.llm.tool_chat import ToolChatClient


def stream(content="", tool=None, finish="stop"):
    delta = {"content": content}
    if tool:
        name, arguments = tool
        delta = {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                "function": {"name": name, "arguments": json.dumps(arguments)}}]}
    chunks = [{"choices": [{"index": 0, "delta": delta}]},
              {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
              {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 15}}]
    return "".join("data: " + json.dumps(item, ensure_ascii=False) + "\n\n" for item in chunks) + "data: [DONE]\n\n"


class Model:
    def __init__(self, *script):
        self.script, self.requests = list(script), []

    def __call__(self, request):
        self.requests.append(json.loads(request.content))
        item = self.script.pop(0)
        if callable(item):
            return item(request)
        return httpx.Response(200, text=item, headers={"content-type": "text/event-stream"})


def login(client, email):
    challenge = client.post("/api/auth/code", json={"email": email, "purpose": "register"}).json()
    response = client.post("/api/auth/verify", json={
        "challenge_id": challenge["challenge_id"], "code": challenge["demo_code"],
        "registration": {"nickname": "读者", "stage": "university", "interests": [], "goal": "interest"}})
    assert response.status_code == 200, response.text


class ReadTools:
    schemas = {"list_learning_resources": {"type": "object", "properties": {},
                                          "additionalProperties": False},
               "search_learning_materials": {"type": "object", "properties": {},
                                             "additionalProperties": False},
               "list_public_courses": {"type": "object", "properties": {},
                                       "additionalProperties": False}}
    descriptions = {name: "Read raw authorized facts" for name in schemas}

    def __init__(self, state, services, actor, context=None, local_courses=None):
        self.state, self.actor = state, actor
        state.constructions.append((actor, context, local_courses))

    def access_scope(self):
        return self.state.scope

    def execute(self, name, arguments):
        self.state.executions.append((self.actor, name, arguments))
        return self.state.result


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    app = configure(tmp_path, monkeypatch)
    app.include_router(routes.assistant_query_router(api))
    app.include_router(assistant_router(api))
    ledger = AssistantLedger(tmp_path / "state" / "assistant.sqlite3")
    monkeypatch.setattr(api, "assistant_ledger", ledger)
    state = SimpleNamespace(scope=["book:biology", "book:chemistry"], result={
        "courses": [{"id": "biology", "name": "遗传与进化"}, {"id": "chemistry", "name": "化学"}]},
        executions=[], constructions=[])
    monkeypatch.setattr(routes, "QueryTools", lambda *a, **k: ReadTools(state, *a, **k))
    monkeypatch.setattr(api, "answer_book_question", lambda *a, **k: pytest.fail("old QA classifier must not run"))
    holder = {}
    admission._states.clear()

    def install(model):
        holder["client"] = ToolChatClient(
            LLMConfig("https://api.deepseek.com/v1", "test-key", "deepseek-chat",
                      timeout_seconds=30, max_retries=0), transport=httpx.MockTransport(model))
        return model

    monkeypatch.setattr(api, "assistant_tool_chat", lambda: holder.get("client"))
    with TestClient(app) as a, TestClient(app) as b:
        login(a, "reader@zhiwo.test")
        login(b, "xiaolin@zhiwo.test")
        yield a, b, install, state, ledger
    for client in holder.values():
        client.close()
    admission._states.clear()


def body(**kwargs):
    return {"request_id": "chat_1", "question": "当前正在学习的课程", "history": [],
            "context": {"book_id": "chemistry", "course_title": "化学"}, "local_courses": [], **kwargs}


def test_original_clarification_loop_becomes_one_model_read_then_answer(gateway):
    a, _, install, state, ledger = gateway
    old_history = [{"role": "user", "content": "当前有哪些课程？"},
                   {"role": "assistant", "content": "是教材章节还是你目前在学的课程？"},
                   {"role": "user", "content": "目前正在学习的课程有哪些？"},
                   {"role": "assistant", "content": "请说明是教材章节还是个人课程。"}]
    model = install(Model(stream(tool=("list_learning_resources", {}), finish="tool_calls"),
                          stream("你目前有遗传与进化和化学两门课程。")))
    response = a.post("/api/assistant/query", json=body(history=old_history))
    assert response.status_code == 200, response.text
    assert "遗传与进化" in response.json()["answer"]
    assert response.headers["cache-control"] == "private, no-store"
    assert len(model.requests) == 2 and len(state.executions) == 1
    assert model.requests[0]["messages"][1:5] == old_history
    second = model.requests[1]["messages"]
    assert second[-1]["role"] == "tool" and "遗传与进化" in second[-1]["content"]
    assert not any(any(key.startswith("_query_") for key in message) for message in second)
    owner = api.account_repository.session_owner(a.cookies.get("zhiwo_account"))
    saved, budget = ledger.step(owner, "query:chat_1", "query_0")
    assert saved.message["_query_tool_results"][0]["result"] == state.result
    assert budget.posts == 2


def test_completed_result_replays_without_model_or_tools(gateway):
    a, _, install, state, _ = gateway
    model = install(Model(stream(tool=("list_learning_resources", {}), finish="tool_calls"), stream("两门课程")))
    first = a.post("/api/assistant/query", json=body())
    again = a.post("/api/assistant/query", json=body())
    assert first.json() == again.json()
    assert len(model.requests) == 2 and len(state.executions) == 1
    changed = body(question="改了一个问题")
    assert a.post("/api/assistant/query", json=changed).status_code == 409
    assert len(model.requests) == 2


def test_query_output_limit_survives_replay_without_limiting_agent_ledger(gateway, monkeypatch):
    a, _, install, state, ledger = gateway
    monkeypatch.setattr(routes, "MAX_QUERY_OUTPUT_TOKENS", 10)
    model = install(Model(stream(tool=("list_learning_resources", {}), finish="tool_calls")))
    for _ in range(2):
        response = a.post("/api/assistant/query", json=body())
        assert response.status_code == 409, response.text
        assert "处理上限" in response.json()["detail"]
    assert len(model.requests) == 1
    assert len(state.executions) == 1
    owner = api.account_repository.session_owner(a.cookies.get("zhiwo_account"))
    _, budget = ledger.step(owner, "query:chat_1", "query_0")
    assert budget.output_tokens >= 10
    assert budget.exhausted() is None, "Query policy must not reinstate Agent-wide limits"


def test_query_step_limit_keeps_completed_tool_receipts_on_replay(gateway, monkeypatch):
    a, _, install, state, _ = gateway
    monkeypatch.setattr(routes, "MAX_QUERY_STEPS", 1)
    model = install(Model(stream(tool=("list_learning_resources", {}), finish="tool_calls")))
    for _ in range(2):
        response = a.post("/api/assistant/query", json=body())
        assert response.status_code == 409, response.text
        assert "步骤上限" in response.json()["detail"]
    assert len(model.requests) == 1
    assert len(state.executions) == 1


def test_retry_after_unsent_continuation_uses_stored_raw_tool_result(gateway):
    a, _, install, state, _ = gateway

    def not_submitted(request):
        raise httpx.ConnectError("connection was not established", request=request)

    model = install(Model(stream(tool=("list_learning_resources", {}), finish="tool_calls"),
                          not_submitted, stream("继续使用已读取的两门课程")))
    assert a.post("/api/assistant/query", json=body()).status_code == 503
    # A restart can read the result from SQLite; raw tools do not run twice.
    api.assistant_ledger = AssistantLedger(api.assistant_ledger.path)
    response = a.post("/api/assistant/query", json=body())
    assert response.status_code == 200, response.text
    assert len(state.executions) == 1 and len(model.requests) == 3
    assert model.requests[2]["messages"][-1]["role"] == "tool"
    assert "遗传与进化" in model.requests[2]["messages"][-1]["content"]


def test_no_selected_book_general_question_and_latest_context_are_accepted(gateway):
    a, _, install, state, _ = gateway
    model = install(Model(stream("可以。")))
    request = body(question="什么是氧化还原反应？", context={}, history=[
        {"role": "user", "content": "刚才的附件摘录：化学学习资料"},
        {"role": "assistant", "content": "我看到了。"},
        {"role": "user", "content": "什么是氧化还原反应？"}])
    response = a.post("/api/assistant/query", json=request)
    assert response.status_code == 200
    sent = model.requests[0]["messages"]
    assert sent[1]["content"] == "刚才的附件摘录：化学学习资料"
    assert len(sent) == 4 and sent[-1]["content"].count("什么是氧化还原反应？") == 1
    assert not state.executions


def test_only_verified_retrieved_citations_are_returned(gateway):
    a, _, install, state, _ = gateway
    evidence = {"evidence_id": "ev_biology_1", "book_id": "biology", "page_number": 3,
                "block_id": "block_3", "quote": "染色体只复制一次，细胞分裂两次。"}
    state.result = {"evidence": [evidence]}
    install(Model(stream(tool=("search_learning_materials", {}), finish="tool_calls"),
                  stream("细胞分裂两次。[ev_biology_1] 编造来源。[ev_fake]")))
    response = a.post("/api/assistant/query", json=body()).json()
    assert response["citations"] == [evidence]
    assert "[1]" in response["answer"] and "ev_fake" not in response["answer"]
    assert response["source_label"] == "textbook" and response["course_ids"] == []


def test_source_label_is_derived_from_tools_read_and_citations(gateway):
    a, _, install, state, _ = gateway
    install(Model(stream(tool=("list_learning_resources", {}), finish="tool_calls"),
                  stream("你一共有 2 门课程。"), stream("氧化还原是电子转移。")))
    records = a.post("/api/assistant/query", json=body(question="当前有多少门课程")).json()
    assert records["source_label"] == "learning_records" and records["citations"] == []
    general = a.post("/api/assistant/query", json=body(request_id="chat_2", question="什么是氧化还原")).json()
    assert general["source_label"] == "general"


def test_textbook_text_seen_but_not_cited_is_never_labelled_general(gateway):
    a, _, install, state, _ = gateway
    state.result = {"ok": True, "evidence": [{"evidence_id": "ev_abc", "book_id": "chemistry",
                                              "page_number": 3, "quote": "氧化是失去电子。"}]}
    install(Model(stream(tool=("search_learning_materials", {}), finish="tool_calls"),
                  stream("氧化是失去电子的过程。"),
                  stream("这一段讲的是电子转移。")))
    searched = a.post("/api/assistant/query", json=body(question="什么是氧化")).json()
    assert searched["source_label"] == "textbook_uncited" and searched["citations"] == []
    page = {"pdf_page": 3, "page_text": "氧化是失去电子。", "note_text": "", "note_truncated": False}
    reader = a.post("/api/assistant/query", json=body(
        request_id="chat_2", question="解释这段",
        context={"book_id": "chemistry", "reading_context": page})).json()
    assert reader["source_label"] == "textbook_uncited"


def test_empty_device_evidence_keeps_the_pre_upgrade_request_digest(gateway):
    a, _, install, _, _ = gateway
    model = install(Model(stream("你的化学资料")))
    assert a.post("/api/assistant/query", json=body()).status_code == 200
    replay = a.post("/api/assistant/query", json=body(device_evidence=[]))
    assert replay.status_code == 200 and replay.json()["answer"] == "你的化学资料"
    assert len(model.requests) == 1


def test_course_count_prompt_requires_reading_courses_without_page_scope(gateway):
    a, _, install, _, _ = gateway
    model = install(Model(stream("好的")))
    assert a.post("/api/assistant/query", json=body(question="当前有多少门课程")).status_code == 200
    system = model.requests[0]["messages"][0]["content"]
    assert "total_courses" in system and "list_learning_resources" in system
    assert "不能只查当前书" in system
    # The stable cacheable prefix is identical regardless of page context.
    other = install(Model(stream("好的")))
    a.post("/api/assistant/query", json=body(request_id="chat_2", context={"book_id": "biology"}))
    assert other.requests[0]["messages"][0] == model.requests[0]["messages"][0]


def test_public_course_links_only_for_named_catalog_entries(gateway):
    a, _, install, state, _ = gateway
    state.result = {"ok": True, "scope": "public_catalog_metadata", "courses": [
        {"id": "community_genetics", "title": "遗传与进化"},
        {"id": "community_post_7", "title": "有机化学基础"}], "evidence": []}
    install(Model(stream(tool=("list_public_courses", {}), finish="tool_calls"),
                  stream("可以看看《遗传与进化》。")))
    response = a.post("/api/assistant/query", json=body(question="发现里有什么生物课")).json()
    assert response["course_ids"] == ["community_genetics"]
    assert response["source_label"] == "public_catalog"


def test_public_course_links_follow_answer_order(gateway):
    a, _, install, state, _ = gateway
    state.result = {"ok": True, "scope": "public_catalog_metadata", "courses": [
        {"id": "community_genetics", "title": "遗传与进化"},
        {"id": "community_post_7", "title": "有机化学基础"}], "evidence": []}
    install(Model(stream(tool=("list_public_courses", {}), finish="tool_calls"),
                  stream("先学《有机化学基础》，再学《遗传与进化》。")))
    response = a.post("/api/assistant/query", json=body(question="发现里有什么课")).json()
    assert response["course_ids"] == ["community_post_7", "community_genetics"]


def test_legacy_public_catalog_scope_entry_does_not_block_replay(gateway):
    a, _, install, state, _ = gateway
    state.scope = ["book:biology", "public_catalog_metadata:" + "0" * 64]
    model = install(Model(stream("你的生物资料")))
    assert a.post("/api/assistant/query", json=body()).status_code == 200
    state.scope = ["book:biology"]
    replay = a.post("/api/assistant/query", json=body())
    assert replay.status_code == 200 and replay.json()["answer"] == "你的生物资料"
    assert len(model.requests) == 1


def test_device_pending_evidence_is_citable_but_only_for_local_books(gateway):
    a, _, install, state, _ = gateway
    excerpt = {"book_id": "book_local_abc123def456", "page_number": 4, "quote": "光合作用产生氧气。"}
    model = install(Model(lambda request: httpx.Response(200, text=stream(
        "光合作用会释放氧气。[" + json.loads(request.content)["messages"][-1]["content"]
        .split('"evidence_id": "')[1].split('"')[0] + "]"),
        headers={"content-type": "text/event-stream"})))
    response = a.post("/api/assistant/query", json=body(device_evidence=[excerpt])).json()
    assert response["source_label"] == "textbook" and "[1]" in response["answer"]
    assert response["citations"][0]["book_id"] == excerpt["book_id"]
    assert response["citations"][0]["page_number"] == 4
    assert "光合作用产生氧气" in model.requests[0]["messages"][-1]["content"]
    forged = {**excerpt, "book_id": "biology"}
    assert a.post("/api/assistant/query", json=body(request_id="chat_2",
                                                     device_evidence=[forged])).status_code == 422
    assert len(model.requests) == 1


def test_request_wall_clock_ends_before_the_gateway_timeout():
    assert routes.MAX_REQUEST_SECONDS + 30 <= 300


def test_revoked_resource_prevents_replaying_stored_answer(gateway):
    a, _, install, state, _ = gateway
    model = install(Model(stream("你的化学资料")))
    assert a.post("/api/assistant/query", json=body()).status_code == 200
    state.scope = ["book:biology"]
    assert a.post("/api/assistant/query", json=body()).status_code == 409
    assert len(model.requests) == 1


def test_note_protocol_cannot_read_or_resume_reserved_query_runs(gateway):
    a, _, install, _, _ = gateway
    model = install(Model(stream("账号的查询资料")))
    assert a.post("/api/assistant/query", json=body()).status_code == 200
    assert a.get("/api/assistant/runs/query:chat_1/steps/query_0").status_code == 404
    note_request = {
        "contract_version": 1, "run_id": "query:chat_1", "step_id": "query_0",
        "step_index": 0, "request": "整理笔记", "context": {
            "book_id": "chemistry", "source_version": "v1", "page": 1},
        "evidence": [], "tools": ["save_note"], "tool_results": []}
    assert a.post("/api/assistant/turn", json=note_request).status_code == 409
    assert len(model.requests) == 1


def test_account_scopes_and_same_request_identifier_do_not_share_answers(gateway):
    a, b, install, state, _ = gateway
    model = install(Model(stream("甲的回答"), stream("乙的回答")))
    assert a.post("/api/assistant/query", json=body()).json()["answer"] == "甲的回答"
    assert b.post("/api/assistant/query", json=body()).json()["answer"] == "乙的回答"
    assert state.constructions[0][0] != state.constructions[1][0]
    assert len(model.requests) == 2


def test_truncated_paid_response_is_unknown_and_never_resent(gateway):
    a, _, install, _, _ = gateway
    truncated = 'data: {"choices":[{"index":0,"delta":{"content":"一半"}}]}\n\n'
    model = install(Model(truncated))
    assert a.post("/api/assistant/query", json=body()).status_code == 409
    assert a.post("/api/assistant/query", json=body()).status_code == 409
    assert len(model.requests) == 1


def test_concurrent_same_question_submits_only_one_model_post(gateway):
    a, _, install, _, _ = gateway
    entered, release = threading.Event(), threading.Event()

    def slow(_):
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, text=stream("完成"), headers={"content-type": "text/event-stream"})

    model = install(Model(slow))
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(a.post, "/api/assistant/query", json=body())
        assert entered.wait(5)
        try:
            other = a.post("/api/assistant/query", json=body())
            assert other.status_code == 409
        finally:
            release.set()
        assert first.result(5).status_code == 200
    assert len(model.requests) == 1


def test_write_tools_are_not_available_or_executed(gateway):
    a, _, install, state, _ = gateway
    model = install(Model(stream(tool=("save_note", {"body": "伪造"}), finish="tool_calls")))
    assert a.post("/api/assistant/query", json=body()).status_code == 502
    assert not state.executions
    assert all(tool["function"]["name"] != "save_note" for tool in model.requests[0]["tools"])


def test_provider_decline_does_not_automatically_repeat_paid_post(gateway):
    a, _, install, _, _ = gateway
    model = install(Model(lambda _: httpx.Response(429, json={"error": "busy"}, headers={"Retry-After": "10"})))
    assert a.post("/api/assistant/query", json=body()).status_code == 503
    assert a.post("/api/assistant/query", json=body()).status_code == 503
    assert len(model.requests) == 1


def test_origin_auth_and_size_are_checked_before_model(gateway):
    a, _, install, _, _ = gateway
    model = install(Model())
    assert a.post("/api/assistant/query", json=body(), headers={"Origin": "https://other.test"}).status_code == 403
    request = body(history=[{"role": "user", "content": "x" * 24_000}] * 6)
    assert a.post("/api/assistant/query", json=request).status_code == 413
    assert not model.requests


def test_material_search_http_is_authenticated_read_only_and_has_strict_arguments(gateway):
    a, b, _, state, ledger = gateway
    arguments = {"query": "遗传物质", "course_ids": ["workspace_biology"],
                 "book_ids": ["biology"], "include_images": True, "limit": 3}
    response = a.post("/api/assistant/search-materials", json=arguments)
    assert response.status_code == 200 and response.json() == state.result
    assert response.headers["cache-control"] == "private, no-store"
    assert state.executions[-1][1:] == ("search_learning_materials", arguments)
    # No model client is installed in this test: this route must not need one.
    count = len(state.executions)
    for bad in [{**arguments, "owner": "other"}, {**arguments, "limit": 13},
                {**arguments, "course_ids": []}, {**arguments, "query": "x"}]:
        assert a.post("/api/assistant/search-materials", json=bad).status_code == 422
    assert len(state.executions) == count
    b.cookies.clear()
    assert b.post("/api/assistant/search-materials", json=arguments).status_code == 401


def test_local_course_workspace_mapping_is_optional_for_existing_query_clients():
    plain = routes.LocalCourse(id="local-course:one", name="草稿", book_ids=[])
    assert plain.workspace_id is None
    mapped = routes.LocalCourse(id="local-course:one", name="草稿", book_ids=[], workspace_id="workspace_one")
    assert mapped.workspace_id == "workspace_one"


def test_optional_workspace_mapping_keeps_legacy_query_request_digest(gateway):
    a, _, install, _, ledger = gateway
    install(Model(stream("保留原请求")))
    request = body(local_courses=[{"id": "local-course:one", "name": "草稿", "book_ids": []}])
    assert a.post("/api/assistant/query", json=request).status_code == 200
    actor = api.account_repository.session_owner(a.cookies.get("zhiwo_account"))
    saved, _ = ledger.step(actor, "query:chat_1", "query_0")
    legacy = routes.QueryRequest.model_validate(request).model_dump(mode="json")
    legacy.pop("device_evidence")
    for course in legacy["local_courses"]:
        course.pop("workspace_id")
    assert saved.request_digest == routes._digest(legacy)
