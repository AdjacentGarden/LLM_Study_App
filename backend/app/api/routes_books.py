from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pathlib import Path

import fitz

from app.assignments.service import diagnose_assignment, get_submission, list_mistakes, submit_assignment
from app.community.catalog import read_community_source_metadata
from app.core.auth import (
    Principal,
    is_strict_mode,
    require_api_key,
    require_book_owner_from_path,
    require_user_match,
)
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.limits import heavy_task_limiter
from app.core.worker import task_queue
from app.document.mineru.exceptions import MinerUProtocolError, MinerUStaleResultError
from app.document.mineru.models import MinerUClientConfig, MinerUParseOptions, sha256_file
from app.document.mineru.task_store import MinerUTaskBegin, make_idempotency_key, mineru_task_store
from app.document.office_preview import render_office_preview
from app.document.pipeline import parse_document
from app.document.rebuilder import rebuild_chunks_and_assets
from app.document.toc_analyzer import analyze_toc_structure, read_toc_analysis, write_toc_analysis
from app.image_generation.service import image_job_store, run_generation_job, source_chunks_for_payload
from app.lessons.service import build_flashcards, build_quizzes, lesson_job_store, run_lesson_build_job
from app.rag.indexing import (
    delete_book_index,
    fail_current_index_build,
    read_index_status,
    retry_finalize,
)
from app.rag.service import answer_query
from app.schemas.books import (
    Asset,
    AssetPublic,
    AssignmentSubmitRequest,
    AssignmentSubmitResponse,
    Chapter,
    ChapterConfirmRequest,
    ChapterUpdate,
    CourseSummary,
    PageMapEntry,
    Chunk,
    DiagnosisResponse,
    Flashcard,
    ImageGenerationJobResponse,
    ImageGenerationRequest,
    JobStatusResponse,
    Lesson,
    LessonBuildJobResponse,
    LessonBuildRequest,
    LearningState,
    MistakeRecord,
    QuizQuestion,
    RagQuery,
    RagResponse,
    ScanResult,
    StudyPlan,
    StudyPlanRequest,
    StudyTask,
    StudyTaskUpdate,
    TocAnalysis,
)
from app.schemas.uploads import ParseRequest, ParseJobResponse
from app.services.artifact_store import (
    RagBundleBuild,
    get_rag_bundle_state,
    mark_rag_bundle_building,
    rag_book_mutation_lock,
    read_assets,
    read_chapters,
    read_chunks,
    read_flashcards,
    read_lessons,
    read_quizzes,
    write_chapters_for_build,
)
from app.services.job_store import job_store, JobRecord
from app.services.file_types import IMAGE_EXTENSIONS, OFFICE_EXTENSIONS
from app.services.storage import find_original_file, artifact_dir, asset_dir, list_book_ids, remove_book, resolve_under_root, read_book_owner
from app.study_plan import service as study_plan_service
from app.study_plan.service import create_plan, get_plan, learning_state, update_task


PARSE_TASK = "parse"
LESSON_BUILD_TASK = "lesson_build"
IMAGE_GENERATION_TASK = "image_generation"
DELETE_SENTINEL = ".deleting"


router = APIRouter(
    prefix="/api",
    dependencies=[Depends(require_api_key), Depends(require_book_owner_from_path)],
)


def _reserve_heavy_task(task_name: str) -> None:
    settings = get_settings()
    if task_name == PARSE_TASK:
        heavy_task_limiter.start(
            task_name,
            max_concurrent=settings.parse_max_concurrent,
            max_per_minute=settings.parse_rate_limit_per_minute,
        )
    elif task_name == LESSON_BUILD_TASK:
        heavy_task_limiter.start(
            task_name,
            max_concurrent=settings.lesson_build_max_concurrent,
            max_per_minute=settings.lesson_build_rate_limit_per_minute,
        )
    elif task_name == IMAGE_GENERATION_TASK:
        heavy_task_limiter.start(
            task_name,
            max_concurrent=settings.image_generation_max_concurrent,
            max_per_minute=settings.image_generation_rate_limit_per_minute,
        )


def _run_parse_job_with_slot(job_id: str, book_id: str, expected_generation: int | None = None) -> None:
    try:
        run_parse_job(job_id, book_id, expected_generation)
    finally:
        heavy_task_limiter.finish(PARSE_TASK)


def _run_lesson_build_job_with_slot(job_id: str, book_id: str, payload: LessonBuildRequest) -> None:
    try:
        run_lesson_build_job(job_id, book_id, payload)
    finally:
        heavy_task_limiter.finish(LESSON_BUILD_TASK)


def _run_generation_job_with_slot(job_id: str, payload: ImageGenerationRequest) -> None:
    try:
        run_generation_job(job_id, payload)
    finally:
        heavy_task_limiter.finish(IMAGE_GENERATION_TASK)


def _enqueue(fn, *args) -> None:
    settings = get_settings()
    if settings.use_worker:
        task_queue.enqueue(fn, *args)
    else:
        # Synchronous fallback used in tests / single-process dev mode.
        fn(*args)


def _assert_book_owner(book_id: str, principal: Principal) -> None:
    if not is_strict_mode():
        return
    owner = read_book_owner(book_id)
    if owner is None:
        raise AppError("book_owner_missing", "课程缺少有效的所有者元数据", status_code=403, details={"book_id": book_id})
    if principal.is_admin or owner == principal.user_id:
        return
    raise AppError("forbidden_book_owner_mismatch", "无权操作该课程", status_code=403, details={"book_id": book_id})


