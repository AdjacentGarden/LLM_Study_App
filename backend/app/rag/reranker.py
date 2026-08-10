from __future__ import annotations

from dataclasses import replace
from typing import Protocol, TYPE_CHECKING

from app.core.config import get_settings
from app.core.logging import get_logger
from app.document.chunk_protocol import is_chunk_indexable
from app.rag.embedding import render_chunk_embedding_text
from app.rag.tokenizer import unique_tokens

if TYPE_CHECKING:
    from app.rag.retrieval import RetrievedChunk


_logger = get_logger("app.rag.reranker")


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, candidates: list["RetrievedChunk"], *, top_k: int) -> list["RetrievedChunk"]:
        ...


class HeuristicReranker:
    name = "heuristic"

    def rerank(self, query: str, candidates: list["RetrievedChunk"], *, top_k: int) -> list["RetrievedChunk"]:
        query_tokens = set(unique_tokens(query))
        reranked: list[RetrievedChunk] = []
        for candidate in candidates:
            if not is_chunk_indexable(candidate.chunk):
                continue
            text_tokens = set(
                unique_tokens(
                    render_chunk_embedding_text(candidate.chunk),
                    extra_terms=candidate.chunk.key_concepts,
                )
            )
            overlap = len(query_tokens.intersection(text_tokens))
            overlap_score = overlap / max(len(query_tokens), 1)
            concept_bonus = 0.15 if any(term.lower() in query.lower() for term in candidate.chunk.key_concepts) else 0.0
            blended = min(1.0, overlap_score + concept_bonus + min(candidate.bm25_score / 8, 0.2) + max(candidate.dense_score, 0) * 0.1)
            reranked.append(replace(candidate, rerank_score=blended))
        reranked.sort(key=lambda item: (item.rerank_score, item.score), reverse=True)
        return reranked[:top_k]


class BGERerankerService:
    name = "bge"

    def __init__(self, model_name: str, *, fail_open: bool = True) -> None:
        self.model_name = model_name
        self.fail_open = fail_open
        self._model = None
        self._fallback = HeuristicReranker()

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
            except Exception as exc:
                raise RuntimeError("sentence_transformers is not installed") from exc
            self._model = CrossEncoder(self.model_name)
        return self._model

    def rerank(self, query: str, candidates: list["RetrievedChunk"], *, top_k: int) -> list["RetrievedChunk"]:
        candidates = [candidate for candidate in candidates if is_chunk_indexable(candidate.chunk)]
        try:
            model = self._load_model()
            pairs = [(query, render_chunk_embedding_text(candidate.chunk)) for candidate in candidates]
            scores = model.predict(pairs)
        except Exception as exc:
            _logger.warning(
                "reranker_fallback",
                extra={
                    "event": "reranker_fallback",
                    "provider": self.name,
                    "error": type(exc).__name__,
                },
            )
            if not self.fail_open:
                raise RuntimeError("configured BGE reranker is unavailable") from exc
            return self._fallback.rerank(query, candidates, top_k=top_k)
        reranked = [replace(candidate, rerank_score=float(score)) for candidate, score in zip(candidates, scores, strict=False)]
        reranked.sort(key=lambda item: (item.rerank_score, item.score), reverse=True)
        return reranked[:top_k]


def get_reranker() -> Reranker:
    settings = get_settings()
    if settings.reranker_provider == "bge":
        return BGERerankerService(settings.reranker_model, fail_open=settings.reranker_fail_open)
    return HeuristicReranker()
