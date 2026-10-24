from __future__ import annotations

import faulthandler
import hashlib
import json
import logging
import math
import os
import shutil
import signal
import sys
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import TypeAdapter

from ..accounts import COOKIE, Accounts
from ..assessment.engine import AdaptiveAssessmentEngine, score_choice
from ..assessment.interview import InterviewOrchestrator
from ..assessment.item_generation import (
    DiagnosticGenerationError,
    DiagnosticItemGenerator,
    diagnostic_bank_fingerprint,
    diagnostic_bank_key,
)
from ..assessment.models import (
    AssessmentResponse,
    DiagnosticItem,
    EvidenceKind,
    InterviewPhase,
    InterviewSession,
    ResponseType,
    ScoredEvidence,
)
from ..assessment.records import learning_records
from ..assessment.repository import SessionConflictError, SQLiteAssessmentRepository
from ..assessment.scoring import OpenAnswerScorer
from ..community import CommunityRepository
from ..config import get_settings
from ..database import close_pools, healthcheck
from ..ingestion.chaptering import (
    ChapterReconstructor,
    load_normalized_pages,
)
from ..ingestion.jobs import OCRWorker, SQLiteOCRJobRepository, SubprocessOCRRunner
from ..ingestion.models import BookStructure, ChapterDraft
from ..ingestion.ocr_job import sha256_file
from ..ingestion.pipeline import new_book_id
from ..ingestion.preparation import PreparationQueue
from ..learning_design import NativeLearningDesign, resolved_profile
from ..learning_preferences import resolve_learning_design
from ..llm.book_parallel import ordered_book_map
from ..llm.client import (
    LLMConfig,
    LLMCooldownError,
    LLMError,
    LLMRefusalError,
    LLMTimeoutError,
    OpenAICompatibleClient,
)
from ..native import NativeRepository
from ..personalization.flashcard_quality import FlashcardQualityError, FlashcardQualityGate
from ..personalization.generator import (
    ChapterCourseCompiler,
    chapter_fingerprint,
    profile_fingerprint,
)
from ..personalization.models import ChapterLearningBundle, PublicChapterLearningBundle
from ..personalization.policy import PersonalizationPolicy
from ..personalization.review import rating_score, review_state_for_point, schedule_review
from ..personalization.teaching_content import (
    TEACHING_CONTENT_VERSION,
    TeachingContentError,
    TeachingContentGenerator,
    TeachingServiceUnavailableError,
)
from ..personalization.teaching_evidence import TeachingEvidence, build_teaching_evidence
from ..personalization.teaching_scope import (
    TEACHING_SCOPE_VERSION,
    TeachingScopeError,
    TeachingScopePlanner,
    validate_scope_plan,
)
from ..rag.grounded_qa import GroundedAnswerValidationError
from ..rag.index import RAGIndexError
from ..rag.service import QABusyError, TextbookQAResult, TextbookQAService
from ..worker_presence import WorkerPresence, readiness
from .account_guard import learning_guard
from .chapter_dependency import require_chapter_reconstructor
from .community_routes import community_router
from .course_jobs import (
    CourseAdmissionDeferred,
    CourseJobFailure,
    CourseJobInput,
    CourseJobQueue,
    CourseRetryLimit,
)
from .native_routes import native_router
from .qa_dependency import (
    build_qa_service,
    prepare_uploaded_book_index,
    qa_service_ready,
    require_qa_service,
)
from .runtime_routes import RuntimeSnapshot, runtime_router
from .schemas import (
    BookCatalogItem,
    BookQuestionRequest,
    BookStatusResponse,
    CourseActivityResponse,
    CoursePracticeRequest,
    CourseResponse,
    DiagnosticAnswerResponse,
    DiagnosticBankResponse,
    DiagnosticResponseRequest,
    FlashcardReviewRequest,
    InterviewResponse,
    ProfileAnswerRequest,
    ProfileConfirmationRequest,
    StartInterviewRequest,
    UploadResponse,
)
from .store import BookRecord, MemoryStore
from .teaching_audit import PrivateTeachingAudit

logger = logging.getLogger(__name__)

if hasattr(signal, "SIGUSR2"):
    faulthandler.register(signal.SIGUSR2, all_threads=True)

settings = get_settings()
healthcheck()
store = MemoryStore()
job_repository = SQLiteOCRJobRepository(settings.data_dir / "state" / "ocr_jobs.sqlite3")
assessment_repository = SQLiteAssessmentRepository(
    settings.data_dir / "state" / "assessments.sqlite3"
)
native_repository = NativeRepository(settings.data_dir / "state" / "native.sqlite3")
ocr_worker = OCRWorker(
    repository=job_repository,
    runner=SubprocessOCRRunner(backend=settings.ocr_backend),
    lease_seconds=settings.ocr_worker_lease_seconds,
    retry_delay_seconds=settings.ocr_retry_delay_seconds,
    poll_interval=settings.ocr_worker_poll_seconds,
)
engine = AdaptiveAssessmentEngine(
    min_items=settings.assessment_min_items,
    max_items=settings.assessment_max_items,
    stop_uncertainty=settings.assessment_stop_uncertainty,
)
assessment_llm = None
if settings.text_api_key:
    assessment_llm = OpenAICompatibleClient(
        LLMConfig(
            base_url=settings.text_base_url,
            api_key=settings.text_api_key,
            model=settings.text_model,
            proxy_url=settings.llm_https_proxy,
            thinking_enabled=settings.text_thinking_enabled,
            reasoning_effort=settings.text_reasoning_effort,
        )
    )
open_answer_scorer = None
item_generator = None
if assessment_llm is not None:
    open_answer_scorer = OpenAnswerScorer(
        assessment_llm,
        minimum_confidence=settings.open_answer_min_confidence,
    )
orchestrator = InterviewOrchestrator(engine, open_answer_scorer)
personalization_policy = PersonalizationPolicy()
course_compiler = ChapterCourseCompiler()
flashcard_quality = FlashcardQualityGate(
    OpenAICompatibleClient(replace(assessment_llm.config, timeout_seconds=120))
    if assessment_llm is not None
    else None,
    settings.data_dir / "state" / "flashcard_quality.sqlite3",
    f"{settings.text_base_url}|{settings.text_model}",
)
teaching_client = (
    OpenAICompatibleClient(replace(assessment_llm.config, timeout_seconds=120))
    if assessment_llm is not None else None
)
def _create_teaching_review_client(configuration, client_type=OpenAICompatibleClient):
    if not configuration.teaching_review_configured:
        return None
    try:
        return client_type(LLMConfig(
            base_url=configuration.pucoding_base_url, api_key=configuration.pucoding_api_key,
            model=configuration.teaching_review_model, proxy_url=configuration.llm_https_proxy,
            timeout_seconds=120, reasoning_effort="low"))
    except Exception:
        # An unavailable independent reviewer must not silently reuse generation.
        logger.warning("independent teaching reviewer could not be initialized")
        return None


