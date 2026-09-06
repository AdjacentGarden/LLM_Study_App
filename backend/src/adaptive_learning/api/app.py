from __future__ import annotations

import faulthandler
import logging
import os
import shutil
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..assessment.engine import AdaptiveAssessmentEngine, score_choice
from ..assessment.interview import InterviewOrchestrator
from ..assessment.item_generation import (
    DiagnosticGenerationError,
    DiagnosticItemGenerator,
    structure_fingerprint,
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
from ..config import get_settings
from ..ingestion.chaptering import (
    ChapterReconstructor,
    load_normalized_pages,
)
from ..ingestion.jobs import OCRWorker, SQLiteOCRJobRepository, SubprocessOCRRunner
from ..ingestion.models import BookStructure
from ..ingestion.ocr_job import sha256_file
from ..ingestion.pipeline import new_book_id
from ..llm.client import LLMConfig, LLMError, LLMTimeoutError, OpenAICompatibleClient
from ..personalization.flashcard_quality import FlashcardQualityError, FlashcardQualityGate
from ..personalization.generator import (
    ChapterCourseCompiler,
    chapter_fingerprint,
    profile_fingerprint,
)
from ..personalization.models import ChapterLearningBundle, PublicChapterLearningBundle
from ..personalization.policy import PersonalizationPolicy
from ..personalization.review import rating_score, schedule_review
from ..rag.grounded_qa import GroundedAnswerValidationError
from ..rag.index import RAGIndexError
from ..rag.service import QABusyError, TextbookQAResult, TextbookQAService
from .chapter_dependency import require_chapter_reconstructor
from .community_routes import community_router
from .qa_dependency import build_qa_service, qa_service_ready, require_qa_service
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

logger = logging.getLogger(__name__)

if hasattr(signal, "SIGUSR2"):
    faulthandler.register(signal.SIGUSR2, all_threads=True)

settings = get_settings()
store = MemoryStore()
job_repository = SQLiteOCRJobRepository(settings.data_dir / "state" / "ocr_jobs.sqlite3")
assessment_repository = SQLiteAssessmentRepository(
    settings.data_dir / "state" / "assessments.sqlite3"
)
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
        )
    )
open_answer_scorer = None
item_generator = None
if assessment_llm is not None:
    open_answer_scorer = OpenAnswerScorer(
        assessment_llm,
        minimum_confidence=settings.open_answer_min_confidence,
    )
    item_generator = DiagnosticItemGenerator(assessment_llm)
orchestrator = InterviewOrchestrator(engine, open_answer_scorer)
personalization_policy = PersonalizationPolicy()
course_compiler = ChapterCourseCompiler()
flashcard_quality = FlashcardQualityGate(
    OpenAICompatibleClient(replace(assessment_llm.config, timeout_seconds=120))
    if assessment_llm is not None else None,
    settings.data_dir / "state" / "flashcard_quality.sqlite3",
    f"{settings.text_base_url}|{settings.text_model}",
)


def _reviewed_course(bundle: ChapterLearningBundle) -> ChapterLearningBundle:
    try:
        reviewed = flashcard_quality.ensure(bundle)
    except FlashcardQualityError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    if reviewed != bundle:
        assessment_repository.update_reviewed_course(reviewed)
    return reviewed


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _install_demo_book()
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
    try:
        yield
    finally:
        if settings.ocr_worker_enabled:
            ocr_worker.stop()


app = FastAPI(
    title="Adaptive Book Learning API",
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


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "service": "adaptive-book-learning",
        "version": app.version,
        "ocr_worker_enabled": settings.ocr_worker_enabled,
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
        "evidence_planner": True,
        "semantic_review": True,
    }


