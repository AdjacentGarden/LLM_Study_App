"""Guard account-bound legacy learning endpoints without erasing guest history."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from ..accounts import COOKIE, Accounts


def learning_guard(
    accounts: Accounts,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        parts = request.url.path.strip("/").split("/")
        if len(parts) >= 3 and parts[:2] == ["api", "interviews"]:
            actor = accounts.request_owner(request)
            if request.cookies.get(COOKIE) and not actor:
                return JSONResponse({"detail": "登录已过期，请重新登录"}, status_code=401)
            if parts[2] != "start":
                with accounts.repo.connect() as db:
                    row = db.execute(
                        "SELECT owner FROM session_owners WHERE session_id=?", (parts[2],)
                    ).fetchone()
                if row and row[0] != actor:
                    return JSONResponse({"detail": "不能访问其他用户的学习档案"}, status_code=403)
            if request.method not in {"GET", "HEAD"} and request.headers.get("origin"):
                from urllib.parse import urlsplit

                if urlsplit(request.headers["origin"]).netloc != request.headers.get("host"):
                    return JSONResponse({"detail": "请在 App 内执行此操作"}, status_code=403)
        return await call_next(request)

    return guard
