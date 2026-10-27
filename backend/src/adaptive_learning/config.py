from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


def _optional_bool(name: str) -> bool | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return normalized == "true"


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if not value else int(value)


def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if not value else float(value)


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str
    app_host: str
    app_port: int
    data_dir: Path
    cors_origins: tuple[str, ...]
    primary_extractor: str
    mineru_command: str
    render_dpi: int
    retry_dpi: int
    quality_accept: float
    quality_review: float
    vision_rescue_enabled: bool
    ocr_worker_enabled: bool
    ocr_worker_lease_seconds: int
    ocr_worker_poll_seconds: float
    ocr_retry_delay_seconds: float
    ocr_max_attempts: int
    ocr_backend: str
    llm_provider: str
    llm_https_proxy: str | None
    pucoding_base_url: str
    pucoding_api_key: str
    pucoding_text_model: str
    pucoding_vision_model: str
    deepseek_base_url: str
    deepseek_api_key: str
    deepseek_model: str
    rag_book_id: str
    published_book_ids: tuple[str, ...]
    rag_index_dir: Path | None
    rag_embedding_model_path: Path | None
    rag_reranker_model_path: Path | None
    rag_device: str
    rag_refusal_score_threshold: float
    rag_top_pages: int
    rag_max_evidence: int
    rag_retrieval_budget_ms: int
    rag_use_evidence_planner: bool
    rag_use_semantic_review: bool
    assessment_min_items: int
    assessment_max_items: int
    assessment_stop_uncertainty: float
    open_answer_min_confidence: float
    studio_worker_enabled: bool = True
    ocr_page_quality_accept: float = 0.82
    ocr_page_quality_review: float = 0.66
    ocr_page_structured_review: float = 0.50
    rag_max_output_tokens: int = 10000
    rag_generation_timeout_seconds: int = 120

    teaching_review_provider: str = ""
    teaching_review_model: str = ""
    teaching_review_timeout_seconds: int = 120

    text_thinking_enabled: bool | None = None
    text_reasoning_effort: str | None = None
    diagnostic_allow_grounded_answer_rewrite: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.rag_max_output_tokens <= 10000:
            raise ValueError("RAG_MAX_OUTPUT_TOKENS must be between 1 and 10000")
        if self.rag_generation_timeout_seconds <= 0:
            raise ValueError("RAG_GENERATION_TIMEOUT_SECONDS must be positive")
        if not math.isfinite(self.teaching_review_timeout_seconds) or self.teaching_review_timeout_seconds <= 0:
            raise ValueError("TEACHING_REVIEW_TIMEOUT_SECONDS must be positive and finite")
        if not 0 <= self.quality_review <= self.quality_accept <= 1:
            raise ValueError("OCR quality thresholds require 0 <= review <= accept <= 1")
        if not 0 <= self.ocr_page_structured_review <= self.ocr_page_quality_review <= self.ocr_page_quality_accept <= 1:
            raise ValueError("OCR page thresholds require 0 <= structured <= review <= accept <= 1")
        for name in ("ocr_worker_lease_seconds", "ocr_worker_poll_seconds", "ocr_max_attempts"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if bool(self.teaching_review_provider) != bool(self.teaching_review_model):
            raise ValueError("TEACHING_REVIEW_PROVIDER and TEACHING_REVIEW_MODEL must be set together")
        if self.teaching_review_provider not in {"", "pucoding", "deepseek"}:
            raise ValueError("TEACHING_REVIEW_PROVIDER must be pucoding, deepseek, or unset")
        model_pattern = (r"deepseek-[A-Za-z0-9_.-]{1,100}"
                         if self.teaching_review_provider == "deepseek"
                         else r"(?:gpt|codex)-[A-Za-z0-9_.-]{1,100}")
        if self.teaching_review_model and not re.fullmatch(model_pattern, self.teaching_review_model):
            raise ValueError("TEACHING_REVIEW_MODEL must be a valid explicitly configured provider model name")

    @property
    def teaching_review_base_url(self) -> str:
        return self.deepseek_base_url if self.teaching_review_provider == "deepseek" else self.pucoding_base_url

    @property
    def teaching_review_api_key(self) -> str:
        return self.deepseek_api_key if self.teaching_review_provider == "deepseek" else self.pucoding_api_key

    @property
    def teaching_review_configured(self) -> bool:
        return bool(self.teaching_review_provider and self.teaching_review_model and self.teaching_review_api_key)

    @property
    def teaching_review_identity(self) -> dict[str, str]:
        return {"provider": self.teaching_review_provider, "model": self.teaching_review_model,
                "reasoning_effort": "low"}

    @property
    def _teaching_review_thinking_policy(self) -> str:
        if self.text_thinking_enabled is None:
            return "default"
        return "enabled" if self.text_thinking_enabled else "disabled"

    @property
    def teaching_review_revision(self) -> str:
        # Credentials never enter cache identity or public metadata.
        parts = [self.teaching_review_provider, self.teaching_review_model,
                 self.teaching_review_base_url if self.teaching_review_provider else "", "reasoning=low"]
        if self.teaching_review_provider == "deepseek":
            parts.append("thinking=" + self._teaching_review_thinking_policy)
        return "|".join(parts)

    @property
    def text_base_url(self) -> str:
        if self.llm_provider == "minimax":
            return os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
        return self.pucoding_base_url if self.llm_provider == "pucoding" else self.deepseek_base_url

    @property
    def text_api_key(self) -> str:
        if self.llm_provider == "minimax":
            return os.getenv("MINIMAX_API_KEY", "")
        return self.pucoding_api_key if self.llm_provider == "pucoding" else self.deepseek_api_key

    @property
    def text_model(self) -> str:
        if self.llm_provider == "minimax":
            return os.getenv("MINIMAX_MODEL", "MiniMax-M3")
        return self.pucoding_text_model if self.llm_provider == "pucoding" else self.deepseek_model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    origins = tuple(
        part.strip()
        for part in os.getenv("CORS_ORIGINS", "http://localhost:5174,http://127.0.0.1:5174").split(
            ","
        )
        if part.strip()
    )
    published_book_ids = tuple(
        part.strip()
        for part in os.getenv(
            "PUBLISHED_BOOK_IDS", os.getenv("RAG_BOOK_ID", "biology-required-2")
        ).split(",")
        if part.strip()
    )

    def optional_path(name: str) -> Path | None:
        value = os.getenv(name, "").strip()
        return Path(value).resolve() if value else None

    return Settings(
        rag_max_output_tokens=_int("RAG_MAX_OUTPUT_TOKENS", 10000),
        rag_generation_timeout_seconds=_int("RAG_GENERATION_TIMEOUT_SECONDS", 120),
        studio_worker_enabled=_bool("STUDIO_WORKER_ENABLED", True),
        ocr_page_quality_accept=_float("OCR_PAGE_QUALITY_ACCEPT", 0.82),
        ocr_page_quality_review=_float("OCR_PAGE_QUALITY_REVIEW", 0.66),
        ocr_page_structured_review=_float("OCR_PAGE_STRUCTURED_REVIEW", 0.50),
        app_env=os.getenv("APP_ENV", "development"),
        app_host=os.getenv("APP_HOST", "0.0.0.0"),
        app_port=_int("APP_PORT", 8100),
        data_dir=Path(os.getenv("APP_DATA_DIR", "./data")).resolve(),
        cors_origins=origins,
        primary_extractor=os.getenv("DOCUMENT_PRIMARY_EXTRACTOR", "mineru"),
        mineru_command=os.getenv("MINERU_COMMAND", "mineru"),
        render_dpi=_int("DOCUMENT_RENDER_DPI", 300),
        retry_dpi=_int("DOCUMENT_OCR_RETRY_DPI", 420),
        quality_accept=_float("DOCUMENT_QUALITY_ACCEPT", 0.90),
        quality_review=_float("DOCUMENT_QUALITY_REVIEW", 0.72),
        vision_rescue_enabled=_bool("DOCUMENT_VISION_RESCUE_ENABLED", True),
        ocr_worker_enabled=_bool("OCR_WORKER_ENABLED", False),
        ocr_worker_lease_seconds=_int("OCR_WORKER_LEASE_SECONDS", 90),
        ocr_worker_poll_seconds=_float("OCR_WORKER_POLL_SECONDS", 1),
        ocr_retry_delay_seconds=_float("OCR_RETRY_DELAY_SECONDS", 30),
        ocr_max_attempts=_int("OCR_MAX_ATTEMPTS", 3),
        ocr_backend=os.getenv("OCR_BACKEND", "vlm-auto-engine"),
        llm_provider=os.getenv("LLM_PROVIDER", "rules"),
        teaching_review_provider=os.getenv("TEACHING_REVIEW_PROVIDER", "").strip(),
        teaching_review_model=os.getenv("TEACHING_REVIEW_MODEL", "").strip(),
        teaching_review_timeout_seconds=_int("TEACHING_REVIEW_TIMEOUT_SECONDS", 120),
        llm_https_proxy=os.getenv("LLM_HTTPS_PROXY") or None,
        pucoding_base_url=os.getenv("PUCODING_BASE_URL", "https://pucoding.com/v1"),
        pucoding_api_key=os.getenv("PUCODING_API_KEY", ""),
        pucoding_text_model=os.getenv("PUCODING_TEXT_MODEL", "grok-4-fast"),
        pucoding_vision_model=os.getenv("PUCODING_VISION_MODEL", "grok-4-fast"),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        rag_book_id=os.getenv("RAG_BOOK_ID", "biology-required-2"),
        published_book_ids=published_book_ids,
        rag_index_dir=optional_path("RAG_INDEX_DIR"),
        rag_embedding_model_path=optional_path("RAG_EMBEDDING_MODEL_PATH"),
        rag_reranker_model_path=optional_path("RAG_RERANKER_MODEL_PATH"),
        rag_device=os.getenv("RAG_DEVICE", "auto"),
        rag_refusal_score_threshold=_float("RAG_REFUSAL_SCORE_THRESHOLD", 0),
        rag_top_pages=_int("RAG_TOP_PAGES", 5),
        rag_max_evidence=_int("RAG_MAX_EVIDENCE", 10),
        rag_retrieval_budget_ms=_int("RAG_RETRIEVAL_BUDGET_MS", 2500),
        rag_use_evidence_planner=_bool("RAG_USE_EVIDENCE_PLANNER", False),
        rag_use_semantic_review=_bool("RAG_USE_SEMANTIC_REVIEW", True),
        assessment_min_items=_int("ASSESSMENT_MIN_ITEMS", 5),
        assessment_max_items=_int("ASSESSMENT_MAX_ITEMS", 12),
        assessment_stop_uncertainty=_float("ASSESSMENT_STOP_UNCERTAINTY", 0.035),
        open_answer_min_confidence=_float("ASSESSMENT_OPEN_ANSWER_MIN_CONFIDENCE", 0.78),
        text_thinking_enabled=_optional_bool("TEXT_LLM_THINKING_ENABLED"),
        text_reasoning_effort=os.getenv("TEXT_LLM_REASONING_EFFORT", "").strip() or None,
        diagnostic_allow_grounded_answer_rewrite=_bool(
            "DIAGNOSTIC_ALLOW_GROUNDED_ANSWER_REWRITE", True
        ),
    )
