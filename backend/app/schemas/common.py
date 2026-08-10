from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    service: str


class RuntimeCapabilitiesResponse(BaseModel):
    auth_mode: str
    parser_provider: str
    ocr_provider: str
    ocr_model: str
    ocr_device: str
    ocr_quality_profile: str
    ocr_cache_enabled: bool
    ocr_adaptive_retry: bool
    ocr_text_fallback_enabled: bool
    mineru_backend: str
    mineru_effort: str
    rag_index_provider: str
    embedding_provider: str
    reranker_provider: str
    reranker_fail_open: bool
    lesson_provider: str
    rag_answer_provider: str
    llm_model: str
    lesson_model: str
    rag_answer_model: str
    ai_max_concurrent: int
    ai_max_retries: int
    ai_circuit_failure_threshold: int
    ai_runtime: list[dict[str, object]] = Field(default_factory=list)
    image_provider: str
    worker_enabled: bool