def _create_diagnostic_generator(configuration, writer, reviewer):
    if writer is None or reviewer is None:
        return None
    try:
        return DiagnosticItemGenerator(
            writer, review_client=reviewer,
            allow_grounded_answer_rewrite=configuration.diagnostic_allow_grounded_answer_rewrite,
        )
    except ValueError:
        logger.warning("independent diagnostic reviewer could not be initialized")
        return None


def diagnostic_fingerprint(structure: BookStructure) -> str:
    return diagnostic_bank_fingerprint(
        structure,
        writer_revision=json.dumps({
            "provider": settings.llm_provider, "base_url": settings.text_base_url,
            "model": settings.text_model, "thinking": settings.text_thinking_enabled,
            "reasoning_effort": settings.text_reasoning_effort,
        }, sort_keys=True),
        review_revision=settings.teaching_review_revision,
        allow_grounded_answer_rewrite=settings.diagnostic_allow_grounded_answer_rewrite,
    )


teaching_review_client = _create_teaching_review_client(settings)
item_generator = _create_diagnostic_generator(settings, assessment_llm, teaching_review_client)
teaching_audit = PrivateTeachingAudit(settings.data_dir / "private-audits",
    context={"provider": settings.llm_provider, "model": settings.text_model,
             "teaching_version": TEACHING_CONTENT_VERSION},
    secrets=tuple(str(value) for value in (
        settings.text_api_key, settings.deepseek_api_key, settings.pucoding_api_key,
        getattr(settings, "minimax_api_key", "")) if value))
teaching_generator = TeachingContentGenerator(
    teaching_client, scope_planner=TeachingScopePlanner(teaching_client),
    audit_callback=teaching_audit, review_client=teaching_review_client,
    review_identity=settings.teaching_review_identity,
)
_course_generation_locks = [threading.RLock() for _ in range(64)]


def _current_scope_plan(bundle, evidence) -> bool:
    if bundle.teaching_scope_version != TEACHING_SCOPE_VERSION:
        return False
    try:
        validate_scope_plan(bundle.teaching_scope_plan,
                            TeachingContentGenerator._inputs(bundle, evidence), evidence)
    except (TeachingScopeError, TeachingContentError):
        return False
    return True


def _reviewed_course(bundle: ChapterLearningBundle) -> ChapterLearningBundle:
    try:
        reviewed = flashcard_quality.ensure(bundle)
    except FlashcardQualityError as error:
        raise HTTPException(status_code=503, detail="学习内容暂未整理完成，请稍后重试。") from error
    if reviewed != bundle:
        assessment_repository.update_reviewed_course(reviewed)
    return reviewed


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    from ..studio import get_studio

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    recovered = job_repository.recover_expired()
    if recovered:
        logger.warning("recovered interrupted OCR jobs", extra={"count": recovered})
    rag_configured = all(
        (
            settings.rag_index_dir,
            settings.rag_embedding_model_path,
            settings.rag_reranker_model_path,
            settings.text_api_key,
        )
    )
    if rag_configured:
        build_qa_service().warmup()
    if settings.ocr_worker_enabled:
        ocr_worker.start()
    if settings.studio_worker_enabled:
        get_studio(settings.data_dir).start()
    preparation_queue.start()
    course_job_queue.start()
    try:
        yield
    finally:
        course_job_queue.stop()
        preparation_queue.stop()
        if settings.studio_worker_enabled:
            get_studio(settings.data_dir).stop()
        if settings.ocr_worker_enabled:
            ocr_worker.stop()
        close_pools()


app = FastAPI(
    title="云径 CloudPath API",
    version="0.1.0",
    description="高精度文档重建、主动学习者诊断与个性化章节课程服务",
    lifespan=lifespan,
)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
def service_index() -> dict[str, str]:
    return {
        "service": "云径 CloudPath API",
        "client": "SwiftUI iOS / iPadOS",
        "docs": "/docs",
        "health": "/api/health",
    }


@app.get("/api/health")
def health() -> dict[str, object]:
    from ..studio import get_studio

    studio_worker_alive = get_studio(settings.data_dir).is_alive()
    ocr_worker_alive = ocr_worker.is_alive()
    external = WorkerPresence(settings.data_dir).active()
    return {
        **readiness(inline_ocr_enabled=settings.ocr_worker_enabled,
                    inline_ocr_alive=ocr_worker_alive, inline_studio_alive=studio_worker_alive,
                    external=external),
        "service": "adaptive-book-learning",
        "version": app.version,
        "release_commit": os.getenv("CLOUDPATH_RELEASE_SHA", ""),
    }


@app.get("/api/rag/health")
def rag_health() -> dict[str, object]:
    configured = all(
        (
            settings.rag_index_dir,
            settings.rag_embedding_model_path,
            settings.rag_reranker_model_path,
            settings.text_api_key,
        )
    )
    ready = bool(configured and qa_service_ready())
    return {
        "ok": ready,
        "configured": configured,
        "ready": ready,
        "book_id": settings.rag_book_id,
        "answer_model": settings.text_model,
        "answer_provider": settings.llm_provider,
        "evidence_planner": settings.rag_use_evidence_planner,
        "semantic_review": settings.rag_use_semantic_review,
        "max_output_tokens": settings.rag_max_output_tokens,
        "generation_timeout_seconds": settings.rag_generation_timeout_seconds,
    }


@app.post("/api/books", response_model=UploadResponse)
async def upload_book(file: Annotated[UploadFile, File()], request: Request, response: Response) -> UploadResponse:
    suffix = Path(file.filename or "book.pdf").suffix.lower()
    if suffix != ".pdf":
        raise HTTPException(
            status_code=415, detail="目前仅支持上传 PDF 文件。"
        )
    book_id = new_book_id()
    target_dir = settings.data_dir / "books" / book_id
    target_dir.mkdir(parents=True, exist_ok=False)
    target = target_dir / "source.pdf"
    owner = account_repository.request_owner(request)
    if request.cookies.get("zhiwo_account") and owner is None:
        shutil.rmtree(target_dir)
        raise HTTPException(401, "登录已过期，请重新登录")
    if owner is None:
        owner, token, _ = account_repository.repo.visitor(request.cookies.get("zhiwo_visitor"), [])
        response.set_cookie("zhiwo_visitor", token, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https", max_age=31536000)
    try:
        size = 0
        with target.open("wb") as output:
            while data := await file.read(1024 * 1024):
                if size == 0 and b"%PDF-" not in data[:1024]:
                    raise HTTPException(415, "文件内容不是有效 PDF")
                size += len(data)
                if size > 100 * 1024 * 1024:
                    raise HTTPException(413, "PDF 不能超过 100 MB")
                output.write(data)
        if not size:
            raise HTTPException(422, "不能上传空文件")
    except BaseException:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    store.books[book_id] = BookRecord(
        book_id=book_id,
        original_name=file.filename or "book.pdf",
        file_path=target,
    )
    job_repository.register_book(
        book_id=book_id,
        original_name=file.filename or "book.pdf",
        file_path=target,
        source_sha256=sha256_file(target),
    )
    account_repository.repo.bind_upload(owner, book_id)
    return UploadResponse(
        book_id=book_id,
        filename=file.filename or "book.pdf",
        status="uploaded",
        next=f"POST /api/books/{book_id}/process",
    )


@app.get("/api/books", response_model=list[BookCatalogItem])
def list_published_books() -> list[BookCatalogItem]:
    result: list[BookCatalogItem] = []
    for book_id in settings.published_book_ids:
        stored = job_repository.get_book(book_id)
        structure = job_repository.get_structure(book_id)
        if stored is None or structure is None:
            continue
        result.append(
            BookCatalogItem(
                book_id=book_id,
                title=structure.title.removesuffix(".pdf"),
                status=stored.status,
                page_count=structure.source_page_count,
                chapter_count=len(structure.chapters),
                summary=structure.summary,
                diagnostics_ready=bool(_items_for_book(book_id)),
                cover_url=(
                    f"/api/books/{book_id}/cover"
                    if (settings.data_dir / "covers" / f"{book_id}.jpg").is_file()
                    else None
                ),
            )
        )
    return result


@app.get("/api/books/{book_id}/cover")
def book_cover(book_id: str) -> FileResponse:
    if book_id not in settings.published_book_ids:
        raise HTTPException(status_code=404, detail="未找到教材封面")
    path = settings.data_dir / "covers" / f"{book_id}.jpg"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="封面尚未生成")
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"}
    )


