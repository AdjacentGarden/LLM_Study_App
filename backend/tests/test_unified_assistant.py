import base64
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO

import pytest
from native_harness import api
from PIL import Image
from test_assistant_turn import Model, body, final_stream, save_call_stream
from test_assistant_turn import gateway as gateway

from adaptive_learning.assistant_ledger import AssistantLedger, LedgerConflict
from adaptive_learning.assistant_sessions import AssistantSessions
from adaptive_learning.native import NativeConflict, NativeRepository


def v2(session="chat", run="run", index=0, receipts=(), **kwargs):
    value = body(step_index=index, run_id=run, tool_results=receipts)
    value.update(
        contract_version=2,
        session_id=session,
        message_id=run + "_message",
        context={},
        evidence=[],
        **kwargs,
    )
    return value


def test_unselected_chat_history_incremental_and_account_isolated(gateway):
    a, b, install, _ = gateway
    model = install(Model(final_stream("第一轮"), final_stream("第二轮")))
    assert a.post("/api/assistant/turn", json=v2()).status_code == 200
    result = a.post("/api/assistant/turn", json=v2(run="next")).json()
    assert result["state"] == "completed"
    assert "第一轮" in str(model.requests[-1]["messages"])
    assert b.get("/api/assistant/conversations/chat").status_code == 404
    assert len(a.get("/api/assistant/conversations").json()) == 1


def test_six_saved_conversations_and_delete_does_not_delete_outputs(gateway):
    a, _, install, _ = gateway
    model = install(Model(*[final_stream() for _ in range(7)]))
    for i in range(6):
        assert (
            a.post("/api/assistant/turn", json=v2(session=f"chat{i}", run=f"run{i}")).status_code
            == 200
        )
    refused = a.post("/api/assistant/turn", json=v2(session="seventh", run="seventh"))
    assert refused.status_code == 409 and "会话数上限" in refused.text
    assert len(model.requests) == 6
    actor = api.account_repository.session_owner(a.cookies.get("zhiwo_account"))
    api.native_repository.save_workspace(actor, "course_history_output", "name", {})
    api.native_repository.execute_assistant_operation(
        actor,
        "save-output",
        "create_flashcards",
        {"course_id": "course_history_output", "cards": [{"front": "question", "back": "answer"}]},
    )
    a.delete("/api/assistant/conversations/chat0")
    assert len(api.native_repository.assistant_objects(actor)) == 1
    assert (
        a.post("/api/assistant/turn", json=v2(session="seventh", run="seventh")).status_code == 200
    )


def test_history_slot_check_is_atomic(tmp_path):
    sessions = AssistantSessions(AssistantLedger(tmp_path / "ledger.db"))
    for i in range(5):
        sessions.begin("a", str(i), str(i), "title", False)

    def claim(i):
        try:
            sessions.begin("a", str(i), str(i), "title", False)
            return True
        except LedgerConflict:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(claim, [5, 6])) == 1
    assert len(sessions.list("a")) == 6


@pytest.mark.parametrize(
    "tokens,done,state",
    [
        (899999, False, "open"),
        (900000, False, "finishing"),
        (999999, False, "finishing"),
        (1000000, False, "archived"),
        (900000, True, "archived"),
    ],
)
def test_capacity_boundaries_and_monotonic_restart(tmp_path, tokens, done, state):
    ledger = AssistantLedger(tmp_path / "ledger.db")
    sessions = AssistantSessions(ledger)
    sessions.begin("a", "s", "r", "title", False)
    assert sessions.capacity("a", "s", tokens, False, "r", False, done)["state"] == state
    reopened = AssistantSessions(AssistantLedger(tmp_path / "ledger.db"))
    assert reopened.get("a", "s")["state"] == state
    if state != "open":
        assert reopened.capacity("a", "s", 1, False, "r", False)["state"] == state
        with pytest.raises(LedgerConflict):
            reopened.begin("a", "s", "new", "title", False)


def test_small_config_rejected_before_model_and_without_saved_slot(gateway):
    a, _, install, _ = gateway
    model = install(Model(final_stream()))
    client = api.assistant_tool_chat()
    client.config = replace(client.config, context_window_tokens=999999)
    response = a.post("/api/assistant/turn", json=v2())
    assert response.status_code == 422 and "不足 1M" in response.text
    assert not model.requests and not a.get("/api/assistant/conversations").json()


