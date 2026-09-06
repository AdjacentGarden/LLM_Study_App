from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..social import SocialRepository


class VisibilityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class AttachmentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    kind: Literal["book", "flashcards", "note", "chapter", "points"]
    book_id: str = Field(min_length=1, max_length=100)
    resource_id: str | None = Field(default=None, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    course_id: str | None = Field(default=None, max_length=128)
    card_ids: list[Annotated[str, Field(max_length=128)]] = Field(
        default_factory=list, max_length=100
    )
    rights_confirmed: Literal[True]


class MessageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    client_id: str = Field(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    text: str = Field(default="", max_length=2000)
    attachment: AttachmentInput | None = None

    @model_validator(mode="after")
    def not_empty(self) -> MessageInput:
        if not self.text and not self.attachment:
            raise ValueError("消息不能为空")
        return self


class ReadInput(BaseModel):
    message_id: int = Field(ge=1)


def social_router(
    social: SocialRepository,
    visitor: Any,
    attachment_builder: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> APIRouter:
    router = APIRouter(prefix="/social", tags=["social"])

    @router.get("/me")
    def me(owner: str = Depends(visitor)) -> dict[str, Any]:
        return social.me(owner)

    @router.post("/discoverability")
    def visibility(body: VisibilityInput, owner: str = Depends(visitor)) -> dict[str, Any]:
        return social.visibility(owner, body.enabled)

    @router.get("/users")
    def search(
        query: str = Query(min_length=2, max_length=80), owner: str = Depends(visitor)
    ) -> list[dict[str, Any]]:
        return social.search(owner, query.strip())

    @router.get("/contacts")
    def contacts(owner: str = Depends(visitor)) -> dict[str, Any]:
        return social.contacts(owner)

    @router.post("/friends/{user_id}/{action}")
    def friend(
        user_id: str,
        action: Literal["request", "accept", "decline", "cancel", "remove", "block", "unblock"],
        owner: str = Depends(visitor),
    ) -> dict[str, Any]:
        return social.change_friend(owner, user_id, action)

    @router.get("/chats/{user_id}")
    def chat(
        user_id: str,
        after: int = Query(default=0, ge=0),
        before: int = Query(default=0, ge=0),
        owner: str = Depends(visitor),
    ) -> dict[str, Any]:
        if after and before:
            raise HTTPException(422, "不能同时查询前后两个方向")
        return social.chat(owner, user_id, after, before)

    @router.post("/chats/{user_id}")
    def send(user_id: str, body: MessageInput, owner: str = Depends(visitor)) -> dict[str, Any]:
        # Check consent before materializing private course snapshots.
        with social.repo.connect() as db:
            peer = social.resolve(db, user_id)
            if social.relationship(db, owner, peer) != "friend":
                raise HTTPException(403, "成为好友后才能发送消息")
        request_key = hashlib.sha256(
            json.dumps(
                body.model_dump(exclude={"client_id"}), sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest()
        replay = social.replay(owner, user_id, body.client_id, request_key)
        if replay is not None:
            return replay
        attachment = (
            attachment_builder(owner, body.attachment.model_dump()) if body.attachment else None
        )
        return social.send(owner, user_id, body.client_id, body.text, attachment, request_key)

    @router.post("/chats/{user_id}/read")
    def read(user_id: str, body: ReadInput, owner: str = Depends(visitor)) -> dict[str, bool]:
        social.read(owner, user_id, body.message_id)
        return {"ok": True}

    @router.post("/messages/{message_id}/acquire")
    def acquire(message_id: int, owner: str = Depends(visitor)) -> dict[str, Any]:
        return social.acquire(owner, message_id)

    @router.get("/users/{user_id}/avatar")
    def avatar(user_id: str, owner: str = Depends(visitor)) -> Response:
        with social.repo.connect() as db:
            peer = social.resolve(db, user_id)
            relation = social.relationship(db, owner, peer)
            visible = db.execute(
                "SELECT discoverable FROM social_users WHERE owner=?", (peer,)
            ).fetchone()[0]
            if relation == "unavailable" or (
                not visible and relation not in {"friend", "incoming", "outgoing", "self"}
            ):
                raise HTTPException(404, "头像不可用")
            row = db.execute("SELECT avatar FROM user_profiles WHERE owner=?", (peer,)).fetchone()
            if not row or not row[0]:
                raise HTTPException(404, "头像不可用")
            return Response(
                bytes(row[0]),
                media_type="image/jpeg",
                headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
            )

    return router