@app.get("/api/interviews/{session_id}/learning-records")
def get_learning_records(session_id: str) -> dict[str, object]:
    session = _session_or_404(session_id)
    return learning_records(
        session,
        _items_for_session(session),
        assessment_repository.courses_for_session(session_id),
    )


@app.post("/api/books/{book_id}/process", response_model=BookStatusResponse)
def process_book(book_id: str) -> BookStatusResponse:
    book = _book_or_404(book_id)
    job = job_repository.enqueue(
        book_id=book_id,
        output_dir=settings.data_dir / "books" / book_id / "ocr",
        max_attempts=settings.ocr_max_attempts,
    )
    persisted = job_repository.get_book(book_id)
    if persisted is not None and persisted.status == "structured":
        _sync_book_from_job(book, persisted.status, persisted.progress, persisted.current_step)
    else:
        _sync_book_from_job(book, job.status, job.progress, job.current_step)
    return _status(book)


@app.post("/api/books/{book_id}/process/retry", response_model=BookStatusResponse)
def retry_process_book(book_id: str) -> BookStatusResponse:
    book = _book_or_404(book_id)
    current = job_repository.get_job(book_id)
    if current is None:
        raise HTTPException(status_code=409, detail="该书尚未开始处理。")
    if current.status not in {"failed", "ocr_review_required", "cancelled"}:
        raise HTTPException(status_code=409, detail="当前状态不支持重新处理。")
    job = job_repository.enqueue(
        book_id=book_id,
        output_dir=settings.data_dir / "books" / book_id / "ocr",
        max_attempts=settings.ocr_max_attempts,
        force_retry=True,
    )
    _sync_book_from_job(book, job.status, job.progress, job.current_step)
    return _status(book)


@app.post("/api/books/{book_id}/qa", response_model=TextbookQAResult)
def answer_book_question(
    book_id: str,
    request: BookQuestionRequest,
    qa_service: Annotated[TextbookQAService, Depends(require_qa_service)],
    http_request: Request,
) -> TextbookQAResult:
    if book_id != qa_service.book_id:
        raise HTTPException(status_code=404, detail="未找到该书的检索索引")
    actor = account_repository.request_owner(http_request)
    config = None
    cache_scope = actor or ""
    wid, rid = request.workspace_id, request.resource_id
    if bool(wid) != bool(rid):
        raise HTTPException(422, "请同时提供课程和资料上下文")
    if wid and rid:
        if actor is None:
            raise HTTPException(401, "请先登录学习账号")
        try:
            row = native_repository.resource(actor, wid, rid)
        except KeyError as error:
            raise HTTPException(404, "课程资料不存在") from error
        if row["book_id"] != book_id:
            raise HTTPException(422, "资料与教材不匹配")
    elif actor:
        matches = [resource for workspace in native_repository.workspaces(actor)
                   for resource in native_repository.resources(actor, workspace["id"])
                   if resource["book_id"] == book_id]
        # A legacy book-only request is ambiguous when a book is used in several
        # courses. Use the account default rather than a potentially stale selection.
        if len(matches) == 1:
            wid, rid = matches[0]["workspace_id"], matches[0]["id"]
    if actor and wid and rid:
        row = native_repository.resource(actor, wid, rid)
        session = assessment_repository.get_session(row["session_id"]) if row["session_id"] else None
        config = NativeLearningDesign(sys.modules[__name__]).config(actor, wid, session.profile if session else None)
        cache_scope = f"{actor}:{wid}:{rid}"
    elif actor:
        account_preferences = native_repository.learner_preferences(actor)
        if account_preferences:
            config = resolve_learning_design(account_preferences)
    context: dict[str, Any] = {"history": [turn.model_dump() for turn in request.history]}
    if request.chapter_id:
        from .. import native_catalog

        structure = None
        if actor and wid and rid:
            row = native_repository.resource(actor, wid, rid)
            if row["structure_json"]:
                structure = BookStructure.model_validate_json(row["structure_json"])
        structure = structure or (native_catalog.structure() if book_id == native_catalog.BOOK_ID
                                  else job_repository.get_structure(book_id))
        chapter = next((c for c in structure.chapters if c.chapter_id == request.chapter_id), None) if structure else None
        if chapter is None:
            raise HTTPException(422, "当前章节不属于这份课程资料")
        context["chapter"] = {"id": chapter.chapter_id, "title": chapter.title,
                              "start_page": chapter.start_page, "end_page": chapter.end_page}
    try:
        extra = {"conversation_context": context} if request.history or request.chapter_id else {}
        if config is not None or cache_scope or extra:
            return qa_service.answer(request.question, teaching_context=config.qa_preferences() if config else None, cache_scope=cache_scope, **extra)
        return qa_service.answer(request.question)
    except QABusyError as error:
        raise HTTPException(
            status_code=503, detail="当前提问较多，请稍后重试", headers={"Retry-After": "5"}
        ) from error
    except LLMRefusalError as error:
        raise HTTPException(
            status_code=422, detail="这个问题暂时无法回答，请换一种表述或询问教材中的具体内容。"
        ) from error
    except LLMTimeoutError as error:
        logger.warning("grounded QA model timed out", exc_info=error)
        raise HTTPException(
            status_code=504, detail="回答服务响应较慢，请稍后重试；你的问题已保留。",
            headers={f"X-QA-{stage}-Ms": str(value) for stage, value in getattr(error, "qa_timings", {}).items()},
        ) from error
    except LLMError as error:
        logger.warning("grounded QA model call failed", exc_info=error)
        raise HTTPException(status_code=502, detail="回答服务暂时不可用，请稍后重试。") from error
    except GroundedAnswerValidationError as error:
        logger.warning("grounded QA evidence validation failed", exc_info=error)
        raise HTTPException(status_code=502, detail="回答未通过教材证据校验") from error
    except RAGIndexError as error:
        logger.error("persistent RAG service failed", exc_info=error)
        raise HTTPException(status_code=503, detail="教材检索服务暂时不可用") from error


