from __future__ import annotations

from hashlib import sha256
import json
import re

from app.rag.audit import now_ms, write_rag_audit
from app.rag.cache import get_or_compute_rag_answer
from app.rag.llm import get_rag_answer_adapter
from app.rag.retrieval import RetrievedChunk, retrieve_chunks
from app.schemas.books import Asset, Citation, Chapter, RagPerformance, RagQuery, RagResponse
from app.services.artifact_store import get_rag_bundle_identity, read_assets, read_chapters


def _chapter_title(chapters: list[Chapter], chapter_id: str) -> str:
    for chapter in chapters:
        if chapter.chapter_id == chapter_id:
            return chapter.source_title or chapter.ai_title
    return chapter_id


def _related_assets(book_id: str, retrieved: list[RetrievedChunk]) -> list[Asset]:
    assets = read_assets(book_id)
    chunk_ids = {item.chunk.chunk_id for item in retrieved}
    matched: list[Asset] = []
    seen: set[str] = set()
    for asset in assets:
        is_direct = bool(chunk_ids.intersection(asset.source_chunk_ids))
        pending_ai = asset.source_type == "ai_generated" and asset.review_status == "pending"
        if not is_direct or pending_ai or asset.asset_id in seen:
            continue
        seen.add(asset.asset_id)
        matched.append(asset)
    return matched[:6]


def _citation_location(item: RetrievedChunk) -> tuple[str, str]:
    chunk = item.chunk
    metadata = chunk.metadata
    source_format = str(metadata.get("source_format") or "").lower().lstrip(".")
    if source_format == "pptx" or metadata.get("slide_number") is not None:
        slide = metadata.get("slide_number") or chunk.page_start
        title = str(metadata.get("slide_title") or "").strip()
        return "slide", f"幻灯片 {slide}" + (f"：{title}" if title else "")
    if source_format == "xlsx" or metadata.get("sheet_name"):
        sheet = str(metadata.get("sheet_name") or f"{chunk.page_start}").strip()
        cell_range = str(metadata.get("cell_range") or "").strip()
        return "sheet", f"工作表 {sheet}" + (f"（{cell_range}）" if cell_range else "")
    if source_format == "docx" or metadata.get("has_stable_page") is False:
        heading = " > ".join(chunk.heading_path)
        block_start = metadata.get("document_block_start")
        block_end = metadata.get("document_block_end")
        block_label = ""
        if block_start is not None:
            block_label = f"块 {block_start}"
            if block_end not in (None, block_start):
                block_label += f"-{block_end}"
        label = " · ".join(part for part in (heading, block_label) if part) or "文档结构位置"
        return "document", label
    if chunk.page_start != chunk.page_end:
        return "page", f"第 {chunk.page_start}-{chunk.page_end} 页"
    return "page", f"第 {chunk.page_start} 页"


