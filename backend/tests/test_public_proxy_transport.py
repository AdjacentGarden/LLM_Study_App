import importlib.util
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("method,error_type,expected_calls,expected_status", [
    ("GET", httpx.ReadError, 2, 200),
    ("POST", httpx.ReadError, 1, 502),
    ("POST", httpx.ConnectError, 2, 200),
])
def test_proxy_recovers_reads_and_never_reposts_an_ambiguous_generation(
    monkeypatch, method, error_type, expected_calls, expected_status,
):
    path = Path(__file__).resolve().parents[2] / "deployment/direct4090/public_api_proxy.py"
    spec = importlib.util.spec_from_file_location("proxy_transport_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"state":"completed"}'

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            calls.append(request.method)
            if len(calls) == 1:
                if error_type is not httpx.ConnectError:
                    await request.aread()
                raise error_type("transport boundary", request=request)
            await request.aread()
            return httpx.Response(200, stream=Stream())

    client_type = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=Transport(), **kwargs))
    with TestClient(module.app) as client:
        response = client.request(method, "/api/assistant/private-model", content=b'{"request_id":"one"}' if method == "POST" else None)
    assert response.status_code == expected_status
    assert len(calls) == expected_calls