def _reserve_parse_generation(book_id: str, original: Path) -> MinerUTaskBegin:
    """Reserve the parse generation before a CloudPath worker is enqueued."""

    with rag_book_mutation_lock(book_id):
        if (artifact_dir(book_id) / DELETE_SENTINEL).exists():
            raise AppError("book_deleting", "课程正在删除，不能启动新的解析任务", status_code=409)
        try:
            config = MinerUClientConfig.from_settings()
            options = MinerUParseOptions.from_settings()
            return mineru_task_store.begin_or_resume(
                book_id=book_id,
                file_sha256=sha256_file(original),
                options_sha256=options.fingerprint(),
                endpoint=config.endpoint,
            )
        except (OSError, ValueError, MinerUProtocolError) as exc:
            raise AppError(
                "parse_generation_reservation_failed",
                "无法安全预留解析任务，请检查 MinerU 配置或任务状态",
                status_code=503,
                details={"reason": exc.__class__.__name__},
            ) from None


def _bound_job_for_generation(reservation: MinerUTaskBegin) -> JobRecord | None:
    job_id = reservation.record.cloudpath_job_id
    if not job_id:
        return None
    job = job_store.get(job_id)
    if job is None:
        raise AppError(
            "parse_generation_job_missing",
            "解析 generation 已绑定任务但任务记录缺失，已停止自动重排",
            status_code=409,
            details={"parse_generation": reservation.record.parse_generation},
        )
    if job.book_id != reservation.record.book_id or job.parse_generation != reservation.record.parse_generation:
        raise AppError(
            "parse_generation_job_mismatch",
            "解析 generation 与任务记录不一致，已停止自动重排",
            status_code=409,
        )
    return job


def _parse_cache_artifacts_ready(book_id: str) -> bool:
    """Require a complete, readable parse publication before reusing a terminal job."""
    artifacts = artifact_dir(book_id)
    scan_path = artifacts / "scan_result.json"
    chapters_path = artifacts / "chapters.json"
    if not scan_path.exists() or not chapters_path.exists() or get_rag_bundle_state(book_id) != "ready":
        return False
    try:
        scan = ScanResult.model_validate_json(scan_path.read_text(encoding="utf-8"))
        chapters = read_chapters(book_id)
    except Exception:
        return False
    return scan.page_count > 0 and bool(chapters) and bool(read_chunks(book_id))


def _completed_parse_cache_job(book_id: str, original: Path) -> JobRecord | None:
    """Return the persisted successful job for identical input/options when its artifacts are intact."""
    try:
        config = MinerUClientConfig.from_settings()
        options = MinerUParseOptions.from_settings()
        current = mineru_task_store.get_current(book_id)
        if current is None:
            return None
        expected_key = make_idempotency_key(
            book_id,
            sha256_file(original),
            options.fingerprint(),
            config.endpoint,
        )
        if (
            current.idempotency_key != expected_key
            or current.endpoint != config.endpoint.rstrip("/")
            or current.worker_outcome != "succeeded"
            or current.worker_finished_at is None
            or not current.cloudpath_job_id
            or not _parse_cache_artifacts_ready(book_id)
        ):
            return None
        job = job_store.get(current.cloudpath_job_id)
        if job is None or job.status != "done" or job.book_id != book_id:
            return None
        return job
    except (OSError, ValueError, MinerUProtocolError):
        return None


def _job_response(record: JobRecord) -> JobStatusResponse:
    return JobStatusResponse(
        job_id=record.job_id,
        book_id=record.book_id,
        status=record.status,
        stage=record.stage,
        progress=record.progress,
        message=record.message,
        error=record.error,
    )


def _ensure_artifact(book_id: str, filename: str) -> None:
    if filename in {"chunks.jsonl", "assets.json"}:
        state = get_rag_bundle_state(book_id)
        if state == "ready":
            return
        if state == "building":
            raise AppError(
                "artifact_building",
                f"{filename} 正在生成，请稍后重试",
                status_code=409,
            )
        if state == "invalid":
            raise AppError(
                "artifact_invalid",
                f"{filename} 当前代不完整，请重新解析文件",
                status_code=409,
            )
        raise AppError("artifact_missing", f"{filename} 不存在，请先解析文件", status_code=404)
    if not (artifact_dir(book_id) / filename).exists():
        raise AppError("artifact_missing", f"{filename} 不存在，请先解析文件", status_code=404)


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _max_mtime(paths: list[Path]) -> float:
    existing = [path.stat().st_mtime for path in paths if path.exists()]
    return max(existing) if existing else 0


def _parse_job_for_book(book_id: str) -> JobRecord | None:
    current_task = mineru_task_store.get_current(book_id)
    if current_task is not None and current_task.cloudpath_job_id:
        current_job = job_store.get(current_task.cloudpath_job_id)
        if current_job is not None:
            return current_job

    candidates = [job for job in job_store.all() if job.book_id == book_id]
    if not candidates:
        return None
    return max(
        enumerate(candidates),
        key=lambda item: (item[1].parse_generation if item[1].parse_generation is not None else -1, item[0]),
    )[1]


