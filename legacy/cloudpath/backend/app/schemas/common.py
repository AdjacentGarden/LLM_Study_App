from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    service: str


class ReadinessResponse(BaseModel):
    status: str
    service: str
    checks: dict[str, str] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    user_id: str
    is_admin: bool = False


class AssistantHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[AssistantHistoryMessage] = Field(default_factory=list, max_length=12)
    context_title: str | None = Field(default=None, max_length=300)
    context_description: str | None = Field(default=None, max_length=1200)


class AssistantChatResponse(BaseModel):
    answer: str
    provider: str
    model: str


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
    rag_answer_cache_enabled: bool
    rag_answer_cache_ttl_seconds: int
    rag_answer_cache_max_items: int
    rag_answer_cache: dict[str, object] = Field(default_factory=dict)
    embedding_provider: str
    reranker_provider: str
    reranker_model: str
    reranker_revision: str
    reranker_device: str
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
