"""Private inference tests use the real fences with provider transports replaced."""
import json
from dataclasses import replace
import httpx
from native_harness import api
from test_assistant_turn import gateway as gateway, Model, final_stream, body
from adaptive_learning.api.private_forwarding import private_ledger, private_forwarding_router, PRIVATE_PREFIX
from adaptive_learning.llm import client as client_module


def private_body():
    value = body()
    value.update(contract_version=3, session_id="phone-chat", message_id="message-private",
                 client_messages=[{"role": "user", "content": "personal-secret-input"}])
    return value


def test_private_reply_is_ram_only_ack_loss_is_unknown_never_resubmitted(gateway):
    a, _, install, _ = gateway
    a.app.include_router(private_forwarding_router(api))
    model = install(Model(final_stream("personal-secret-output")))
    first = a.post("/api/assistant/turn", json=private_body())
    assert first.status_code == 200, first.text
    assert first.json()["message"]["content"] == "personal-secret-output"
    owner = api.account_repository.session_owner(a.cookies.get("zhiwo_account"))
    with api.assistant_ledger.connect() as db:
        row = db.execute("SELECT * FROM assistant_steps WHERE owner=? AND run_id=?", (owner, PRIVATE_PREFIX + "run_1")).fetchone()
        assert row["message_json"] is None
        assert "personal-secret" not in json.dumps(dict(row))
    assert a.post("/api/assistant/runs/run_1/steps/step_0/ack").status_code == 200
    replay = a.post("/api/assistant/turn", json=private_body()).json()
    assert replay["state"] == "uncertain" and replay["message"] is None
    assert len(model.requests) == 1
    assert a.get("/api/assistant/runs/run_1/steps/step_0").json()["state"] == "uncertain"


def test_private_worker_cache_loss_keeps_claim_and_cannot_purchase_another_call(gateway):
    a, _, install, _ = gateway
    model = install(Model(final_stream()))
    assert a.post("/api/assistant/turn", json=private_body()).status_code == 200
    private_ledger(api).cache.clear()
    assert a.post("/api/assistant/turn", json=private_body()).json()["state"] == "uncertain"
    assert len(model.requests) == 1