@app.get("/api/books/{book_id}/status", response_model=BookStatusResponse)
def book_status(book_id: str) -> BookStatusResponse:
    return _status(_book_or_404(book_id))


@app.post("/api/books/{book_id}/structure", response_model=BookStructure)
def generate_book_structure(
    book_id: str,
    reconstructor: Annotated[ChapterReconstructor, Depends(require_chapter_reconstructor)],
) -> BookStructure:
    book = _book_or_404(book_id)
    existing = job_repository.get_structure(book_id)
    if existing is not None:
        return existing
    job = job_repository.get_job(book_id)
    if job is None or job.status != "ocr_ready":
        raise HTTPException(status_code=409, detail="书籍内容尚未准备完成。")
    pages_path = job.output_dir / "normalized" / "pages.jsonl"
    try:
        pages = load_normalized_pages(pages_path)
        # Do not announce an uploaded book as ready while its QA path is missing.
        if settings.rag_index_dir is not None:
            prepare_uploaded_book_index(book_id)
        structure = reconstructor.reconstruct_book(pages, Path(book.original_name).stem)
        job_repository.save_structure(book_id, structure)
    except (OSError, ValueError, LLMError) as error:
        logger.warning("chapter reconstruction failed", exc_info=error)
        raise HTTPException(status_code=502, detail="章节结构生成未通过证据校验") from error
    _sync_book_from_job(book, "structured", 1, "章节结构与摘要已生成")
    return structure


def _prepare_index(book_id: str) -> None:
    # A text-only/local deployment can still prepare chapter learning content.
    # Partial RAG configuration fails here instead of silently promising QA.
    if any((settings.rag_index_dir, settings.rag_embedding_model_path, settings.rag_reranker_model_path)):
        prepare_uploaded_book_index(book_id)


def _prepare_structure(book_id: str) -> None:
    # Recovery after diagnostics failure should reuse the verified structure.
    if job_repository.get_structure(book_id) is None:
        generate_book_structure(book_id, require_chapter_reconstructor())


def _prepare_diagnostics(book_id: str) -> None:
    generate_diagnostic_bank(book_id)


preparation_queue = PreparationQueue(settings.data_dir / "state" / "preparation.sqlite3", [
    ("indexing", _prepare_index),
    ("structuring", _prepare_structure),
    ("diagnostics", _prepare_diagnostics),
])


def _require_upload_owner(book_id: str, request: Request) -> None:
    from urllib.parse import urlsplit
    if request.method not in {"GET", "HEAD"} and request.headers.get("origin"):
        if urlsplit(request.headers["origin"]).netloc != request.headers.get("host"):
            raise HTTPException(403, "请在 App 内执行此操作")
    owner = account_repository.request_owner(request)
    if not owner and request.cookies.get(COOKIE):
        raise HTTPException(401, "登录已过期，请重新登录")
    if not owner or not (
        account_repository.repo.owns_upload(owner, book_id)
        or account_repository.repo.owns(owner, book_id)
    ):
        raise HTTPException(403, "请先上传这本教材或将它加入自己的书架")


@app.post("/api/books/{book_id}/prepare")
def prepare_book(book_id: str, request: Request, response: Response, retry: bool = False) -> dict:
    _require_upload_owner(book_id, request)
    response.headers["Cache-Control"] = "private, no-store"
    job = job_repository.get_job(book_id)
    if job is None or job.status != "ocr_ready":
        raise HTTPException(409, "教材仍在读取中，请稍后再试")
    existing = preparation_queue.get(book_id)
    structure = job_repository.get_structure(book_id)
    bank = assessment_repository.get_bank(
        diagnostic_bank_key(book_id), expected_fingerprint=diagnostic_fingerprint(structure)
    ) if structure is not None else None
    ready = bool(bank) and all(item.response_type == ResponseType.SINGLE_CHOICE for item in bank)
    return preparation_queue.enqueue(
        book_id, retry=retry,
        refresh=bool(existing and existing['status'] == 'succeeded' and not ready),
    )


@app.get("/api/books/{book_id}/prepare")
def preparation_status(book_id: str, request: Request, response: Response) -> dict:
    _require_upload_owner(book_id, request)
    response.headers["Cache-Control"] = "private, no-store"
    value = preparation_queue.get(book_id)
    if value is None:
        raise HTTPException(404, "尚未开始整理教材")
    return value


@app.get("/api/books/{book_id}/structure", response_model=BookStructure)
def get_book_structure(book_id: str) -> BookStructure:
    _book_or_404(book_id)
    structure = job_repository.get_structure(book_id)
    if structure is None:
        raise HTTPException(status_code=404, detail="该书尚未生成章节结构")
    return structure


@app.post("/api/books/{book_id}/diagnostics", response_model=DiagnosticBankResponse)
def generate_diagnostic_bank(book_id: str) -> DiagnosticBankResponse:
    _book_or_404(book_id)
    structure = job_repository.get_structure(book_id)
    if structure is None:
        raise HTTPException(status_code=409, detail="书籍尚未生成可核验章节结构")
    fingerprint = diagnostic_fingerprint(structure)
    existing = assessment_repository.get_bank(diagnostic_bank_key(book_id), expected_fingerprint=fingerprint)
    if existing is not None and all(
        item.response_type == ResponseType.SINGLE_CHOICE for item in existing
    ):
        return _bank_response(book_id, existing)
    if item_generator is None:
        raise HTTPException(status_code=503, detail="诊断内容暂时不可用，请稍后重试。")
    try:
        items = item_generator.generate(structure)
        assessment_repository.save_bank(
            book_id=diagnostic_bank_key(book_id),
            structure_fingerprint=fingerprint,
            items=items,
        )
    except (DiagnosticGenerationError, LLMError) as error:
        logger.warning("diagnostic bank generation failed", exc_info=error)
        raise HTTPException(status_code=502, detail="诊断题未通过原文证据校验") from error
    return _bank_response(book_id, items)


@app.get("/api/books/{book_id}/diagnostics", response_model=DiagnosticBankResponse)
def get_diagnostic_bank(book_id: str) -> DiagnosticBankResponse:
    _book_or_404(book_id)
    items = _items_for_book(book_id)
    if not items:
        raise HTTPException(status_code=404, detail="该书尚未生成诊断题")
    return _bank_response(book_id, items)