def _course_summary(book_id: str) -> CourseSummary | None:
    original = find_original_file(book_id)
    artifacts = artifact_dir(book_id)
    scan_path = artifacts / "scan_result.json"
    chapters_path = artifacts / "chapters.json"
    chunks_path = artifacts / "chunks.jsonl"
    assets_path = artifacts / "assets.json"
    bundle_manifest_path = artifacts / ".rag_bundles" / "manifest.json"
    index_status_path = artifacts / "rag_index_status.json"
    source_metadata_path = resolve_under_root("books", book_id, "_community_source.json")
    source_metadata = read_community_source_metadata(book_id)

    if original is None and not artifacts.exists():
        return None

    scan: ScanResult | None = None
    if scan_path.exists():
        scan = ScanResult.model_validate_json(scan_path.read_text(encoding="utf-8"))

    chapters = read_chapters(book_id) if chapters_path.exists() else []
    bundle_state = get_rag_bundle_state(book_id)
    chunks = read_chunks(book_id) if bundle_state == "ready" else []
    assets = read_assets(book_id) if bundle_state == "ready" else []
    chunk_count = len(chunks)
    title = (
        str(source_metadata.get("title"))
        if source_metadata and source_metadata.get("title")
        else scan.filename if scan else original.name if original else book_id
    )
    average_confidence = round(sum(chapter.confidence for chapter in chapters) / len(chapters)) if chapters else 0
    source_page_count = source_metadata.get("page_count") if source_metadata else None
    community_page_count = (
        source_page_count
        if isinstance(source_page_count, int) and not isinstance(source_page_count, bool) and source_page_count > 0
        else 0
    )
    try:
        rag_status = read_index_status(book_id)
    except Exception:
        rag_status = None
    parse_job = _parse_job_for_book(book_id)
    if bundle_state == "building":
        status = "processing"
    elif bundle_state == "invalid":
        status = "error"
    else:
        status = "ready" if bundle_state == "ready" and chapters and chunk_count else "uploaded"
    if status == "ready" and chapters and any(chapter.status in {"需检查", "需人工确认"} for chapter in chapters):
        status = "needs_review"
    if rag_status is not None and rag_status.status in {"building", "committed"}:
        status = "processing"
    elif rag_status is not None and rag_status.status in {"failed", "cache_failed"}:
        status = "error"
    if parse_job is not None and parse_job.status in {"pending", "processing"}:
        status = "processing"
    elif parse_job is not None and parse_job.status == "failed" and status not in {"ready", "needs_review"}:
        status = "error"

    return CourseSummary(
        book_id=book_id,
        title=title,
        filename=original.name if original else scan.filename if scan else None,
        status=status,
        # A verified community PDF is usable before the asynchronous parse has
        # produced scan_result.json. Preserve its fixed catalog page count so
        # clients can immediately enable the original-page reader.
        page_count=scan.page_count if scan else community_page_count,
        chapter_count=len(chapters),
        chunk_count=chunk_count,
        asset_count=len(assets),
        average_confidence=average_confidence,
        next_title=chapters[0].ai_title if chapters else None,
        rag_index_status=rag_status.status if rag_status else None,
        rag_index_provider=rag_status.active_provider if rag_status else None,
        rag_index_generation=rag_status.index_generation if rag_status else None,
        rag_fallback_reason=rag_status.fallback_reason if rag_status else None,
        parse_job_id=parse_job.job_id if parse_job else None,
        parse_job_status=parse_job.status if parse_job else None,
        parse_job_stage=parse_job.stage if parse_job else None,
        parse_job_progress=parse_job.progress if parse_job else None,
        parse_job_message=parse_job.message if parse_job else None,
        parse_job_error=parse_job.error if parse_job else None,
        updated_at=_max_mtime(
            [
                path
                for path in [
                    original,
                    scan_path,
                    chapters_path,
                    chunks_path,
                    assets_path,
                    bundle_manifest_path,
                    index_status_path,
                    source_metadata_path,
                ]
                if path is not None
            ]
        ),
        author=str(source_metadata.get("author")) if source_metadata and source_metadata.get("author") else None,
        source_catalog_id=str(source_metadata.get("id")) if source_metadata and source_metadata.get("id") else None,
        cover_url=(
            f"/api/community/books/{source_metadata.get('id')}/cover"
            if source_metadata and source_metadata.get("id")
            else None
        ),
    )


def _validate_chapters(chapters: list[Chapter]) -> None:
    by_id = {chapter.chapter_id: chapter for chapter in chapters}
    for chapter in chapters:
        if chapter.page_start < 1 or chapter.page_end < chapter.page_start:
            raise AppError("invalid_chapter_range", "章节页码范围无效", details={"chapter_id": chapter.chapter_id})
        if chapter.parent_id:
            parent = by_id.get(chapter.parent_id)
            if parent is None:
                raise AppError("parent_chapter_missing", "父章节不存在", details={"chapter_id": chapter.chapter_id, "parent_id": chapter.parent_id})
            if chapter.page_start < parent.page_start or chapter.page_end > parent.page_end:
                raise AppError("child_chapter_out_of_parent", "子章节必须位于父章节页码范围内", details={"chapter_id": chapter.chapter_id, "parent_id": chapter.parent_id})

    for index, chapter in enumerate(chapters):
        for other in chapters[index + 1 :]:
            if chapter.level != other.level or chapter.parent_id != other.parent_id:
                continue
            overlaps = chapter.page_start <= other.page_end and other.page_start <= chapter.page_end
            if overlaps:
                raise AppError("chapter_range_overlap", "同层级章节页码范围不能重叠", details={"chapter_id": chapter.chapter_id, "overlap_with": other.chapter_id})


def _rebuild_current_structure(
    book_id: str,
    chapters: list[Chapter],
    *,
    reserved_build: RagBundleBuild | None = None,
) -> None:
    original = find_original_file(book_id)
    if original is None:
        raise AppError("file_missing", "未找到已上传的原始文件", status_code=404)
    rebuild_chunks_and_assets(
        book_id,
        original,
        artifact_dir(book_id),
        chapters,
        reserved_build=reserved_build,
    )


