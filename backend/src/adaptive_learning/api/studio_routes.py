from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import Field

from ..studio import get_studio
from ..studio_models import MediaInput, NoteAction, NoteInput, Strict


class Accept(Strict):
    job_id: str = Field(min_length=8, max_length=100)
    revision: int = Field(ge=1)
    accepted: bool


def studio_router(
    data_dir: Path, visitor: Callable[..., str], owned_book: Callable[[str, str], None]
) -> APIRouter:
    router = APIRouter(tags=["learning-studio"])
    studio = get_studio(data_dir)
    with studio.repo.connect() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS studio_note_editions (job_id TEXT PRIMARY KEY,owner TEXT NOT NULL,note_id TEXT NOT NULL,accepted INTEGER NOT NULL,updated REAL NOT NULL)"
        )

    @router.get("/studio/capabilities")
    def capabilities(owner: str = Depends(visitor)) -> dict[str, Any]:
        return {
            **studio.capabilities(),
            "draft_scope": hashlib.sha256(owner.encode()).hexdigest()[:24],
        }

    @router.post("/studio/media")
    def media(data: MediaInput, owner: str = Depends(visitor)) -> dict[str, Any]:
        owned_book(owner, data.book_id)
        if not studio.capabilities()[data.kind]:
            raise HTTPException(503, "图解或短片服务暂未就绪，原文阅读与笔记仍可使用")
        return studio.submit(owner, data.model_dump(), data.kind)

    @router.get("/studio/notes")
    def notes(book_id: str, owner: str = Depends(visitor)) -> dict[str, Any]:
        owned_book(owner, book_id)
        with studio.repo.connect() as db:
            rows = db.execute(
                "SELECT * FROM studio_notes WHERE owner=? AND book_id=? ORDER BY updated DESC LIMIT 200",
                (owner, book_id),
            ).fetchall()
        return {
            "items": [
                {k: v for k, v in studio.public_note(r).items() if k != "strokes"} for r in rows
            ]
        }

    @router.post("/studio/notes")
    def save(data: NoteInput, owner: str = Depends(visitor)) -> dict[str, Any]:
        owned_book(owner, data.book_id)
        return studio.save_note(owner, data)

    @router.get("/studio/notes/{key}")
    def note(key: str, owner: str = Depends(visitor)) -> dict[str, Any]:
        value = studio.note(owner, key)
        owned_book(owner, value["book_id"])
        with studio.repo.connect() as db:
            row = db.execute(
                "SELECT j.result,e.job_id FROM studio_note_editions e JOIN studio_jobs j ON j.id=e.job_id WHERE e.owner=? AND e.note_id=? AND e.accepted=1 ORDER BY e.updated DESC LIMIT 1",
                (owner, key),
            ).fetchone()
        return {
            **value,
            "edition": {"job_id": row["job_id"], **json.loads(row["result"])} if row else None,
        }

    @router.put("/studio/notes/{key}/audio")
    async def save_audio(
        key: str, request: Request, owner: str = Depends(visitor)
    ) -> dict[str, Any]:
        value = studio.note(owner, key)
        owned_book(owner, value["book_id"])
        try:
            revision = int(request.headers.get("x-note-revision", "-1"))
            duration = float(request.headers.get("x-audio-duration", "0"))
            content_length = int(request.headers.get("content-length", "0") or 0)
        except ValueError as error:
            raise HTTPException(422, "录音信息不完整") from error
        if content_length > 20 * 1024 * 1024:
            raise HTTPException(413, "录音需小于 20 MB")
        content = await request.body()
        return studio.save_voice_audio(
            owner,
            key,
            revision,
            content,
            request.headers.get("content-type", "").lower(),
            duration,
        )

    @router.get("/studio/notes/{key}/audio")
    def audio(key: str, owner: str = Depends(visitor)) -> FileResponse:
        value = studio.note(owner, key)
        owned_book(owner, value["book_id"])
        if value.get("input_mode") != "voice" or not value.get("audio_ready"):
            raise HTTPException(404, "录音不存在")
        path = studio.voice_path(owner, key)
        if not path.is_file():
            raise HTTPException(404, "录音不存在")
        return FileResponse(
            path,
            media_type=value.get("audio_mime") or "application/octet-stream",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "inline",
            },
        )

    @router.post("/studio/notes/{key}/analyze")
    def analyze(key: str, data: NoteAction, owner: str = Depends(visitor)) -> dict[str, Any]:
        value = studio.note(owner, key)
        owned_book(owner, value["book_id"])
        if value["revision"] != data.revision:
            raise HTTPException(409, "笔记已有新版本，请重新打开")
        voice = value.get("input_mode", "ink") == "voice"
        if voice and not value.get("audio_ready"):
            raise HTTPException(422, "请先录下一点内容，再请 AI 帮你整理")
        if not voice and not value["strokes"]:
            raise HTTPException(422, "先写下一点内容，再请 AI 帮你看看")
        if data.action == "improve" and (len(data.transcript) < 2 or "[待确认]" in data.transcript):
            raise HTTPException(422, "请先核对并补充识别文字，再检查理解")
        capability = "voice_notes" if voice else "notes_ai"
        if not studio.capabilities()[capability]:
            raise HTTPException(
                503, f"笔记 AI 暂未就绪，你的{'录音' if voice else '手写内容'}已保存"
            )
        return studio.submit(
            owner,
            {
                **data.model_dump(),
                "book_id": value["book_id"],
                "chapter_title": value["chapter_title"],
                "note_id": key,
            },
            data.action,
        )

    @router.post("/studio/notes/{key}/edition")
    def accept(key: str, data: Accept, owner: str = Depends(visitor)) -> dict[str, Any]:
        note = studio.note(owner, key)
        owned_book(owner, note["book_id"])
        row = studio.job(owner, data.job_id)
        spec = json.loads(row["data"])
        if (
            row["kind"] not in {"improve", "complete"}
            or row["status"] != "succeeded"
            or spec.get("note_id") != key
        ):
            raise HTTPException(404, "整理结果不存在")
        if note["revision"] != data.revision or (
            data.accepted and spec["revision"] != data.revision
        ):
            raise HTTPException(409, "原笔记已更新，请重新分析；旧整理结果仍在历史中")
        with studio.repo.connect() as db:
            db.execute(
                "INSERT INTO studio_note_editions VALUES(?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET accepted=excluded.accepted,updated=excluded.updated",
                (data.job_id, owner, key, int(data.accepted), time.time()),
            )
        return {"ok": True}

    @router.get("/studio/jobs")
    def jobs(book_id: str, owner: str = Depends(visitor)) -> dict[str, Any]:
        owned_book(owner, book_id)
        with studio.repo.connect() as db:
            rows = db.execute(
                "SELECT * FROM studio_jobs WHERE owner=? AND book_id=? ORDER BY created DESC LIMIT 100",
                (owner, book_id),
            ).fetchall()
        return {"items": [studio.public_job(r) for r in rows]}

    @router.get("/studio/jobs/{key}")
    def job(key: str, owner: str = Depends(visitor)) -> dict[str, Any]:
        row = studio.job(owner, key)
        owned_book(owner, row["book_id"])
        return studio.public_job(row)

    @router.get("/studio/jobs/{key}/asset")
    def asset(key: str, owner: str = Depends(visitor)) -> FileResponse:
        row = studio.job(owner, key)
        owned_book(owner, row["book_id"])
        if row["status"] != "succeeded" or row["kind"] not in {"image", "video"}:
            raise HTTPException(404, "学习图像尚未准备好")
        suffix, mime = ("jpg", "image/jpeg") if row["kind"] == "image" else ("mp4", "video/mp4")
        path = studio.assets / f"{row['id']}.{suffix}"
        if not path.is_file():
            raise HTTPException(404, "学习图像暂不可用")
        return FileResponse(
            path,
            media_type=mime,
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    return router
