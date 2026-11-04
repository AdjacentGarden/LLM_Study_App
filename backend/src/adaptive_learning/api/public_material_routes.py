"""Read-only public textbook downloads and explicit new-flow group intents."""
from __future__ import annotations
from typing import Annotated, Any
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from ..accounts import COOKIE
from ..public_materials import MaterialsPending, PublicMaterials


class GroupIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,100}$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    book_ids: list[Annotated[str, Field(min_length=1, max_length=150)]] = Field(min_length=1, max_length=50)


def public_material_router(services: Any) -> APIRouter:
    router = APIRouter(prefix="/api/public/library", tags=["public-materials"])

    def owner(request: Request, response: Response) -> str:
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD"} and origin and urlsplit(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "请在 App 内执行此操作")
        actor = services.account_repository.session_owner(request.cookies.get(COOKIE))
        if actor is None:
            raise HTTPException(401, "请先登录学习账号")
        response.headers["Cache-Control"] = "private, no-store"
        return str(actor)

    @router.post("/course-groups")
    def register(body: GroupIntent, actor: str = Depends(owner)) -> dict[str, Any]:
        try:
            return PublicMaterials(services).register(actor, title=body.title, description=body.description,
                                                     book_ids=body.book_ids, group_id=body.id)
        except PermissionError as error:
            raise HTTPException(403, "只能发布自己的教材资料") from error
        except ValueError as error:
            raise HTTPException(422, "课程组标识或教材列表无效") from error

    @router.get("/course-groups")
    def groups(actor: str = Depends(owner)) -> dict[str, Any]:
        return {"schema_version": 1, "items": PublicMaterials(services).list_ready(), "next_cursor": None}

    @router.get("/course-groups/{group_id}")
    def status(group_id: str, actor: str = Depends(owner)) -> dict[str, Any]:
        try:
            return PublicMaterials(services).status(group_id, actor)
        except KeyError as error:
            raise HTTPException(404, "公开课程不存在") from error

    @router.delete("/course-groups/{group_id}")
    def withdraw(group_id: str, actor: str = Depends(owner)) -> dict[str, bool]:
        try:
            PublicMaterials(services).withdraw(group_id, actor)
            return {"withdrawn": True}
        except KeyError as error:
            raise HTTPException(404, "公开课程不存在") from error
        except PermissionError as error:
            raise HTTPException(403, "只能撤回自己的公开课程") from error

    @router.get("/course-groups/{group_id}/manifest")
    def manifest(group_id: str, actor: str = Depends(owner)) -> dict[str, Any]:
        try:
            return PublicMaterials(services).manifest(group_id)
        except KeyError as error:
            raise HTTPException(404, "公开课程不存在") from error
        except MaterialsPending as error:
            raise HTTPException(409, "公共教材资料尚未准备完成") from error

    @router.get("/course-groups/{group_id}/assets/{digest}")
    def asset(group_id: str, digest: str, actor: str = Depends(owner)) -> FileResponse:
        try:
            path, descriptor = PublicMaterials(services).asset(group_id, digest)
        except (KeyError, MaterialsPending) as error:
            raise HTTPException(404, "公共教材文件不存在或已撤回") from error
        return FileResponse(path, media_type=descriptor["media_type"], headers={
            "ETag": '"'+digest+'"', "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-SHA256": digest, "X-Content-Type-Options": "nosniff", "Vary": "Cookie"})

    return router