def _quote_window(text: str, question: str, limit: int = 360) -> str:
    """Return a compact citation window centered on the strongest query hit."""

    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    terms = sorted(
        set(re.findall(r"[A-Za-z0-9][A-Za-z0-9_.:-]{2,}|[\u4e00-\u9fff]{2,}", question)),
        key=len,
        reverse=True,
    )
    lowered = normalized.casefold()
    match_start = -1
    match_length = 0
    for term in terms:
        index = lowered.find(term.casefold())
        if index >= 0:
            match_start = index
            match_length = len(term)
            break
    if match_start < 0:
        return normalized[:limit].rstrip() + "…"
    start = max(0, match_start - max(48, (limit - match_length) // 2))
    end = min(len(normalized), start + limit)
    start = max(0, end - limit)
    quote = normalized[start:end].strip()
    return ("…" if start else "") + quote + ("…" if end < len(normalized) else "")


def _citations(chapters: list[Chapter], retrieved: list[RetrievedChunk], question: str) -> list[Citation]:
    citations: list[Citation] = []
    for item in retrieved[:3]:
        location_type, location_label = _citation_location(item)
        chunk_source_metadata = {
            key: item.chunk.metadata[key]
            for key in (
                "source_format",
                "source_unit",
                "slide_number",
                "slide_title",
                "sheet_name",
                "cell_range",
                "has_stable_page",
                "document_block_start",
                "document_block_end",
            )
            if key in item.chunk.metadata
        }
        citations.append(Citation(
            chapter_id=item.chunk.chapter_id,
            chapter_title=_chapter_title(chapters, item.chunk.chapter_id),
            page=item.chunk.page_start,
            chunk_id=item.chunk.chunk_id,
            quote=_quote_window(item.chunk.text or "", question),
            score=round(item.score, 6),
            retrieval_method=item.retrieval_method,
            source_type=item.chunk.content_type,
            location_type=location_type,
            location_label=location_label,
            source_metadata={
                **chunk_source_metadata,
                "page_start": item.chunk.page_start,
                "page_end": item.chunk.page_end,
                "rank": item.rank,
                "bm25_score": round(item.bm25_score, 6),
                "dense_score": round(item.dense_score, 6),
                "rerank_score": round(item.rerank_score, 6),
                "index": item.index_name,
                "index_provider": item.index_provider,
                "index_generation": item.index_generation,
                "fallback_reason": item.fallback_reason,
                "embedding": item.embedding_descriptor,
            },
        ))
    return citations


def _confidence(citations: list[Citation], retrieved: list[RetrievedChunk]) -> str:
    if not citations:
        return "low"
    top = retrieved[0] if retrieved else None
    if top and top.rerank_score >= 0.85 and len(citations) >= 2:
        return "high"
    if top and top.rerank_score >= 0.5:
        return "medium"
    return "low"


def _answer_cache_key(payload: RagQuery) -> tuple[str, str | None, str, str]:
    identity = get_rag_bundle_identity(payload.book_id)
    generation = identity.generation or identity.build_id or identity.state
    serialized = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        payload.book_id,
        payload.chapter_id,
        generation,
        sha256(serialized.encode("utf-8")).hexdigest(),
    )


def _answer_query_uncached(payload: RagQuery) -> RagResponse:
    request_started = now_ms()
    started = now_ms()
    retrieved = retrieve_chunks(payload.book_id, payload.question, payload.chapter_id)
    retrieval_ms = now_ms() - started
    chapters = read_chapters(payload.book_id)
    citations = _citations(chapters, retrieved, payload.question)
    adapter = get_rag_answer_adapter()
    answer_started = now_ms()
    answer, prompt = adapter.answer(payload.question, citations, payload.history)
    llm_ms = now_ms() - answer_started
    confidence = _confidence(citations, retrieved)
    related_assets = _related_assets(payload.book_id, retrieved) if citations else []

    retrieval_cache = retrieved[0].cache_hit if retrieved else "none"
    total_ms = now_ms() - request_started
    write_rag_audit(
        payload.book_id,
        {
            "question_length": len(payload.question),
            "chapter_id": payload.chapter_id,
            "retrieval_ms": round(retrieval_ms, 2),
            "llm_ms": round(llm_ms, 2),
            "adapter": adapter.name,
            "citation_count": len(citations),
            "prompt_length": len(prompt),
            "confidence": confidence,
            "retriever": "bm25_vector_rrf_reranker",
            "top_index": retrieved[0].index_name if retrieved else None,
            "top_index_provider": retrieved[0].index_provider if retrieved else None,
            "index_generation": retrieved[0].index_generation if retrieved else None,
            "fallback_reason": retrieved[0].fallback_reason if retrieved else None,
            "embedding": retrieved[0].embedding_descriptor if retrieved else None,
            "top_rerank_score": round(retrieved[0].rerank_score, 6) if retrieved else None,
            "cache_hit": retrieval_cache,
            "total_ms": round(total_ms, 2),
        },
    )
    return RagResponse(
        answer=answer,
        citations=citations,
        related_assets=related_assets,
        confidence=confidence,
        performance=RagPerformance(
            total_ms=round(total_ms, 2),
            retrieval_ms=round(retrieval_ms, 2),
            generation_ms=round(llm_ms, 2),
            response_cache_hit=False,
            retrieval_cache=retrieval_cache,
            retrieved_chunks=len(retrieved),
        ),
    )


def answer_query(payload: RagQuery) -> RagResponse:
    started = now_ms()
    response, cache_hit = get_or_compute_rag_answer(
        _answer_cache_key(payload),
        lambda: _answer_query_uncached(payload),
    )
    if not cache_hit:
        return response
    performance = response.performance or RagPerformance()
    return response.model_copy(update={
        "performance": performance.model_copy(update={
            "total_ms": round(now_ms() - started, 2),
            "retrieval_ms": 0.0,
            "generation_ms": 0.0,
            "response_cache_hit": True,
        }),
    })
