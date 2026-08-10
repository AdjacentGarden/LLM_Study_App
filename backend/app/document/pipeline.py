from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import threading
import time

from app.assets.source_figure_extractor import extract_source_figures
from app.document.chapter_recognizer import recognize_chapters
from app.document.chunker import bind_assets_to_chunks, build_chunks
from app.document.detector import detect_document
from app.document.mineru.exceptions import MinerUStaleResultError
from app.document.mineru.task_store import mineru_task_store
from app.document.page_artifacts import atomic_write_json, write_parsed_document_artifacts
from app.document.parser_report import ParserAttempt, ParserReport, write_parser_report
from app.document.parsers.base import ParseRequest
from app.document.parsers.router import get_configured_parser_name, get_parser_router
from app.document.toc_analyzer import analyze_toc_structure, write_toc_analysis
from app.rag.indexing import publish_index_build, reserve_index_build
from app.schemas.books import Asset, Chapter, ScanResult
from app.services.artifact_store import (
    RagBundleBuild,
    mark_rag_bundle_building,
    write_assets_and_chunks,
    write_chapters_for_build,
)
from app.services.storage import asset_dir


ProgressCallback = Callable[[str, int, str], None]


class DocumentParseQualityError(RuntimeError):
    pass


class _ParserProgressHeartbeat:
    """Keep long MinerU/Paddle operations visible without faking completion."""

    def __init__(self, callback: ProgressCallback | None, *, interval_seconds: float = 30.0) -> None:
        self._callback = callback
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._started = time.monotonic()
        self._last_activity = self._started
        self._last_progress = 18
        self._activity_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="parser-progress-heartbeat", daemon=True)

    def touch(self, progress: int) -> None:
        with self._activity_lock:
            self._last_activity = time.monotonic()
            self._last_progress = max(self._last_progress, progress)

    def __enter__(self) -> "_ParserProgressHeartbeat":
        if self._callback is not None:
            self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            with self._activity_lock:
                inactive_for = time.monotonic() - self._last_activity
                last_progress = self._last_progress
            if inactive_for < self._interval:
                continue
            elapsed = time.monotonic() - self._started
            progress = min(68, max(last_progress, 18 + int(elapsed // self._interval) * 2))
            try:
                assert self._callback is not None
                self._callback("parser_running", progress, "文档解析仍在进行中")
                self.touch(progress)
            except Exception:
                # The worker's main generation guards remain authoritative;
                # a best-effort UI heartbeat must never hide or replace them.
                continue


def _chapter_for_page(chapters: list[Chapter], page: int | None) -> Chapter | None:
    if page is None:
        return None
    candidates = [chapter for chapter in chapters if chapter.page_start <= page <= chapter.page_end]
    return max(candidates, key=lambda item: item.level, default=None)


def _assign_asset_chapters(assets: list[Asset], chapters: list[Chapter]) -> list[Asset]:
    assigned: list[Asset] = []
    for asset in assets:
        chapter = _chapter_for_page(chapters, asset.page)
        assigned.append(asset.model_copy(update={"chapter_id": chapter.chapter_id if chapter else asset.chapter_id}))
    return assigned


def parse_document(
    book_id: str,
    file_path: Path,
    artifact_path: Path,
    on_progress: ProgressCallback | None = None,
    *,
    expected_generation: int | None = None,
    cloudpath_job_id: str | None = None,
) -> ScanResult:
    """Parse one source through MinerU-first routing and publish normalized artifacts.

    The caller may pass a pre-reserved generation.  Every publication boundary
    verifies that generation, so an older worker stops instead of falling back
    or overwriting a newer parse.
    """

    heartbeat: _ParserProgressHeartbeat | None = None

    def progress(stage: str, value: int, message: str) -> None:
        if heartbeat is not None:
            heartbeat.touch(value)
        if on_progress:
            on_progress(stage, value, message)

    def publish_current(action: Callable[[], object]) -> object:
        if expected_generation is None:
            return action()
        return mineru_task_store.commit_if_current(
            book_id,
            expected_generation,
            action,
            cloudpath_job_id=cloudpath_job_id,
        )

    # A new parse generation invalidates the previous RAG evidence from the
    # moment work begins. This fail-closed policy is used consistently for
    # parser, mapper, quality-gate, chapter, chunker, and publication failures;
    # readers never mix an old bundle with new normalized artifacts.
    artifact_path.mkdir(parents=True, exist_ok=True)
    bundle_build = publish_current(lambda: mark_rag_bundle_building(book_id))
    if not isinstance(bundle_build, RagBundleBuild):
        raise RuntimeError("Unable to reserve RAG bundle generation")
    index_build = publish_current(
        lambda: reserve_index_build(
            book_id,
            bundle_build.build_id,
            f"parse:{expected_generation if expected_generation is not None else 'legacy'}",
        )
    )

    progress("detecting", 8, "正在检测文档结构与页面文字层")
    scan = detect_document(book_id, file_path)
    publish_current(
        lambda: atomic_write_json(artifact_path / "scan_result.json", scan.model_dump(mode="json"))
    )

    progress("mineru_running", 18, "正在解析整份教材")
    request = ParseRequest(
        book_id=book_id,
        file_path=file_path,
        artifact_path=artifact_path,
        scan=scan,
        preferred_parser=get_configured_parser_name(),
        expected_generation=expected_generation,
        cloudpath_job_id=cloudpath_job_id,
        progress_callback=progress,
    )

    try:
        heartbeat = _ParserProgressHeartbeat(on_progress)
        with heartbeat:
            parsed = get_parser_router().parse(request)
    except MinerUStaleResultError:
        raise
    except Exception as exc:
        report = ParserReport(
            book_id=book_id,
            requested_parser=request.preferred_parser,
            final_parser="failed",
            parse_generation=expected_generation,
            source_page_count=scan.page_count,
            output_page_count=0,
            attempts=[
                ParserAttempt.failure(
                    parser="router",
                    duration_ms=0,
                    error_code="parser_router_failed",
                    detail=exc.__class__.__name__,
                )
            ],
            missing_pages=list(range(1, scan.page_count + 1)),
            warnings=["parser_router_failed"],
        )
        publish_current(lambda: write_parser_report(artifact_path / "parser_report.json", report))
        raise

    # The unified writer runs once after page-level merging.  Individual
    # parsers cannot overwrite each other's good pages.
    report = ParserReport.model_validate(parsed.metadata["parser_report"])
    scan = parsed.scan.model_copy(
        update={
            "page_count": len(parsed.pages),
            "needs_ocr": any(page.needs_ocr for page in parsed.pages),
        }
    )
    def publish_normalized_parse() -> None:
        write_parsed_document_artifacts(parsed, artifact_path)
        write_parser_report(artifact_path / "parser_report.json", report)
        atomic_write_json(artifact_path / "scan_result.json", scan.model_dump(mode="json"))

    publish_current(publish_normalized_parse)

    page_scores = [page.quality_score if page.quality_score is not None else (1.0 if page.text.strip() else 0.0) for page in parsed.pages]
    document_quality = sum(page_scores) / len(page_scores) if page_scores else 0.0
    unrecoverable = [page.page_number for page in parsed.pages if page.parser == "unrecoverable" or page.needs_ocr]
    low_quality = [page.page_number for page in parsed.pages if (page.quality_score or 0.0) < 0.60]
    if unrecoverable or low_quality or document_quality < 0.75 or report.missing_pages:
        # Do not generate ocr_pending chunks.  The normalized evidence and
        # parser report remain available for diagnosis, while the parse job is
        # correctly marked failed and RAG cannot ingest placeholder prose.
        raise DocumentParseQualityError(
            f"Document quality gate failed (unrecoverable={unrecoverable}, low_quality={low_quality}, score={document_quality:.3f})"
        )

    progress("toc_analyzing", 72, "正在识别目录、印刷页码与标题")
    toc_analysis = analyze_toc_structure(book_id, artifact_path, scan.page_count)
    publish_current(lambda: write_toc_analysis(artifact_path, toc_analysis))
    progress("chapter_generating", 78, "正在生成候选章节层级")
    chapters = recognize_chapters(book_id, file_path.name, artifact_path, scan.page_count)
    publish_current(
        lambda: write_chapters_for_build(
            book_id,
            chapters,
            build_id=bundle_build.build_id,
            preserve_original=True,
        )
    )

    progress("asset_extracting", 84, "正在整理教材图表与原文内容")
    assets = _assign_asset_chapters(parsed.assets, chapters)
    if file_path.suffix.lower() == ".pdf" and not assets and not parsed.metadata.get("mineru_backend"):
        assets = extract_source_figures(book_id, file_path, asset_dir(book_id), chapters)
    progress("rag_chunking", 90, "正在构建可检索的课程内容")
    chunks = build_chunks(book_id, artifact_path, chapters, assets=assets)
    chunks = bind_assets_to_chunks(chunks, assets)
    publish_current(
        lambda: write_assets_and_chunks(
            book_id,
            assets,
            chunks,
            build_id=bundle_build.build_id,
        )
    )
    progress("rag_indexing", 94, "正在生成并发布课程检索索引")
    publish_index_build(index_build, chunks)
    progress("course_ready", 98, "课程资料已就绪，等待确认目录")
    return scan
