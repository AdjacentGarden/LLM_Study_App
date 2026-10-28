from __future__ import annotations

import json
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path

from fastapi import HTTPException, Request

from .. import native_catalog
from ..accounts import COOKIE, Accounts
from ..community import CommunityRepository
from ..config import get_settings
from ..ingestion.coverage import load_coverage
from ..ingestion.jobs import SQLiteOCRJobRepository
from ..llm.client import LLMConfig, OpenAICompatibleClient
from ..rag.backends import TransformerPairReranker, TransformerQueryEncoder
from ..rag.grounded_qa import GroundedAnswerGenerator
from ..rag.index import PersistentRAGIndex, RAGIndexError
from ..rag.service import TextbookQAService
from ..rag.upload_index import build_upload_index, published_upload_index


class QAConfigurationError(RuntimeError):
    pass


_service: TextbookQAService | None = None
_service_lock = threading.Lock()
_book_services: OrderedDict[str, TextbookQAService] = OrderedDict()
_book_service_lock = threading.Lock()
_components: tuple[
    TransformerQueryEncoder, TransformerPairReranker, GroundedAnswerGenerator
] | None = None
_components_lock = threading.Lock()
_upload_indexes: OrderedDict[str, tuple[tuple[object, ...], Path]] = OrderedDict()
_upload_index_lock = threading.Lock()


def _book_coverage(data_dir: Path | None, book_id: str) -> dict:
    if data_dir is None:
        return {}
    path = data_dir / "books" / book_id / "ocr/normalized/pages.jsonl"
    return load_coverage(path) if path.is_file() else {}


def upload_components() -> tuple[TransformerQueryEncoder, TransformerPairReranker, GroundedAnswerGenerator]:
    # Preserve the native index API while sharing one model pair without needing
    # a published/default textbook index in upload-only deployments.
    if _service is not None:
        return _service.index.encoder, _service.index.reranker, _service.generator  # type: ignore[return-value]
    return _shared_components()


def prepare_upload_index(book_directory: Path) -> None:
    encoder, _, _ = upload_components()
    build_upload_index(book_directory, encoder)


def _shared_components() -> tuple[
    TransformerQueryEncoder, TransformerPairReranker, GroundedAnswerGenerator
]:
    """Share models without requiring a pre-existing/default textbook index."""
    global _components
    if _components is not None:
        return _components
    with _components_lock:
        if _components is None:
            _components = _create_components()
        return _components


def _create_components() -> tuple[
    TransformerQueryEncoder, TransformerPairReranker, GroundedAnswerGenerator
]:
    settings = get_settings()
    if settings.rag_embedding_model_path is None:
        raise QAConfigurationError("RAG_EMBEDDING_MODEL_PATH is not configured")
    if settings.rag_reranker_model_path is None:
        raise QAConfigurationError("RAG_RERANKER_MODEL_PATH is not configured")
    if not settings.text_api_key:
        raise QAConfigurationError("Selected text provider API key is not configured")

    encoder = TransformerQueryEncoder(settings.rag_embedding_model_path, device=settings.rag_device)
    reranker = TransformerPairReranker(settings.rag_reranker_model_path, device=settings.rag_device)
    client = OpenAICompatibleClient(
        LLMConfig(
            base_url=settings.text_base_url,
            api_key=settings.text_api_key,
            model=settings.text_model,
            timeout_seconds=settings.rag_generation_timeout_seconds,
            max_retries=0,
            proxy_url=settings.llm_https_proxy,
            thinking_enabled=settings.text_thinking_enabled,
            reasoning_effort=settings.text_reasoning_effort,
        )
    )
    generator = GroundedAnswerGenerator(
        client,
        refusal_score_threshold=settings.rag_refusal_score_threshold,
        # The answer prompt already receives the complete bounded evidence set.
        # A separate pre-answer planner added another network/model round trip without
        # improving deterministic citation checks, so production keeps it opt-in.
        use_evidence_planner=settings.rag_use_evidence_planner,
        use_semantic_review=settings.rag_use_semantic_review,
        max_output_tokens=settings.rag_max_output_tokens,
        draft_budget_seconds=settings.rag_generation_timeout_seconds,
    )
    return encoder, reranker, generator


def _create_qa_service() -> TextbookQAService:
    settings = get_settings()
    if settings.rag_index_dir is None:
        raise QAConfigurationError("RAG_INDEX_DIR is not configured")
    encoder, reranker, generator = _shared_components()
    index = PersistentRAGIndex.load(settings.rag_index_dir, encoder=encoder, reranker=reranker)
    return TextbookQAService(
        book_id=settings.rag_book_id,
        index=index,
        generator=generator,
        top_pages=settings.rag_top_pages,
        max_evidence=settings.rag_max_evidence,
        retrieval_budget_seconds=settings.rag_retrieval_budget_ms / 1000,
        generation_budget_seconds=settings.rag_generation_timeout_seconds,
        wait_seconds=settings.rag_generation_timeout_seconds + 5,
    )


def build_qa_service() -> TextbookQAService:
    global _service
    if _service is not None:
        return _service
    with _service_lock:
        if _service is None:
            _service = _create_qa_service()
        return _service


def qa_service_ready() -> bool:
    return _service is not None and _service.ready