@app.get("/api/demo")
def demo() -> dict[str, object]:
    book = store.book("demo_book")
    return {
        "book": {
            "book_id": book.book_id,
            "title": "理解复杂系统",
            "page_count": 186,
            "status": "ready",
            "quality_score": 0.94,
            "chapters": [
                {"id": "ch_1", "title": "从局部到整体", "summary": "认识系统、要素与关系。"},
                {"id": "ch_2", "title": "反馈与变化", "summary": "理解正反馈、负反馈和动态行为。"},
                {"id": "ch_3", "title": "边界、延迟与涌现", "summary": "识别直觉容易失效的地方。"},
                {"id": "ch_4", "title": "用系统方法解决问题", "summary": "把概念迁移到真实问题。"},
            ],
        },
        "processing": [
            {"label": "页面识别与版面还原", "state": "done", "detail": "186/186 页"},
            {"label": "双模型校对与质量门控", "state": "done", "detail": "94% 可直接使用"},
            {"label": "章节结构与摘要", "state": "done", "detail": "识别 4 章"},
        ],
    }


@app.post("/api/books", response_model=UploadResponse)
async def upload_book(file: Annotated[UploadFile, File()]) -> UploadResponse:
    suffix = Path(file.filename or "book.pdf").suffix.lower()
    if suffix != ".pdf":
        raise HTTPException(
            status_code=415, detail="当前框架仅接受 PDF；Office/EPUB 适配器在下一阶段启用"
        )
    book_id = new_book_id()
    target_dir = settings.data_dir / "books" / book_id
    target_dir.mkdir(parents=True, exist_ok=False)
    target = target_dir / "source.pdf"
    with target.open("wb") as output:
        shutil.copyfileobj(file.file, output)
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
                cover_url=(f"/api/books/{book_id}/cover" if
                           (settings.data_dir / "covers" / f"{book_id}.jpg").is_file() else None),
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
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control":"public, max-age=86400"})