@app.post("/api/interviews/start", response_model=InterviewResponse)
def start_interview(request: StartInterviewRequest, http_request: Request) -> InterviewResponse:
    _book_or_404(request.book_id)
    with account_repository.repo.connect() as db:
        bound = db.execute("SELECT owner FROM upload_owners WHERE book_id=?", (request.book_id,)).fetchone()
    actor = account_repository.request_owner(http_request)
    if bound and bound[0] != actor and not (actor and account_repository.repo.owns(actor, request.book_id)):
        raise HTTPException(403, "不能访问其他账号的教材")
    if request.book_id == "demo_book":
        chapter_titles = ["从局部到整体", "反馈与变化", "边界、延迟与涌现", "用系统方法解决问题"]
        book_title = "理解复杂系统"
        chapter_options = [
            {"id": f"ch_{index + 1}", "label": title} for index, title in enumerate(chapter_titles)
        ]
    else:
        structure = job_repository.get_structure(request.book_id)
        if structure is None:
            raise HTTPException(status_code=409, detail="书籍尚未完成重建")
        chapter_titles = [chapter.title for chapter in structure.chapters]
        book_title = structure.title
        chapter_options = [
            {"id": chapter.chapter_id, "label": chapter.title} for chapter in structure.chapters
        ]
    items = _items_for_book(request.book_id)
    if not items:
        raise HTTPException(status_code=409, detail="书籍诊断题尚未生成")
    session = orchestrator.start(
        user_id=request.user_id,
        book_id=request.book_id,
        book_title=book_title,
        chapter_titles=chapter_titles,
        items=items,
        chapter_options=chapter_options,
    )
    # Persist the exact option order before exposing a session. Protocol upgrades
    # must not alter the meaning of an answer/event in a historical interview.
    assessment_repository.save_bank(
        book_id=f"interview:{session.session_id}",
        structure_fingerprint="session-snapshot-v1", items=items,
    )
    assessment_repository.create_session(session)
    owner = account_repository.request_owner(http_request)
    if owner:
        account_repository.repo.claim_session(owner, session.session_id)
    assert session.pending_turn is not None
    return InterviewResponse(
        session_id=session.session_id,
        phase=session.phase,
        turn=session.pending_turn,
        profile=session.profile,
    )


@app.post("/api/interviews/{session_id}/profile", response_model=InterviewResponse)
def answer_profile(session_id: str, request: ProfileAnswerRequest) -> InterviewResponse:
    session = _session_or_404(session_id)
    session.profile = resolved_profile(sys.modules[__name__], session)
    try:
        turn = orchestrator.answer_profile_question(
            session,
            request.answer,
            chapter_options=session.chapter_options,
            selected_option_ids=request.selected_option_ids,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail="当前会话步骤不接受该回答") from error
    _save_session(session)
    return InterviewResponse(
        session_id=session.session_id,
        phase=session.phase,
        turn=turn,
        profile=session.profile,
    )


@app.get("/api/interviews/{session_id}", response_model=InterviewResponse)
def get_interview(session_id: str) -> InterviewResponse:
    session = _session_or_404(session_id)
    if session.pending_turn is None:
        raise HTTPException(status_code=409, detail="会话当前没有待处理步骤")
    return InterviewResponse(
        session_id=session.session_id,
        phase=session.phase,
        turn=session.pending_turn,
        profile=session.profile,
    )


@app.post("/api/interviews/{session_id}/next", response_model=InterviewResponse)
def next_diagnostic(session_id: str) -> InterviewResponse:
    session = _session_or_404(session_id)
    items = _items_for_session(session)
    try:
        turn = orchestrator.next_diagnostic(session, items)
    except ValueError as error:
        raise HTTPException(status_code=409, detail="当前会话还不能进入诊断题") from error
    _save_session(session)
    return InterviewResponse(
        session_id=session.session_id,
        phase=session.phase,
        turn=turn,
        profile=session.profile,
    )


@app.post("/api/interviews/{session_id}/respond", response_model=DiagnosticAnswerResponse)
def respond(session_id: str, request: DiagnosticResponseRequest) -> DiagnosticAnswerResponse:
    session = _session_or_404(session_id)
    items = _items_for_session(session)
    previous_turn_id = session.pending_turn.turn_id if session.pending_turn else None
    try:
        evidence = orchestrator.record_diagnostic(session, request, items)
    except ValueError as error:
        raise HTTPException(status_code=409, detail="回答与当前诊断题不匹配") from error
    if session.pending_turn and session.pending_turn.turn_id != previous_turn_id:
        next_turn = session.pending_turn
    else:
        next_turn = orchestrator.next_diagnostic(session, items)
    _save_session(session)
    return DiagnosticAnswerResponse(evidence=evidence, next_turn=next_turn, profile=session.profile)


@app.post("/api/interviews/{session_id}/confirm", response_model=InterviewResponse)
def confirm(session_id: str, request: ProfileConfirmationRequest) -> InterviewResponse:
    session = _session_or_404(session_id)
    try:
        turn = (
            orchestrator.confirm_profile(session)
            if request.confirmed
            else orchestrator.revise_profile(session)
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail="画像尚未进入确认阶段") from error
    _save_session(session)
    return InterviewResponse(
        session_id=session.session_id,
        phase=session.phase,
        turn=turn,
        profile=session.profile,
    )


@app.post("/api/interviews/{session_id}/courses/{chapter_id}", response_model=CourseResponse)
def compile_course(session_id: str, chapter_id: str) -> PublicChapterLearningBundle:
    digest = hashlib.sha256(f"{session_id}:{chapter_id}".encode()).digest()
    with _course_generation_locks[digest[0] % len(_course_generation_locks)]:
        return _compile_course(session_id, chapter_id)


def _course_context(session_id: str, chapter_id: str) -> tuple[InterviewSession, ChapterDraft, TeachingEvidence, str, str]:
    session = _session_or_404(session_id)
    if session.phase != InterviewPhase.COMPLETE:
        raise HTTPException(status_code=409, detail="请先确认学习画像再生成课程")
    context = native_repository.session_context(session_id)
    structure = (BookStructure.model_validate_json(context["structure_json"]) if context
                 else job_repository.get_structure(session.profile.book_id))
    if structure is None:
        raise HTTPException(status_code=409, detail="书籍尚未完成章节重建")
    chapter = next(
        (candidate for candidate in structure.chapters if candidate.chapter_id == chapter_id), None
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail="未找到该章节")
    try:
        job = job_repository.get_job(session.profile.book_id)
        if job is None:
            raise ValueError("missing source OCR job")
        pages = load_normalized_pages(job.output_dir / "normalized" / "pages.jsonl")
        evidence = build_teaching_evidence(chapter, pages)
    except (OSError, ValueError) as error:
        logger.warning("teaching source context unavailable", exc_info=error)
        raise HTTPException(status_code=409, detail="本章原文上下文不完整，暂时不能生成课程") from error
    current_profile_hash = profile_fingerprint(resolved_profile(sys.modules[__name__], session))
    current_source_hash = hashlib.sha256((
        chapter_fingerprint(chapter, evidence.fingerprint) + "|" + TEACHING_CONTENT_VERSION
        + "|" + settings.text_base_url + "|" + settings.text_model
        + "|review=" + settings.teaching_review_revision
    ).encode()).hexdigest()
    return session, chapter, evidence, current_profile_hash, current_source_hash


