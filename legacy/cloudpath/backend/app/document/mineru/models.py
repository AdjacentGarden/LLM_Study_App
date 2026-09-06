from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal
import json
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from app.core.config import Settings, get_settings


MinerUTaskStatus = Literal["pending", "processing", "completed", "failed"]


class MinerUClientConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    endpoint: str
    connect_timeout_seconds: float = Field(default=10.0, gt=0)
    request_timeout_seconds: float = Field(default=60.0, gt=0)
    total_timeout_seconds: float = Field(default=900.0, gt=0)
    poll_interval_seconds: float = Field(default=2.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    retry_backoff_seconds: float = Field(default=0.5, ge=0)
    retry_max_backoff_seconds: float = Field(default=10.0, ge=0)
    max_result_bytes: int = Field(default=256 * 1024 * 1024, ge=1024)

    @field_validator("endpoint")
    @classmethod
    def normalize_endpoint(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("MinerU endpoint must use http or https")
        if parsed.username or parsed.password:
            raise ValueError("MinerU endpoint cannot contain user information")
        if parsed.query or parsed.fragment:
            raise ValueError("MinerU endpoint cannot contain a query or fragment")
        if parsed.path not in {"", "/"}:
            raise ValueError("MinerU endpoint must be an origin without a path prefix")
        try:
            is_loopback = ip_address(parsed.hostname).is_loopback
        except ValueError:
            is_loopback = parsed.hostname.lower() == "localhost"
        if not is_loopback:
            raise ValueError("MinerU endpoint must resolve to an explicit loopback host")
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "MinerUClientConfig":
        resolved = settings or get_settings()
        if not resolved.mineru_endpoint:
            raise ValueError("BOOKCOURSE_MINERU_ENDPOINT is not configured")
        return cls(
            endpoint=resolved.mineru_endpoint,
            connect_timeout_seconds=resolved.mineru_connect_timeout_seconds,
            request_timeout_seconds=resolved.mineru_request_timeout_seconds,
            total_timeout_seconds=resolved.mineru_timeout_seconds,
            poll_interval_seconds=resolved.mineru_poll_interval_seconds,
            max_retries=resolved.mineru_max_retries,
            retry_backoff_seconds=resolved.mineru_retry_backoff_seconds,
            retry_max_backoff_seconds=resolved.mineru_retry_max_backoff_seconds,
            max_result_bytes=resolved.mineru_max_result_bytes,
        )


class MinerUParseOptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    lang_list: list[str] = Field(default_factory=lambda: ["ch"], min_length=1)
    backend: str = "pipeline"
    effort: str = "medium"
    parse_method: Literal["auto", "txt", "ocr"] = "auto"
    formula_enable: bool = True
    table_enable: bool = True
    image_analysis: bool = True
    return_md: bool = True
    return_middle_json: Literal[True] = True
    return_model_output: bool = False
    return_content_list: Literal[True] = True
    return_images: bool = True
    response_format_zip: Literal[False] = False
    return_original_file: bool = False
    client_side_output_generation: Literal[False] = False
    start_page_id: int = Field(default=0, ge=0)
    end_page_id: int = Field(default=99999, ge=0)

    @field_validator("end_page_id")
    @classmethod
    def validate_page_range(cls, value: int, info) -> int:
        start = info.data.get("start_page_id", 0)
        if value < start:
            raise ValueError("end_page_id cannot be less than start_page_id")
        return value

    @field_validator("backend", "effort")
    @classmethod
    def normalize_choice(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("value cannot be empty")
        return normalized

    @field_validator("lang_list")
    @classmethod
    def normalize_languages(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized:
            raise ValueError("lang_list cannot be empty")
        return normalized

    def form_fields(self) -> list[tuple[str, str]]:
        """Return MinerU's exact repeated multipart form fields.

        Both ``files`` and ``lang_list`` are arrays in MinerU protocol v2.
        Returning a list instead of a dict preserves repeated ``lang_list``
        parts all the way through multipart encoding.
        """

        fields: list[tuple[str, str]] = []
        for key, value in self.model_dump(mode="json").items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                rendered = str(item).lower() if isinstance(item, bool) else str(item)
                fields.append((key, rendered))
        return fields

    def form_data(self) -> dict[str, Any]:
        """Compatibility view for diagnostics; HTTP code uses form_fields()."""

        data: dict[str, Any] = {}
        for key, value in self.form_fields():
            if key in data:
                previous = data[key]
                data[key] = [*previous, value] if isinstance(previous, list) else [previous, value]
            else:
                data[key] = value
        return data

    def fingerprint(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "MinerUParseOptions":
        resolved = settings or get_settings()
        return cls(
            lang_list=[resolved.mineru_language],
            backend=resolved.mineru_backend,
            effort=resolved.mineru_effort,
            parse_method=resolved.mineru_parse_method,
            formula_enable=resolved.mineru_formula_enable,
            table_enable=resolved.mineru_table_enable,
            image_analysis=resolved.mineru_image_analysis,
            return_images=resolved.mineru_return_images,
        )


class MinerUHealthResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    version: str
    protocol_version: int | None = None
    queued_tasks: int = 0
    processing_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    max_concurrent_requests: int | None = None
    processing_window_size: int | None = None
    task_retention_seconds: int | None = None
    task_cleanup_interval_seconds: int | None = None

    @field_validator("status", "version")
    @classmethod
    def validate_health_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("MinerU health text fields cannot be empty")
        return normalized

    @field_validator(
        "queued_tasks",
        "processing_tasks",
        "completed_tasks",
        "failed_tasks",
        "max_concurrent_requests",
        "processing_window_size",
        "task_retention_seconds",
        "task_cleanup_interval_seconds",
    )
    @classmethod
    def validate_non_negative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("MinerU health counters cannot be negative")
        return value


class MinerUTaskResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str
    status: MinerUTaskStatus
    backend: str | None = None
    file_names: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    status_url: str | None = None
    result_url: str | None = None
    queued_ahead: int | None = None
    message: str | None = None

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 128:
            raise ValueError("MinerU task id is invalid")
        if not all(character.isalnum() or character in {"-", "_"} for character in normalized):
            raise ValueError("MinerU task id contains unsafe characters")
        return normalized

    @field_validator("queued_ahead")
    @classmethod
    def validate_queued_ahead(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("queued_ahead cannot be negative")
        return value


class MinerUResultDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    md_content: str | None = None
    middle_json: dict[str, Any] | None = None
    model_output: str | dict[str, Any] | list[Any] | None = None
    content_list: list[dict[str, Any]] | None = None
    images: dict[str, str] | None = None

    @field_validator("middle_json", "content_list", mode="before")
    @classmethod
    def decode_embedded_json(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return json.loads(value)


class MinerUResultResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    _response_sha256: str | None = PrivateAttr(default=None)

    backend: str
    version: str
    results: dict[str, MinerUResultDocument]

    @field_validator("backend", "version")
    @classmethod
    def validate_result_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("MinerU result identity fields cannot be empty")
        return normalized

    @property
    def response_sha256(self) -> str | None:
        return self._response_sha256

    def set_response_sha256(self, value: str) -> None:
        self._response_sha256 = value


class MinerUExecutionResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task: MinerUTaskResponse
    result: MinerUResultResponse
    parse_generation: int
    idempotency_key: str
    resumed: bool = False


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