def test_private_generation_validates_json_and_omits_empty_tool_protocol(gateway):
    a, _, install, _ = gateway
    a.app.include_router(private_forwarding_router(api))
    model = install(Model(final_stream('{"correct":true,"score":1,"feedback":"private feedback"}')))
    request = {"request_id": "private_diagnostic", "operation": "diagnostic", "messages": [{"role": "user", "content": "private answer"}],
               "response_schema": {"type": "object", "required": ["correct", "score", "feedback"], "properties": {
                   "correct": {"type": "boolean"}, "score": {"type": "number", "minimum": 0, "maximum": 1}, "feedback": {"type": "string"}}}}
    response = a.post("/api/assistant/private-model", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "completed"
    assert "tools" not in model.requests[0] and "tool_choice" not in model.requests[0]
    assert a.post("/api/assistant/private-model/private_diagnostic/ack").status_code == 200
    assert a.get("/api/assistant/private-model/private_diagnostic").json()["state"] == "uncertain"
    assert len(model.requests) == 1


def test_private_generation_failure_keeps_only_a_fixed_cause_code(gateway):
    from test_assistant_turn import sse
    a, _, install, _ = gateway
    a.app.include_router(private_forwarding_router(api))
    truncated = sse({"choices": [{"index": 0, "delta": {"content": '{"feedback":"personal-secret'}}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]})
    model = install(Model(truncated))
    request = {"request_id": "private_lecture", "operation": "lecture", "max_tokens": 65536,
               "messages": [{"role": "user", "content": "private chapter"}],
               "response_schema": {"type": "object", "properties": {"feedback": {"type": "string"}}}}
    response = a.post("/api/assistant/private-model", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "failed"
    assert response.json()["error"] == "private_model_output_limit"
    assert model.requests[0]["max_tokens"] == 65536
    assert "紧凑的单行JSON" in model.requests[0]["messages"][0]["content"]
    owner = api.account_repository.session_owner(a.cookies.get("zhiwo_account"))
    with api.assistant_ledger.connect() as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM assistant_steps WHERE owner=?", (owner,)).fetchall()]
        assert "personal-secret" not in json.dumps(rows)
    # The same request ID is never resent; a user retry must use a new one.
    assert a.post("/api/assistant/private-model", json=request).json()["state"] == "failed"
    assert len(model.requests) == 1
    request["max_tokens"] = 65537
    assert a.post("/api/assistant/private-model", json=request).status_code == 422


def test_private_error_codes_never_copy_free_text():
    from adaptive_learning.api.private_forwarding import error_code
    assert error_code("model time budget exceeded", "uncertain") == "private_model_timeout"
    assert error_code("provider returned HTTP 402", "failed") == "private_model_http_402"
    assert error_code("invalid model output: personal-secret", "failed") == "private_model_invalid_output"
    assert error_code("personal-secret", "failed") == "private_model_failed"


def test_vision_step_uses_configured_responses_images_and_same_tool_fence(gateway, monkeypatch):
    a, _, install, _ = gateway
    install(Model())  # Text route must not be called for this image-bearing step.
    settings = replace(api.settings, pucoding_base_url="https://pucoding.com/v1", pucoding_api_key="vision-test", pucoding_vision_model="gpt-6-sol")
    monkeypatch.setattr(api, "settings", settings)
    seen = []
    def provider(request):
        seen.append((str(request.url), json.loads(request.content)))
        decision = {"content": "inspect figure", "tool_calls": [{"id": "vision_call", "name": "open_note", "arguments": {"note_id": "note-local"}}]}
        return httpx.Response(200, json={"id": "resp_test", "object": "response", "created_at": 0, "status": "completed", "model": "gpt-6-sol",
            "output": [{"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(decision), "annotations": []}]}],
            "usage": {"input_tokens": 42, "output_tokens": 12, "total_tokens": 54}})
    original_init = client_module.OpenAICompatibleClient.__init__
    def initialize_with_mock_transport(self, config):
        original_init(self, config)
        # Preserve the SDK's actual httpx.Client isinstance contract. Constructing
        # clients sends no network request; replace both handles before inference.
        if self._responses_sdk is not None:
            self._responses_sdk.close()
        else:
            self._http.close()
        self._http = httpx.Client(transport=httpx.MockTransport(provider), trust_env=False)
        self._responses_sdk = client_module.OpenAI(api_key=config.api_key, base_url=config.base_url,
                                                  http_client=self._http, max_retries=0)
    monkeypatch.setattr(client_module.OpenAICompatibleClient, "__init__", initialize_with_mock_transport)
    request = private_body()
    request["client_messages"] = [{"role": "user", "content": [{"type": "text", "text": "private figure"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="}}]}]
    response = a.post("/api/assistant/turn", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["state"] == "completed" and result["message"]["tool_calls"][0]["id"] == "vision_call"
    assert seen[0][0].endswith("/responses")
    assert "data:image/png;base64," in json.dumps(seen[0][1])
    assert result["budget"]["input_tokens"] == 42
    private_ledger(api).cache.clear()
    assert a.post("/api/assistant/turn", json=request).json()["state"] == "uncertain"
    assert len(seen) == 1


def test_private_cache_timeout_and_cancel_release_payload_without_resend(gateway, monkeypatch):
    from adaptive_learning.api import private_forwarding as module
    a, b, install, _ = gateway
    model = install(Model(final_stream("private-output")))
    assert a.post("/api/assistant/turn", json=private_body()).status_code == 200
    owner = api.account_repository.session_owner(a.cookies.get("zhiwo_account"))
    ledger = private_ledger(api)
    for key, (_, attempt, value) in list(ledger.cache.items()):
        ledger.cache[key] = (0, attempt, value)
    found = ledger.step(owner, "private:run_1", "private:step_0")
    assert found[0].state == "uncertain" and found[0].message is None
    assert not ledger.cache
    assert b.get("/api/assistant/runs/run_1/steps/step_0").status_code == 404
    assert a.post("/api/assistant/runs/run_1/cancel").status_code == 200
    replay = a.post("/api/assistant/turn", json=private_body())
    assert replay.status_code == 200 and replay.json()["message"] is None
    assert len(model.requests) == 1


def test_private_transcript_tools_and_images_never_enter_durable_ledger(gateway):
    from test_assistant_turn import save_call_stream
    a, _, install, _ = gateway
    model = install(Model(save_call_stream(), final_stream("secret-final")))
    first = private_body()
    assert a.post("/api/assistant/turn", json=first).status_code == 200
    second = private_body()
    second.update(step_index=1, step_id="step_1", client_messages=[
        {"role": "user", "content": "private-original-question"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "save_note", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "private-tool-receipt"}])
    assert a.post("/api/assistant/turn", json=second).status_code == 200
    assert "private-tool-receipt" in json.dumps(model.requests[-1])
    owner = api.account_repository.session_owner(a.cookies.get("zhiwo_account"))
    with api.assistant_ledger.connect() as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM assistant_steps WHERE owner=?", (owner,)).fetchall()]
        assert all(row["message_json"] is None for row in rows)
        assert "private-tool-receipt" not in json.dumps(rows)
        assert "secret-final" not in json.dumps(rows)


def test_vision_invalid_tool_arguments_are_unknown_not_text_fallback(gateway, monkeypatch):
    from adaptive_learning.api.private_forwarding import complete_private
    from types import SimpleNamespace
    a, _, install, _ = gateway
    text = install(Model())
    monkeypatch.setattr(api, "settings", replace(api.settings, pucoding_api_key="vision-test"))
    calls = []
    class Vision:
        def __init__(self, config): self.config = config
        def structured(self, **kwargs):
            calls.append(kwargs)
            return {"content": "", "tool_calls": [{"id": "invalid id spaces", "name": "save_note", "arguments": {}}]}
        def usage_totals(self): return {"input_tokens": 1, "output_tokens": 1}
        def close(self): pass
    monkeypatch.setattr(client_module, "OpenAICompatibleClient", Vision)
    response = complete_private(api, messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,YWJj"}}]}],
        tools={"save_note": {"type": "object", "required": ["title"], "properties": {"title": {"type": "string"}}}}, descriptions={}, max_tokens=100)
    assert response.kind == "uncertain"
    assert len(calls) == 1 and calls[0]["images"] == [("image/png", b"abc")]
    assert text.requests == []
