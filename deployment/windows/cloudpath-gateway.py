"""Streaming LAN gateway. All business state and authentication stay upstream.

There are deliberately no retries, local login, provider keys, disk request
buffers or application fallbacks. Configure the fixed private upstream explicitly.
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path
from urllib.parse import urlsplit

import httpx

HOP_HEADERS = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
               b"te", b"trailer", b"transfer-encoding", b"upgrade"}


def filtered_headers(headers, *, request=False):
    blocked = set(HOP_HEADERS)
    for name, value in headers:
        if name.lower() == b"connection":
            blocked.update(part.strip().lower() for part in value.split(b","))
    if request:
        # Preserve the visible Host so the backend's same-origin checks and
        # framework redirects use the client's origin. The TCP target is fixed.
        blocked.update({b"forwarded", b"expect"})
    return [(name, value) for name, value in headers
            if name.lower() not in blocked
            and not (request and name.lower().startswith(b"x-forwarded-"))]


def validate_upstream(value: str) -> httpx.URL:
    url = httpx.URL(value)
    if (url.scheme not in {"http", "https"} or url.username or url.password
            or url.query or url.fragment or url.path not in {"", "/"}):
        raise ValueError("upstream_must_be_a_private_origin_without_credentials")
    try:
        address = ipaddress.ip_address(url.host)
    except ValueError as error:
        raise ValueError("upstream_must_be_an_explicit_private_ip") from error
    if not (address.is_private or address.is_loopback) or address.is_unspecified:
        raise ValueError("upstream_must_be_an_explicit_private_ip")
    return url


class ClientDisconnected(Exception):
    pass


class NeverStoreCookies(DefaultCookiePolicy):
    """A connection pool must never become a shared user session."""

    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False


class Gateway:
    def __init__(self, upstream: str):
        self.upstream = validate_upstream(upstream)
        self.client = None

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.lifespan(receive, send)
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        if self.client is None:
            await self.error(send, 503, "gateway_starting")
            return
        raw_path = scope.get("raw_path") or scope["path"].encode("utf-8")
        if not raw_path.startswith(b"/"):
            await self.error(send, 400, "invalid_path")
            return
        query = scope.get("query_string", b"")
        target = self.upstream.copy_with(raw_path=raw_path + (b"?" + query if query else b""))

        body_finished = asyncio.Event()

        async def body():
            while True:
                event = await receive()
                if event["type"] == "http.disconnect":
                    raise ClientDisconnected()
                if event["type"] != "http.request":
                    continue
                chunk = event.get("body", b"")
                if chunk:
                    yield chunk
                if not event.get("more_body", False):
                    body_finished.set()
                    break

        async def forward():
            response = None
            started = False
            try:
                request = self.client.build_request(scope["method"], target,
                    headers=filtered_headers(scope.get("headers", []), request=True), content=body())
                response = await self.client.send(request, stream=True, follow_redirects=False)
                headers = filtered_headers(response.headers.raw)
                # Framework slash redirects must keep the client's visible origin.
                downstream_host = next((value for name, value in scope.get("headers", [])
                                        if name.lower() == b"host"), None)
                if downstream_host:
                    rewritten = []
                    for name, value in headers:
                        if name.lower() == b"location":
                            location = urlsplit(value.decode("latin-1"))
                            if (location.scheme, location.netloc) == (self.upstream.scheme, self.upstream.netloc.decode("ascii")):
                                suffix = location.path + ("?" + location.query if location.query else "") + ("#" + location.fragment if location.fragment else "")
                                value = (scope.get("scheme", "http").encode("ascii") + b"://" + downstream_host + suffix.encode("latin-1"))
                        rewritten.append((name, value))
                    headers = rewritten
                await send({"type": "http.response.start", "status": response.status_code, "headers": headers})
                started = True
                # Raw bytes preserve Content-Encoding and Content-Length exactly.
                async for chunk in response.aiter_raw():
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            except ClientDisconnected:
                return
            except (httpx.TimeoutException, httpx.TransportError) as error:
                if started:
                    # Raising closes a truncated response; never disguise it as success.
                    raise
                await self.error(send, 504 if isinstance(error, httpx.TimeoutException) else 502,
                                 "upstream_timeout" if isinstance(error, httpx.TimeoutException) else "upstream_unavailable")
            except (asyncio.CancelledError, OSError):
                raise
            finally:
                if response is not None:
                    await response.aclose()

        async def disconnected():
            # Never race the upload reader for receive(). Once its final event
            # was consumed, monitor disconnect even during a stalled upstream.
            await body_finished.wait()
            while True:
                if (await receive())["type"] == "http.disconnect":
                    return

        forwarding = asyncio.create_task(forward())
        disconnect = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait({forwarding, disconnect},
                                         return_when=asyncio.FIRST_COMPLETED)
            if forwarding in done:
                await forwarding
        finally:
            for task in (forwarding, disconnect):
                if not task.done():
                    task.cancel()
            await asyncio.gather(forwarding, disconnect, return_exceptions=True)

    @staticmethod
    async def error(send, status, code):
        data = json.dumps({"detail": code}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"cache-control", b"no-store"),
                                (b"content-length", str(len(data)).encode())]})
        await send({"type": "http.response.body", "body": data})

    async def lifespan(self, receive, send):
        while True:
            event = await receive()
            if event["type"] == "lifespan.startup":
                self.client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(
                    connect=10, read=300, write=300, pool=10),
                    cookies=CookieJar(policy=NeverStoreCookies()),
                    limits=httpx.Limits(max_connections=32, max_keepalive_connections=8),
                    transport=httpx.AsyncHTTPTransport(retries=0, limits=httpx.Limits(
                        max_connections=32, max_keepalive_connections=8)), follow_redirects=False)
                await send({"type": "lifespan.startup.complete"})
            elif event["type"] == "lifespan.shutdown":
                if self.client:
                    await self.client.aclose()
                    self.client = None
                await send({"type": "lifespan.shutdown.complete"})
                return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    if set(config) != {"upstream_url", "host", "port"}:
        raise ValueError("gateway_config_fields_invalid")
    if config["host"] not in {"127.0.0.1", "0.0.0.0"} or config["port"] not in {8100, 8101}:
        raise ValueError("gateway_listen_address_invalid")
    app = Gateway(config["upstream_url"])
    args.log.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.log, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    os.dup2(descriptor, 1)
    os.dup2(descriptor, 2)
    os.close(descriptor)
    import uvicorn
    uvicorn.run(app, host=config["host"], port=config["port"], workers=1,
                access_log=False, proxy_headers=False, server_header=False)


if __name__ == "__main__":
    main()
