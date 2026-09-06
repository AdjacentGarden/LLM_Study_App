from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..accounts import COOKIE, Accounts
from ..assessment.models import InterviewSession
from ..assessment.repository import SQLiteAssessmentRepository
from ..community import CommunityRepository, text_fingerprint
from ..ingestion.jobs import SQLiteOCRJobRepository
from ..social import SocialRepository
from .account_routes import account_router
from .schemas import BookCatalogItem
from .social_routes import social_router
from .user_profile import UserProfileInput, normalize_avatar, public_profile


class NoteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    book_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=20000)
    resource_id: str | None = None


class ShareInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    kind: Literal["book", "flashcards", "note"]
    book_id: str = Field(min_length=1, max_length=100)
    title: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    rights_confirmed: Literal[True]
    resource_id: str | None = None
    session_id: str | None = None
    course_id: str | None = None
    card_ids: list[str] = Field(default_factory=list, max_length=100)


def community_router(
    data_dir: Path,
    catalog: Callable[[], list[BookCatalogItem]],
    jobs: Callable[[], SQLiteOCRJobRepository],
    assessments: Callable[[], SQLiteAssessmentRepository],
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["community"])
    repo = CommunityRepository(data_dir / "state" / "community.sqlite3")
    social = SocialRepository(repo)
    accounts = Accounts(repo)
    initialized = False
    lock = threading.Lock()
    initial: list[str] = []
    published: set[str] = set()

    def prepare() -> None:
        nonlocal initialized
        if initialized:
            return
        with lock:
            if initialized:
                return
            entries = catalog()
            manifest: dict[str, str] = {}
            manifest_path = os.getenv("RAG_BOOK_INDEX_MANIFEST", "")
            if manifest_path:
                try:
                    manifest = json.loads(Path(manifest_path).read_text())
                except (OSError, ValueError):
                    pass  # PDF fingerprint still provides safe exact-file deduplication.
            for index, entry in enumerate(entries):
                source = jobs().source_fingerprint(entry.book_id)
                if not source:
                    continue
                normalized = None
                if not repo.asset(entry.book_id):
                    index_dir = manifest.get(entry.book_id)
                    if not index_dir and entry.book_id == "biology-required-2":
                        index_dir = os.getenv("RAG_INDEX_DIR")
                    if index_dir:
                        try:
                            chunks = json.loads((Path(index_dir) / "chunks.json").read_text())
                            normalized = text_fingerprint(
                                "\n".join(chunk["text"] for chunk in chunks)
                            )
                        except (OSError, ValueError, TypeError, KeyError):
                            pass
                old = repo.asset(entry.book_id)
                canonical = repo.register_asset(
                    entry.model_dump(), source, old["text_hash"] if old else normalized
                )
                published.add(entry.book_id)
                if index < 5:
                    initial.append(canonical)
                else:
                    repo.publish(
                        "curated",
                        "book",
                        entry.book_id,
                        entry.title,
                        "已完成解析，可直接加入书架，建立你自己的学习路径。",
                        {},
                    )
            initialized = True

    def visitor(request: Request, response: Response) -> str:
        # Cookie-authenticated writes must not be triggered from another website.
        origin = request.headers.get("origin")
        if (
            request.method not in {"GET", "HEAD"}
            and origin
            and urlsplit(origin).netloc != request.headers.get("host")
        ):
            raise HTTPException(403, "请在 App 内执行此操作")
        prepare()
        if request.cookies.get(COOKIE):
            account_owner = accounts.session_owner(request.cookies[COOKIE])
            if not account_owner:
                raise HTTPException(401, "登录已过期，请重新登录")
            response.headers["Cache-Control"] = "private, no-store"
            return account_owner
        owner, token, new = repo.visitor(request.cookies.get("zhiwo_visitor"), initial)
        if accounts.account(owner):
            raise HTTPException(401, "请使用邮箱验证码登录")
        response.headers["Cache-Control"] = "private, no-store"
        if new:
            with repo.connect() as db:
                social.ensure(db, owner)
            response.set_cookie(
                "zhiwo_visitor",
                token,
                httponly=True,
                samesite="lax",
                secure=request.url.scheme == "https",
                max_age=31536000,
            )
        return owner

    def owned_book(owner: str, book_id: str) -> None:
        if book_id not in published or not repo.owns(owner, book_id):
            raise HTTPException(403, "请先将这本书加入自己的书架")

    def owned_session(owner: str, session_id: str) -> InterviewSession:
        session = assessments().get_session(session_id)
        if session is None:
            raise HTTPException(404, "学习档案不存在")
        owned_book(owner, session.profile.book_id)
        if not repo.claim_session(owner, session_id):
            raise HTTPException(403, "该学习档案不属于当前浏览器身份")
        return session

    @router.get("/library", response_model=list[BookCatalogItem])
    def library(owner: str = Depends(visitor)) -> list[dict[str, Any]]:
        return repo.library(owner)

    @router.post("/library/books/{book_id}/remove")
    def remove_book(book_id: str, owner: str = Depends(visitor)) -> dict[str, Any]:
        repo.remove_book(owner, book_id)
        return {"ok": True, "message": "已移出书架，解析内容和个人学习记录仍保留"}

    @router.get("/library/resources")
    def resources(owner: str = Depends(visitor)) -> list[dict[str, Any]]:
        return repo.resources(owner)

    @router.post("/library/books/{book_id}/restore")
    def restore_book(book_id: str, owner: str = Depends(visitor)) -> dict[str, bool]:
        if not repo.owns(owner, book_id) and not repo.restore_book(owner, book_id):
            raise HTTPException(404, "只能恢复自己移出过的教材")
        return {"ok": True}

    @router.post("/library/notes")
    def save_note(body: NoteInput, owner: str = Depends(visitor)) -> dict[str, Any] | None:
        owned_book(owner, body.book_id)
        try:
            resource_id = repo.save_note(
                owner, body.book_id, body.title, body.body, body.resource_id
            )
        except KeyError as error:
            raise HTTPException(404, "这篇笔记不可编辑") from error
        return repo.resource(owner, resource_id)

    @router.get("/community")
    def feed(
        kind: Literal["all", "book", "flashcards", "note"] = "all",
        search: str = Query(default="", max_length=120),
        page: int = Query(default=0, ge=0, le=10000),
        owner: str = Depends(visitor),
    ) -> dict[str, Any]:
        return repo.list_posts(owner, kind, search.strip(), page)

    @router.get("/community/share-candidates/{session_id}")
    def candidates(session_id: str, owner: str = Depends(visitor)) -> list[dict[str, Any]]:
        owned_session(owner, session_id)
        # Latest saved course per chapter; never serialize goal/profile/answer keys.
        latest = {
            course.chapter_id: course for course in assessments().courses_for_session(session_id)
        }
        return [
            {
                "course_id": course.course_id,
                "title": course.chapter_title,
                "cards": [
                    {
                        "id": card.card_id,
                        "front": card.front,
                        "back": card.back,
                        "pages": sorted({c.page_number for c in card.citations}),
                    }
                    for card in course.flashcards
                ],
            }
            for course in latest.values()
        ]

    def prepare_share(body: ShareInput, owner: str) -> tuple[str, dict[str, Any]]:
        owned_book(owner, body.book_id)
        book_asset = repo.asset(body.book_id)
        if book_asset is None:
            raise HTTPException(404, "教材不存在")
        content: dict[str, Any] = {}
        title = body.title
        if body.kind == "book":
            title = json.loads(book_asset["catalog"])["title"]
        elif body.resource_id:
            resource = repo.resource(owner, body.resource_id)
            if (
                not resource
                or resource["kind"] != body.kind
                or resource["book_id"] != book_asset["canonical"]
            ):
                raise HTTPException(404, "未找到可分享的个人资料")
            content = resource["content"]
            title = title or resource["title"]
        elif body.kind == "flashcards" and body.session_id and body.course_id:
            session = owned_session(owner, body.session_id)
            if session.profile.book_id != body.book_id:
                raise HTTPException(422, "闪卡与所选书籍不一致")
            course = assessments().get_course_for_session(body.session_id, body.course_id)
            if course is None:
                raise HTTPException(404, "请先生成自己的章节课程")
            selected = set(body.card_ids)
            valid = {card.card_id for card in course.flashcards}
            if not selected or not selected <= valid:
                raise HTTPException(422, "请选择本章有效的闪卡")
            content = {
                "cards": [
                    {
                        "front": card.front,
                        "back": card.back,
                        "pages": sorted({c.page_number for c in card.citations}),
                    }
                    for card in course.flashcards
                    if card.card_id in selected
                ]
            }
            title = title or course.chapter_title + " · 闪卡"
        else:
            raise HTTPException(422, "请先保存笔记，或选择已生成的闪卡")
        return title, content

    @router.post("/community/share")
    def share(body: ShareInput, owner: str = Depends(visitor)) -> dict[str, Any]:
        title, content = prepare_share(body, owner)
        post_id, duplicate = repo.publish(
            owner, body.kind, body.book_id, title, body.description, content
        )
        return {"post_id": post_id, "status": "already_shared" if duplicate else "shared"}

    @router.get("/community/{post_id}/check")
    def check(post_id: str, owner: str = Depends(visitor)) -> dict[str, Any]:
        with repo.connect() as db:
            post = db.execute("SELECT * FROM posts WHERE id=? AND active=1", (post_id,)).fetchone()
        if not post:
            raise HTTPException(404, "分享已撤回或不存在")
        library = repo.library(owner)
        book_asset = repo.asset(post["book_id"])
        if book_asset is None:
            raise HTTPException(404, "教材不存在")
        asset = json.loads(book_asset["catalog"])
        same_title = [
            book["title"]
            for book in library
            if book["title"].strip().casefold() == asset["title"].strip().casefold()
            and book["book_id"] != post["book_id"]
        ]
        exists = (
            repo.owns(owner, post["book_id"])
            if post["kind"] == "book"
            else any(r["source_post"] == post_id for r in repo.resources(owner))
        )
        return {
            "already_owned": exists,
            "book_id": post["book_id"],
            "similar_titles": same_title,
            "method": "PDF 指纹 / 解析文本指纹",
        }

    @router.post("/community/{post_id}/acquire")
    def acquire(post_id: str, owner: str = Depends(visitor)) -> dict[str, Any]:
        try:
            return repo.acquire(owner, post_id)
        except KeyError as error:
            raise HTTPException(404, "分享已撤回或不存在") from error

    @router.post("/community/{post_id}/withdraw")
    def withdraw(post_id: str, owner: str = Depends(visitor)) -> dict[str, Any]:
        if not repo.withdraw(owner, post_id):
            raise HTTPException(403, "只能撤回自己的分享")
        return {"ok": True}

    @router.post("/library/resources/{resource_id}/archive")
    def archive(
        resource_id: str, archived: bool = True, owner: str = Depends(visitor)
    ) -> dict[str, bool]:
        if not repo.archive_resource(owner, resource_id, archived):
            raise HTTPException(404, "资料不存在")
        return {"ok": True}

    @router.get("/user/profile")
    def user_profile(owner: str = Depends(visitor)) -> dict[str, Any]:
        with repo.connect() as db:
            return public_profile(
                db.execute("SELECT * FROM user_profiles WHERE owner=?", (owner,)).fetchone()
            )

    @router.post("/user/profile")
    def save_user_profile(body: UserProfileInput, owner: str = Depends(visitor)) -> dict[str, Any]:
        avatar = normalize_avatar(body.avatar_data_url) if body.avatar_data_url else None
        with repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM user_profiles WHERE owner=?", (owner,)).fetchone()
            revision = previous["revision"] if previous else 0
            if body.revision != revision:
                raise HTTPException(409, "资料已在其他页面更新，请重新载入后再保存")
            if not body.reset_avatar and not body.avatar_data_url and previous:
                avatar = previous["avatar"]
            db.execute(
                "INSERT INTO user_profiles VALUES (?,?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET nickname=excluded.nickname,age=excluded.age,bio=excluded.bio,avatar=excluded.avatar,revision=excluded.revision",
                (owner, body.nickname, body.age, body.bio, avatar, revision + 1),
            )
            return public_profile(
                db.execute("SELECT * FROM user_profiles WHERE owner=?", (owner,)).fetchone()
            )

    @router.get("/user/profile/avatar")
    def user_avatar(owner: str = Depends(visitor)) -> Response:
        with repo.connect() as db:
            row = db.execute("SELECT avatar FROM user_profiles WHERE owner=?", (owner,)).fetchone()
        if not row or not row["avatar"]:
            raise HTTPException(404, "尚未设置自定义头像")
        return Response(
            bytes(row["avatar"]),
            media_type="image/jpeg",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    def make_attachment(owner: str, spec: dict[str, Any]) -> dict[str, Any]:
        kind = spec["kind"]
        if kind in {"book", "flashcards", "note"}:
            title, content = prepare_share(ShareInput.model_validate(spec), owner)
        else:
            owned_book(owner, spec["book_id"])
            if not spec.get("session_id") or not spec.get("course_id"):
                raise HTTPException(422, "请选择自己的章节课程")
            session = owned_session(owner, spec["session_id"])
            if session.profile.book_id != spec["book_id"]:
                raise HTTPException(422, "章节与教材不一致")
            course = assessments().get_course_for_session(spec["session_id"], spec["course_id"])
            if not course:
                raise HTTPException(404, "课程不存在")
            if kind == "chapter":
                title = course.chapter_title + " · 章节总结"
                content = {"body": course.summary}
            else:
                title = course.chapter_title + " · 知识点清单"
                content = {
                    "body": "\n\n".join(
                        f"{index + 1}. {point.title}\n{point.explanation}"
                        for index, point in enumerate(course.knowledge_points)
                    )
                }
        asset = repo.asset(spec["book_id"])
        if not asset:
            raise HTTPException(404, "教材不存在")
        catalog_item = json.loads(asset["catalog"])
        return {
            "kind": kind,
            "book_id": asset["canonical"],
            "title": title,
            "content": content,
            "book_title": catalog_item["title"],
            "cover_url": catalog_item.get("cover_url"),
        }

    router.include_router(social_router(social, visitor, make_attachment))
    router.include_router(account_router(accounts, social, visitor, assessments))
    return router