def run_parse_job(job_id: str, book_id: str, expected_generation: int | None = None) -> None:
    claimed = False
    succeeded = False
    if expected_generation is not None:
        try:
            mineru_task_store.claim_worker(book_id, expected_generation, job_id)
            claimed = True
        except (MinerUProtocolError, MinerUStaleResultError):
            # A duplicate invocation of the same persisted job must not run a
            # second parser or upload another MinerU task.
            current = mineru_task_store.get_record(book_id, expected_generation)
            if current is not None and current.cloudpath_job_id == job_id and current.worker_claimed_at is not None:
                return
            job_store.update(
                job_id,
                stage="failed",
                progress=100,
                status="failed",
                error="parse_generation_worker_claim_rejected",
                message="解析 generation 已失效",
            )
            return
    try:
        if expected_generation is not None:
            mineru_task_store.assert_current(
                book_id,
                expected_generation,
                cloudpath_job_id=job_id,
            )
        job_store.update(job_id, status="processing", stage="detecting", progress=5, message="正在检测文件类型")
        original = find_original_file(book_id)
        if original is None:
            raise AppError("file_missing", "未找到已上传的原始文件", status_code=404)

        def on_progress(stage: str, progress: int, message: str) -> None:
            if expected_generation is not None:
                mineru_task_store.assert_current(
                    book_id,
                    expected_generation,
                    cloudpath_job_id=job_id,
                )
            job_store.update(job_id, stage=stage, progress=progress, message=message)

        parse_document(
            book_id,
            original,
            artifact_dir(book_id),
            on_progress=on_progress,
            expected_generation=expected_generation,
            cloudpath_job_id=job_id if expected_generation is not None else None,
        )
        if expected_generation is not None:
            mineru_task_store.assert_current(
                book_id,
                expected_generation,
                cloudpath_job_id=job_id,
            )
        job_store.update(job_id, stage="done", progress=100, status="done", message="解析完成，课程已进入待确认目录")
        succeeded = True
    except Exception as exc:
        try:
            fail_current_index_build(
                book_id,
                exc,
                expected_cause=f"parse:{expected_generation if expected_generation is not None else 'legacy'}",
            )
        except Exception:
            pass
        job_store.update(job_id, stage="failed", progress=100, status="failed", error=str(exc), message="解析失败")
    finally:
        if claimed and expected_generation is not None:
            try:
                mineru_task_store.finish_worker(
                    book_id,
                    expected_generation,
                    job_id,
                    succeeded=succeeded,
                )
            except (MinerUProtocolError, MinerUStaleResultError):
                # A newer generation may supersede this worker while parsing.
                # The old record is already auditable as discarded_stale.
                pass


@router.post("/books/{book_id}/parse", response_model=ParseJobResponse)
def parse_book(
    book_id: str,
    _: ParseRequest,
    principal: Principal = Depends(require_api_key),
) -> ParseJobResponse:
    original = find_original_file(book_id)
    if original is None:
        raise AppError("file_missing", "请先上传教材文件", status_code=404)
    _assert_book_owner(book_id, principal)
    cached_job = _completed_parse_cache_job(book_id, original)
    if cached_job is not None:
        cached_job = job_store.update(
            cached_job.job_id,
            message="已命中后端解析缓存，无需重复解析",
        )
        return ParseJobResponse(book_id=book_id, job_id=cached_job.job_id, status=cached_job.status)
    reservation = _reserve_parse_generation(book_id, original)
    existing_job = _bound_job_for_generation(reservation)
    if existing_job is not None:
        return ParseJobResponse(
            book_id=book_id,
            job_id=existing_job.job_id,
            status=existing_job.status,
        )

    _reserve_heavy_task(PARSE_TASK)
    candidate = job_store.create(
        book_id,
        stage="queued",
        parse_generation=reservation.record.parse_generation,
        mineru_record_id=reservation.record.record_id,
    )
    try:
        bound = mineru_task_store.bind_cloudpath_job(
            book_id,
            reservation.record.parse_generation,
            candidate.job_id,
        )
    except (MinerUProtocolError, MinerUStaleResultError):
        # Two API calls may race between reservation and binding. Only the
        # winner is enqueued; the loser returns that same persisted job.
        heavy_task_limiter.finish(PARSE_TASK)
        job_store.update(
            candidate.job_id,
            stage="failed",
            progress=100,
            status="failed",
            error="duplicate_parse_generation_job",
            message="相同解析 generation 已由另一任务处理",
        )
        current = mineru_task_store.get_current(book_id)
        if current is None or current.parse_generation != reservation.record.parse_generation or not current.cloudpath_job_id:
            raise AppError(
                "parse_generation_bind_failed",
                "无法绑定解析任务",
                status_code=409,
            ) from None
        winner = job_store.get(current.cloudpath_job_id)
        if winner is None:
            raise AppError(
                "parse_generation_job_missing",
                "解析 generation 的任务记录缺失",
                status_code=409,
            ) from None
        return ParseJobResponse(book_id=book_id, job_id=winner.job_id, status=winner.status)

    _enqueue(
        _run_parse_job_with_slot,
        candidate.job_id,
        book_id,
        bound.parse_generation,
    )
    return ParseJobResponse(book_id=book_id, job_id=candidate.job_id, status=candidate.status)


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str, principal: Principal = Depends(require_api_key)) -> JobStatusResponse:
    record = job_store.get(job_id)
    if record is None:
        raise AppError("job_not_found", "任务不存在", status_code=404)
    _assert_book_owner(record.book_id, principal)
    return _job_response(record)


@router.get("/books", response_model=list[CourseSummary])
def list_courses(principal: Principal = Depends(require_api_key)) -> list[CourseSummary]:
    visible_book_ids = [
        book_id
        for book_id in list_book_ids()
        if not is_strict_mode()
        or principal.is_admin
        or read_book_owner(book_id) == principal.user_id
    ]
    summaries = [_course_summary(book_id) for book_id in visible_book_ids]
    courses = [summary for summary in summaries if summary is not None]
    return sorted(courses, key=lambda item: item.updated_at, reverse=True)