def test_temporary_chat_is_not_in_any_persistent_archive(gateway):
    a, _, install, root = gateway
    install(Model(save_call_stream(), final_stream()))
    assert a.post("/api/assistant/turn", json=v2(ephemeral=True)).json()["state"] == "completed"
    assert a.get("/api/assistant/conversations").json() == []
    with sqlite3.connect(root / "state" / "assistant.sqlite3") as db:
        assert db.execute("select count(*) from assistant_steps").fetchone()[0] == 0
        assert db.execute("select count(*) from assistant_conversation_events").fetchone()[0] == 0
    assert a.get("/api/assistant/runs/run/steps/step_0").status_code == 200
    a.post("/api/assistant/conversations/chat/close")
    assert a.get("/api/assistant/conversations/chat").status_code == 404
    assert a.get("/api/assistant/runs/run/steps/step_0").status_code == 404


def test_images_reach_provider_and_context_is_current_usage(gateway):
    a, _, install, _ = gateway
    model = install(Model(final_stream()))
    image = BytesIO()
    Image.new("RGB", (10, 10)).save(image, format="PNG")
    result = a.post(
        "/api/assistant/turn",
        json=v2(
            attachments=[
                {
                    "attachment_id": "image1",
                    "media_type": "image/png",
                    "data_base64": base64.b64encode(image.getvalue()).decode(),
                }
            ]
        ),
    ).json()
    assert any(p["type"] == "image_url" for p in model.requests[0]["messages"][1]["content"])
    assert result["context_tokens"] == 200 and result["context_estimated"] is False


def test_more_than_twelve_model_submissions(gateway):
    a, _, install, _ = gateway
    model = install(Model(*[save_call_stream(f"call_{i}") for i in range(14)], final_stream()))
    for index in range(15):
        receipts = (
            []
            if index == 0
            else [{"step_index": index - 1, "tool_call_id": f"call_{index - 1}", "content": "{}"}]
        )
        response = a.post("/api/assistant/turn", json=v2(index=index, receipts=receipts))
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "completed", response.text
    assert len(model.requests) == 15
    assert a.get("/api/assistant/conversations/chat").json()["state"] == "open"


def test_cloud_mutations_are_idempotent_and_delete_isolated_shared_book(tmp_path):
    repo = NativeRepository(tmp_path / "native.db")
    for wid in ["a", "b"]:
        repo.save_workspace("owner", wid, "same", {})
        resource = repo.add_resource("owner", wid, "same-book")
        receipt = repo.execute_assistant_operation(
            "owner",
            "create" + wid,
            "create_flashcards",
            {
                "course_id": wid,
                "course_resource_id": resource["id"],
                "cards": [{"front": "question", "back": "answer"}],
            },
        )
        assert receipt == repo.execute_assistant_operation(
            "owner",
            "create" + wid,
            "create_flashcards",
            {
                "course_id": wid,
                "course_resource_id": resource["id"],
                "cards": [{"front": "question", "back": "answer"}],
            },
        )
    impact = repo.deletion_impact("owner", "a")
    assert impact["counts"]["flashcards"] == 1
    args = {"course_id": "a", "confirmed": True, "impact_version": impact["version"]}
    result = repo.execute_assistant_operation("owner", "remove", "remove_course", args)
    assert result == repo.execute_assistant_operation("owner", "remove", "remove_course", args)
    assert len(repo.assistant_objects("owner")) == 1
    assert repo.workspace("owner", "b")
    with pytest.raises(NativeConflict):
        repo.save_workspace("owner", "a", "same", {})
    assert repo.assistant_operation("other", "remove") is None


