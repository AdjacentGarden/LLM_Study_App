from __future__ import annotations

from pathlib import Path
from time import monotonic
from uuid import uuid4

from fastapi import APIRouter, Depends, Response

from app.core.ai_runtime import ai_runtime_snapshots
from app.core.config import get_settings
from app.core.auth import Principal, require_api_key
from app.document.mineru.client import MinerUClient
from app.document.mineru.exceptions import MinerUError
from app.document.mineru.models import MinerUClientConfig
from app.rag.cache import rag_answer_cache_snapshot
from app.schemas.common import HealthResponse, ReadinessResponse, RuntimeCapabilitiesResponse, SessionResponse
from app.services.persistence import state_dir, validate_persisted_state


router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="bookcourse-ai-backend")


@router.get("/session", response_model=SessionResponse)
def session(principal: Principal = Depends(require_api_key)) -> SessionResponse:
    """Expose only the identity already verified by the trusted edge."""

    return SessionResponse(user_id=principal.user_id, is_admin=principal.is_admin)


@router.get("/ready", response_model=ReadinessResponse)
def readiness(response: Response) -> ReadinessResponse:
    """Dependency-aware readiness probe for load balancers and releases."""

    settings = get_settings()
    checks: dict[str, str] = {}

    try:
        path: Path = state_dir()
        probe = path / f".readiness-{uuid4().hex}.probe"
        try:
            probe.write_text("ok", encoding="utf-8")
        finally:
            probe.unlink(missing_ok=True)
        checks["storage"] = "ok"
    except OSError as exc:
        checks["storage"] = f"failed:{exc.__class__.__name__}"

    corrupt_state = validate_persisted_state()
    checks["persistent_state"] = (
        "ok" if not corrupt_state else "failed:corrupt:" + ",".join(corrupt_state)
    )

    if settings.auth_mode != "strict":
        checks["authentication"] = "ok"
    elif not settings.api_key:
        checks["authentication"] = "failed:api_key_missing"
    elif settings.environment == "production" and not settings.trusted_proxy_token:
        checks["authentication"] = "failed:trusted_proxy_missing"
    else:
        checks["authentication"] = "ok"
    if settings.rag_index_provider == "pgvector":
        if not settings.database_url:
            checks["vector_store"] = "failed:database_url_missing"
        else:
            try:
                import psycopg

                with psycopg.connect(settings.database_url, connect_timeout=1) as connection:
                    row = connection.execute(
                        """
                        select
                          to_regclass('public.rag_index_state') is not null,
                          to_regclass('public.rag_chunk_vectors') is not null,
                          exists(select 1 from pg_extension where extname = 'vector')
                        """
                    ).fetchone()
                checks["vector_store"] = "ok" if row and all(row) else "failed:schema_missing"
            except Exception as exc:
                checks["vector_store"] = f"failed:{exc.__class__.__name__}"
    else:
        checks["vector_store"] = "ok"

    if settings.parser_provider == "mineru":
        try:
            config = MinerUClientConfig.from_settings(settings).model_copy(
                update={
                    "connect_timeout_seconds": min(settings.mineru_connect_timeout_seconds, 0.5),
                    "request_timeout_seconds": min(settings.mineru_request_timeout_seconds, 1.0),
                    "total_timeout_seconds": 2.0,
                    "max_retries": 0,
                }
            )
            with MinerUClient(config) as client:
                client.health(deadline=monotonic() + 2.0)
            checks["parser"] = "ok"
        except (MinerUError, OSError, ValueError) as exc:
            checks["parser"] = f"failed:{exc.__class__.__name__}"
    else:
        checks["parser"] = "ok"

    if settings.environment == "production":
        checks["worker"] = "ok" if settings.use_worker else "failed:worker_disabled"

        provider_failures: list[str] = []
        if settings.embedding_provider == "hashing":
            provider_failures.append("embedding_hashing")
        if settings.llm_provider == "template":
            provider_failures.append("lesson_template")
        if settings.rag_llm_provider == "template":
            provider_failures.append("rag_template")
        if settings.image_provider == "mock":
            provider_failures.append("image_mock")
        if "deepseek" in {settings.llm_provider, settings.rag_llm_provider} and not settings.deepseek_api_key:
            provider_failures.append("deepseek_key_missing")
        if "openai_compatible" in {settings.llm_provider, settings.rag_llm_provider} and not settings.llm_api_url:
            provider_failures.append("llm_endpoint_missing")
        if settings.image_provider == "openai_compatible" and not settings.image_api_url:
            provider_failures.append("image_endpoint_missing")
        checks["production_providers"] = (
            "ok" if not provider_failures else "failed:" + ",".join(provider_failures)
        )

    ready = all(value == "ok" for value in checks.values())
    if not ready:
        response.status_code = 503
    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        service="bookcourse-ai-backend",
        checks=checks,
    )


@router.get(
    "/runtime-capabilities",
    response_model=RuntimeCapabilitiesResponse,
    dependencies=[Depends(require_api_key)],
)
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
        rag_answer_cache_enabled=settings.rag_cache_enabled,
        rag_answer_cache_ttl_seconds=settings.rag_cache_ttl_seconds,
        rag_answer_cache_max_items=settings.rag_answer_cache_max_items,
        rag_answer_cache=rag_answer_cache_snapshot(),
        embedding_provider=settings.embedding_provider,
        reranker_provider=settings.reranker_provider,
        reranker_model=settings.reranker_model,
        reranker_revision=settings.reranker_revision,
        reranker_device=settings.reranker_device,
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