@router.delete("/books/{book_id}", status_code=204)
def delete_course(book_id: str, principal: Principal = Depends(require_api_key)) -> None:
    target = resolve_under_root("books", book_id)
    if not target.exists():
        raise AppError("book_not_found", "课程不存在", status_code=404)
    _assert_book_owner(book_id, principal)
    sentinel = artifact_dir(book_id) / DELETE_SENTINEL
    try:
        with rag_book_mutation_lock(book_id):
            sentinel.write_text("deleting\n", encoding="utf-8")
            for _ in range(3):
                current = mineru_task_store.get_current(book_id)
                if current is None:
                    break
                try:
                    mineru_task_store.invalidate(
                        book_id,
                        current.parse_generation,
                        detail="book deletion requested",
                    )
                    break
                except MinerUStaleResultError:
                    continue
            else:
                raise AppError(
                    "book_delete_generation_race",
                    "删除期间解析 generation 持续变化，请稍后重试",
                    status_code=409,
                )
            delete_book_index(book_id)
    except Exception:
        sentinel.unlink(missing_ok=True)
        raise
    remove_book(book_id)


@router.get("/books/{book_id}/rag-index-status", response_model=dict[str, object])
def get_rag_index_status(
    book_id: str,
    principal: Principal = Depends(require_api_key),
) -> dict[str, object]:
    _assert_book_owner(book_id, principal)
    status = read_index_status(book_id)
    if status is None:
        raise AppError("rag_index_status_missing", "RAG 索引状态不存在", status_code=404)
    return status.as_dict()


@router.post("/books/{book_id}/rag-index/retry", response_model=dict[str, object])
def retry_rag_index_finalize(
    book_id: str,
    principal: Principal = Depends(require_api_key),
) -> dict[str, object]:
    _assert_book_owner(book_id, principal)
    status = read_index_status(book_id)
    if (
        status is None
        or status.active_provider != "pgvector"
        or status.status not in {"committed", "cache_failed"}
        or status.index_generation is None
        or status.build_id is None
    ):
        raise AppError(
            "rag_index_not_retryable",
            "当前 RAG 索引状态不能执行 finalize 重试",
            status_code=409,
        )
    receipt = retry_finalize(book_id, status.index_generation, status.build_id)
    refreshed = read_index_status(book_id)
    return refreshed.as_dict() if refreshed is not None else {
        "book_id": receipt.book_id,
        "status": receipt.status,
        "index_generation": receipt.index_generation,
    }


@router.get("/books/{book_id}/scan-result", response_model=ScanResult)
def get_scan_result(book_id: str) -> ScanResult:
    path = artifact_dir(book_id) / "scan_result.json"
    if not path.exists():
        raise AppError("scan_result_missing", "扫描结果不存在，请先解析文件", status_code=404)
    return ScanResult.model_validate_json(path.read_text(encoding="utf-8"))


@router.get("/books/{book_id}/chapters", response_model=list[Chapter])
def get_chapters(book_id: str) -> list[Chapter]:
    _ensure_artifact(book_id, "chapters.json")
    chapters = read_chapters(book_id)
    if not chapters:
        raise AppError("chapters_missing", "章节结果不存在，请先解析文件", status_code=404)
    return chapters


def _read_or_build_toc_analysis(book_id: str) -> TocAnalysis:
    artifacts = artifact_dir(book_id)
    analysis_path = artifacts / "toc_analysis.json"
    if analysis_path.exists():
        return read_toc_analysis(book_id, artifacts)
    scan_path = artifacts / "scan_result.json"
    pages_path = artifacts / "pages.json"
    if not scan_path.exists() or not pages_path.exists():
        raise AppError("toc_analysis_missing", "目录分析结果不存在，请先解析文件", status_code=404)
    scan = ScanResult.model_validate_json(scan_path.read_text(encoding="utf-8"))
    analysis = analyze_toc_structure(book_id, artifacts, scan.page_count)
    write_toc_analysis(artifacts, analysis)
    return analysis


@router.get("/books/{book_id}/toc-candidates", response_model=TocAnalysis)
def get_toc_candidates(book_id: str) -> TocAnalysis:
    return _read_or_build_toc_analysis(book_id)


@router.get("/books/{book_id}/page-map", response_model=list[PageMapEntry])
def get_page_map(book_id: str) -> list[PageMapEntry]:
    return _read_or_build_toc_analysis(book_id).page_map


@router.patch("/books/{book_id}/chapters/{chapter_id}", response_model=Chapter)
def update_chapter(
    book_id: str,
    chapter_id: str,
    payload: ChapterUpdate,
    principal: Principal = Depends(require_api_key),
) -> Chapter:
    _ensure_artifact(book_id, "chapters.json")
    _assert_book_owner(book_id, principal)
    chapters = read_chapters(book_id)
    for index, chapter in enumerate(chapters):
        if chapter.chapter_id != chapter_id:
            continue
        updated = chapter.model_copy(update=payload.model_dump(exclude_unset=True))
        chapters[index] = updated
        _validate_chapters(chapters)
        reserved_build = mark_rag_bundle_building(book_id)
        write_chapters_for_build(book_id, chapters, build_id=reserved_build.build_id)
        _rebuild_current_structure(book_id, chapters, reserved_build=reserved_build)
        return updated
    raise AppError("chapter_not_found", "章节不存在", status_code=404)


