from __future__ import annotations

from fastapi import APIRouter

from app.core.ai_runtime import ai_runtime_snapshots
from app.core.config import get_settings
from app.schemas.common import HealthResponse, RuntimeCapabilitiesResponse


router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="bookcourse-ai-backend")


@router.get("/runtime-capabilities", response_model=RuntimeCapabilitiesResponse)
def runtime_capabilities() -> RuntimeCapabilitiesResponse:
    settings = get_settings()
    return RuntimeCapabilitiesResponse(
        auth_mode=settings.auth_mode,
        parser_provider=settings.parser_provider,
        ocr_provider=settings.ocr_provider,
        ocr_model=settings.ocr_model,
        ocr_device=settings.ocr_device,
        ocr_quality_profile=settings.ocr_quality_profile,
        ocr_cache_enabled=settings.ocr_cache_enabled,
        ocr_adaptive_retry=settings.ocr_adaptive_retry,
        ocr_text_fallback_enabled=settings.ocr_text_fallback_enabled,
        mineru_backend=settings.mineru_backend,
        mineru_effort=settings.mineru_effort,
        rag_index_provider=settings.rag_index_provider,
        embedding_provider=settings.embedding_provider,
        reranker_provider=settings.reranker_provider,
        reranker_fail_open=settings.reranker_fail_open,
        lesson_provider=settings.llm_provider,
        rag_answer_provider=settings.rag_llm_provider,
        llm_model=settings.deepseek_model if "deepseek" in {settings.llm_provider, settings.rag_llm_provider} else settings.llm_model,
        lesson_model=settings.deepseek_lesson_model if settings.llm_provider == "deepseek" else settings.lesson_llm_model,
        rag_answer_model=settings.deepseek_rag_model if settings.rag_llm_provider == "deepseek" else settings.rag_llm_model,
        ai_max_concurrent=settings.ai_max_concurrent,
        ai_max_retries=settings.ai_max_retries,
        ai_circuit_failure_threshold=settings.ai_circuit_failure_threshold,
        ai_runtime=ai_runtime_snapshots(),
        image_provider=settings.image_provider,
        worker_enabled=settings.use_worker,
    )
