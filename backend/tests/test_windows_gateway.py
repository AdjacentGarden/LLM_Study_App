"""Real TCP contracts for the Windows gateway; no providers or business fixtures."""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import importlib.util
import json
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn

MODULE = Path(__file__).resolve().parents[2] / "deployment/windows/cloudpath-gateway.py"
spec = importlib.util.spec_from_file_location("cloudpath_gateway", MODULE)
gateway_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway_module)


@contextmanager
def tcp_server(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_level="critical", access_log=False,
                            proxy_headers=False, lifespan="auto")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()


def test_raw_path_query_duplicate_cookies_host_and_hop_headers():
    calls = []

    async def upstream(scope, receive, send):
        assert scope["type"] == "http"
        calls.append(scope)
        while (await receive()).get("more_body", False):
            pass
        payload = json.dumps({"path": scope["raw_path"].decode(),
                              "query": scope["query_string"].decode()}).encode()
        await send({"type": "http.response.start", "status": 429, "headers": [
            (b"set-cookie", b"one=1; HttpOnly; Path=/"),
            (b"set-cookie", b"two=2; SameSite=Lax; Path=/"),
            (b"connection", b"x-remove"), (b"x-remove", b"hidden"),
            (b"retry-after", b"11"), (b"cache-control", b"private, no-store")]})
        await send({"type": "http.response.body", "body": payload})

    with tcp_server(upstream) as origin, tcp_server(gateway_module.Gateway(origin)) as base:
        response = httpx.post(base + "/books/a%2Fb?x=a%2Bb&x=%2F&blank=", headers={
            "Host": "10.249.44.137:8100", "Origin": "http://10.249.44.137:8100",
            "Connection": "x-remove", "x-remove": "hidden", "X-Forwarded-For": "127.0.0.1",
            "Forwarded": "for=127.0.0.1", "Cookie": "session=real"}, content=b"data")
    assert response.status_code == 429
    assert response.json() == {"path": "/books/a%2Fb", "query": "x=a%2Bb&x=%2F&blank="}
    assert response.headers.get_list("set-cookie") == [
        "one=1; HttpOnly; Path=/", "two=2; SameSite=Lax; Path=/"]
    assert response.headers["retry-after"] == "11"
    assert "x-remove" not in response.headers
    headers = dict(calls[0]["headers"])
    assert headers[b"host"] == b"10.249.44.137:8100"
    assert headers[b"origin"] == b"http://10.249.44.137:8100"
    assert headers[b"cookie"] == b"session=real"
    assert not ({b"x-remove", b"x-forwarded-for", b"forwarded"} & headers.keys())
    assert len(calls) == 1


def test_upload_and_response_stream_before_the_remaining_body_exists():
    first_upload = threading.Event()
    allow_response_end = threading.Event()

    async def upstream(scope, receive, send):
        if scope["path"] == "/upload":
            digest = hashlib.sha256()
            while True:
                event = await receive()
                digest.update(event.get("body", b""))
                first_upload.set()
                if not event.get("more_body", False):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": digest.hexdigest().encode()})
        else:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"first", "more_body": True})
            assert await asyncio.to_thread(allow_response_end.wait, 3)
            await send({"type": "http.response.body", "body": b"last"})

    async def run(base):
        async with httpx.AsyncClient(timeout=4) as client:
            async def upload():
                yield b"prefix"
                assert await asyncio.to_thread(first_upload.wait, 2)
                for _ in range(192):
                    yield b"x" * 65536
            response = await client.post(base + "/upload", content=upload())
            expected = hashlib.sha256(b"prefix" + b"x" * (192 * 65536)).hexdigest()
            assert response.text == expected
            async with client.stream("GET", base + "/stream") as response:
                chunks = response.aiter_raw()
                assert await anext(chunks) == b"first"
                allow_response_end.set()
                assert b"".join([chunk async for chunk in chunks]) == b"last"

    with tcp_server(upstream) as origin, tcp_server(gateway_module.Gateway(origin)) as base:
        asyncio.run(run(base))


