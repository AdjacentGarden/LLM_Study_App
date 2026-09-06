from __future__ import annotations

import re
from collections.abc import Callable
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..accounts import COOKIE, DEMO_USERS, TTL, Accounts
from ..assessment.repository import SQLiteAssessmentRepository
from ..social import SocialRepository


class CodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=5, max_length=254)
    purpose: Literal["register", "login"]

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.lower()
        if (
            not re.fullmatch(
                r"[a-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}",
                value,
            )
            or ".." in value
            or value.startswith(".")
            or ".@" in value
        ):
            raise ValueError("请输入有效的邮箱地址")
        if any(
            not part or len(part) > 63 or part.startswith("-") or part.endswith("-")
            for part in value.split("@")[1].split(".")
        ):
            raise ValueError("请输入有效的邮箱域名")
        return value


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    nickname: str = Field(min_length=1, max_length=32)
    stage: Literal["middle", "high", "university", "working", "other"]
    interests: list[
        Literal["science", "engineering", "humanities", "language", "business", "other"]
    ] = Field(default_factory=list, max_length=6)
    goal: Literal["exam", "work", "interest"]

    @field_validator("nickname")
    @classmethod
    def no_controls(cls, value: str) -> str:
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("昵称不能含控制字符")
        return value


class VerifyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: str = Field(min_length=16, max_length=100)
    code: str = Field(pattern=r"^\d{6}$")
    registration: Registration | None = None
    legacy_sessions: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=30
    )


def account_router(
    accounts: Accounts,
    social: SocialRepository,
    visitor: Any,
    assessments: Callable[[], SQLiteAssessmentRepository],
) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["accounts"])

    def state(owner: str) -> dict[str, Any]:
        account = accounts.account(owner)
        with accounts.repo.connect() as db:
            ready_demo = {
                row[0] for row in db.execute("SELECT email FROM accounts WHERE is_demo=1")
            }
        result: dict[str, Any] = {
            "account": account,
            "user_id": social.me(owner)["user_id"],
            "legacy_profile": accounts.has_profile(owner),
            "email_available": accounts.smtp_ready(),
            "demo_available": accounts.demo_enabled(),
            "demo_users": [
                {"email": email, "nickname": name, "registered": email in ready_demo}
                for email, name in DEMO_USERS.items()
            ]
            if accounts.demo_enabled()
            else [],
            "learning_sessions": {},
        }
        if account:
            with accounts.repo.connect() as db:
                ids = [
                    row[0]
                    for row in db.execute(
                        "SELECT session_id FROM session_owners WHERE owner=?", (owner,)
                    )
                ]
            sessions = [s for sid in ids if (s := assessments().get_session(sid)) is not None]
            for session in sorted(sessions, key=lambda s: s.updated_at):
                result["learning_sessions"][session.profile.book_id] = session.session_id
        return result

    @router.get("/me")
    def me(owner: str = Depends(visitor)) -> dict[str, Any]:
        return state(owner)

    @router.post("/code")
    def code(body: CodeInput, request: Request, owner: str = Depends(visitor)) -> dict[str, Any]:
        return accounts.send_code(
            owner, body.email, body.purpose, request.client.host if request.client else "unknown"
        )

    @router.post("/verify")
    def verify(
        body: VerifyInput, request: Request, response: Response, owner: str = Depends(visitor)
    ) -> dict[str, Any]:
        # Validate old bearer-session migration BEFORE consuming the one-time code.
        legacy = []
        if body.registration:
            for sid in body.legacy_sessions:
                session = assessments().get_session(sid)
                if session and accounts.repo.owns(owner, session.profile.book_id):
                    with accounts.repo.connect() as db:
                        row = db.execute(
                            "SELECT owner FROM session_owners WHERE session_id=?", (sid,)
                        ).fetchone()
                    if row and row[0] != owner:
                        raise HTTPException(403, "这份学习档案属于其他用户")
                    legacy.append(sid)
        target, token = accounts.complete(
            owner,
            body.challenge_id,
            body.code,
            body.registration.model_dump() if body.registration else None,
            request.cookies.get(COOKIE),
        )
        for sid in legacy:
            accounts.repo.claim_session(target, sid)
        response.set_cookie(
            COOKIE,
            token,
            max_age=TTL,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
        response.delete_cookie("zhiwo_visitor")
        return state(target)

    @router.post("/logout")
    def logout(request: Request, response: Response) -> dict[str, bool]:
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "请在 App 内执行此操作")
        accounts.logout(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE)
        response.delete_cookie("zhiwo_visitor")
        response.headers["Cache-Control"] = "private, no-store"
        return {"ok": True}

    @router.post("/demo-friends")
    def demo_friends(owner: str = Depends(visitor)) -> dict[str, Any]:
        if not accounts.demo_enabled():
            raise HTTPException(404, "演示体验已关闭")
        result = []
        with accounts.repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for email in DEMO_USERS:
                row = db.execute(
                    "SELECT owner FROM accounts WHERE email=? AND is_demo=1", (email,)
                ).fetchone()
                if not row or row[0] == owner:
                    continue
                peer = row[0]
                if social.blocked(db, owner, peer):
                    continue
                pair = social.pair(owner, peer)
                import time

                db.execute(
                    "INSERT INTO friendships VALUES(?,?,?,'accepted',?) ON CONFLICT(lo,hi) DO UPDATE SET status='accepted',updated=excluded.updated",
                    (*pair, peer, time.time()),
                )
                result.append(social.card(db, owner, peer))
        # A labelled one-time welcome; these accounts are not human agents or AI bots.
        for person in result:
            with accounts.repo.connect() as db:
                peer = social.resolve(db, person["user_id"])
            social.send(
                peer,
                social.me(owner)["user_id"],
                "demo-welcome-" + owner,
                "[演示账号] 你好！这是一条测试欢迎消息。你可以在另一个窗口登录这个账号，测试双向聊天和资料分享。",
                None,
            )
        return {"friends": result, "count": len(result)}

    return router