def _compile_course(session_id: str, chapter_id: str, *, refresh_profile: bool = True) -> PublicChapterLearningBundle:
    session, chapter, evidence, current_profile_hash, current_source_hash = _course_context(session_id, chapter_id)
    latest = assessment_repository.get_latest_course(session_id, chapter_id)
    profile = resolved_profile(sys.modules[__name__], session)
    if (
        latest is not None
        and (not refresh_profile or latest.profile_fingerprint == current_profile_hash)
        and latest.source_fingerprint == current_source_hash
        and latest.teaching_quality_version == TEACHING_CONTENT_VERSION
        and latest.teaching_evidence_fingerprint == evidence.fingerprint
        and latest.teaching_review.get("accepted") is True
        and latest.teaching_fact_review.get("accepted") is True
        and _current_scope_plan(latest, evidence)
    ):
        return PublicChapterLearningBundle.from_private(latest)
    version = assessment_repository.next_course_version(session_id, chapter_id)
    decision = personalization_policy.decide(profile, chapter_id)
    try:
        bundle = course_compiler.compile(
            chapter=chapter,
            profile=profile,
            decision=decision,
            version=version,
            instance_key=session_id,
            source_fingerprint=current_source_hash,
        )
        # A model/source/profile can return to an earlier combination. Its
        # deterministic course ID already exists even when it is not the latest
        # version, so reuse that approved content before spending another model
        # call or colliding with the immutable historical row.
        historical = assessment_repository.get_course_for_session(session_id, bundle.course_id)
        if historical is not None:
            if (
                historical.profile_fingerprint == current_profile_hash
                and historical.source_fingerprint == current_source_hash
                and historical.teaching_quality_version == TEACHING_CONTENT_VERSION
                and historical.teaching_evidence_fingerprint == evidence.fingerprint
                and historical.teaching_review.get("accepted") is True
                and historical.teaching_fact_review.get("accepted") is True
                and _current_scope_plan(historical, evidence)
            ):
                return PublicChapterLearningBundle.from_private(historical)
            # Never overwrite attempted questions, or publish an incomplete
            # historical draft merely because its content ID matches.
            raise HTTPException(status_code=409, detail="已有课程缺少当前完整审核，请更新课程版本后重试")
        bundle = teaching_generator.generate(bundle, chapter, evidence)
        if not _current_scope_plan(bundle, evidence):
            raise TeachingContentError("课程缺少有效的当前范围规划")
        bundle.teaching_evidence_fingerprint = evidence.fingerprint
        inserted = assessment_repository.save_course(session_id, bundle)
    except TeachingContentError as error:
        try:
            teaching_audit({"event": "publication_failed", "error_type": type(error).__name__,
                "detail": str(error), "review_feedback": getattr(error, "review_feedback", None),
                "chapter_id": chapter_id, "session_hash": hashlib.sha256(session_id.encode()).hexdigest(),
                "source_fingerprint": current_source_hash, "evidence_fingerprint": evidence.fingerprint})
        except (OSError, ValueError, TypeError):
            logger.warning("private teaching failure audit unavailable")
        logger.warning("whole chapter teaching review failed", exc_info=error)
        if isinstance(error, TeachingServiceUnavailableError):
            raise HTTPException(status_code=503, detail="教学服务暂时不可用，课程尚未完成审核，请稍后重试") from error
        raise HTTPException(status_code=503, detail="本章教学内容未通过完整性与原文审核，请稍后重试") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail="该章证据不足，暂时不能生成课程") from error
    if not inserted:
        concurrent = assessment_repository.get_course_for_session(session_id, bundle.course_id)
        if concurrent is None:
            raise HTTPException(status_code=409, detail="课程版本发生并发冲突，请重试")
        if (concurrent.profile_fingerprint != current_profile_hash
                or concurrent.teaching_quality_version != TEACHING_CONTENT_VERSION
                or concurrent.source_fingerprint != current_source_hash
                or concurrent.teaching_evidence_fingerprint != evidence.fingerprint
                or concurrent.teaching_review.get("accepted") is not True
                or concurrent.teaching_fact_review.get("accepted") is not True
                or not _current_scope_plan(concurrent, evidence)):
            raise HTTPException(status_code=409, detail="课程版本发生变化，请重试")
        return PublicChapterLearningBundle.from_private(concurrent)
    return PublicChapterLearningBundle.from_private(bundle)


@app.get("/api/interviews/{session_id}/courses/{chapter_id}", response_model=CourseResponse)
def get_course(session_id: str, chapter_id: str) -> PublicChapterLearningBundle:
    _session_or_404(session_id)
    bundle = assessment_repository.get_latest_course(session_id, chapter_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="该章尚未生成个人课程")
    # A read must never create a paid model request or block on generation.
    # Preserve the learner's current lesson after practice, but fail closed if
    # its source/model/review revision changed. Explicit background POST refreshes it.
    _, _, evidence, _, current_source_hash = _course_context(session_id, chapter_id)
    candidates = [bundle, *sorted(assessment_repository.courses_for_session(session_id),
                                  key=lambda item: item.version, reverse=True)]
    for candidate in candidates:
        if (
            candidate.chapter_id == chapter_id
            and candidate.source_fingerprint == current_source_hash
            and candidate.teaching_quality_version == TEACHING_CONTENT_VERSION
            and candidate.teaching_evidence_fingerprint == evidence.fingerprint
            and candidate.teaching_review.get('accepted') is True
            and candidate.teaching_fact_review.get('accepted') is True
            and _current_scope_plan(candidate, evidence)
        ):
            return PublicChapterLearningBundle.from_private(candidate)
    raise HTTPException(409, '教材或课程审核版本已更新，请重新生成本章课程。')


def _generate_course_task(session_id: str, chapter_id: str) -> dict:
    try:
        return compile_course(session_id, chapter_id).model_dump(mode='json')
    except HTTPException as error:
        raise CourseJobFailure(str(error.detail)) from error


def _check_course_admission() -> None:
    # This only reads recent route cooldown, not configuration or model health.
    # It cannot reserve the recovery probe or issue a paid request.
    try:
        for prerequisite in (teaching_client, teaching_review_client):
            check = getattr(prerequisite, 'check_admission', None)
            if callable(check):
                check()
    except LLMCooldownError as error:
        raise CourseAdmissionDeferred(error.retry_after_seconds) from error


course_job_queue = CourseJobQueue(
    settings.data_dir / 'state' / 'course_jobs.sqlite3', _generate_course_task,
    admission_check=_check_course_admission,
)


def _runtime_snapshot() -> RuntimeSnapshot:
    from ..studio import get_studio

    studio = get_studio(settings.data_dir)
    capabilities = studio.capabilities()
    return RuntimeSnapshot(
        text_configured=assessment_llm is not None,
        review_configured=teaching_review_client is not None,
        diagnostics_configured=item_generator is not None,
        ocr_configured=bool(settings.ocr_worker_enabled),
        ocr_worker_alive=ocr_worker.is_alive(),
        preparation_worker_alive=bool(preparation_queue.thread and preparation_queue.thread.is_alive()),
        course_worker_alive=course_job_queue.is_alive(),
        rag_configured=bool(all((settings.rag_index_dir, settings.rag_embedding_model_path,
                                settings.rag_reranker_model_path, settings.text_api_key))),
        rag_ready=qa_service_ready(),
        note_worker_alive=bool(studio.note_thread and studio.note_thread.is_alive()),
        media_worker_alive=bool(studio.thread and studio.thread.is_alive()),
        image_configured=bool(capabilities.get('image')),
        video_configured=bool(capabilities.get('video')),
        notes_configured=bool(capabilities.get('notes_ai')),
        voice_configured=bool(capabilities.get('voice_notes')),
    )