def test_capacity_finishes_existing_task_but_never_reopens(gateway):
    a, _, install, _ = gateway
    model = install(
        Model(
            save_call_stream().replace('"prompt_tokens": 120', '"prompt_tokens": 900000'),
            final_stream(),
        )
    )
    first = a.post("/api/assistant/turn", json=v2()).json()
    assert first["capacity_state"] == "finishing"
    assert a.post("/api/assistant/turn", json=v2(run="other")).status_code == 409
    second = a.post(
        "/api/assistant/turn",
        json=v2(index=1, receipts=[{"step_index": 0, "tool_call_id": "call_1", "content": "{}"}]),
    ).json()
    assert second["capacity_state"] == "archived"
    assert len(model.requests) == 2
    assert a.post("/api/assistant/turn", json=v2(run="new")).status_code == 409


def test_one_million_stops_actions_but_accepts_reconciliation(gateway):
    a, _, install, _ = gateway
    model = install(
        Model(save_call_stream().replace('"prompt_tokens": 120', '"prompt_tokens": 1000000'))
    )
    first = a.post("/api/assistant/turn", json=v2()).json()
    assert first["capacity_state"] == "archived"
    assert first["message"]["tool_calls"] == []
    receipt = a.post(
        "/api/assistant/conversations/chat/receipts",
        json={
            "run_id": "run",
            "tool_results": [
                {"step_index": 0, "tool_call_id": "call_1", "content": '{"state":"not_executed"}'}
            ],
        },
    )
    assert receipt.status_code == 200 and len(model.requests) == 1
    assert a.post("/api/assistant/turn", json=v2(index=1)).status_code == 409


def test_cost_sum_is_not_context_capacity(gateway):
    a, _, install, _ = gateway
    install(
        Model(
            *[
                final_stream().replace('"prompt_tokens": 200', '"prompt_tokens": 500000')
                for _ in range(2)
            ]
        )
    )
    a.post("/api/assistant/turn", json=v2())
    response = a.post("/api/assistant/turn", json=v2(run="next")).json()
    assert response["context_tokens"] == 500000 and response["capacity_state"] == "open"


def test_original_note_requires_course_but_old_note_and_independent_note_survive(tmp_path):
    from fastapi import HTTPException

    from adaptive_learning.studio import Studio
    from adaptive_learning.studio_models import NoteInput

    studio = Studio(tmp_path)
    original = {
        "id": "original_note_1",
        "book_id": "book",
        "revision": 0,
        "title": "original",
        "pages": [1],
    }
    with pytest.raises(HTTPException, match="无指定课程"):
        studio.save_note("owner", NoteInput.model_validate(original))
    separate = {**original, "id": "independent_note_1", "pages": []}
    assert studio.save_note("owner", NoteInput.model_validate(separate))["revision"] == 1
    # Existing ambiguous source notes are available for migration rather than
    # being inferred into the only currently visible course.
    legacy = {**original, "revision": 1}
    with studio.repo.connect() as db:
        import json

        db.execute(
            "INSERT INTO studio_notes VALUES(?,?,?,?,?,?,?)",
            (legacy["id"], "owner", "book", 1, json.dumps(legacy), 1, 1),
        )
    assert studio.save_note("owner", NoteInput.model_validate(legacy))["revision"] == 2


