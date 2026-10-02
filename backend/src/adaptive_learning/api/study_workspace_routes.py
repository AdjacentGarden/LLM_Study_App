"""Account-bound study plan and graded mistake review endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..accounts import Accounts
from ..study_workspace import ReviewStatus, StudyWorkspace, TaskStatus, WorkspaceError
from .origin import trusted_write_origin


class TaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: TaskStatus


class MistakePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: ReviewStatus
    reason: str | None = Field(default=None, max_length=500)


def study_workspace_router(
    workspace: StudyWorkspace,
    accounts: Accounts,
    allowed_origins: tuple[str, ...] = (),
) -> APIRouter:
    router = APIRouter(prefix="/api/interviews/{session_id}", tags=["study-workspace"])

    def owner(request: Request) -> str | None:
        return accounts.request_owner(request)

    def writable(request: Request) -> None:
        if not trusted_write_origin(request, allowed_origins):
            raise HTTPException(status_code=403, detail="请在 App 内执行此操作")

    @router.get("/study-workspace")
    def read_workspace(
        session_id: str,
        request: Request,
        response: Response,
        chapter_id: str | None = Query(default=None, min_length=1, max_length=128),
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "private, no-store"
        try:
            return workspace.get(owner(request), session_id, chapter_id)
        except WorkspaceError as error:
            raise HTTPException(error.status_code, error.detail) from error

    @router.patch("/study-tasks/{task_id}")
    def patch_task(
        session_id: str,
        task_id: str,
        body: TaskPatch,
        request: Request,
        response: Response,
    ) -> dict[str, object]:
        writable(request)
        response.headers["Cache-Control"] = "private, no-store"
        try:
            return workspace.set_task(owner(request), session_id, task_id, body.status)
        except WorkspaceError as error:
            raise HTTPException(error.status_code, error.detail) from error

    @router.patch("/mistakes/{mistake_id}")
    def patch_mistake(
        session_id: str,
        mistake_id: str,
        body: MistakePatch,
        request: Request,
        response: Response,
    ) -> dict[str, object]:
        writable(request)
        response.headers["Cache-Control"] = "private, no-store"
        try:
            return workspace.set_mistake(
                owner(request),
                session_id,
                mistake_id,
                body.status,
                body.reason,
                "reason" in body.model_fields_set,
            )
        except WorkspaceError as error:
            raise HTTPException(error.status_code, error.detail) from error

    return router
