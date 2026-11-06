"""Public AutoDL HTTP entry for the loopback CloudPath API."""
from contextlib import asynccontextmanager
import httpx
import os
import json
import time
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, StreamingResponse

HOP_HEADERS = {
    b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
    b"te", b"trailer", b"transfer-encoding", b"upgrade",
}
UPSTREAM = httpx.URL(os.environ.get("CLOUDPATH_PUBLIC_API_UPSTREAM", "http://127.0.0.1:18126"))

@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(1000.0, connect=10.0, pool=15.0),
        limits=httpx.Limits(max_connections=128, max_keepalive_connections=32),
        follow_redirects=False, trust_env=False,
    ) as client:
        app.state.client = client
        yield

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

@app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def forward(request: Request, path: str):
    # Local development login stays exclusive to the private loopback entry.
    if request.url.path.rstrip("/") == "/api/native/local-login":
        return JSONResponse({"detail": "请使用邮箱验证码登录"}, status_code=403)
    connection_headers = {
        token.strip().lower().encode()
        for token in request.headers.get("connection", "").split(",") if token.strip()
    }
    excluded = HOP_HEADERS | connection_headers | {
        b"x-forwarded-proto", b"x-forwarded-host", b"x-forwarded-for", b"x-real-ip",
    }
    headers = [(k, v) for k, v in request.headers.raw if k.lower() not in excluded]
    headers.extend([
        (b"x-forwarded-proto", b"https"),
        (b"x-forwarded-for", (request.client.host if request.client else "unknown").encode()),
    ])

    target = UPSTREAM.copy_with(
        raw_path=request.scope.get("raw_path", request.url.path.encode())
        + (b"?" + request.scope["query_string"] if request.scope.get("query_string") else b"")
    )
    client = request.app.state.client
    received_bytes = 0
    started = time.monotonic()
    async def request_body():
        nonlocal received_bytes
        async for chunk in request.stream():
            received_bytes += len(chunk)
            yield chunk
    outgoing = client.build_request(request.method, target, headers=headers, content=request_body())
    try:
        upstream = await client.send(outgoing, stream=True)
    except httpx.HTTPError:
        return JSONResponse({"detail": "学习服务暂时不可用，请稍后重试"}, status_code=502)

    if request.method == "POST" and request.url.path == "/api/native/uploads":
        print("CLOUDPATH_RELAY_UPLOAD " + json.dumps({
            "request_id": request.headers.get("x-cloudpath-upload-id", ""),
            "received_bytes": received_bytes,
            "content_length": request.headers.get("content-length"),
            "upstream_status": upstream.status_code,
            "elapsed_seconds": time.monotonic() - started,
        }), flush=True)

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    response = StreamingResponse(body(), status_code=upstream.status_code)
    response.raw_headers = [
        (k, v) for k, v in upstream.headers.raw if k.lower() not in HOP_HEADERS
    ]
    return response
