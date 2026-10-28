"""Native client API, reusing the existing verified ingestion and learning services."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, date, datetime
from ipaddress import ip_address
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from .. import native_catalog as curated
from ..accounts import COOKIE, TTL
from ..assessment.diagnostic_cache import (
    DiagnosticChapterCacheError,
    DiagnosticChapterSubmissionBlocked,
    RepositoryDiagnosticChapterCache,
)
from ..assessment.item_generation import (
    DiagnosticGenerationError,
    diagnostic_bank_key,
    structure_fingerprint,
)
from ..assessment.models import DiagnosticItem
from ..assessment.preview import create_preview_session
from ..bootstrap import BootstrapService, builtin_content_version
from ..ingestion.chaptering import load_normalized_pages
from ..ingestion.covers import cover_url
from ..ingestion.models import BookStructure
from ..learning_design import NativeLearningDesign
from ..learning_preferences import LearningDesignOverrides
from ..llm.client import LLMCooldownError, LLMError
from ..native import NativeConflict
from ..personalization.models import ChapterLearningBundle, PublicChapterLearningBundle
from ..rag.index import RAGIndexError
from ..rag.upload_index import published_upload_index
from ..social import SocialRepository
from .chapter_dependency import require_chapter_reconstructor
from .qa_dependency import QAConfigurationError, prepare_upload_index, require_qa_service
from .schemas import BookQuestionRequest, FlashcardReviewRequest


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WorkspaceInput(Strict):
    id: str = Field(
        default_factory=lambda: "workspace_" + uuid.uuid4().hex, pattern=r"^[A-Za-z0-9_-]{8,100}$"
    )
    name: str = Field(min_length=1, max_length=120)
    preferences: dict[str, list[str]] = Field(default_factory=dict, max_length=20)


class LearnerInput(Strict):
    learningStage: Literal["middle", "high", "university", "working", "other"] | None = None
    displayName: str = Field(min_length=1, max_length=30)
    primaryGoal: Literal["systematic", "exam", "growth", "interest"] | None = None
    dailyTime: Literal["under30", "30to60", "1to2h", "over2h"] | None = None
    learningBarriers: list[Literal["starting", "focus", "understanding", "memory"]] = Field(
        default_factory=list, max_length=2
    )
    guidanceStyles: list[Literal["keyPoints", "principles", "examples", "stepByStep"]] = Field(
        default_factory=list, max_length=2
    )
    planningSupport: Literal["selfDirected", "collaborative", "structured", "explore"] | None = None
    onboardingVersion: Literal[1, 2] = 1

    @field_validator("learningBarriers", "guidanceStyles")
    @classmethod
    def unique_choices(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("不能重复选择同一项")
        return value


class CatalogPracticeInput(Strict):
    event_id: str = Field(min_length=8, max_length=100)
    answer: str = Field(min_length=1, max_length=4000)
    response_seconds: float = Field(ge=0.1, le=3600)
    confidence: float = Field(ge=0, le=1)


class ResourceInput(Strict):
    book_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class DesignInput(Strict):
    revision: int = Field(ge=0)
    overrides: LearningDesignOverrides


class PlanConfirmation(Strict):
    day: date
    plan_id: str = Field(min_length=1, max_length=100)
    task_ids: list[str] = Field(max_length=500)
    revision: int = Field(default=0, ge=0)


class TaskCompletion(Strict):
    event_id: str = Field(min_length=8, max_length=100)
    day: date


class AdjustmentInput(Strict):
    suggestion_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=0)


class SelectionInput(Strict):
    workspace_id: str = Field(min_length=1, max_length=100)
    resource_id: str = Field(min_length=1, max_length=100)


class ChapterInput(Strict):
    chapter_id: str = Field(min_length=1, max_length=150)
    title: str = Field(min_length=1, max_length=250)
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)


class StructureInput(Strict):
    revision: int = Field(ge=0)
    chapters: list[ChapterInput] = Field(min_length=1, max_length=500)


class ProgressInput(Strict):
    revision: int = Field(ge=0)
    mutation_id: str = Field(min_length=8, max_length=100)
    page: int = Field(ge=1)
    chapter_id: str | None = Field(default=None, max_length=150)
    lesson_id: str | None = Field(default=None, max_length=150)
    completed_chapters: list[str] = Field(default_factory=list, max_length=500)
    task_status: dict[str, str] = Field(default_factory=dict, max_length=1000)
    mastered_ids: list[str] = Field(default_factory=list, max_length=2000)


def native_router(services: Any) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["native-client"])
    design = NativeLearningDesign(services)

    @router.post("/native/local-login")
    def local_login(request: Request, response: Response) -> dict[str, bool]:
        accounts = services.account_repository
        if not accounts.local_login_enabled():
            raise HTTPException(404, "本地默认登录未启用")
        try:
            local_peer = bool(request.client and ip_address(request.client.host).is_loopback)
        except ValueError:
            local_peer = False
        if not local_peer or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise HTTPException(403, "默认账号仅供本机开发使用")
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "请在 App 内执行此操作")
        token = accounts.local_session(request.cookies.get(COOKIE))
        actor = accounts.session_owner(token)
        assert actor is not None
        social = SocialRepository(accounts.repo)
        with accounts.repo.connect() as db:
            social.ensure(db, actor)
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            max_age=TTL,
        )
        response.headers["Cache-Control"] = "private, no-store"
        return {"ok": True}

    def owner(request: Request, response: Response) -> str:
        if request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                raise HTTPException(403, "请在 App 内执行此操作")
        value = services.account_repository.session_owner(request.cookies.get(COOKIE))
        if value is None:
            raise HTTPException(401, "请先登录学习账号")
        response.headers["Cache-Control"] = "private, no-store"
        return str(value)

    def resource(wid: str, rid: str, actor: str) -> dict[str, Any]:
        try:
            result = services.native_repository.resource(actor, wid, rid)
        except KeyError as error:
            raise HTTPException(404, "课程资料不存在") from error
        accessible_book(actor, result["book_id"])
        return dict(result)

    def accessible_book(actor: str, book_id: str) -> None:
        repo = services.account_repository.repo
        if not (repo.owns_upload(actor, book_id) or repo.owns(actor, book_id)):
            raise HTTPException(403, "不能访问其他账号的教材")

    def effective_structure(row: dict[str, Any]) -> BookStructure | None:
        if row["structure_json"]:
            return BookStructure.model_validate_json(row["structure_json"])
        if row["book_id"] == curated.BOOK_ID:
            return curated.structure()
        value = services.job_repository.get_structure(row["book_id"])
        return value if isinstance(value, BookStructure) else None

    def resource_state(row: dict[str, Any]) -> dict[str, Any]:
        structure = effective_structure(row)
        stored = services.job_repository.get_book(row["book_id"])
        bundles = []
        if row["session_id"]:
            latest: dict[str, ChapterLearningBundle] = {}
            for bundle in services.assessment_repository.courses_for_session(row["session_id"]):
                previous = latest.get(bundle.chapter_id)
                if previous is None or bundle.version > previous.version:
                    latest[bundle.chapter_id] = bundle
            bundles = [
                PublicChapterLearningBundle.from_private(v).model_dump(mode="json")
                for v in latest.values()
            ]
        progress = services.native_repository.progress(row["id"])
        reviews = services.native_repository.catalog_review_states(row["id"]) if row["book_id"] == curated.BOOK_ID else {}
        session_purpose = None
        if row["session_id"]:
            session = services.assessment_repository.get_session(row["session_id"])
            if session:
                session_purpose = session.purpose
                reviews = {key: value.model_dump(mode="json") for key, value in session.profile.flashcard_reviews.items()}
        return {
            "id": row["id"],
            "book_id": row["book_id"],
            "workspace_id": row["workspace_id"],
            "revision": row["revision"],
            "session_id": row["session_id"],
            "session_purpose": session_purpose,
            "filename": stored.original_name if stored else "教材.pdf",
            "cover_url": cover_url(services.settings.data_dir, row["book_id"])
            or (curated.snapshot()["source"]["summary"].get("cover_url")
                if row["book_id"] == curated.BOOK_ID else None),
            "status": "ready"
            if row["book_id"] == curated.BOOK_ID
            else stored.status
            if stored
            else "missing",
            "structure": structure.model_dump(mode="json") if structure else None,
            "courses": bundles,
            "progress": progress,
            "flashcard_reviews": reviews,
            "source_content": curated.public_source(row["id"], progress)
            if row["book_id"] == curated.BOOK_ID
            else None,
        }

    def workspace_state(actor: str, wid: str) -> dict[str, Any]:
        try:
            row = services.native_repository.workspace(actor, wid)
            rows = services.native_repository.resources(actor, wid)
        except KeyError as error:
            raise HTTPException(404, "课程不存在") from error
        return {
            k: v
            for k, v in {
                **row,
                "resources": [resource_state(r) for r in rows],
                "learning_design": design.state(actor, wid),
            }.items()
            if k != "owner"
        }

    bootstrap_service = BootstrapService(services.native_repository, services.assessment_repository,
                                         services.job_repository, services.account_repository.repo,
                                         data_dir=services.settings.data_dir)

    @router.get("/native/bootstrap")
    def bootstrap(mode: Literal["full", "metadata"] = "full", actor: str = Depends(owner)) -> dict[str, Any]:
        return bootstrap_service.assemble(actor, light=mode == "metadata")

    @router.get("/native/builtin-content/{version}")
    def builtin_content(version: str, actor: str = Depends(owner)) -> dict[str, Any]:
        if version != builtin_content_version():
            raise HTTPException(404, "课程内容版本已更新，请刷新课程")
        return {"structure": curated.structure().model_dump(mode="json"), "courses": [],
                "source_content": curated.public_source("__shared__", {})}

    @router.get("/native/capabilities")
    def capabilities(actor: str = Depends(owner)) -> dict[str, Any]:
        return {"contract_version": 1, "bootstrap_metadata": True,
                "teaching_units": {"backend": True, "native": False},
                "server_planning": {"backend": True, "native": False},
                "note_cloud_sync": {"backend": True, "native": False},
                "voice_transcription": {"backend": True, "native": True,
                    "runtime_checked_at": "/api/studio/capabilities", "runtime_status_path": "/api/studio/capabilities"},
                "handwriting": {"backend": True, "native": True, "runtime_status_path": "/api/studio/capabilities"},
                "media_generation": {"backend": True, "native": True, "runtime_status_path": "/api/studio/capabilities"},
                "community_social": {"backend": True, "native": False}}

    @router.put("/native/learner-preferences")
    def learner_preferences(body: LearnerInput, actor: str = Depends(owner)) -> dict[str, Any]:
        # Older clients only edit the original fields. Preserve the extended answers
        # and completion version unless they are explicitly included in the request.
        previous = services.native_repository.learner_preferences(actor) or {}
        payload = LearnerInput.model_validate(
            {**previous, **body.model_dump(exclude_unset=True)}
        ).model_dump()
        services.native_repository.save_learner_preferences(actor, payload)
        with services.account_repository.repo.connect() as db:
            db.execute(
                "UPDATE user_profiles SET nickname=?,revision=revision+1 WHERE owner=?",
                (body.displayName, actor),
            )
            if "learningStage" in body.model_fields_set:
                db.execute(
                    "UPDATE accounts SET stage=? WHERE owner=?",
                    (body.learningStage or "other", actor),
                )
        return payload

    @router.get("/workspaces/{wid}/learning-design")
    def learning_design(wid: str, actor: str = Depends(owner)) -> dict[str, Any]:
        try:
            return design.state(actor, wid)
        except KeyError as error:
            raise HTTPException(404, "课程不存在") from error

    @router.put("/workspaces/{wid}/learning-design")
    def update_learning_design(
        wid: str, body: DesignInput, actor: str = Depends(owner)
    ) -> dict[str, Any]:
        try:
            services.native_repository.save_design_overrides(
                actor, wid, body.revision, body.overrides.model_dump(exclude_none=True)
            )
            return design.state(actor, wid)
        except KeyError as error:
            raise HTTPException(404, "课程不存在") from error
        except NativeConflict as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/workspaces/{wid}/study-plan")
    def study_plan(
        wid: str, day: date | None = None, actor: str = Depends(owner)
    ) -> dict[str, Any]:
        try:
            return design.plan(actor, wid, day or datetime.now(UTC).date())
        except KeyError as error:
            raise HTTPException(404, "课程不存在") from error

    @router.post("/workspaces/{wid}/study-plan/confirm")
    def confirm_plan(
        wid: str, body: PlanConfirmation, actor: str = Depends(owner)
    ) -> dict[str, Any]:
        try:
            return design.confirm_plan(
                actor, wid, body.day, body.plan_id, body.task_ids, body.revision
            )
        except KeyError as error:
            raise HTTPException(404, "课程不存在") from error
        except NativeConflict as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/workspaces/{wid}/study-plan/tasks/{task_id}/complete")
    def complete_task(
        wid: str, task_id: str, body: TaskCompletion, actor: str = Depends(owner)
    ) -> dict[str, bool]:
        try:
            if not any(t["task_id"] == task_id for t in design.queue(actor, wid, body.day)):
                raise HTTPException(404, "学习任务不存在")
            inserted = services.native_repository.complete_task(actor, wid, body.event_id, task_id)
            return {"ok": True, "duplicate": not inserted}
        except KeyError as error:
            raise HTTPException(404, "课程不存在") from error
        except NativeConflict as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/workspaces/{wid}/learning-design/feedback")
    def design_feedback(wid: str, actor: str = Depends(owner)) -> list[dict[str, Any]]:
        try:
            return design.feedback(actor, wid)
        except KeyError as error:
            raise HTTPException(404, "课程不存在") from error

    @router.post("/workspaces/{wid}/learning-design/adjustments")
    def accept_adjustment(
        wid: str, body: AdjustmentInput, actor: str = Depends(owner)
    ) -> dict[str, Any]:
        try:
            suggestion = next(
                (
                    s
                    for s in design.feedback(actor, wid)
                    if s["suggestion_id"] == body.suggestion_id
                ),
                None,
            )
            if suggestion is None:
                raise HTTPException(404, "调整建议不存在或证据已改变")
            if suggestion["state"] == "accepted":
                return {"adjustment": suggestion, "design": design.state(actor, wid)}
            current = services.native_repository.design_overrides(actor, wid)
            overrides = LearningDesignOverrides.model_validate(
                {**current["overrides"], **suggestion["proposed_overrides"]}
            )
            record = {
                **suggestion,
                "state": "accepted",
                "previous_overrides": current["overrides"],
                "applied_revision": body.revision + 1,
            }
            services.native_repository.save_design_overrides(
                actor,
                wid,
                body.revision,
                overrides.model_dump(exclude_none=True),
                adjustment=record,
            )
            return {"adjustment": record, "design": design.state(actor, wid)}
        except KeyError as error:
            raise HTTPException(404, "课程不存在") from error
        except NativeConflict as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/workspaces/{wid}/learning-design/adjustments/{adjustment_id}/revert")
    def revert_adjustment(
        wid: str, adjustment_id: str, actor: str = Depends(owner)
    ) -> dict[str, Any]:
        try:
            record = next(
                (
                    s
                    for s in services.native_repository.adjustments(actor, wid)
                    if s["suggestion_id"] == adjustment_id
                ),
                None,
            )
            if record is None:
                raise HTTPException(404, "调整记录不存在")
            if record["state"] == "accepted":
                changed = {**record, "state": "reverted"}
                services.native_repository.save_design_overrides(
                    actor,
                    wid,
                    record["applied_revision"],
                    record["previous_overrides"],
                    adjustment=changed,
                )
            return design.state(actor, wid)
        except KeyError as error:
            raise HTTPException(404, "课程不存在") from error
        except NativeConflict as error:
            raise HTTPException(
                409, "偏好已有其他修改，请在课程设置中调整，避免覆盖新设置"
            ) from error

    @router.post("/workspaces/{wid}/resources/{rid}/qa")
    def resource_qa(
        wid: str, rid: str, body: BookQuestionRequest, request: Request, actor: str = Depends(owner)
    ) -> Any:
        row = resource(wid, rid, actor)
        scoped = body.model_copy(update={"workspace_id": wid, "resource_id": rid})
        book_ids = list(dict.fromkeys([row["book_id"], *body.book_ids]))
        resources = {r["book_id"]: r for r in services.native_repository.resources(actor, wid)}
        if any(book_id not in resources for book_id in book_ids):
            raise HTTPException(422, "答疑资料必须属于当前课程")
        results = []
        for book_id in book_ids:
            selected = resource(wid, resources[book_id]["id"], actor)
            question = scoped.model_copy(update={
                "resource_id": selected["id"], "book_ids": [],
                "chapter_id": body.chapter_id if book_id == row["book_id"] else None,
            })
            result = services.answer_book_question(
                book_id, question, require_qa_service(book_id), request
            ).model_dump(mode="json")
            for claim in result["claims"]:
                claim["book_id"] = book_id
            structure = effective_structure(selected)
            results.append((structure.title if structure else book_id, result))
        if len(results) == 1:
            return results[0][1]
        supported = [result for _, result in results if result["status"] == "supported"]
        general = any(result["status"] == "general" for _, result in results)
        return {
            "book_id": row["book_id"], "status": "general" if general else "supported" if supported else "insufficient",
            "answer": "\n\n".join(f"{title}\n{result['answer']}" for title, result in results
                                      if not general or result["status"] != "insufficient"),
            "claims": [claim for _, result in results for claim in result["claims"]],
            "confidence": min((result["confidence"] for result in supported), default=0),
        }

    @router.get("/native/catalog")
    def catalog(actor: str = Depends(owner)) -> list[dict[str, Any]]:
        return curated.catalog(repository=services.account_repository.repo, owner=actor)

    @router.post("/native/catalog/{catalog_id}/import")
    def import_catalog(
        catalog_id: str, body: WorkspaceInput, actor: str = Depends(owner)
    ) -> dict[str, Any]:
        if catalog_id.startswith("community_post_"):
            post_id = catalog_id.removeprefix("community_post_")
            repo = services.account_repository.repo
            with repo.connect() as db:
                post = db.execute(
                    "SELECT p.book_id FROM posts p JOIN assets a ON a.book_id=p.book_id "
                    "WHERE p.id=? AND p.kind='book' AND p.active=1", (post_id,),
                ).fetchone()
            if post is None or services.job_repository.get_book(str(post["book_id"])) is None:
                raise HTTPException(404, "社区教材原文件暂不可用")
            try:
                services.native_repository.save_workspace(actor, body.id, body.name, body.preferences)
                acquired = repo.acquire(actor, post_id)
            except KeyError as error:
                raise HTTPException(404, "社区课程不存在或已撤回") from error
            row = services.native_repository.add_resource(actor, body.id, str(acquired["book_id"]))
            services.native_repository.save_selection(actor, body.id, row["id"])
            # Preserve the real source state; this path does not run OCR, create
            # synthetic chapters or borrow the built-in biology course content.
            return workspace_state(actor, body.id)
        if catalog_id != curated.CATALOG_ID or not curated.catalog():
            raise HTTPException(404, "已有课程不存在")
        try:
            services.native_repository.save_workspace(actor, body.id, body.name, body.preferences)
        except KeyError as error:
            raise HTTPException(404, "课程不存在") from error
        source = curated.snapshot()["source"]
        repo = services.account_repository.repo
        canonical = repo.register_asset(
            {
                "book_id": curated.BOOK_ID,
                "title": source["summary"]["title"],
                "status": "ready",
                "page_count": source["summary"]["page_count"],
                "chapter_count": source["summary"]["chapter_count"],
                "summary": curated.snapshot()["entry"]["description"],
                "diagnostics_ready": False,
                "cover_url": source["summary"]["cover_url"],
            },
            curated.snapshot()["provenance"]["sources_sha256"],
            None,
        )
        repo.add_book(actor, canonical)
        row = services.native_repository.add_resource(actor, body.id, curated.BOOK_ID)
        services.native_repository.save_selection(actor, body.id, row["id"])
        return workspace_state(actor, body.id)

    @router.get("/workspaces/{wid}/resources/{rid}/catalog-records")
    def catalog_records(wid: str, rid: str, actor: str = Depends(owner)) -> dict[str, Any]:
        row = resource(wid, rid, actor)
        if row["book_id"] != curated.BOOK_ID:
            raise HTTPException(404, "已有课程不存在")
        return {
            "book_id": row["book_id"],
            "evidence": services.native_repository.catalog_practice(rid),
        }

    @router.post("/workspaces/{wid}/resources/{rid}/catalog-flashcards/{card_id}")
    def catalog_flashcard(
        wid: str, rid: str, card_id: str, body: FlashcardReviewRequest, actor: str = Depends(owner)
    ) -> dict[str, Any]:
        row = resource(wid, rid, actor)
        if row["book_id"] != curated.BOOK_ID:
            raise HTTPException(404, "已有课程不存在")
        card = next((c for c in curated.snapshot()["source"]["flashcards"] if c["card_id"] == card_id), None)
        if card is None:
            raise HTTPException(404, "闪卡不存在")
        try:
            return cast(dict[str, Any], services.native_repository.review_catalog_card(
                rid, body.event_id, card, body.rating, body.response_seconds))
        except NativeConflict as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, "闪卡反馈必须是 again/hard/good/easy") from error

    @router.post("/workspaces/{wid}/resources/{rid}/catalog-practice/{question_id}")
    def catalog_practice(
        wid: str,
        rid: str,
        question_id: str,
        body: CatalogPracticeInput,
        actor: str = Depends(owner),
    ) -> dict[str, Any]:
        row = resource(wid, rid, actor)
        if row["book_id"] != curated.BOOK_ID:
            raise HTTPException(404, "已有课程不存在")
        question = next(
            (q for q in curated.snapshot()["source"]["quizzes"] if q["question_id"] == question_id),
            None,
        )
        if not question:
            raise HTTPException(404, "题目不存在")
        previous = next(
            (
                r
                for r in services.native_repository.catalog_practice(rid)
                if r["id"] == body.event_id
            ),
            None,
        )
        if previous:
            if previous["question_id"] != question_id or previous["answer"] != body.answer:
                raise HTTPException(409, "重复提交的内容不一致")
            return {"duplicate": True, "evidence": previous["feedback"]}
        if not question["choices"]:
            raise HTTPException(503, "主观题评分需要模型服务；已有课程与闪卡可继续学习")
        answer = body.answer
        if (
            len(answer) == 1
            and answer in "ABCD"
            and "ABCD".index(answer) < len(question["choices"])
        ):
            answer = question["choices"]["ABCD".index(answer)]
        score = float(answer == question["answer"])
        feedback = {
            "score": score,
            "misconception_candidates": [] if score else [question["concept"]],
            "missing_rubric": [] if score else [question["explanation"]],
            "needs_follow_up": not bool(score),
        }
        record = {
            "id": body.event_id,
            "question_id": question_id,
            "title": question["prompt"],
            "kind": "已有课程练习",
            "chapter": question["concept"],
            "score": score,
            "at": datetime.now(UTC).isoformat(),
            "answer": body.answer,
            "pages": [question["page_start"]] if question["page_start"] else [],
            "feedback": feedback,
        }
        inserted = services.native_repository.save_catalog_practice(rid, body.event_id, record)
        if not inserted:
            # Resolve races through the same duplicate-content validation.
            return catalog_practice(wid, rid, question_id, body, actor)
        return {"duplicate": False, "evidence": feedback}

    @router.get("/workspaces")
    def workspaces(actor: str = Depends(owner)) -> list[dict[str, Any]]:
        return [
            workspace_state(actor, w["id"]) for w in services.native_repository.workspaces(actor)
        ]

    @router.put("/native/selection")
    def selection(body: SelectionInput, actor: str = Depends(owner)) -> dict[str, bool]:
        resource(body.workspace_id, body.resource_id, actor)
        services.native_repository.save_selection(actor, body.workspace_id, body.resource_id)
        return {"ok": True}

    @router.post("/workspaces")
    def save_workspace(body: WorkspaceInput, actor: str = Depends(owner)) -> dict[str, Any]:
        if sum(len(s) for values in body.preferences.values() for s in values) > 10000:
            raise HTTPException(422, "课程偏好内容过长")
        try:
            services.native_repository.save_workspace(actor, body.id, body.name, body.preferences)
        except KeyError as error:
            raise HTTPException(404, "课程不存在") from error
        return workspace_state(actor, body.id)

    @router.get("/workspaces/{wid}")
    def get_workspace(wid: str, actor: str = Depends(owner)) -> dict[str, Any]:
        return workspace_state(actor, wid)

    @router.delete("/workspaces/{wid}")
    def delete_workspace(wid: str, actor: str = Depends(owner)) -> dict[str, bool]:
        try:
            services.native_repository.delete_workspace(actor, wid)
        except KeyError as error:
            raise HTTPException(404, "课程不存在") from error
        return {"ok": True}

    @router.post("/workspaces/{wid}/resources")
    def add_resource(wid: str, body: ResourceInput, actor: str = Depends(owner)) -> dict[str, Any]:
        accessible_book(actor, body.book_id)
        try:
            row = services.native_repository.add_resource(actor, wid, body.book_id)
        except KeyError as error:
            raise HTTPException(404, "课程不存在") from error
        return resource_state(row)

    @router.get("/workspaces/{wid}/resources/{rid}")
    def get_resource(wid: str, rid: str, actor: str = Depends(owner)) -> dict[str, Any]:
        return resource_state(resource(wid, rid, actor))

    @router.delete("/workspaces/{wid}/resources/{rid}")
    def remove_resource(wid: str, rid: str, actor: str = Depends(owner)) -> dict[str, bool]:
        resource(wid, rid, actor)
        services.native_repository.remove_resource(actor, wid, rid)
        return {"ok": True}

    @router.post("/native/uploads")
    async def upload(
        request: Request,
        response: Response,
        file: Annotated[UploadFile, File()],
        actor: str = Depends(owner),
    ) -> Any:
        result = await services.upload_book(file, request, response)
        if not services.account_repository.repo.bind_upload(actor, result.book_id):
            raise HTTPException(409, "资料归属发生冲突")
        return result

    @router.post("/native/books/{book_id}/prepare")
    def prepare(book_id: str, actor: str = Depends(owner)) -> dict[str, Any]:
        accessible_book(actor, book_id)
        stored = services.job_repository.get_book(book_id)
        if stored is None:
            raise HTTPException(404, "教材不存在")
        structure = services.job_repository.get_structure(book_id)
        if structure is None:
            services.generate_book_structure(book_id, require_chapter_reconstructor())
            structure = services.job_repository.get_structure(book_id)
        if book_id in services.settings.published_book_ids:
            # Existing published indexes need not be rebuilt or moved into upload storage.
            from .qa_dependency import require_qa_service

            require_qa_service(book_id)
            return {"structure": structure, "rag_ready": True}
        try:
            prepare_upload_index(stored.file_path.parent)
        except (OSError, ValueError, QAConfigurationError, RAGIndexError) as error:
            raise HTTPException(
                503, "目录已保存，检索索引尚未就绪，请检查服务配置后重试"
            ) from error
        return {"structure": structure, "rag_ready": True}

    @router.post("/native/books/{book_id}/cancel")
    def cancel(book_id: str, actor: str = Depends(owner)) -> dict[str, bool]:
        accessible_book(actor, book_id)
        return {"ok": bool(services.job_repository.cancel(book_id))}

    @router.get("/native/books/{book_id}/readiness")
    def readiness(book_id: str, actor: str = Depends(owner)) -> dict[str, Any]:
        accessible_book(actor, book_id)
        stored = services.job_repository.get_book(book_id)
        if stored is None:
            raise HTTPException(404, "教材不存在")
        return {
            "rag_ready": published_upload_index(stored.file_path.parent) is not None,
            "structure_ready": services.job_repository.get_structure(book_id) is not None,
        }

    @router.get("/books/{book_id}/source")
    def source(book_id: str, actor: str = Depends(owner)) -> FileResponse:
        accessible_book(actor, book_id)
        stored = services.job_repository.get_book(book_id)
        if stored is None or not stored.file_path.is_file():
            raise HTTPException(404, "原 PDF 暂不可用")
        return FileResponse(
            stored.file_path,
            media_type="application/pdf",
            filename=stored.original_name,
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.get("/books/{book_id}/pages/{page}")
    def page_info(book_id: str, page: int, actor: str = Depends(owner)) -> dict[str, Any]:
        accessible_book(actor, book_id)
        stored = services.job_repository.get_book(book_id)
        if stored is None or page < 1:
            raise HTTPException(404, "原文页不存在")
        path = stored.file_path.parent / "ocr" / "normalized" / "pages.jsonl"
        if path.is_file():
            with path.open() as stream:
                for line in stream:
                    value = json.loads(line)
                    if value["page_number"] == page:
                        return {
                            "book_id": book_id,
                            "pdf_page": page,
                            "printed_page": None,
                            "text": value.get("cleaned_text") or value.get("raw_text", ""),
                            "blocks": value.get("blocks", []),
                        }
        raise HTTPException(404, "原文页不存在")

    @router.put("/workspaces/{wid}/resources/{rid}/chapters")
    def save_chapters(
        wid: str, rid: str, body: StructureInput, actor: str = Depends(owner)
    ) -> dict[str, Any]:
        row = resource(wid, rid, actor)
        original = services.job_repository.get_structure(row["book_id"])
        if original is None:
            raise HTTPException(409, "教材目录尚未准备完成")
        originals = {c.chapter_id: c for c in original.chapters}
        seen = set()
        chapters = []
        for index, value in enumerate(body.chapters):
            if value.chapter_id in seen or value.chapter_id not in originals:
                raise HTTPException(422, "目录章节标识无效或重复")
            if not 1 <= value.start_page <= value.end_page <= original.source_page_count:
                raise HTTPException(422, "章节页码超出教材范围")
            seen.add(value.chapter_id)
            before = originals[value.chapter_id]
            updated = before.model_copy(
                update={
                    "title": value.title,
                    "order": index + 1,
                    "start_page": value.start_page,
                    "end_page": value.end_page,
                }
            )
            if (before.start_page, before.end_page) != (value.start_page, value.end_page):
                stored = services.job_repository.get_book(row["book_id"])
                if stored is None:
                    raise HTTPException(404, "教材不存在")
                pages = load_normalized_pages(
                    stored.file_path.parent / "ocr" / "normalized" / "pages.jsonl"
                )
                updated = updated.model_copy(
                    update={
                        "source_block_ids": [
                            b.block_id
                            for p in pages
                            if value.start_page <= p.page_number <= value.end_page
                            for b in p.blocks
                        ]
                    }
                )
                # Revalidate summaries and knowledge points against the changed range.
                updated = require_chapter_reconstructor()._summarize(updated, pages)
            chapters.append(updated)
        structure = original.model_copy(update={"chapters": chapters})
        try:
            services.native_repository.save_structure(actor, wid, rid, body.revision, structure)
        except NativeConflict as error:
            raise HTTPException(409, str(error)) from error
        return resource_state(resource(wid, rid, actor))

    @router.post("/workspaces/{wid}/resources/{rid}/preview-session")
    def preview_session(wid: str, rid: str, actor: str = Depends(owner)) -> Any:
        row = resource(wid, rid, actor)
        if row["session_id"]:
            existing = services.assessment_repository.get_session(row["session_id"])
            if existing is None or existing.purpose != "preview":
                raise HTTPException(409, "已有诊断与学习记录，不能改为课程预览；请创建独立课程")
            return services.get_interview(existing.session_id)
        structure = effective_structure(row)
        if structure is None:
            raise HTTPException(409, "请先完成教材解析并确认目录")
        job = services.job_repository.get_job(row["book_id"])
        if job is None or job.status != "ocr_ready":
            raise HTTPException(409, "教材原文尚未准备完成，暂时不能预览课程")
        session = create_preview_session(
            user_id=actor, book_id=row["book_id"], structure=structure,
            learning_design=design.config(actor, wid),
        )
        services.assessment_repository.create_session(session)
        services.account_repository.repo.claim_session(actor, session.session_id)
        try:
            sid = services.native_repository.attach_session(
                rid, row["revision"], session.session_id, structure, "[]",
            )
        except NativeConflict as error:
            raise HTTPException(409, str(error)) from error
        attached = services.assessment_repository.get_session(sid)
        if attached is None or attached.purpose != "preview":
            raise HTTPException(409, "已有诊断与学习记录，不能改为课程预览；请创建独立课程")
        return services.get_interview(sid)

    @router.post("/workspaces/{wid}/resources/{rid}/session")
    def start_session(wid: str, rid: str, actor: str = Depends(owner), retry: bool = False,
                      retry_request_id: str | None = None) -> Any:
        row = resource(wid, rid, actor)
        if row["session_id"]:
            return services.get_interview(row["session_id"])
        if retry and (retry_request_id is None or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", retry_request_id)):
            raise HTTPException(422, "请通过明确的重试操作继续诊断")
        structure = effective_structure(row)
        if structure is None:
            raise HTTPException(409, "请先完成教材解析并确认目录")
        fingerprint = services.diagnostic_fingerprint(structure)
        bank_key = diagnostic_bank_key(f"native:{rid}:{row['revision']}")
        original = services.job_repository.get_structure(row["book_id"])
        if original is not None and structure_fingerprint(original) == structure_fingerprint(structure):
            items = services.assessment_repository.get_bank(
                diagnostic_bank_key(row["book_id"]), expected_fingerprint=fingerprint
            )
        else:
            items = None
        items = items or services.assessment_repository.get_bank(
            bank_key, expected_fingerprint=fingerprint
        )
        if not items:
            if services.item_generator is None:
                raise HTTPException(503, "诊断服务尚未配置，目录已保存，可稍后继续")
            if retry and services.assessment_repository.has_pending_diagnostic_submission(row["book_id"]):
                services.assessment_repository.grant_diagnostic_recovery(
                    row["book_id"], f"native:{rid}:{retry_request_id}")
            try:
                items = services.item_generator.generate(structure,
                    chapter_cache=RepositoryDiagnosticChapterCache(services.assessment_repository),
                    cache_scope=row["book_id"])
            except DiagnosticChapterSubmissionBlocked as error:
                raise HTTPException(409, "上次诊断提交结果待确认，已审核章节已保留，请确认后点击重试诊断",
                                    headers={"X-CloudPath-Error-Code": "diagnostic_submission_uncertain"}) from error
            except DiagnosticChapterCacheError as error:
                raise HTTPException(409, "诊断章节记录需核验，已审核内容已保留，请检查服务后继续") from error
            except DiagnosticGenerationError as error:
                raise HTTPException(502, "诊断题未通过教材证据校验，已审核章节已保留，请重试诊断",
                                    headers={"X-CloudPath-Error-Code": "diagnostic_generation_rejected"}) from error
            except LLMCooldownError as error:
                raise HTTPException(503, "诊断服务暂时繁忙，本次未提交，请稍后重试诊断",
                                    headers={"X-CloudPath-Error-Code": "diagnostic_generation_not_admitted"}) from error
            except LLMError as error:
                raise HTTPException(409, "上次诊断提交结果待确认，已审核章节已保留，请确认后点击重试诊断",
                                    headers={"X-CloudPath-Error-Code": "diagnostic_submission_uncertain"}) from error
            services.assessment_repository.save_bank(
                book_id=bank_key, structure_fingerprint=fingerprint, items=items
            )
        session = services.orchestrator.start(
            user_id=actor,
            book_id=row["book_id"],
            book_title=structure.title,
            chapter_titles=[c.title for c in structure.chapters],
            items=items,
            chapter_options=[{"id": c.chapter_id, "label": c.title} for c in structure.chapters],
            learning_design=design.config(actor, wid),
        )
        services.assessment_repository.create_session(session)
        services.account_repository.repo.claim_session(actor, session.session_id)
        try:
            sid = services.native_repository.attach_session(
                rid,
                row["revision"],
                session.session_id,
                structure,
                TypeAdapter(list[DiagnosticItem]).dump_json(items).decode(),
            )
        except NativeConflict as error:
            raise HTTPException(409, str(error)) from error
        return services.get_interview(sid)

    @router.put("/workspaces/{wid}/resources/{rid}/progress")
    def progress(
        wid: str, rid: str, body: ProgressInput, actor: str = Depends(owner)
    ) -> dict[str, Any]:
        row = resource(wid, rid, actor)
        structure = effective_structure(row)
        if structure is None:
            raise HTTPException(409, "教材尚未准备完成")
        chapter_ids = {c.chapter_id for c in structure.chapters}
        if body.page > structure.source_page_count or (
            body.chapter_id and body.chapter_id not in chapter_ids
        ):
            raise HTTPException(422, "学习位置超出当前课程范围")
        if set(body.completed_chapters) - chapter_ids:
            raise HTTPException(422, "完成记录含有不存在的章节")
        courses = (
            services.assessment_repository.courses_for_session(row["session_id"])
            if row["session_id"]
            else []
        )
        lesson_ids = {c.course_id for c in courses}
        if row["book_id"] == curated.BOOK_ID:
            lesson_ids = {lesson["lesson_id"] for lesson in curated.snapshot()["source"]["lessons"]}
        if body.lesson_id and body.lesson_id not in lesson_ids:
            raise HTTPException(422, "课时不属于当前课程资料")
        try:
            return dict(
                services.native_repository.save_progress(
                    actor,
                    wid,
                    rid,
                    body.revision,
                    body.mutation_id,
                    body.model_dump(exclude={"revision", "mutation_id"}),
                )
            )
        except NativeConflict as error:
            raise HTTPException(409, str(error)) from error

    return router
