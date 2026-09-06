from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout

from pydantic import BaseModel, Field

from ..llm.client import model_time_budget
from .grounded_qa import (
    AnswerStatus,
    EvidenceChunk,
    GroundedAnswerGenerator,
    VerifiedClaim,
)
from .index import PersistentRAGIndex

logger = logging.getLogger(__name__)


class QABusyError(RuntimeError):
    """Bounded admission prevents unbounded GPU/model queues."""


class TextbookQAResult(BaseModel):
    book_id: str
    status: AnswerStatus
    answer: str
    claims: list[VerifiedClaim]
    confidence: float = Field(ge=0, le=1)
    insufficiency_reason: str | None = None
    evidence_pages: list[int]
    retrieval_duration_ms: int = Field(ge=0)
    generation_duration_ms: int = Field(ge=0)
    semantic_checked: bool = False
    cache_hit: bool = False


class TextbookQAService:
    def __init__(
        self,
        *,
        book_id: str,
        index: PersistentRAGIndex,
        generator: GroundedAnswerGenerator,
        top_pages: int = 5,
        max_evidence: int = 10,
        cache_seconds: float = 300,
        cache_size: int = 64,
        max_concurrent: int = 3,
        wait_seconds: float = 150,
    ) -> None:
        if not book_id.strip():
            raise ValueError("book_id must not be empty")
        if cache_seconds < 0 or cache_size < 0 or max_concurrent < 1 or wait_seconds <= 0:
            raise ValueError("invalid QA resource limits")
        self.book_id = book_id
        self.index = index
        self.generator = generator
        self.top_pages = top_pages
        self.max_evidence = max_evidence
        self._ready = False
        self.cache_seconds, self.cache_size = cache_seconds, cache_size
        self.max_concurrent, self.wait_seconds = max_concurrent, wait_seconds
        # Service/index scoped; never shared across books or model configurations.
        self._cache: OrderedDict[str, tuple[float, TextbookQAResult]] = OrderedDict()
        self._inflight: dict[str, Future[TextbookQAResult]] = {}
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self._ready

    def warmup(self) -> None:
        """Load retrieval models before accepting concurrent requests."""
        self.index.search("教材核心概念", top_pages=1, max_evidence=1, per_page=1)
        self._ready = True

    def answer(self, question: str) -> TextbookQAResult:
        key = question.strip()
        if not key or len(key) > 2000:
            raise ValueError("question must contain 1–2000 characters")
        with self._lock:
            now = time.monotonic()
            for expired in [q for q, (until, _) in self._cache.items() if until <= now]:
                del self._cache[expired]
            cached = self._cache.get(key)
            if cached:
                self._cache.move_to_end(key)
                result = cached[1].model_copy(deep=True)
                result.cache_hit = True
                result.retrieval_duration_ms = result.generation_duration_ms = 0
                return result
            future = self._inflight.get(key)
            owner = future is None
            if owner:
                if len(self._inflight) >= self.max_concurrent:
                    raise QABusyError("answer queue is full")
                future = Future()
                self._inflight[key] = future
        assert future is not None
        if not owner:
            try:
                return future.result(timeout=self.wait_seconds).model_copy(deep=True)
            except FutureTimeout as error:
                raise QABusyError("waiting for shared answer timed out") from error
        try:
            result = self._answer(key)
            with self._lock:
                if (
                    result.status == AnswerStatus.SUPPORTED
                    and self.cache_size
                    and self.cache_seconds
                ):
                    self._cache[key] = (
                        time.monotonic() + self.cache_seconds,
                        result.model_copy(deep=True),
                    )
                    while len(self._cache) > self.cache_size:
                        self._cache.popitem(last=False)
            future.set_result(result)
            return result.model_copy(deep=True)
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)

    def _answer(self, question: str) -> TextbookQAResult:
        retrieval_started = time.monotonic()
        retrieval = self.index.search(
            question,
            top_pages=self.top_pages,
            max_evidence=self.max_evidence,
        )
        self._ready = True
        retrieval_duration_ms = round((time.monotonic() - retrieval_started) * 1000)
        logger.info(
            "textbook retrieval completed",
            extra={"duration_ms": retrieval_duration_ms, "evidence_count": len(retrieval.evidence)},
        )
        evidence = [
            EvidenceChunk(
                source_id=f"E{position}",
                page_number=item.page_number,
                text=item.text,
            )
            for position, item in enumerate(retrieval.evidence, start=1)
        ]
        generation_started = time.monotonic()
        with model_time_budget(120):
            generated = self.generator.answer(
                question=question,
                evidence=evidence,
                retrieval_score=retrieval.score,
                lexical_support=retrieval.lexical_support,
            )
        generation_duration_ms = round((time.monotonic() - generation_started) * 1000)
        logger.info(
            "grounded answer generation completed",
            extra={"duration_ms": generation_duration_ms, "claim_count": len(generated.claims)},
        )
        citation_pages = {
            citation.page_number for claim in generated.claims for citation in claim.citations
        }
        return TextbookQAResult(
            book_id=self.book_id,
            status=generated.status,
            answer=generated.answer,
            claims=generated.claims,
            confidence=generated.confidence,
            insufficiency_reason=generated.insufficiency_reason,
            evidence_pages=sorted(citation_pages),
            retrieval_duration_ms=retrieval_duration_ms,
            generation_duration_ms=generation_duration_ms,
            semantic_checked=generated.semantic_checked,
        )
