"""Only a proven pre-POST connection failure may cross the submission fence."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpcore
import httpx
import pytest
from test_llm_retry_after_production import FakeTransport

from adaptive_learning.llm import client as llm


def failure(protocol, kind):
    request = httpx.Request("POST", "https://offline.test/v1/model")
    if kind == "uncaused_sdk":
        return llm.APIConnectionError(request=request)
    error = getattr(httpx, kind)("controlled transport failure", request=request)
    if protocol == "http":
        return error
    wrapped = (llm.APITimeoutError(request=request) if isinstance(error, httpx.TimeoutException)
               else llm.APIConnectionError(request=request))
    wrapped.__cause__ = error
    return wrapped


@pytest.mark.parametrize("protocol", ["http", "sdk"])
@pytest.mark.parametrize("kind", ["ConnectTimeout", "ConnectError"])
def test_real_transport_reconnects_but_server_receives_only_one_model_post(monkeypatch, protocol, kind):
    received, attempts = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = ({"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": '{"ok":true}'}]}]}
                if protocol == "sdk" else {"choices": [{"message": {"content": '{"ok":true}'}}]})
            body["usage"] = {"input_tokens": 1, "output_tokens": 1}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = httpcore.SyncBackend.connect_tcp

    def connect(self, *args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise getattr(httpcore, kind)("controlled pre-send connection failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect)
    # The SDK route is selected using a path on our local server, never an
    # external model host. It uses the real official SDK with hidden retries off.
    base = f"http://127.0.0.1:{server.server_port}/" + ("pucoding.com/v1" if protocol == "sdk" else "v1")
    client = llm.OpenAICompatibleClient(llm.LLMConfig(base, "offline-key", "gpt-offline-model"))
    try:
        if protocol == "sdk":
            assert client._responses_sdk.max_retries == 0
        with llm.no_ambiguous_model_retries():
            assert client.structured(system="Return JSON", user="Controlled local request") == {"ok": True}
        assert len(attempts) == 2 and len(received) == 1
        assert client.usage_totals()["responses"] == 1
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("protocol", ["http", "sdk"])
@pytest.mark.parametrize("kind", ["ConnectTimeout", "ConnectError"])
@pytest.mark.parametrize("retries", [0, 1])
def test_only_exhausted_proven_connection_attempts_are_typed_not_submitted(monkeypatch, protocol, kind, retries):
    transport = FakeTransport(protocol, monkeypatch).install(
        [failure(protocol, kind) for _ in range(retries + 1)], max_retries=retries)
    try:
        with llm.no_ambiguous_model_retries(), pytest.raises(llm.LLMNotSubmittedError) as caught:
            transport.client.structured(system="s", user="u")
        assert caught.value.__cause__ is not None
        assert len(transport.calls) == retries + 1
        assert len(transport.clock.sleeps) == retries
        if protocol == "sdk":
            assert transport.client._responses_sdk.max_retries == 0
    finally:
        transport.client.close()


@pytest.mark.parametrize("protocol", ["http", "sdk"])
@pytest.mark.parametrize("kind", ["ReadTimeout", "WriteTimeout", "ReadError", "WriteError",
                                  "RemoteProtocolError", "ProxyError", "uncaused_sdk"])
def test_unknown_or_post_submission_failure_stays_single_attempt(monkeypatch, protocol, kind):
    transport = FakeTransport(protocol, monkeypatch).install([failure(protocol, kind)], max_retries=3)
    try:
        with llm.no_ambiguous_model_retries(), pytest.raises(llm.LLMError) as caught:
            transport.client.structured(system="s", user="u")
        assert not isinstance(caught.value, llm.LLMNotSubmittedError)
        assert len(transport.calls) == 1 and transport.clock.sleeps == []
    finally:
        transport.client.close()


@pytest.mark.parametrize("protocol", ["http", "sdk"])
@pytest.mark.parametrize("kind", ["ReadError", "RemoteProtocolError", "ReadTimeout"])
def test_full_cause_and_context_chain_cannot_hide_an_unknown_submission(monkeypatch, protocol, kind):
    ambiguous = failure("http", kind)
    outer = failure(protocol, "ConnectError")
    connection = outer if protocol == "http" else outer.__cause__
    connection.__context__ = ambiguous
    transport = FakeTransport(protocol, monkeypatch).install([outer], max_retries=3)
    try:
        with llm.no_ambiguous_model_retries(), pytest.raises(llm.LLMError) as caught:
            transport.client.structured(system="s", user="u")
        assert not isinstance(caught.value, llm.LLMNotSubmittedError)
        assert len(transport.calls) == 1
    finally:
        transport.client.close()


@pytest.mark.parametrize("protocol", ["http", "sdk"])
@pytest.mark.parametrize("kind", ["ReadTimeout", "WriteError", "RemoteProtocolError"])
def test_nested_httpcore_unknown_failure_is_not_hidden_by_a_connection_label(monkeypatch, protocol, kind):
    outer = failure(protocol, "ConnectTimeout")
    connection = outer if protocol == "http" else outer.__cause__
    connection.__cause__ = getattr(httpcore, kind)("controlled ambiguous lower-level failure")
    transport = FakeTransport(protocol, monkeypatch).install([outer], max_retries=3)
    try:
        with llm.no_ambiguous_model_retries(), pytest.raises(llm.LLMError) as caught:
            transport.client.structured(system="s", user="u")
        assert not isinstance(caught.value, llm.LLMNotSubmittedError)
        assert len(transport.calls) == 1
    finally:
        transport.client.close()


def test_uncorroborated_sdk_message_cannot_claim_a_connect_timeout(monkeypatch):
    error = llm.APIConnectionError(message="httpx.ConnectTimeout: no request sent",
        request=httpx.Request("POST", "https://offline.test/v1/model"))
    transport = FakeTransport("sdk", monkeypatch).install([error], max_retries=3)
    try:
        with llm.no_ambiguous_model_retries(), pytest.raises(llm.LLMError) as caught:
            transport.client.structured(system="s", user="u")
        assert not isinstance(caught.value, llm.LLMNotSubmittedError)
        assert len(transport.calls) == 1
    finally:
        transport.client.close()


@pytest.mark.parametrize("protocol", ["http", "sdk"])
@pytest.mark.parametrize("content", ['{"incomplete":[', "[]"])
def test_received_bad_body_is_never_reclassified_or_replayed(monkeypatch, protocol, content):
    transport = FakeTransport(protocol, monkeypatch)
    transport.install([transport.success(content)], max_retries=3)
    try:
        with llm.no_ambiguous_model_retries(), pytest.raises(llm.LLMError) as caught:
            transport.client.structured(system="s", user="u")
        assert not isinstance(caught.value, llm.LLMNotSubmittedError)
        assert len(transport.calls) == 1
    finally:
        transport.client.close()


@pytest.mark.parametrize("protocol", ["http", "sdk"])
def test_prior_http_response_prevents_later_connection_failure_claiming_no_post(monkeypatch, protocol):
    transport = FakeTransport(protocol, monkeypatch).install([
        {"status": 500, "body": {"error": {"message": "controlled server failure"}}},
        failure(protocol, "ConnectTimeout"),
    ], max_retries=1)
    try:
        with llm.no_ambiguous_model_retries(), pytest.raises(llm.LLMError) as caught:
            transport.client.structured(system="s", user="u")
        assert not isinstance(caught.value, llm.LLMNotSubmittedError)
        assert len(transport.calls) == 2
    finally:
        transport.client.close()


@pytest.mark.parametrize("protocol", ["http", "sdk"])
def test_ordinary_parse_retry_followed_by_connect_error_cannot_claim_no_submission(monkeypatch, protocol):
    transport = FakeTransport(protocol, monkeypatch)
    transport.install([transport.success("[]"), failure(protocol, "ConnectError")], max_retries=1)
    try:
        with pytest.raises(llm.LLMError) as caught:
            transport.client.structured(system="s", user="u")
        assert not isinstance(caught.value, llm.LLMNotSubmittedError)
        assert len(transport.calls) == 2
    finally:
        transport.client.close()


@pytest.mark.parametrize("protocol", ["http", "sdk"])
def test_connection_retry_cannot_extend_shared_deadline(monkeypatch, protocol):
    transport = FakeTransport(protocol, monkeypatch).install([failure(protocol, "ConnectTimeout")], max_retries=3)
    try:
        with llm.no_ambiguous_model_retries(), llm.model_time_budget(.1):
            with pytest.raises(llm.LLMNotSubmittedError):
                transport.client.structured(system="s", user="u")
        assert len(transport.calls) == 1 and transport.clock.sleeps == []
        assert transport.calls[0]["timeout"].read <= .1 + 1e-9
    finally:
        transport.client.close()


@pytest.mark.parametrize("protocol", ["http", "sdk"])
def test_connection_retry_uses_only_remaining_deadline_and_records_one_response(monkeypatch, protocol):
    transport = FakeTransport(protocol, monkeypatch)
    transport.install([failure(protocol, "ConnectError"), transport.success(
        usage={"input_tokens": 1, "output_tokens": 1})], max_retries=1)
    try:
        with llm.no_ambiguous_model_retries(), llm.model_time_budget(2):
            assert transport.client.structured(system="s", user="u") == {"ok": True}
        assert len(transport.calls) == 2
        assert transport.calls[1]["timeout"].read < transport.calls[0]["timeout"].read <= 2
        assert transport.client.usage_totals()["responses"] == 1
    finally:
        transport.client.close()


def test_draining_call_is_never_retried_even_when_late_failure_is_connect_error(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    client = llm.OpenAICompatibleClient(llm.LLMConfig("https://offline.test/v1", "offline-key", "draining", max_retries=3))

    def post(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(2)
        raise httpx.ConnectTimeout("late connection outcome")

    monkeypatch.setattr(client._http, "post", post)
    try:
        with llm.no_ambiguous_model_retries(), llm.model_time_budget(.04):
            with pytest.raises(llm.LLMTimeoutError) as caught:
                client.structured(system="s", user="u")
        assert entered.is_set() and len(calls) == 1
        assert caught.value._draining_future is not None
        release.set()
        with pytest.raises(httpx.ConnectTimeout):
            caught.value._draining_future.result(timeout=2)
        assert len(calls) == 1
    finally:
        release.set()
        client.close()


def test_exhausted_transport_slots_do_not_start_or_retry_a_post(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(llm, "_transport_slots", slots)
    client = llm.OpenAICompatibleClient(llm.LLMConfig("https://offline.test/v1", "offline-key", "slot", max_retries=3))
    calls = []
    monkeypatch.setattr(client._http, "post", lambda *args, **kwargs: calls.append(1))
    try:
        with llm.no_ambiguous_model_retries(), llm.model_time_budget(.01):
            with pytest.raises(llm.LLMTimeoutError):
                client.structured(system="s", user="u")
        assert calls == []
    finally:
        slots.release()
        client.close()