def test_scoped_studio_notes_and_assessment_records_delete_without_shared_book_loss(tmp_path):
    import json

    repo = NativeRepository(tmp_path / "native.sqlite3")
    resources = {}
    for wid in ("a", "b"):
        repo.save_workspace("owner", wid, "same", {})
        resources[wid] = repo.add_resource("owner", wid, "shared")["id"]
    with sqlite3.connect(tmp_path / "community.sqlite3") as db:
        db.executescript(
            "CREATE TABLE studio_notes(id TEXT,owner TEXT,data TEXT); CREATE TABLE studio_note_logical_inputs(note_id TEXT); CREATE TABLE studio_note_editions(note_id TEXT,job_id TEXT); CREATE TABLE studio_jobs(id TEXT,owner TEXT,data TEXT); CREATE TABLE studio_note_request_aliases(owner TEXT,job_id TEXT)"
        )
        for wid in ("a", "b", "unassigned"):
            db.execute(
                "INSERT INTO studio_notes VALUES(?,?,?)",
                (
                    wid,
                    "owner",
                    json.dumps({"book_id": "shared", "course_resource_id": resources.get(wid)}),
                ),
            )
    with sqlite3.connect(tmp_path / "assessments.sqlite3") as db:
        db.executescript(
            "CREATE TABLE learning_events(event_id TEXT,session_id TEXT); CREATE TABLE course_bundles(course_id TEXT,session_id TEXT,bundle_json TEXT); CREATE TABLE interview_sessions(session_id TEXT)"
        )
        for wid in ("a", "b"):
            db.execute(
                "INSERT INTO learning_events VALUES(?,?)", ("event_" + wid, "session_" + wid)
            )
            db.execute(
                "INSERT INTO course_bundles VALUES(?,?,?)",
                (
                    "bundle_" + wid,
                    "session_" + wid,
                    json.dumps({"flashcards": [{"card_id": "card_" + wid}]}),
                ),
            )
            db.execute("INSERT INTO interview_sessions VALUES(?)", ("session_" + wid,))
    with repo.connect() as db:
        for wid in ("a", "b"):
            db.execute(
                "UPDATE native_resources SET session_id=? WHERE id=?",
                ("session_" + wid, resources[wid]),
            )
    impact = repo.deletion_impact("owner", "a")
    assert impact["counts"]["notes"] == 1 and impact["unassigned_legacy_notes_preserved"] == 1
    assert impact["counts"]["flashcards"] == 1 and impact["counts"]["learning_records"] == 1
    repo.delete_workspace("owner", "a")
    with sqlite3.connect(tmp_path / "community.sqlite3") as db:
        assert [r[0] for r in db.execute("SELECT id FROM studio_notes ORDER BY id")] == [
            "b",
            "unassigned",
        ]
    with sqlite3.connect(tmp_path / "assessments.sqlite3") as db:
        assert db.execute("SELECT session_id FROM learning_events").fetchall() == [("session_b",)]
        assert db.execute("SELECT session_id FROM course_bundles").fetchall() == [("session_b",)]
        assert db.execute("SELECT session_id FROM interview_sessions").fetchall() == [
            ("session_b",)
        ]


def test_estimated_overflow_returns_archived_state_without_provider_post(gateway, monkeypatch):
    from adaptive_learning.api import assistant_routes

    a, _, install, _ = gateway
    model = install(Model(final_stream()))
    monkeypatch.setattr(assistant_routes, "estimate_context", lambda *args: 1000000)
    response = a.post("/api/assistant/turn", json=v2()).json()
    assert response["state"] == "refused" and response["refused"] == "context_capacity"
    assert response["capacity_state"] == "archived" and not model.requests


def test_attachment_reference_only_resolves_inside_current_session(gateway):
    a, _, install, _ = gateway
    image = BytesIO()
    Image.new("RGB", (10, 10)).save(image, format="PNG")
    attachment = {
        "attachment_id": "image1",
        "media_type": "image/png",
        "data_base64": base64.b64encode(image.getvalue()).decode(),
    }
    model = install(Model(final_stream(), final_stream()))
    a.post("/api/assistant/turn", json=v2(attachments=[attachment]))
    assert (
        a.post(
            "/api/assistant/turn", json=v2(run="next", attachments=[{"attachment_id": "image1"}])
        ).status_code
        == 200
    )
    images = [
        part
        for m in model.requests[-1]["messages"]
        if isinstance(m["content"], list)
        for part in m["content"]
        if part["type"] == "image_url"
    ]
    assert len(images) == 1
    assert (
        a.post(
            "/api/assistant/turn",
            json=v2(session="other", run="foreign", attachments=[{"attachment_id": "image1"}]),
        ).status_code
        == 404
    )
    assert len(model.requests) == 2