app.include_router(runtime_router(_runtime_snapshot))


def _require_course_actor(session_id: str, request: Request) -> None:
    _session_or_404(session_id)
    owner = account_repository.request_owner(request)
    if not owner or not account_repository.repo.claim_session(owner, session_id):
        raise HTTPException(403, '请先恢复自己的学习身份。')


@app.post('/api/interviews/{session_id}/course-jobs')
def start_course_task(session_id: str, body: CourseJobInput, request: Request) -> JSONResponse:
    _require_course_actor(session_id, request)
    _, _, _, profile_hash, source_hash = _course_context(session_id, body.chapter_id)
    try:
        job = course_job_queue.enqueue(session_id, body.chapter_id,
            profile_hash + '|' + source_hash, retry=body.retry, request_id=body.request_id)
    except CourseAdmissionDeferred as error:
        raise HTTPException(503, str(error), headers={
            'Retry-After': str(max(1, math.ceil(error.retry_after_seconds)))}) from error
    except (CourseRetryLimit, CourseJobFailure) as error:
        raise HTTPException(409, str(error)) from error
    if job['status'] == 'succeeded':
        job['course'] = get_course(session_id, body.chapter_id).model_dump(mode='json')
    return JSONResponse(job, status_code=202 if job['status'] in {'queued', 'running'} else 200)


@app.get('/api/interviews/{session_id}/course-jobs/{job_id}')
def course_task_status(session_id: str, job_id: str, request: Request) -> dict:
    _require_course_actor(session_id, request)
    job = course_job_queue.get(session_id, job_id)
    if job is None:
        raise HTTPException(404, '课程任务不存在。')
    if job['status'] == 'succeeded':
        # Reuse only the current approved publication; never leak the queue's
        # historical snapshot after a source/model/review revision change.
        job['course'] = get_course(session_id, job['chapter_id']).model_dump(mode='json')
    return job


@app.post("/api/interviews/{session_id}/courses", response_model=list[CourseResponse])
def compile_book_courses(session_id: str) -> list[PublicChapterLearningBundle]:
    session = _session_or_404(session_id)
    if session.phase != InterviewPhase.COMPLETE:
        raise HTTPException(status_code=409, detail="请先确认学习画像再生成课程")
    context = native_repository.session_context(session_id)
    structure = (BookStructure.model_validate_json(context["structure_json"]) if context
                 else job_repository.get_structure(session.profile.book_id))
    if structure is None:
        raise HTTPException(status_code=409, detail="书籍尚未完成章节重建")
    # Teaching batches are sequential inside each chapter; the shared executor
    # bounds total chapter work and preserves source order in this response.
    def generate(chapter):
        try:
            return compile_course(session_id, chapter.chapter_id)
        except Exception as error:
            return error
    results = ordered_book_map(generate, structure.chapters)
    for result in results:
        if isinstance(result, Exception):
            raise result
    return results


@app.post(
    "/api/interviews/{session_id}/courses/{course_id}/practice/{item_id}",
    response_model=CourseActivityResponse,
)
def answer_course_practice(
    session_id: str,
    course_id: str,
    item_id: str,
    request: CoursePracticeRequest,
) -> CourseActivityResponse:
    duplicate = _existing_activity(session_id, request.event_id)
    if duplicate is not None:
        return duplicate
    session = _session_or_404(session_id)
    bundle = _course_for_session_or_404(session_id, course_id)
    item = next(
        (candidate for candidate in bundle.practice_items if candidate.item_id == item_id), None
    )
    if item is None:
        raise HTTPException(status_code=404, detail="未找到该练习")
    diagnostic = DiagnosticItem(
        item_id=item.item_id,
        chapter_id=bundle.chapter_id,
        knowledge_point_ids=[item.point_id],
        prompt=item.prompt,
        response_type=ResponseType(item.response_type),
        options=item.options,
        correct_option_ids=item.correct_option_ids,
        expected_answer=item.expected_answer,
        rubric=item.rubric,
        difficulty=item.difficulty,
        discrimination=1.3,
        estimated_seconds=item.estimated_seconds,
        source_pages=sorted({citation.page_number for citation in item.citations}),
    )
    response = AssessmentResponse(
        item_id=item.item_id,
        answer=request.answer,
        selected_option_ids=request.selected_option_ids,
        confidence=request.confidence,
        response_seconds=request.response_seconds,
        hints_used=request.hints_used,
        revisions=request.revisions,
    )
    if diagnostic.response_type in {ResponseType.SINGLE_CHOICE, ResponseType.MULTIPLE_CHOICE}:
        evidence = score_choice(diagnostic, response)
    elif open_answer_scorer is not None:
        evidence = open_answer_scorer.score(diagnostic, response)
    else:
        raise HTTPException(status_code=503, detail="练习评分暂时不可用，请稍后重试。")
    if evidence.scoring_confidence > 0:
        engine.update_profile(session.profile, diagnostic, response, evidence)
    stale = profile_fingerprint(resolved_profile(sys.modules[__name__], session)) != bundle.profile_fingerprint
    activity = CourseActivityResponse(
        evidence=evidence,
        profile=session.profile,
        course_stale=stale,
    )
    return _save_course_activity(
        session,
        event_id=request.event_id,
        course_id=course_id,
        item_id=item_id,
        event_type="practice_answer",
        activity=activity,
    )


