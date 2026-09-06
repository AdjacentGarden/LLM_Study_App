from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from pathlib import Path

from fastapi import HTTPException

from ..config import get_settings
from ..llm.client import LLMConfig, OpenAICompatibleClient
from ..rag.backends import TransformerPairReranker, TransformerQueryEncoder
from ..rag.grounded_qa import GroundedAnswerGenerator
from ..rag.index import PersistentRAGIndex, RAGIndexError
from ..rag.service import TextbookQAService


class QAConfigurationError(RuntimeError):
    pass


_service: TextbookQAService | None = None
_service_lock = threading.Lock()
_book_services: OrderedDict[str, TextbookQAService] = OrderedDict()
_book_service_lock = threading.Lock()


def _create_qa_service() -> TextbookQAService:
    settings = get_settings()
    if settings.rag_index_dir is None:
        raise QAConfigurationError("RAG_INDEX_DIR is not configured")
    if settings.rag_embedding_model_path is None:
        raise QAConfigurationError("RAG_EMBEDDING_MODEL_PATH is not configured")
    if settings.rag_reranker_model_path is None:
        raise QAConfigurationError("RAG_RERANKER_MODEL_PATH is not configured")
    if not settings.text_api_key:
        raise QAConfigurationError("Selected text provider API key is not configured")

    encoder = TransformerQueryEncoder(settings.rag_embedding_model_path, device=settings.rag_device)
    reranker = TransformerPairReranker(settings.rag_reranker_model_path, device=settings.rag_device)
    index = PersistentRAGIndex.load(settings.rag_index_dir, encoder=encoder, reranker=reranker)
    client = OpenAICompatibleClient(
        LLMConfig(
            base_url=settings.text_base_url,
            api_key=settings.text_api_key,
            model=settings.text_model,
            timeout_seconds=120,
            proxy_url=settings.llm_https_proxy,
        )
    )
    generator = GroundedAnswerGenerator(
        client,
        refusal_score_threshold=settings.rag_refusal_score_threshold,
        use_evidence_planner=True,
        use_semantic_review=True,
    )
    return TextbookQAService(
        book_id=settings.rag_book_id,
        index=index,
        generator=generator,
        top_pages=settings.rag_top_pages,
        max_evidence=settings.rag_max_evidence,
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
    if book_id == settings.rag_book_id:
        return build_qa_service()
    if book_id not in settings.published_book_ids:
        raise HTTPException(status_code=404, detail="未找到该书的检索索引")
    manifest_path = os.getenv("RAG_BOOK_INDEX_MANIFEST", "")
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        index_path = Path(manifest[book_id])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise QAConfigurationError("Book index manifest is missing or invalid") from error
    # Keep one GPU model pair for all books; only document indexes and answer
    # caches are book-specific. Bound the number of resident CPU indexes.
    primary = build_qa_service()
    with _book_service_lock:
        existing = _book_services.get(book_id)
        if existing is not None:
            _book_services.move_to_end(book_id)
            return existing
        index = PersistentRAGIndex.load(
            index_path, encoder=primary.index.encoder, reranker=primary.index.reranker
        )
        service = TextbookQAService(
            book_id=book_id, index=index, generator=primary.generator,
            top_pages=settings.rag_top_pages, max_evidence=settings.rag_max_evidence,
        )
        _book_services[book_id] = service
        while len(_book_services) > 4:
            _book_services.popitem(last=False)
        return service


def require_qa_service(book_id: str) -> TextbookQAService:
    try:
        return build_book_qa_service(book_id)
    except (QAConfigurationError, RAGIndexError) as error:
        raise HTTPException(status_code=503, detail="教材答疑服务尚未就绪") from error