def test_shared_connection_pool_never_inherits_another_users_cookie():
    seen = []

    async def upstream(scope, receive, send):
        seen.append(dict(scope["headers"]).get(b"cookie", b""))
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"set-cookie", b"account=user-a; Path=/; HttpOnly"),
            (b"set-cookie", b"visitor=a; Path=/")]})
        await send({"type": "http.response.body", "body": b"ok"})

    with tcp_server(upstream) as origin:
        gateway = gateway_module.Gateway(origin)
        with tcp_server(gateway) as base:
            with httpx.Client(base_url=base) as user_a:
                first = user_a.get("/login")
                assert len(first.headers.get_list("set-cookie")) == 2
                assert httpx.get(base + "/anonymous").status_code == 200
                assert user_a.get("/account").status_code == 200
            assert not list(gateway.client.cookies.jar)
    assert seen[0] == seen[1] == b""
    assert b"account=user-a" in seen[2] and b"visitor=a" in seen[2]


def test_compressed_bytes_content_length_head_and_redirect_are_not_rewritten():
    compressed = gzip.compress(b"real PDF bytes" * 20000)
    origin_holder = {}

    async def upstream(scope, receive, send):
        headers = [(b"content-encoding", b"gzip"),
                   (b"content-length", str(len(compressed)).encode())]
        status = 200
        body = compressed
        if scope["path"] == "/redirect":
            status, body = 307, b""
            headers = [(b"location", (origin_holder["url"] + "/file/?a=%2F").encode())]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else body})

    with tcp_server(upstream) as origin, tcp_server(gateway_module.Gateway(origin)) as base:
        origin_holder["url"] = origin
        with httpx.stream("GET", base + "/file") as response:
            assert response.headers["content-length"] == str(len(compressed))
            assert b"".join(response.iter_raw()) == compressed
        assert httpx.head(base + "/file").content == b""
        redirect = httpx.get(base + "/redirect", headers={"Host": "10.249.44.137:8100"})
        assert redirect.status_code == 307
        assert redirect.headers["location"] == "http://10.249.44.137:8100/file/?a=%2F"


def test_unavailable_upstream_is_redacted_502_and_post_is_not_retried():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    sock.close()
    with tcp_server(gateway_module.Gateway(origin)) as base:
        response = httpx.post(base + "/job?private=value", content=b"secret")
    assert response.status_code == 502
    assert response.json() == {"detail": "upstream_unavailable"}
    assert response.headers["cache-control"] == "no-store"


def test_disconnect_after_upload_cancels_stalled_response_and_closes_upstream():
    async def run():
        closed = asyncio.Event()
        first = asyncio.Event()
        queue = asyncio.Queue()
        await queue.put({"type": "http.request", "body": b"", "more_body": False})

        class Stalled(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"first"
                await asyncio.Event().wait()

            async def aclose(self):
                closed.set()

        class Transport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                await request.aread()
                return httpx.Response(200, stream=Stalled())

        async def send(event):
            if event["type"] == "http.response.body":
                first.set()

        gateway = gateway_module.Gateway("http://127.0.0.1:12345")
        async with httpx.AsyncClient(transport=Transport()) as client:
            gateway.client = client
            task = asyncio.create_task(gateway({"type": "http", "method": "GET",
                "path": "/file", "raw_path": b"/file", "headers": [],
                "query_string": b"", "scheme": "http"}, queue.get, send))
            await asyncio.wait_for(first.wait(), 1)
            await queue.put({"type": "http.disconnect"})
            await asyncio.wait_for(task, 1)
            assert closed.is_set()
            assert queue.empty()

    asyncio.run(run())


def test_timeout_is_redacted_504_without_replaying_post():
    calls = []

    async def upstream(scope, receive, send):
        calls.append(scope["method"])
        await asyncio.sleep(0.15)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"done"})

    with tcp_server(upstream) as origin:
        gateway = gateway_module.Gateway(origin)
        with tcp_server(gateway) as base:
            gateway.client.timeout = httpx.Timeout(0.03)
            response = httpx.post(base + "/paid-job", content=b"request")
    assert response.status_code == 504
    assert response.json() == {"detail": "upstream_timeout"}
    assert calls == ["POST"]


@pytest.mark.parametrize("origin", ["https://example.com", "http://0.0.0.0:80", "http://8.8.8.8", "http://u:p@127.0.0.1", "http://127.0.0.1/path", "http://127.0.0.1?q=x"])
def test_only_explicit_private_origins_are_accepted(origin):
    with pytest.raises(ValueError):
        gateway_module.Gateway(origin)