@router.post("/books/{book_id}/chapters/confirm", response_model=list[Chapter])
def confirm_chapters(
    book_id: str,
    payload: ChapterConfirmRequest,
    principal: Principal = Depends(require_api_key),
) -> list[Chapter]:
    _ensure_artifact(book_id, "chapters.json")
    _assert_book_owner(book_id, principal)
    chapters = payload.chapters if payload.chapters is not None else read_chapters(book_id)
    if not chapters:
        raise AppError("chapters_missing", "章节结果不存在，请先解析文件", status_code=404)
    confirmed = [chapter.model_copy(update={"status": "已确认"}) for chapter in chapters]
    _validate_chapters(confirmed)
    reserved_build = mark_rag_bundle_building(book_id)
    write_chapters_for_build(book_id, confirmed, build_id=reserved_build.build_id)
    _rebuild_current_structure(book_id, confirmed, reserved_build=reserved_build)
    return confirmed


@router.post("/books/{book_id}/chapters/rebuild", response_model=list[Chapter])
def rebuild_chapters(
    book_id: str,
    principal: Principal = Depends(require_api_key),
) -> list[Chapter]:
    _ensure_artifact(book_id, "chapters.json")
    _assert_book_owner(book_id, principal)
    chapters = read_chapters(book_id)
    _validate_chapters(chapters)
    reserved_build = mark_rag_bundle_building(book_id)
    write_chapters_for_build(book_id, chapters, build_id=reserved_build.build_id)
    _rebuild_current_structure(book_id, chapters, reserved_build=reserved_build)
    return chapters


@router.get("/books/{book_id}/chunks", response_model=list[Chunk])
def get_chunks(book_id: str) -> list[Chunk]:
    _ensure_artifact(book_id, "chunks.jsonl")
    return read_chunks(book_id)


@router.get("/chunks/{chunk_id}", response_model=Chunk)
def get_chunk(chunk_id: str, principal: Principal = Depends(require_api_key)) -> Chunk:
    for book_id in list_book_ids():
        if is_strict_mode() and not principal.is_admin and read_book_owner(book_id) != principal.user_id:
            continue
        for chunk in read_chunks(book_id):
            if chunk.chunk_id == chunk_id:
                return chunk
    raise AppError("chunk_not_found", "chunk 不存在", status_code=404)


@router.get("/books/{book_id}/assets", response_model=list[AssetPublic])
def get_assets(book_id: str, source_type: str | None = None) -> list[Asset]:
    _ensure_artifact(book_id, "assets.json")
    assets = read_assets(book_id)
    if source_type:
        assets = [asset for asset in assets if asset.source_type == source_type]
    return assets


@router.get("/chapters/{chapter_id}/figures", response_model=list[AssetPublic])
def get_chapter_figures(
    chapter_id: str,
    book_id: str | None = None,
    source_type: str | None = None,
    principal: Principal = Depends(require_api_key),
) -> list[Asset]:
    book_ids = [book_id] if book_id else list_book_ids()
    figures: list[Asset] = []
    for candidate_book_id in book_ids:
        if (
            is_strict_mode()
            and not principal.is_admin
            and read_book_owner(candidate_book_id) != principal.user_id
        ):
            # Global queries filter inaccessible books; an explicit book_id
            # remains fail-closed with the ordinary owner error.
            if book_id is not None:
                _assert_book_owner(candidate_book_id, principal)
            continue
        if not (artifact_dir(candidate_book_id) / "assets.json").exists():
            continue
        figures.extend(
            asset
            for asset in read_assets(candidate_book_id)
            if asset.chapter_id == chapter_id and (source_type is None or asset.source_type == source_type)
        )
    return figures


@router.get("/assets/{asset_id}", response_model=AssetPublic)
def get_asset(
    asset_id: str,
    book_id: str | None = None,
    principal: Principal = Depends(require_api_key),
) -> Asset:
    if not book_id:
        raise AppError("book_id_required", "查询 asset 必须提供 book_id", status_code=400)
    _assert_book_owner(book_id, principal)
    return get_book_asset(book_id, asset_id)


@router.get("/books/{book_id}/assets/{asset_id}", response_model=AssetPublic)
def get_book_asset(book_id: str, asset_id: str) -> Asset:
    _ensure_artifact(book_id, "assets.json")
    for asset in read_assets(book_id):
        if asset.asset_id == asset_id:
            return asset
    raise AppError("asset_not_found", "asset 不存在", status_code=404)


@router.get("/books/{book_id}/assets/{asset_id}/file")
def get_asset_file(book_id: str, asset_id: str) -> FileResponse:
    asset = get_book_asset(book_id, asset_id)
    matches = list(asset_dir(book_id).glob(f"{asset.asset_id}.*"))
    if not matches:
        raise AppError("asset_file_missing", "asset 文件不存在", status_code=404)
    return FileResponse(matches[0])


@router.get("/books/{book_id}/assets/{asset_id}/thumbnail")
def get_asset_thumbnail(book_id: str, asset_id: str) -> FileResponse:
    asset = get_book_asset(book_id, asset_id)
    matches = list(asset_dir(book_id).glob(f"thumb_{asset.asset_id}.*"))
    if not matches:
        raise AppError("asset_thumbnail_missing", "asset 缩略图不存在", status_code=404)
    return FileResponse(matches[0])


@router.get("/books/{book_id}/assets/{asset_id}/source-page")
def get_asset_source_page(book_id: str, asset_id: str) -> FileResponse:
    asset = get_book_asset(book_id, asset_id)
    if asset.page is None:
        raise AppError("source_page_missing", "AI 生成图片没有原书页截图", status_code=404)
    path = asset_dir(book_id) / f"page_{asset.page:03d}.png"
    if not path.exists():
        raise AppError("source_page_missing", "原书页截图不存在", status_code=404)
    return FileResponse(path)


