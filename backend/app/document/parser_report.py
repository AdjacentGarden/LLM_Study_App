from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.document.mineru.exceptions import safe_error_detail
from app.document.page_artifacts import GenerationGuard, atomic_write_json


AttemptStatus = Literal["succeeded", "degraded", "failed", "skipped"]


class ParserAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parser: str
    scope: Literal["document", "pages"] = "document"
    pages: list[int] = Field(default_factory=list)
    status: AttemptStatus
    duration_ms: int = Field(ge=0)
    error_code: str | None = None
    retryable: bool | None = None
    detail: str | None = None
    quality_score: float | None = Field(default=None, ge=0, le=1)

    @classmethod
    def failure(
        cls,
        *,
        parser: str,
        duration_ms: int,
        error_code: str,
        detail: object = None,
        pages: list[int] | None = None,
        retryable: bool | None = None,
    ) -> "ParserAttempt":
        return cls(
            parser=parser,
            scope="pages" if pages else "document",
            pages=pages or [],
            status="failed",
            duration_ms=max(0, duration_ms),
            error_code=error_code,
            retryable=retryable,
            detail=safe_error_detail(detail),
        )


class PageParserDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    parser: str
    quality_score: float | None = Field(default=None, ge=0, le=1)
    status: Literal["accepted", "unrecoverable"] = "accepted"
    warnings: list[str] = Field(default_factory=list)


class ParserReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    book_id: str
    requested_parser: str
    final_parser: str
    parse_generation: int | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_page_count: int = Field(ge=0)
    output_page_count: int = Field(ge=0)
    attempts: list[ParserAttempt] = Field(default_factory=list)
    pages: list[PageParserDecision] = Field(default_factory=list)
    missing_pages: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    mineru_backend: str | None = None
    mineru_version: str | None = None


def write_parser_report(
    path: Path,
    report: ParserReport,
    *,
    generation_guard: GenerationGuard | None = None,
) -> None:
    atomic_write_json(path, report.model_dump(mode="json"), generation_guard=generation_guard)