def test_plan_edit_preserves_unrequested_tasks_and_completion_is_idempotent(tmp_path):
    repo = NativeRepository(tmp_path / "native.db")
    repo.save_workspace("owner", "course", "name", {})
    created = repo.execute_assistant_operation(
        "owner",
        "plan-create",
        "update_study_plan",
        {
            "course_id": "course",
            "date": "2026-11-03",
            "tasks": [{"title": "first"}, {"title": "second"}],
        },
    )["objects"][0]
    first, second = created["data"]["tasks"]
    arguments = {
        "course_id": "course",
        "object_id": created["id"],
        "revision": 1,
        "date": "2026-11-03",
        "tasks": [{"id": first["id"], "title": "updated"}],
    }
    edited = repo.execute_assistant_operation("owner", "plan-edit", "update_study_plan", arguments)[
        "objects"
    ][0]
    assert edited["revision"] == 2 and edited["data"]["tasks"][1] == second
    with pytest.raises(NativeConflict, match="新版本"):
        repo.execute_assistant_operation("owner", "plan-stale", "update_study_plan", arguments)
    complete = {"course_id": "course", "object_id": created["id"], "task_id": first["id"]}
    result = repo.execute_assistant_operation(
        "owner", "complete", "complete_learning_task", complete
    )
    assert result == repo.execute_assistant_operation(
        "owner", "complete", "complete_learning_task", complete
    )
    assert result["data"]["tasks"][0]["status"] == "done"
    assert "status" not in result["data"]["tasks"][1]
    assert result["revision"] == 3
    with pytest.raises(KeyError):
        repo.assistant_object("other", created["id"])


def test_agent_card_review_accepts_both_wire_forms_without_duplicate_review(gateway):
    a, _, _, _ = gateway
    assert (
        a.post(
            "/api/workspaces", json={"id": "course_review", "name": "test", "preferences": {}}
        ).status_code
        == 200
    )
    created = a.post(
        "/api/assistant/tools/create_flashcards",
        json={
            "operation_id": "create-card",
            "arguments": {
                "course_id": "course_review",
                "cards": [{"front": "front", "back": "back"}],
            },
        },
    ).json()["objects"][0]
    path = f"/api/assistant/learning-data/{created['id']}/review"
    first = a.post(
        path, json={"event_id": "review-1", "rating": "good", "response_seconds": 2}
    ).json()
    duplicate = a.post(
        path, json={"operation_id": "review-1", "rating": "mastered", "seconds": 2}
    ).json()
    assert first["review_state"]["repetitions"] == duplicate["review_state"]["repetitions"] == 1
    assert duplicate["duplicate"] is True
    obj = a.get("/api/assistant/learning-data").json()["objects"][0]
    assert obj["data"]["mastery"] == 1 and len(obj["data"]["review_history"]) == 1


def test_retryable_image_submission_keeps_immutable_original_user_message(gateway, monkeypatch):
    import httpx

    from adaptive_learning.llm import tool_chat

    a, _, install, _ = gateway
    monkeypatch.setattr(tool_chat, "overload_wait", lambda *args: 0)
    model = install(
        Model(lambda _: httpx.Response(429, json={"error": {"message": "busy"}}), final_stream())
    )
    image = BytesIO()
    Image.new("RGB", (10, 10)).save(image, format="PNG")
    value = v2(
        attachments=[
            {
                "attachment_id": "image1",
                "media_type": "image/png",
                "data_base64": base64.b64encode(image.getvalue()).decode(),
            }
        ]
    )
    assert a.post("/api/assistant/turn", json=value).json()["state"] == "retryable"
    assert a.post("/api/assistant/turn", json=value).json()["state"] == "completed"
    assert len(model.requests) == 2
    user = a.get("/api/assistant/conversations/chat").json()["messages"][0]
    assert user["display_text"] == value["request"]
    assert "display_text" not in model.requests[-1]["messages"][1]


def test_non_builtin_resource_records_return_actual_empty_and_enforce_owner(gateway):
    a, b, _, _ = gateway
    actor = api.account_repository.session_owner(a.cookies.get("zhiwo_account"))
    api.account_repository.repo.bind_upload(actor, "private-other-source")
    api.native_repository.save_workspace(actor, "course_records", "name", {})
    resource = api.native_repository.add_resource(actor, "course_records", "private-other-source")
    path = f"/api/workspaces/course_records/resources/{resource['id']}/catalog-records"
    response = a.get(path)
    assert response.status_code == 200
    assert response.json()["state"] == "empty" and response.json()["evidence"] == []
    assert b.get(path).status_code == 404