def build_book_qa_service(book_id: str) -> TextbookQAService:
    settings = get_settings()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", book_id):
        raise HTTPException(status_code=404, detail="未找到该书的检索索引")
    data_dir = getattr(settings, "data_dir", None)
    upload_path = published_upload_index(data_dir / "books" / book_id) if data_dir else None
    if book_id == native_catalog.BOOK_ID:
        if data_dir is None:
            raise QAConfigurationError("Built-in textbook index needs a data directory")
        encoder, _, _ = upload_components()
        upload_path = native_catalog.prepare_qa_index(data_dir, encoder)
    if upload_path is not None:
        encoder, reranker, generator = upload_components()
        key = f"{book_id}:{upload_path.name}"
        with _book_service_lock:
            existing = _book_services.get(key)
            if existing is not None:
                _book_services.move_to_end(key)
                return existing
            index = PersistentRAGIndex.load(upload_path, encoder=encoder, reranker=reranker)
            service = TextbookQAService(book_id=book_id, index=index, generator=generator,
                source_coverage=_book_coverage(data_dir, book_id),
                top_pages=settings.rag_top_pages, max_evidence=settings.rag_max_evidence,
                generation_budget_seconds=settings.rag_generation_timeout_seconds,
                wait_seconds=settings.rag_generation_timeout_seconds + 5,
                retrieval_budget_seconds=settings.rag_retrieval_budget_ms / 1000)
            _book_services[key] = service
            while len(_book_services) > 4:
                _book_services.popitem(last=False)
            return service
    if book_id == settings.rag_book_id:
        return build_qa_service()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", book_id):
        raise HTTPException(status_code=404, detail="未找到该书的检索索引")
    if book_id in settings.published_book_ids:
        manifest_path = os.getenv("RAG_BOOK_INDEX_MANIFEST", "")
        try:
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            index_path = Path(manifest[book_id])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise QAConfigurationError("Book index manifest is missing or invalid") from error
    else:
        index_path = prepare_uploaded_book_index(book_id)
    # Keep one GPU model pair for all books; only document indexes and answer
    # caches are book-specific. Bound the number of resident CPU indexes.
    if book_id in settings.published_book_ids and settings.rag_index_dir is not None:
        primary = build_qa_service()
        encoder, reranker, generator = primary.index.encoder, primary.index.reranker, primary.generator
    else:
        encoder, reranker, generator = _shared_components()
    with _book_service_lock:
        existing = _book_services.get(book_id)
        if existing is not None and getattr(existing.index, "index_dir", index_path) == index_path:
            _book_services.move_to_end(book_id)
            return existing
        index = PersistentRAGIndex.load(
            index_path, encoder=encoder, reranker=reranker
        )
        service = TextbookQAService(
            book_id=book_id, index=index, generator=generator,
            source_coverage=_book_coverage(data_dir, book_id),
            top_pages=settings.rag_top_pages, max_evidence=settings.rag_max_evidence,
            retrieval_budget_seconds=settings.rag_retrieval_budget_ms / 1000,
            generation_budget_seconds=settings.rag_generation_timeout_seconds,
            wait_seconds=settings.rag_generation_timeout_seconds + 5,
        )
        _book_services[book_id] = service
        while len(_book_services) > 4:
            _book_services.popitem(last=False)
        return service


def prepare_uploaded_book_index(book_id: str) -> Path:
    settings = get_settings()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", book_id):
        raise HTTPException(status_code=404, detail="未找到该书的检索索引")
    jobs = SQLiteOCRJobRepository(settings.data_dir / "state" / "ocr_jobs.sqlite3")
    book = jobs.get_book(book_id)
    job = jobs.get_job(book_id)
    if book is None or job is None or job.status != "ocr_ready":
        raise HTTPException(status_code=409, detail="教材仍在解析，请稍后再试")
    encoder, reranker, _ = _shared_components()
    pages_path = job.output_dir / "normalized" / "pages.jsonl"
    info = pages_path.stat()
    revision = (str(pages_path), info.st_mtime_ns, info.st_size, info.st_ino, id(encoder))
    with _upload_index_lock:
        cached = _upload_indexes.get(book_id)
        if (cached is not None and cached[0] == revision
                and published_upload_index(book.file_path.parent) == cached[1]):
            _upload_indexes.move_to_end(book_id)
            return cached[1]
    path = build_upload_index(book.file_path.parent, encoder)
    # Upload-only deployments have no default index to warm at startup. A real
    # retrieval verifies the query encoder and reranker before preparation can
    # advance; failed warmups remain uncached so the indexing stage can retry.
    index = PersistentRAGIndex.load(path, encoder=encoder, reranker=reranker)
    index.search("教材核心概念", top_pages=1, max_evidence=1, per_page=1)
    with _upload_index_lock:
        _upload_indexes[book_id] = (revision, path)
        while len(_upload_indexes) > 16:
            _upload_indexes.popitem(last=False)
    return path


def require_qa_service(
    book_id: str, request: Request = None,  # type: ignore[assignment]
) -> TextbookQAService:
    # Native routes call directly after workspace/resource authorization.
    # FastAPI dependencies always inject Request and enforce legacy shelf ownership.
    settings = get_settings()
    if (request is not None and book_id != native_catalog.BOOK_ID
            and book_id != settings.rag_book_id and book_id not in settings.published_book_ids):
        repo = CommunityRepository(settings.data_dir / "state" / "community.sqlite3")
        owner = Accounts(repo).request_owner(request)
        if not owner and request.cookies.get(COOKIE):
            raise HTTPException(401, "登录已过期，请重新登录")
        if not owner or not (repo.owns(owner, book_id) or repo.owns_upload(owner, book_id)):
            raise HTTPException(403, "请先将这本教材加入书架")
    try:
        return build_book_qa_service(book_id)
    except (QAConfigurationError, RAGIndexError, OSError, ValueError) as error:
        raise HTTPException(status_code=503, detail="教材答疑服务尚未就绪") from error
