from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import os
import secrets


# Stage 0 froze the exact BGE-M3 snapshot used by the embedding protocol.
# Query and indexing code must use the same immutable snapshot; accepting a
# floating ``main`` revision would make persisted vectors unauditable.
BGE_M3_MODEL = "BAAI/bge-m3"
BGE_M3_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
BGE_M3_DIMENSIONS = 1024
BGE_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
BGE_RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.environ.get(name)
    try:
        value = float(raw) if raw is not None else default
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _env_choice(name: str, default: str, choices: set[str]) -> str:
    value = os.environ.get(name, default).strip().lower()
    if value not in choices:
        allowed = ", ".join(sorted(choices))
        raise ValueError(f"{name} must be one of: {allowed}")
    return value


def _looks_like_placeholder(value: str | None) -> bool:
    normalized = (value or "").strip().upper()
    return normalized.startswith("REQUIRED_") or normalized.startswith("REPLACE_")


class Settings:
    def __init__(self) -> None:
        self.environment = os.environ.get("BOOKCOURSE_ENVIRONMENT", "development").strip().lower()
        if self.environment not in {"development", "test", "production"}:
            raise ValueError(
                "BOOKCOURSE_ENVIRONMENT must be one of: development, test, production"
            )
        self.docs_enabled = _env_bool("BOOKCOURSE_DOCS_ENABLED", self.environment != "production")
        root = os.environ.get("BOOKCOURSE_STORAGE_ROOT")
        self.storage_root = Path(root) if root else Path.cwd() / "data"
        origins = os.environ.get("BOOKCOURSE_ALLOWED_ORIGINS")
        self.allowed_origins = (
            [item.strip() for item in origins.split(",") if item.strip()]
            if origins
            else [
                "http://127.0.0.1:5173",
                "http://localhost:5173",
                "http://127.0.0.1:5174",
                "http://localhost:5174",
                "http://127.0.0.1:5175",
                "http://localhost:5175",
            ]
        )
        self.allow_credentials = _env_bool("BOOKCOURSE_ALLOW_CREDENTIALS", False)
        self.api_key = os.environ.get("BOOKCOURSE_API_KEY")
        self.trusted_proxy_token = os.environ.get("BOOKCOURSE_TRUSTED_PROXY_TOKEN")
        raw_auth_mode = os.environ.get("BOOKCOURSE_AUTH_MODE", "strict").strip().lower()
        if raw_auth_mode == "api_key":
            raw_auth_mode = "strict"
        if raw_auth_mode not in {"strict", "optional"}:
            raw_auth_mode = "strict"
        self.auth_mode = raw_auth_mode
        if self.environment == "production" and self.auth_mode != "strict":
            raise ValueError("BOOKCOURSE_AUTH_MODE must be strict in production")
        self.api_key_required = self.auth_mode == "strict" or bool(self.api_key)
        self.default_user_id = os.environ.get("BOOKCOURSE_DEFAULT_USER_ID", "local_user")
        self.persist_state = _env_bool("BOOKCOURSE_PERSIST_STATE", True)
        self.global_rate_per_minute = _env_int("BOOKCOURSE_GLOBAL_RATE_PER_MINUTE", 120, minimum=1)
        self.write_rate_per_minute = _env_int("BOOKCOURSE_WRITE_RATE_PER_MINUTE", 30, minimum=1)
        self.rate_limit_max_buckets = _env_int("BOOKCOURSE_RATE_LIMIT_MAX_BUCKETS", 10_000, minimum=100)
        self.rate_limit_bucket_ttl_seconds = _env_int(
            "BOOKCOURSE_RATE_LIMIT_BUCKET_TTL_SECONDS",
            600,
            minimum=60,
        )
        self.use_worker = _env_bool("BOOKCOURSE_USE_WORKER", True)
        self.max_upload_bytes = int(os.environ.get("BOOKCOURSE_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
        self.max_pdf_pages = int(os.environ.get("BOOKCOURSE_MAX_PDF_PAGES", "500"))
        self.community_download_timeout_seconds = _env_float(
            "BOOKCOURSE_COMMUNITY_DOWNLOAD_TIMEOUT_SECONDS",
            45.0,
            minimum=1.0,
        )
        self.max_pdf_render_pixels = _env_int(
            "BOOKCOURSE_MAX_PDF_RENDER_PIXELS",
            40_000_000,
            minimum=1,
        )
        self.max_image_pixels = int(os.environ.get("BOOKCOURSE_MAX_IMAGE_PIXELS", str(40_000_000)))
        self.max_zip_entries = _env_int("BOOKCOURSE_MAX_ZIP_ENTRIES", 10_000, minimum=1)
        self.max_zip_entry_uncompressed_bytes = _env_int(
            "BOOKCOURSE_MAX_ZIP_ENTRY_UNCOMPRESSED_BYTES",
            64 * 1024 * 1024,
            minimum=1,
        )
        self.max_zip_total_uncompressed_bytes = _env_int(
            "BOOKCOURSE_MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES",
            512 * 1024 * 1024,
            minimum=1,
        )
        self.max_zip_compression_ratio = _env_float(
            "BOOKCOURSE_MAX_ZIP_COMPRESSION_RATIO",
            100.0,
            minimum=1.0,
        )
        self.max_office_xml_bytes = _env_int(
            "BOOKCOURSE_MAX_OFFICE_XML_BYTES",
            16 * 1024 * 1024,
            minimum=1,
        )
        self.max_ooxml_relationships = _env_int(
            "BOOKCOURSE_MAX_OOXML_RELATIONSHIPS",
            10_000,
            minimum=1,
        )
        self.max_pptx_slides = _env_int("BOOKCOURSE_MAX_PPTX_SLIDES", 500, minimum=1)
        self.max_xlsx_sheets = _env_int("BOOKCOURSE_MAX_XLSX_SHEETS", 256, minimum=1)
        self.max_docx_resources = _env_int("BOOKCOURSE_MAX_DOCX_RESOURCES", 2_000, minimum=1)
        self.office_validation_timeout_seconds = _env_float(
            "BOOKCOURSE_OFFICE_VALIDATION_TIMEOUT_SECONDS",
            15.0,
            minimum=0.05,
        )
        self.parse_max_concurrent = int(os.environ.get("BOOKCOURSE_PARSE_MAX_CONCURRENT", "2"))
        self.parse_rate_limit_per_minute = int(os.environ.get("BOOKCOURSE_PARSE_RATE_LIMIT_PER_MINUTE", "12"))
        self.lesson_build_max_concurrent = int(os.environ.get("BOOKCOURSE_LESSON_BUILD_MAX_CONCURRENT", "2"))
        self.lesson_build_rate_limit_per_minute = int(os.environ.get("BOOKCOURSE_LESSON_BUILD_RATE_LIMIT_PER_MINUTE", "12"))
        self.image_generation_max_concurrent = int(os.environ.get("BOOKCOURSE_IMAGE_GENERATION_MAX_CONCURRENT", "2"))
        self.image_generation_rate_limit_per_minute = int(os.environ.get("BOOKCOURSE_IMAGE_GENERATION_RATE_LIMIT_PER_MINUTE", "20"))
        self.admin_token = os.environ.get("BOOKCOURSE_ADMIN_TOKEN")
        self.image_provider = _env_choice(
            "BOOKCOURSE_IMAGE_PROVIDER",
            "mock",
            {"mock", "openai_compatible"},
        )
        self.image_api_url = os.environ.get("BOOKCOURSE_IMAGE_API_URL")
        self.image_api_key = os.environ.get("BOOKCOURSE_IMAGE_API_KEY")
        self.image_provider_max_response_bytes = _env_int(
            "BOOKCOURSE_IMAGE_PROVIDER_MAX_RESPONSE_BYTES",
            20 * 1024 * 1024,
            minimum=1024,
        )
        self.database_url = os.environ.get("BOOKCOURSE_DATABASE_URL")
        self.parser_provider = _env_choice(
            "BOOKCOURSE_PARSER_PROVIDER",
            "mineru",
            {"auto", "pymupdf", "marker", "mineru", "ocr"},
        )
        self.marker_endpoint = os.environ.get("BOOKCOURSE_MARKER_ENDPOINT")
        self.marker_command = os.environ.get("BOOKCOURSE_MARKER_COMMAND")
        self.mineru_endpoint = os.environ.get("BOOKCOURSE_MINERU_ENDPOINT", "http://127.0.0.1:8001").strip()
        self.mineru_command = os.environ.get("BOOKCOURSE_MINERU_COMMAND")
        self.mineru_backend = os.environ.get("BOOKCOURSE_MINERU_BACKEND", "pipeline").strip().lower()
        self.mineru_effort = os.environ.get("BOOKCOURSE_MINERU_EFFORT", "medium").strip().lower()
        self.mineru_parse_method = os.environ.get("BOOKCOURSE_MINERU_PARSE_METHOD", "auto").strip().lower()
        self.mineru_language = os.environ.get("BOOKCOURSE_MINERU_LANGUAGE", "ch").strip() or "ch"
        self.mineru_timeout_seconds = _env_float("BOOKCOURSE_MINERU_TIMEOUT_SECONDS", 900.0, minimum=1.0)
        self.mineru_connect_timeout_seconds = _env_float("BOOKCOURSE_MINERU_CONNECT_TIMEOUT_SECONDS", 10.0, minimum=0.1)
        self.mineru_request_timeout_seconds = _env_float("BOOKCOURSE_MINERU_REQUEST_TIMEOUT_SECONDS", 60.0, minimum=1.0)
        self.mineru_poll_interval_seconds = _env_float("BOOKCOURSE_MINERU_POLL_INTERVAL_SECONDS", 2.0, minimum=0.05)
        self.mineru_max_retries = _env_int("BOOKCOURSE_MINERU_MAX_RETRIES", 2, minimum=0)
        self.mineru_retry_backoff_seconds = _env_float("BOOKCOURSE_MINERU_RETRY_BACKOFF_SECONDS", 0.5, minimum=0.0)
        self.mineru_retry_max_backoff_seconds = _env_float("BOOKCOURSE_MINERU_RETRY_MAX_BACKOFF_SECONDS", 10.0, minimum=0.0)
        self.mineru_max_result_bytes = _env_int(
            "BOOKCOURSE_MINERU_MAX_RESULT_BYTES",
            256 * 1024 * 1024,
            minimum=1024,
        )
        self.mineru_formula_enable = _env_bool("BOOKCOURSE_MINERU_FORMULA_ENABLE", True)
        self.mineru_table_enable = _env_bool("BOOKCOURSE_MINERU_TABLE_ENABLE", True)
        self.mineru_image_analysis = _env_bool("BOOKCOURSE_MINERU_IMAGE_ANALYSIS", True)
        self.mineru_return_images = _env_bool("BOOKCOURSE_MINERU_RETURN_IMAGES", True)
        self.layout_provider = _env_choice(
            "BOOKCOURSE_LAYOUT_PROVIDER",
            "opencv",
            {"opencv", "surya"},
        )
        # Chunk V2 parameters are frozen by the Stage 0 acceptance protocol.
        # FrozenChunkConfig performs the cross-field invariant checks at the
        # point where the chunker consumes them.
        self.chunk_target_tokens = _env_int("BOOKCOURSE_CHUNK_TARGET_TOKENS", 450, minimum=1)
        self.chunk_max_tokens = _env_int("BOOKCOURSE_CHUNK_MAX_TOKENS", 700, minimum=1)
        self.chunk_min_tokens = _env_int("BOOKCOURSE_CHUNK_MIN_TOKENS", 120, minimum=1)
        self.chunk_overlap_tokens = _env_int("BOOKCOURSE_CHUNK_OVERLAP_TOKENS", 80, minimum=0)
        self.chunk_atomic_content_hard_max_tokens = _env_int(
            "BOOKCOURSE_CHUNK_ATOMIC_CONTENT_HARD_MAX_TOKENS",
            900,
            minimum=1,
        )
        # Short alias retained for callers that do not need to distinguish
        # atomic content from the ordinary maximum.
        self.chunk_atomic_hard_max_tokens = self.chunk_atomic_content_hard_max_tokens
        self.chunk_quality_threshold = _env_float(
            "BOOKCOURSE_CHUNK_QUALITY_THRESHOLD",
            0.45,
            minimum=0.0,
        )
        self.chunk_version = os.environ.get("BOOKCOURSE_CHUNK_VERSION", "v2").strip() or "v2"
        self.embedding_provider = _env_choice(
            "BOOKCOURSE_EMBEDDING_PROVIDER",
            "hashing",
            {"hashing", "local_hashing", "bge_m3", "bge-m3"},
        )
        self.bge_m3_model = os.environ.get("BOOKCOURSE_BGE_M3_MODEL", BGE_M3_MODEL).strip() or BGE_M3_MODEL
        configured_revision = os.environ.get("BOOKCOURSE_BGE_M3_REVISION", BGE_M3_REVISION).strip()
        if configured_revision != BGE_M3_REVISION:
            raise ValueError(
                "BOOKCOURSE_BGE_M3_REVISION must match the Stage 0 frozen revision "
                f"{BGE_M3_REVISION}"
            )
        self.bge_m3_revision = BGE_M3_REVISION
        self.embedding_version = os.environ.get("BOOKCOURSE_EMBEDDING_VERSION", "v1").strip() or "v1"
        self.embedding_device = os.environ.get("BOOKCOURSE_EMBEDDING_DEVICE", "auto").strip().lower() or "auto"
        self.embedding_batch_size = _env_int("BOOKCOURSE_EMBEDDING_BATCH_SIZE", 16, minimum=1)
        self.embedding_dimensions = _env_int(
            "BOOKCOURSE_EMBEDDING_DIMENSIONS",
            BGE_M3_DIMENSIONS,
            minimum=1,
        )
        self.rag_index_provider = _env_choice(
            "BOOKCOURSE_RAG_INDEX_PROVIDER",
            "pgvector",
            {"artifact", "pgvector", "faiss", "chroma", "milvus"},
        )
        self.vector_top_k = _env_int("BOOKCOURSE_VECTOR_TOP_K", 80, minimum=1)
        self.bm25_top_k = _env_int("BOOKCOURSE_BM25_TOP_K", 80, minimum=1)
        self.rerank_input_k = _env_int("BOOKCOURSE_RERANK_INPUT_K", 30, minimum=1)
        self.final_context_k = _env_int("BOOKCOURSE_FINAL_CONTEXT_K", 5, minimum=1)
        self.reranker_provider = _env_choice(
            "BOOKCOURSE_RERANKER_PROVIDER",
            "heuristic",
            {"heuristic", "bge"},
        )
        self.reranker_model = (
            os.environ.get("BOOKCOURSE_RERANKER_MODEL", BGE_RERANKER_MODEL).strip()
            or BGE_RERANKER_MODEL
        )
        configured_reranker_revision = os.environ.get(
            "BOOKCOURSE_RERANKER_REVISION",
            BGE_RERANKER_REVISION,
        ).strip()
        if configured_reranker_revision != BGE_RERANKER_REVISION:
            raise ValueError(
                "BOOKCOURSE_RERANKER_REVISION must match the frozen revision "
                f"{BGE_RERANKER_REVISION}"
            )
        self.reranker_revision = BGE_RERANKER_REVISION
        self.reranker_device = (
            os.environ.get("BOOKCOURSE_RERANKER_DEVICE", "auto").strip().lower()
            or "auto"
        )
        self.reranker_fail_open = _env_bool("BOOKCOURSE_RERANKER_FAIL_OPEN", True)
        self.rag_cache_enabled = os.environ.get("BOOKCOURSE_RAG_CACHE_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
        self.rag_cache_max_books = _env_int("BOOKCOURSE_RAG_CACHE_MAX_BOOKS", 8, minimum=1)
        self.rag_cache_ttl_seconds = _env_int("BOOKCOURSE_RAG_CACHE_TTL_SECONDS", 600, minimum=0)
        self.rag_answer_cache_max_items = _env_int("BOOKCOURSE_RAG_ANSWER_CACHE_MAX_ITEMS", 256, minimum=1)
        self.rag_llm_provider = _env_choice(
            "BOOKCOURSE_RAG_LLM_PROVIDER",
            "template",
            {"template", "openai_compatible", "deepseek"},
        )
        self.llm_provider = _env_choice(
            "BOOKCOURSE_LLM_PROVIDER",
            "template",
            {"template", "openai_compatible", "deepseek"},
        )
        self.llm_api_url = os.environ.get("BOOKCOURSE_LLM_API_URL")
        self.llm_api_key = os.environ.get("BOOKCOURSE_LLM_API_KEY")
        self.llm_model = os.environ.get("BOOKCOURSE_LLM_MODEL", "gpt-4.1-mini")
        self.lesson_llm_model = os.environ.get("BOOKCOURSE_LESSON_LLM_MODEL", self.llm_model).strip() or self.llm_model
        self.rag_llm_model = os.environ.get("BOOKCOURSE_RAG_LLM_MODEL", self.llm_model).strip() or self.llm_model
        self.llm_timeout_seconds = _env_float("BOOKCOURSE_LLM_TIMEOUT_SECONDS", 60.0, minimum=1.0)
        self.llm_max_input_chars = _env_int("BOOKCOURSE_LLM_MAX_INPUT_CHARS", 120_000, minimum=1_000)
        self.llm_max_output_tokens = _env_int("BOOKCOURSE_LLM_MAX_OUTPUT_TOKENS", 4_096, minimum=128)
        # Shared controls for all remote LLM calls.  Keeping these independent
        # from the HTTP adapter makes provider changes operationally safe.
        self.ai_max_concurrent = _env_int("BOOKCOURSE_AI_MAX_CONCURRENT", 4, minimum=1)
        self.ai_queue_timeout_seconds = _env_float("BOOKCOURSE_AI_QUEUE_TIMEOUT_SECONDS", 5.0, minimum=0.05)
        self.ai_max_retries = _env_int("BOOKCOURSE_AI_MAX_RETRIES", 2, minimum=0)
        self.ai_retry_backoff_seconds = _env_float("BOOKCOURSE_AI_RETRY_BACKOFF_SECONDS", 0.5, minimum=0.0)
        self.ai_retry_max_backoff_seconds = _env_float("BOOKCOURSE_AI_RETRY_MAX_BACKOFF_SECONDS", 8.0, minimum=0.0)
        self.ai_retry_transport_errors = _env_bool("BOOKCOURSE_AI_RETRY_TRANSPORT_ERRORS", False)
        self.ai_circuit_failure_threshold = _env_int("BOOKCOURSE_AI_CIRCUIT_FAILURE_THRESHOLD", 5, minimum=1)
        self.ai_circuit_reset_seconds = _env_float("BOOKCOURSE_AI_CIRCUIT_RESET_SECONDS", 30.0, minimum=0.1)
        self.ai_max_response_bytes = _env_int("BOOKCOURSE_AI_MAX_RESPONSE_BYTES", 2 * 1024 * 1024, minimum=1024)
        self.ai_cache_enabled = _env_bool("BOOKCOURSE_AI_CACHE_ENABLED", True)
        self.ai_cache_ttl_seconds = _env_int("BOOKCOURSE_AI_CACHE_TTL_SECONDS", 300, minimum=0)
        self.ai_cache_max_items = _env_int("BOOKCOURSE_AI_CACHE_MAX_ITEMS", 256, minimum=1)
        self.deepseek_api_url = os.environ.get("BOOKCOURSE_DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
        self.deepseek_api_key = os.environ.get("BOOKCOURSE_DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        self.deepseek_model = os.environ.get("BOOKCOURSE_DEEPSEEK_MODEL", "deepseek-v4-flash")
        self.deepseek_lesson_model = os.environ.get("BOOKCOURSE_DEEPSEEK_LESSON_MODEL", self.deepseek_model).strip() or self.deepseek_model
        self.deepseek_rag_model = os.environ.get("BOOKCOURSE_DEEPSEEK_RAG_MODEL", self.deepseek_model).strip() or self.deepseek_model
        self.deepseek_thinking = os.environ.get("BOOKCOURSE_DEEPSEEK_THINKING", "disabled").strip().lower()
        if self.deepseek_thinking not in {"enabled", "disabled"}:
            self.deepseek_thinking = "disabled"
        self.ocr_provider = _env_choice(
            "BOOKCOURSE_OCR_PROVIDER",
            "paddleocr-vl",
            {"mock", "paddle", "paddleocr", "paddleocr-vl", "paddleocr_vl", "paddlevl", "paddle-vl"},
        )
        self.ocr_language = os.environ.get("BOOKCOURSE_OCR_LANGUAGE", "ch")
        default_ocr_model = (
            "PaddleOCR-VL-1.6"
            if self.ocr_provider in {"paddleocr-vl", "paddleocr_vl", "paddlevl", "paddle-vl"}
            else "PP-OCRv6-medium"
        )
        self.ocr_model = os.environ.get("BOOKCOURSE_OCR_MODEL", default_ocr_model).strip() or default_ocr_model
        self.ocr_vl_pipeline_version = os.environ.get("BOOKCOURSE_OCR_VL_PIPELINE_VERSION", "v1.6").strip().lower()
        if self.ocr_vl_pipeline_version not in {"v1", "v1.5", "v1.6"}:
            self.ocr_vl_pipeline_version = "v1.6"
        self.ocr_vl_backend = os.environ.get("BOOKCOURSE_OCR_VL_BACKEND", "").strip().lower()
        self.ocr_vl_server_url = os.environ.get("BOOKCOURSE_OCR_VL_SERVER_URL", "").strip()
        self.ocr_vl_api_key = os.environ.get("BOOKCOURSE_OCR_VL_API_KEY")
        self.ocr_vl_max_concurrency = _env_int("BOOKCOURSE_OCR_VL_MAX_CONCURRENCY", 4, minimum=1)
        # PaddleOCR is a safety fallback.  Keep it on CPU by default so it can
        # recover while MinerU owns the CUDA device; operators may explicitly
        # select e.g. ``gpu:0`` when a compatible Paddle GPU runtime exists.
        self.ocr_device = os.environ.get("BOOKCOURSE_OCR_DEVICE", "cpu").strip().lower() or "cpu"
        self.ocr_enable_mkldnn = _env_bool("BOOKCOURSE_OCR_ENABLE_MKLDNN", False)
        # Paddle inference can retain the GIL for several seconds on Windows.
        # Keep it in a persistent child process so parse progress heartbeats,
        # generation guards, and API cancellation checks remain responsive.
        self.ocr_process_isolation = _env_bool("BOOKCOURSE_OCR_PROCESS_ISOLATION", True)
        self.ocr_worker_startup_timeout_seconds = _env_float(
            "BOOKCOURSE_OCR_WORKER_STARTUP_TIMEOUT_SECONDS",
            120.0,
            minimum=1.0,
        )
        self.ocr_recognition_timeout_seconds = _env_float(
            "BOOKCOURSE_OCR_RECOGNITION_TIMEOUT_SECONDS",
            300.0,
            minimum=1.0,
        )
        self.ocr_quality_profile = os.environ.get("BOOKCOURSE_OCR_QUALITY_PROFILE", "balanced").strip().lower()
        if self.ocr_quality_profile not in {"fast", "balanced", "quality"}:
            self.ocr_quality_profile = "balanced"
        default_render_zoom = {"fast": 1.5, "balanced": 2.0, "quality": 2.5}[self.ocr_quality_profile]
        self.ocr_render_zoom = _env_float("BOOKCOURSE_OCR_RENDER_ZOOM", default_render_zoom, minimum=1.0)
        self.ocr_adaptive_retry = _env_bool("BOOKCOURSE_OCR_ADAPTIVE_RETRY", True)
        self.ocr_text_fallback_enabled = _env_bool("BOOKCOURSE_OCR_TEXT_FALLBACK_ENABLED", True)
        self.ocr_retry_min_semantic_chars = _env_int("BOOKCOURSE_OCR_RETRY_MIN_SEMANTIC_CHARS", 20, minimum=1)
        self.ocr_cache_enabled = _env_bool("BOOKCOURSE_OCR_CACHE_ENABLED", True)
        self.ocr_cache_ttl_seconds = _env_int("BOOKCOURSE_OCR_CACHE_TTL_SECONDS", 7 * 24 * 60 * 60, minimum=0)
        self.ocr_cache_max_items = _env_int("BOOKCOURSE_OCR_CACHE_MAX_ITEMS", 2000, minimum=1)
        self.image_dark_threshold = float(os.environ.get("BOOKCOURSE_IMAGE_DARK_THRESHOLD", "55"))
        self.image_bright_threshold = float(os.environ.get("BOOKCOURSE_IMAGE_BRIGHT_THRESHOLD", "235"))
        self.image_blur_threshold = float(os.environ.get("BOOKCOURSE_IMAGE_BLUR_THRESHOLD", "35"))
        self.image_dark_border_ratio = float(os.environ.get("BOOKCOURSE_IMAGE_DARK_BORDER_RATIO", "0.18"))
        self.preprocess_median_kernel = int(os.environ.get("BOOKCOURSE_PREPROCESS_MEDIAN_KERNEL", "3"))
        if self.preprocess_median_kernel % 2 == 0:
            self.preprocess_median_kernel += 1
        self.preprocess_median_kernel = max(self.preprocess_median_kernel, 3)
        self.preprocess_adaptive_block_size = int(os.environ.get("BOOKCOURSE_PREPROCESS_ADAPTIVE_BLOCK_SIZE", "35"))
        if self.preprocess_adaptive_block_size % 2 == 0:
            self.preprocess_adaptive_block_size += 1
        self.preprocess_adaptive_block_size = max(self.preprocess_adaptive_block_size, 3)
        self.preprocess_adaptive_c = int(os.environ.get("BOOKCOURSE_PREPROCESS_ADAPTIVE_C", "11"))
        self.ocr_low_confidence_threshold = float(os.environ.get("BOOKCOURSE_OCR_LOW_CONFIDENCE_THRESHOLD", "0.55"))
        self.ocr_text_confidence_threshold = float(os.environ.get("BOOKCOURSE_OCR_TEXT_CONFIDENCE_THRESHOLD", "0.5"))

        if self.environment == "production":
            if self.allow_credentials and "*" in self.allowed_origins:
                raise ValueError(
                    "Production CORS cannot combine wildcard origins with credentials"
                )
            production_provider_errors: list[str] = []
            if self.parser_provider != "mineru":
                production_provider_errors.append("PARSER_PROVIDER=mineru")
            if self.rag_index_provider != "pgvector":
                production_provider_errors.append("RAG_INDEX_PROVIDER=pgvector")
            if self.embedding_provider not in {"bge_m3", "bge-m3"}:
                production_provider_errors.append("EMBEDDING_PROVIDER=bge_m3")
            if self.reranker_provider != "bge":
                production_provider_errors.append("RERANKER_PROVIDER=bge")
            if self.reranker_fail_open:
                production_provider_errors.append("RERANKER_FAIL_OPEN=false")
            if not self.embedding_device.startswith("cuda"):
                production_provider_errors.append("EMBEDDING_DEVICE=cuda:<index>")
            if not self.reranker_device.startswith("cuda"):
                production_provider_errors.append("RERANKER_DEVICE=cuda:<index>")
            if self.llm_provider not in {"deepseek", "openai_compatible"}:
                production_provider_errors.append("LLM_PROVIDER=deepseek|openai_compatible")
            if self.rag_llm_provider not in {"deepseek", "openai_compatible"}:
                production_provider_errors.append("RAG_LLM_PROVIDER=deepseek|openai_compatible")
            if self.image_provider != "openai_compatible":
                production_provider_errors.append("IMAGE_PROVIDER=openai_compatible")
            if production_provider_errors:
                raise ValueError(
                    "Production requires real providers: " + ", ".join(production_provider_errors)
                )

            named_secrets = {
                "BOOKCOURSE_API_KEY": self.api_key,
                "BOOKCOURSE_TRUSTED_PROXY_TOKEN": self.trusted_proxy_token,
            }
            for name, value in named_secrets.items():
                if not value or len(value) < 24 or _looks_like_placeholder(value):
                    raise ValueError(f"{name} must be a non-placeholder secret of at least 24 characters")
            if secrets.compare_digest(self.api_key or "", self.trusted_proxy_token or ""):
                raise ValueError("BOOKCOURSE_API_KEY and BOOKCOURSE_TRUSTED_PROXY_TOKEN must be distinct")

            if self.admin_token:
                if len(self.admin_token) < 24 or _looks_like_placeholder(self.admin_token):
                    raise ValueError(
                        "BOOKCOURSE_ADMIN_TOKEN must be a non-placeholder secret of at least 24 characters"
                    )
                compared_secrets = {
                    self.api_key,
                    self.trusted_proxy_token,
                    self.deepseek_api_key,
                    self.llm_api_key,
                    self.image_api_key,
                }
                if any(
                    candidate and secrets.compare_digest(self.admin_token, candidate)
                    for candidate in compared_secrets
                ):
                    raise ValueError(
                        "BOOKCOURSE_ADMIN_TOKEN must be distinct from API, proxy, and provider secrets"
                    )

            if "deepseek" in {self.llm_provider, self.rag_llm_provider}:
                if not self.deepseek_api_key or _looks_like_placeholder(self.deepseek_api_key):
                    raise ValueError("BOOKCOURSE_DEEPSEEK_API_KEY must be configured in production")
            if "openai_compatible" in {self.llm_provider, self.rag_llm_provider}:
                if not self.llm_api_url or not self.llm_api_key or _looks_like_placeholder(self.llm_api_key):
                    raise ValueError("OpenAI-compatible LLM endpoint and API key are required in production")
            if not self.image_api_url or not self.image_api_key or _looks_like_placeholder(self.image_api_key):
                raise ValueError("Image provider endpoint and API key are required in production")


@lru_cache
def get_settings() -> Settings:
    return Settings()