def test_legacy_current_history_migrates_once_and_temporary_history_is_ignored(gateway):
    a, _, install, _ = gateway
    model = install(Model(final_stream(), final_stream(), final_stream()))
    old = [
        {"role": "user", "text": "only this conversation's old question"},
        {"role": "assistant", "text": "old answer"},
    ]
    assert a.post("/api/assistant/turn", json=v2(history=old)).status_code == 200
    assert "old answer" in str(model.requests[-1]["messages"])
    assert (
        a.post(
            "/api/assistant/turn",
            json=v2(run="next", history=[{"role": "user", "text": "must be ignored"}]),
        ).status_code
        == 200
    )
    assert "must be ignored" not in str(model.requests[-1]["messages"])
    assert (
        a.post(
            "/api/assistant/turn", json=v2(session="temp", run="temp", ephemeral=True, history=old)
        ).status_code
        == 200
    )
    assert "old answer" not in str(model.requests[-1]["messages"])


def test_one_time_bulk_migration_preserves_over_quota_archives_without_bypassing_new_limit(
    tmp_path,
):
    sessions = AssistantSessions(AssistantLedger(tmp_path / "assistant.db"))
    old = [
        {
            "session_id": f"legacy{i}",
            "title": f"old {i}",
            "messages": [{"role": "user", "text": "old message"}],
        }
        for i in range(7)
    ]
    assert sessions.migrate_history("owner", old) is True
    assert len(sessions.list("owner")) == 7
    # The seventh pre-existing, writable archive can continue.
    sessions.begin("owner", "legacy6", "resume", "request", False)
    with pytest.raises(LedgerConflict, match="会话数上限"):
        sessions.begin("owner", "new", "new-run", "new", False)
    for index in range(2):
        sessions.delete("owner", f"legacy{index}")
    sessions.begin("owner", "new", "new-run", "new", False)
    assert len(sessions.list("owner")) == 6
    assert (
        sessions.migrate_history(
            "owner", [{"session_id": "forged", "title": "new", "messages": []}]
        )
        is False
    )
    for item in sessions.list("owner"):
        sessions.delete("owner", item["session_id"])
    assert sessions.migrate_history("owner", old) is False

def test_read_answer_then_save_with_new_evidence(gateway):
    from test_assistant_turn import sse

    a, _, install, _ = gateway
    read_call = {"index": 0, "id": "read_1", "type": "function",
                 "function": {"name": "read_material", "arguments": "{}"}}
    read = sse({"choices": [{"index": 0, "delta": {"tool_calls": [read_call]}}]},
               {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
               {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 10}})
    model = install(Model(read, final_stream("这是教材的解释。"), save_call_stream(), final_stream("已保存。")))
    first = v2()
    first.update(request="查教材解释这个概念", tools=["read_material", "save_note"])
    assert a.post("/api/assistant/turn", json=first).json()["message"]["tool_calls"][0]["id"] == "read_1"
    follow = dict(first, step_index=1, step_id="step_1", evidence=[{"evidence_id": "ev1", "page": 3, "quote": "原文"}],
                  tool_results=[{"step_index": 0, "tool_call_id": "read_1", "content": '{"evidence_id":"ev1","text":"原文"}'}])
    assert a.post("/api/assistant/turn", json=follow).json()["message"]["content"] == "这是教材的解释。"
    assert "ev1" in str(model.requests[-1]["messages"])
    save = v2(run="save")
    save["request"] = "把刚才的解释保存为笔记"
    assert a.post("/api/assistant/turn", json=save).json()["message"]["tool_calls"][0]["name"] == "save_note"
    saved = dict(save, step_index=1, step_id="save_1", tool_results=[
        {"step_index": 0, "tool_call_id": "call_1", "content": '{"status":"saved","note_id":"note1"}'}])
    assert a.post("/api/assistant/turn", json=saved).json()["message"]["content"] == "已保存。"


def test_cancel_pending_tool_then_continue_same_conversation(gateway):
    a, _, install, _ = gateway
    model = install(Model(save_call_stream(), final_stream("继续学习")))
    assert a.post("/api/assistant/turn", json=v2()).json()["message"]["tool_calls"]
    assert a.post("/api/assistant/runs/run/cancel").status_code == 200
    response = a.post("/api/assistant/turn", json=v2(run="new_run"))
    assert response.json()["message"]["content"] == "继续学习"
    tool = next(m for m in model.requests[-1]["messages"] if m["role"] == "tool")
    assert "cancelled_without_receipt" in tool["content"]