@router.get("/books/{book_id}/pages/{page}/image")
def get_book_page_image(book_id: str, page: int) -> FileResponse:
    if page < 1:
        raise AppError("invalid_page", "页码必须从 1 开始", status_code=400)
    original = find_original_file(book_id)
    if original is None:
        raise AppError("file_missing", "未找到原始文件", status_code=404)

    suffix = original.suffix.lower()
    if suffix == ".pdf":
        output_dir = artifact_dir(book_id) / "page_images"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"page_{page:03d}.png"
        if output.exists():
            return FileResponse(output)
        document = fitz.open(original)
        try:
            if page > document.page_count:
                raise AppError("page_out_of_range", "页码超出原文范围", details={"page_count": document.page_count}, status_code=404)
            pix = document.load_page(page - 1).get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            pix.save(output)
        finally:
            document.close()
        return FileResponse(output)

    if suffix in IMAGE_EXTENSIONS:
        if page != 1:
            raise AppError("page_out_of_range", "图片文件只有第 1 页", details={"page_count": 1}, status_code=404)
        return FileResponse(original)

    if suffix in OFFICE_EXTENSIONS:
        preview = render_office_preview(artifact_dir(book_id), page)
        return FileResponse(preview, media_type="image/svg+xml")

    raise AppError("unsupported_page_source", "当前文件类型不支持原文页预览", status_code=400)


@router.get("/books/{book_id}/artifacts/preprocessed/{filename}")
def get_preprocessed_page_image(book_id: str, filename: str) -> FileResponse:
    if "/" in filename or "\\" in filename:
        raise AppError("invalid_artifact_path", "invalid artifact path", status_code=400)
    if not filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        raise AppError("unsupported_artifact_file", "unsupported artifact file", status_code=400)
    path = artifact_dir(book_id) / "preprocessed" / filename
    root = (artifact_dir(book_id) / "preprocessed").resolve()
    resolved = path.resolve()
    if root != resolved and root not in resolved.parents:
        raise AppError("invalid_artifact_path", "invalid artifact path", status_code=400)
    if not resolved.exists():
        raise AppError("artifact_file_missing", "artifact file not found", status_code=404)
    return FileResponse(resolved)


def _image_job_response(job_id: str) -> ImageGenerationJobResponse:
    job = image_job_store.get(job_id)
    if job is None:
        raise AppError("image_job_not_found", "图片生成任务不存在", status_code=404)
    return ImageGenerationJobResponse(
        job_id=job.job_id,
        book_id=job.book_id,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        asset=job.asset,
        error=job.error,
    )


def _lesson_job_response(job_id: str) -> LessonBuildJobResponse:
    job = lesson_job_store.get(job_id)
    if job is None:
        raise AppError("lesson_job_not_found", "课程生成任务不存在", status_code=404)
    return LessonBuildJobResponse(
        job_id=job.job_id,
        book_id=job.book_id,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        lessons=job.lessons,
        chapter_results=job.chapter_results,
        error=job.error,
    )


@router.post("/books/{book_id}/lessons/build", response_model=LessonBuildJobResponse)
def build_book_lessons(
    book_id: str,
    payload: LessonBuildRequest,
    principal: Principal = Depends(require_api_key),
) -> LessonBuildJobResponse:
    _ensure_artifact(book_id, "chapters.json")
    _ensure_artifact(book_id, "chunks.jsonl")
    _assert_book_owner(book_id, principal)
    _reserve_heavy_task(LESSON_BUILD_TASK)
    job = lesson_job_store.create(book_id)
    _enqueue(_run_lesson_build_job_with_slot, job.job_id, book_id, payload)
    return _lesson_job_response(job.job_id)


@router.get("/lesson-generation/jobs/{job_id}", response_model=LessonBuildJobResponse)
def get_lesson_generation_job(
    job_id: str,
    principal: Principal = Depends(require_api_key),
) -> LessonBuildJobResponse:
    job = lesson_job_store.get(job_id)
    if job is None:
        raise AppError("lesson_job_not_found", "课程生成任务不存在", status_code=404)
    _assert_book_owner(job.book_id, principal)
    return _lesson_job_response(job_id)


@router.get("/books/{book_id}/lessons", response_model=list[Lesson])
def get_lessons(book_id: str) -> list[Lesson]:
    return read_lessons(book_id)


@router.get("/books/{book_id}/lessons/{lesson_id}", response_model=Lesson)
def get_lesson(book_id: str, lesson_id: str) -> Lesson:
    for lesson in read_lessons(book_id):
        if lesson.lesson_id == lesson_id:
            return lesson
    raise AppError("lesson_not_found", "课程不存在，请先生成课程内容", status_code=404)


@router.post("/books/{book_id}/flashcards/build", response_model=list[Flashcard])
def build_book_flashcards(
    book_id: str,
    payload: LessonBuildRequest,
    principal: Principal = Depends(require_api_key),
) -> list[Flashcard]:
    _assert_book_owner(book_id, principal)
    return build_flashcards(book_id, payload.chapter_ids)


@router.get("/books/{book_id}/flashcards", response_model=list[Flashcard])
def get_book_flashcards(book_id: str) -> list[Flashcard]:
    return read_flashcards(book_id)


@router.post("/books/{book_id}/quizzes/build", response_model=list[QuizQuestion])
def build_book_quizzes(
    book_id: str,
    payload: LessonBuildRequest,
    principal: Principal = Depends(require_api_key),
) -> list[QuizQuestion]:
    _assert_book_owner(book_id, principal)
    return build_quizzes(book_id, payload.chapter_ids)


@router.get("/books/{book_id}/quizzes", response_model=list[QuizQuestion])
def get_book_quizzes(book_id: str) -> list[QuizQuestion]:
    return read_quizzes(book_id)