@app.get("/api/interviews/{session_id}/learning-records")
def get_learning_records(session_id: str) -> dict[str, object]:
    session = _session_or_404(session_id)
    return learning_records(session, _items_for_book(session.profile.book_id),
                            assessment_repository.courses_for_session(session_id))


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
        raise HTTPException(status_code=409, detail="该书尚未提交 OCR 任务")
    if current.status not in {"failed", "ocr_review_required"}:
        raise HTTPException(status_code=409, detail="当前 OCR 状态不允许手动重试")
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
) -> TextbookQAResult:
    if book_id != qa_service.book_id:
        raise HTTPException(status_code=404, detail="未找到该书的检索索引")
    try:
        return qa_service.answer(request.question)
    except QABusyError as error:
        raise HTTPException(status_code=503, detail="当前提问较多，请稍后重试", headers={"Retry-After": "5"}) from error
    except LLMTimeoutError as error:
        logger.warning("grounded QA model timed out", exc_info=error)
        raise HTTPException(status_code=504, detail="回答模型响应超时，请稍后重试；你的问题已保留。") from error
    except LLMError as error:
        logger.warning("grounded QA model call failed", exc_info=error)
        raise HTTPException(status_code=502, detail="回答模型暂时不可用") from error
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
    job = job_repository.get_job(book_id)
    if job is None or job.status != "ocr_ready":
        raise HTTPException(status_code=409, detail="书籍 OCR 尚未通过质量门控")
    pages_path = job.output_dir / "normalized" / "pages.jsonl"
    try:
        pages = load_normalized_pages(pages_path)
        structure = reconstructor.reconstruct_book(pages, Path(book.original_name).stem)
        job_repository.save_structure(book_id, structure)
    except (OSError, ValueError, LLMError) as error:
        logger.warning("chapter reconstruction failed", exc_info=error)
        raise HTTPException(status_code=502, detail="章节结构生成未通过证据校验") from error
    _sync_book_from_job(book, "structured", 1, "章节结构与摘要已生成")
    return structure


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
    fingerprint = structure_fingerprint(structure)
    existing = assessment_repository.get_bank(book_id, expected_fingerprint=fingerprint)
    if existing is not None and all(
        item.response_type == ResponseType.SINGLE_CHOICE for item in existing
    ):
        return _bank_response(book_id, existing)
    if item_generator is None:
        raise HTTPException(status_code=503, detail="诊断题生成模型尚未配置")
    try:
        items = item_generator.generate(structure)
        assessment_repository.save_bank(
            book_id=book_id,
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
def start_interview(request: StartInterviewRequest) -> InterviewResponse:
    _book_or_404(request.book_id)
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
    assessment_repository.create_session(session)
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
    items = _items_for_book(session.profile.book_id)
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
    items = _items_for_book(session.profile.book_id)
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
    session = _session_or_404(session_id)
    if session.phase != InterviewPhase.COMPLETE:
        raise HTTPException(status_code=409, detail="请先确认学习画像再生成课程")
    structure = job_repository.get_structure(session.profile.book_id)
    if structure is None:
        raise HTTPException(status_code=409, detail="书籍尚未完成章节重建")
    chapter = next(
        (candidate for candidate in structure.chapters if candidate.chapter_id == chapter_id), None
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail="未找到该章节")
    latest = assessment_repository.get_latest_course(session_id, chapter_id)
    current_profile_hash = profile_fingerprint(session.profile)
    current_source_hash = chapter_fingerprint(chapter)
    if (
        latest is not None
        and latest.profile_fingerprint == current_profile_hash
        and latest.source_fingerprint == current_source_hash
    ):
        return PublicChapterLearningBundle.from_private(_reviewed_course(latest))
    version = assessment_repository.next_course_version(session_id, chapter_id)
    decision = personalization_policy.decide(session.profile, chapter_id)
    try:
        bundle = course_compiler.compile(
            chapter=chapter,
            profile=session.profile,
            decision=decision,
            version=version,
            instance_key=session_id,
        )
        bundle = _reviewed_course(bundle)
        inserted = assessment_repository.save_course(session_id, bundle)
    except ValueError as error:
        raise HTTPException(status_code=409, detail="该章证据不足，暂时不能生成课程") from error
    if not inserted:
        concurrent = assessment_repository.get_latest_course(session_id, chapter_id)
        if concurrent is None:
            raise HTTPException(status_code=409, detail="课程版本发生并发冲突，请重试")
        return PublicChapterLearningBundle.from_private(_reviewed_course(concurrent))
    return PublicChapterLearningBundle.from_private(bundle)


@app.get("/api/interviews/{session_id}/courses/{chapter_id}", response_model=CourseResponse)
def get_course(session_id: str, chapter_id: str) -> PublicChapterLearningBundle:
    _session_or_404(session_id)
    bundle = assessment_repository.get_latest_course(session_id, chapter_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="该章尚未生成个人课程")
    return PublicChapterLearningBundle.from_private(_reviewed_course(bundle))


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
        raise HTTPException(status_code=503, detail="开放练习评分模型尚未配置")
    if evidence.scoring_confidence > 0:
        engine.update_profile(session.profile, diagnostic, response, evidence)
    stale = profile_fingerprint(session.profile) != bundle.profile_fingerprint
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
            session.profile.flashcard_reviews.get(card_id),
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
    stale = profile_fingerprint(session.profile) != bundle.profile_fingerprint
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


def _items_for_book(book_id: str) -> list[DiagnosticItem]:
    if book_id == "demo_book":
        items = store.book(book_id).diagnostic_items
        return [item for item in items if item.response_type == ResponseType.SINGLE_CHOICE]
    structure = job_repository.get_structure(book_id)
    if structure is None:
        return []
    items = (
        assessment_repository.get_bank(
            book_id, expected_fingerprint=structure_fingerprint(structure)
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
        retryable=job is not None and job.status in {"failed", "ocr_review_required"},
    )


def _sync_book_from_job(book: BookRecord, status: str, progress: float, step: str) -> None:
    book.status = status
    book.progress = progress
    book.current_step = step


def _install_demo_book() -> None:
    if "demo_book" in store.books:
        return
    items = [
        DiagnosticItem(
            item_id="diag_1",
            chapter_id="ch_1",
            knowledge_point_ids=["system_definition"],
            prompt="下面哪项最符合“系统”的含义？",
            response_type=ResponseType.SINGLE_CHOICE,
            options=[
                "若干事物的简单集合",
                "相互作用并形成整体行为的要素集合",
                "只有机器才是系统",
                "任何复杂对象",
            ],
            correct_option_ids=["1"],
            difficulty=-0.7,
            discrimination=1.2,
        ),
        DiagnosticItem(
            item_id="diag_2",
            chapter_id="ch_2",
            knowledge_point_ids=["feedback"],
            prompt="一个变化会进一步强化自身，这通常属于哪类反馈？",
            response_type=ResponseType.SINGLE_CHOICE,
            options=["负反馈", "正反馈", "无反馈", "随机反馈"],
            correct_option_ids=["1"],
            difficulty=-0.2,
            discrimination=1.4,
        ),
        DiagnosticItem(
            item_id="diag_3",
            chapter_id="ch_3",
            knowledge_point_ids=["delay", "emergence"],
            prompt="系统中的时间延迟为什么容易导致判断失误？",
            response_type=ResponseType.SINGLE_CHOICE,
            options=[
                "行动与结果存在间隔，容易造成错误归因和过度调整",
                "所有结果都会立即出现，因此人们通常反应不足",
                "时间延迟会消除反馈，所以系统不再发生变化",
                "时间延迟只改变记录方式，不影响人的判断过程",
            ],
            correct_option_ids=["0"],
            difficulty=0.6,
            discrimination=1.5,
        ),
        DiagnosticItem(
            item_id="diag_4",
            chapter_id="ch_4",
            knowledge_point_ids=["intervention"],
            prompt="解决复杂问题时，为什么不应只处理最明显的症状？",
            response_type=ResponseType.SINGLE_CHOICE,
            options=[
                "症状可能源于深层结构，局部处理还可能转移问题或产生反作用",
                "最明显的症状通常与系统结构完全无关，也不需要继续观察",
                "复杂问题没有可以分析的原因，所以只能等待症状自行消失",
                "处理症状一定会解决所有结构问题，不需要检查后续影响",
            ],
            correct_option_ids=["0"],
            difficulty=0.9,
            discrimination=1.4,
        ),
        DiagnosticItem(
            item_id="diag_5",
            chapter_id="ch_1",
            knowledge_point_ids=["boundary"],
            prompt="分析一个问题时，系统边界的主要作用是什么？",
            response_type=ResponseType.SINGLE_CHOICE,
            options=[
                "证明问题很简单",
                "决定暂时纳入哪些要素和关系",
                "排除所有外部影响",
                "找到唯一正确答案",
            ],
            correct_option_ids=["1"],
            difficulty=0.1,
            discrimination=1.3,
        ),
    ]
    store.books["demo_book"] = BookRecord(
        book_id="demo_book",
        original_name="理解复杂系统.pdf",
        file_path=Path("demo.pdf"),
        status="ready",
        progress=1,
        current_step="已生成章节结构与诊断题",
        diagnostic_items=items,
    )


def _mount_frontend() -> None:
    configured = os.getenv("FRONTEND_DIST_DIR", "").strip()
    if not configured:
        return
    frontend_dist = Path(configured).resolve()
    index_file = frontend_dist / "index.html"
    assets_dir = frontend_dist / "assets"
    if not index_file.is_file() or not assets_dir.is_dir():
        logger.warning("frontend dist is incomplete: %s", frontend_dist)
        return

    app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def frontend_index() -> FileResponse:
        return FileResponse(index_file, headers={"Cache-Control": "no-cache"})

    @app.get("/{frontend_path:path}", include_in_schema=False)
    def frontend_fallback(frontend_path: str) -> FileResponse:
        if frontend_path == "api" or frontend_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="未找到该接口")
        return FileResponse(index_file, headers={"Cache-Control": "no-cache"})


app.include_router(community_router(settings.data_dir, list_published_books,
                                   lambda: job_repository, lambda: assessment_repository))
_mount_frontend()