@app.post(
    "/api/interviews/{session_id}/courses/{course_id}/flashcards/{card_id}",
    response_model=CourseActivityResponse,
)
def review_flashcard(
    session_id: str,
    course_id: str,
    card_id: str,
    request: FlashcardReviewRequest,
) -> CourseActivityResponse:
    duplicate = _existing_activity(session_id, request.event_id)
    if duplicate is not None:
        return duplicate
    session = _session_or_404(session_id)
    bundle = _course_for_session_or_404(session_id, course_id)
    card = next(
        (candidate for candidate in bundle.flashcards if candidate.card_id == card_id), None
    )
    if card is None:
        raise HTTPException(status_code=404, detail="未找到该闪卡")
    try:
        review_state = schedule_review(
            review_state_for_point(session.profile, card.point_id, assessment_repository.courses_for_session(session_id)),
            request.rating,
            response_seconds=request.response_seconds,
        )
        score = rating_score(request.rating)
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail="闪卡反馈必须是 again/hard/good/easy"
        ) from error
    diagnostic = DiagnosticItem(
        item_id=card.card_id,
        chapter_id=bundle.chapter_id,
        knowledge_point_ids=[card.point_id],
        prompt=card.front,
        response_type=ResponseType.SELF_REPORT,
        expected_answer=card.back,
        rubric=[card.back],
        difficulty=bundle.decision.exercise_difficulty * 2 - 1,
        discrimination=0.8,
        estimated_seconds=30,
        source_pages=sorted({citation.page_number for citation in card.citations}),
    )
    response = AssessmentResponse(
        item_id=card.card_id,
        answer=request.rating,
        confidence=score,
        response_seconds=request.response_seconds,
    )
    evidence = ScoredEvidence(score=score, scoring_confidence=0.55)
    engine.update_profile(
        session.profile,
        diagnostic,
        response,
        evidence,
        evidence_kind=EvidenceKind.BEHAVIORAL,
    )
    session.profile.flashcard_reviews[card_id] = review_state
    stale = profile_fingerprint(resolved_profile(sys.modules[__name__], session)) != bundle.profile_fingerprint
    activity = CourseActivityResponse(
        evidence=evidence,
        profile=session.profile,
        course_stale=stale,
        review_state=review_state,
    )
    return _save_course_activity(
        session,
        event_id=request.event_id,
        course_id=course_id,
        item_id=card_id,
        event_type="flashcard_review",
        activity=activity,
    )


def _book_or_404(book_id: str) -> BookRecord:
    stored = job_repository.get_book(book_id)
    try:
        book = store.book(book_id)
    except KeyError as error:
        if stored is None:
            raise HTTPException(status_code=404, detail="未找到书籍") from error
        book = BookRecord(
            book_id=stored.book_id,
            original_name=stored.original_name,
            file_path=stored.file_path,
            status=stored.status,
            progress=stored.progress,
            current_step=stored.current_step,
        )
        store.books[book_id] = book
    job = job_repository.get_job(book_id)
    if stored is not None and stored.status == "structured":
        _sync_book_from_job(book, stored.status, stored.progress, stored.current_step)
    elif job is not None:
        _sync_book_from_job(book, job.status, job.progress, job.current_step)
    return book


def _session_or_404(session_id: str) -> InterviewSession:
    session = assessment_repository.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="未找到诊断会话")
    return session


def _save_session(session: InterviewSession) -> None:
    try:
        assessment_repository.save_session(session)
    except SessionConflictError as error:
        raise HTTPException(status_code=409, detail="会话已在另一端更新，请刷新后继续") from error


def _course_for_session_or_404(session_id: str, course_id: str) -> ChapterLearningBundle:
    bundle = assessment_repository.get_course_for_session(session_id, course_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="未找到该个人课程")
    return bundle


def _existing_activity(session_id: str, event_id: str) -> CourseActivityResponse | None:
    payload = assessment_repository.get_event_payload(session_id, event_id)
    if payload is None:
        return None
    activity = CourseActivityResponse.model_validate_json(payload)
    activity.duplicate = True
    return activity


def _save_course_activity(
    session: InterviewSession,
    *,
    event_id: str,
    course_id: str,
    item_id: str,
    event_type: str,
    activity: CourseActivityResponse,
) -> CourseActivityResponse:
    try:
        inserted = assessment_repository.save_session_with_event(
            session,
            event_id=event_id,
            course_id=course_id,
            item_id=item_id,
            event_type=event_type,
            payload_json=activity.model_dump_json(),
        )
    except SessionConflictError as error:
        raise HTTPException(
            status_code=409, detail="学习记录已在另一端更新，请刷新后继续"
        ) from error
    if not inserted:
        existing = _existing_activity(session.session_id, event_id)
        if existing is None:
            raise HTTPException(status_code=409, detail="该学习事件已经提交")
        return existing
    return activity


def _items_for_session(session: InterviewSession) -> list[DiagnosticItem]:
    context = native_repository.session_context(session.session_id)
    if context:
        return TypeAdapter(list[DiagnosticItem]).validate_json(context["items_json"])
    snapshot = assessment_repository.get_bank(f"interview:{session.session_id}")
    if snapshot is not None:
        return snapshot
    # Unversioned banks are never overwritten by the new generation protocol.
    # They preserve old non-native sessions that predate per-session snapshots.
    historical = assessment_repository.get_bank(session.profile.book_id)
    if historical is not None:
        return [item for item in historical if item.response_type == ResponseType.SINGLE_CHOICE]
    return _items_for_book(session.profile.book_id)


def _items_for_book(book_id: str) -> list[DiagnosticItem]:
    if book_id == "demo_book":
        items = store.book(book_id).diagnostic_items
        return [item for item in items if item.response_type == ResponseType.SINGLE_CHOICE]
    structure = job_repository.get_structure(book_id)
    if structure is None:
        return []
    items = (
        assessment_repository.get_bank(
            diagnostic_bank_key(book_id), expected_fingerprint=diagnostic_fingerprint(structure)
        )
        or []
    )
    # Older cached banks may contain open questions. Product diagnostics are
    # deliberately choice-only so a stale cache can never reintroduce typing.
    return [item for item in items if item.response_type == ResponseType.SINGLE_CHOICE]


def _bank_response(book_id: str, items: list[DiagnosticItem]) -> DiagnosticBankResponse:
    return DiagnosticBankResponse(
        book_id=book_id,
        item_count=len(items),
        chapter_count=len({item.chapter_id for item in items}),
        choice_count=sum(item.response_type == ResponseType.SINGLE_CHOICE for item in items),
        explanation_count=sum(item.response_type == ResponseType.EXPLANATION for item in items),
    )


def _status(book: BookRecord) -> BookStatusResponse:
    reconstruction = book.reconstruction
    job = job_repository.get_job(book.book_id)
    structure = job_repository.get_structure(book.book_id)
    return BookStatusResponse(
        book_id=book.book_id,
        status=book.status,
        progress=book.progress,
        current_step=book.current_step,
        quality_score=(
            reconstruction.quality_score
            if reconstruction
            else (job.quality_score if job is not None else None)
        ),
        needs_human_review=(
            reconstruction.needs_human_review
            if reconstruction
            else (job.needs_human_review if job is not None else False)
        ),
        attempts=job.attempts if job is not None else 0,
        max_attempts=job.max_attempts if job is not None else 0,
        page_count=(
            job.page_count
            if job is not None
            else (structure.source_page_count if structure is not None else None)
        ),
        retryable=job is not None and job.status in {"failed", "ocr_review_required", "cancelled"},
    )


def _sync_book_from_job(book: BookRecord, status: str, progress: float, step: str) -> None:
    book.status = status
    book.progress = progress
    book.current_step = step




account_repository = Accounts(
    CommunityRepository(settings.data_dir / "state" / "community.sqlite3")
)
app.middleware("http")(learning_guard(account_repository))
app.include_router(
    community_router(
        settings.data_dir,
        list_published_books,
        lambda: job_repository,
        lambda: assessment_repository,
        diagnostic_fingerprint=diagnostic_fingerprint,
    )
)
app.include_router(native_router(sys.modules[__name__]))