@router.post("/lessons/{lesson_id}/figures/generate", response_model=ImageGenerationJobResponse)
def generate_lesson_figure(
    lesson_id: str,
    payload: ImageGenerationRequest,
    principal: Principal = Depends(require_api_key),
) -> ImageGenerationJobResponse:
    request_payload = payload.model_copy(update={"lesson_id": payload.lesson_id or lesson_id})
    _assert_book_owner(request_payload.book_id, principal)
    source_chunks_for_payload(request_payload)
    _reserve_heavy_task(IMAGE_GENERATION_TASK)
    job = image_job_store.create(request_payload.book_id)
    _enqueue(_run_generation_job_with_slot, job.job_id, request_payload)
    return _image_job_response(job.job_id)


@router.post("/assets/generate", response_model=ImageGenerationJobResponse)
def generate_asset(
    payload: ImageGenerationRequest,
    principal: Principal = Depends(require_api_key),
) -> ImageGenerationJobResponse:
    _assert_book_owner(payload.book_id, principal)
    source_chunks_for_payload(payload)
    _reserve_heavy_task(IMAGE_GENERATION_TASK)
    job = image_job_store.create(payload.book_id)
    _enqueue(_run_generation_job_with_slot, job.job_id, payload)
    return _image_job_response(job.job_id)


@router.get("/image-generation/jobs/{job_id}", response_model=ImageGenerationJobResponse)
def get_image_generation_job(
    job_id: str,
    principal: Principal = Depends(require_api_key),
) -> ImageGenerationJobResponse:
    record = image_job_store.get(job_id)
    if record is None:
        raise AppError("image_job_not_found", "图片生成任务不存在", status_code=404)
    _assert_book_owner(record.book_id, principal)
    return _image_job_response(job_id)


@router.post("/rag/query", response_model=RagResponse)
def rag_query(
    payload: RagQuery,
    principal: Principal = Depends(require_api_key),
) -> RagResponse:
    _assert_book_owner(payload.book_id, principal)
    _ensure_artifact(payload.book_id, "chunks.jsonl")
    return answer_query(payload)


@router.post("/assignments/{assignment_id}/submit", response_model=AssignmentSubmitResponse)
def submit_assignment_route(
    assignment_id: str,
    payload: AssignmentSubmitRequest,
    principal: Principal = Depends(require_api_key),
) -> AssignmentSubmitResponse:
    _assert_book_owner(payload.book_id, principal)
    _ensure_artifact(payload.book_id, "chunks.jsonl")
    resolved = _check_user_payload(payload.user_id, principal)
    if payload.user_id != resolved:
        payload = payload.model_copy(update={"user_id": resolved})
    return submit_assignment(assignment_id, payload)


@router.post("/assignments/{assignment_id}/diagnose", response_model=DiagnosisResponse)
def diagnose_assignment_route(
    assignment_id: str,
    submission_id: str,
    principal: Principal = Depends(require_api_key),
) -> DiagnosisResponse:
    submission = get_submission(submission_id)
    if submission is None:
        raise AppError("submission_not_found", "submission_id not found", status_code=404)
    _assert_book_owner(submission.book_id, principal)
    if is_strict_mode() and not principal.is_admin and submission.user_id != principal.user_id:
        raise AppError("forbidden_user_mismatch", "无权访问该提交", status_code=403)
    return diagnose_assignment(assignment_id, submission_id)


@router.get("/users/{user_id}/mistakes", response_model=list[MistakeRecord])
def get_mistakes(
    user_id: str,
    book_id: str | None = None,
    principal: Principal = Depends(require_api_key),
) -> list[MistakeRecord]:
    require_user_match(user_id, principal)
    return list_mistakes(user_id=user_id, book_id=book_id)


def _check_user_payload(payload_user_id: str | None, principal: Principal) -> str:
    """Return the user_id that should be recorded for the request, enforcing
    IDOR protection in strict mode."""
    if payload_user_id and payload_user_id != principal.user_id and not principal.is_admin and is_strict_mode():
        raise AppError("forbidden_user_mismatch", "无权以其他用户身份操作", status_code=403)
    return payload_user_id or principal.user_id


@router.post("/books/{book_id}/plan", response_model=StudyPlan)
def create_study_plan(
    book_id: str,
    payload: StudyPlanRequest,
    principal: Principal = Depends(require_api_key),
) -> StudyPlan:
    _ensure_artifact(book_id, "chapters.json")
    resolved = _check_user_payload(payload.user_id, principal)
    if payload.user_id != resolved:
        payload = payload.model_copy(update={"user_id": resolved})
    return create_plan(book_id, payload)


@router.get("/books/{book_id}/plan", response_model=StudyPlan)
def get_study_plan(
    book_id: str,
    user_id: str = "anonymous",
    principal: Principal = Depends(require_api_key),
) -> StudyPlan:
    effective_user = user_id or principal.user_id
    require_user_match(effective_user, principal)
    plan = get_plan(book_id, effective_user)
    if plan is None:
        _ensure_artifact(book_id, "chapters.json")
        plan = create_plan(book_id, StudyPlanRequest(user_id=effective_user))
    return plan


@router.patch("/study-tasks/{task_id}", response_model=StudyTask)
def patch_study_task(
    task_id: str,
    payload: StudyTaskUpdate,
    principal: Principal = Depends(require_api_key),
) -> StudyTask:
    owner = study_plan_service.task_owner(task_id)
    if owner is None:
        raise AppError("study_task_not_found", "study task not found", status_code=404)
    if is_strict_mode() and not principal.is_admin and owner != principal.user_id:
        raise AppError("forbidden_user_mismatch", "无权修改该任务", status_code=403)
    return update_task(task_id, payload)


@router.get("/users/{user_id}/learning-state", response_model=LearningState)
def get_learning_state(
    user_id: str,
    principal: Principal = Depends(require_api_key),
) -> LearningState:
    require_user_match(user_id, principal)
    return learning_state(user_id)
