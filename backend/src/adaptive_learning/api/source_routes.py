"""Authorized, on-demand access to pages of a learner's source PDF."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi import Path as PathParameter
from pydantic import BaseModel, Field

from ..community import CommunityRepository
from ..ingestion.jobs import SQLiteOCRJobRepository, StoredBook


class SourcePageResponse(BaseModel):
    book_id: str
    page_number: int = Field(ge=1)
    page_count: int = Field(ge=1)
    text: str | None
    image_url: str
    printed_page_number: str | None = None


class _PageOutOfRange(Exception):
    pass


class _UnreadablePDF(Exception):
    pass


def _actual_pdf_hash(source: Path) -> str:
    with source.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _pymupdf() -> Any:
    try:
        return importlib.import_module("pymupdf")
    except ModuleNotFoundError as error:
        raise HTTPException(
            status_code=503,
            detail='PDF 页图服务不可用，请安装后端的 "pdf" 可选依赖',
        ) from error


def _pdf_page(source: Path, page_number: int) -> tuple[int, str | None]:
    pymupdf = _pymupdf()
    try:
        with pymupdf.open(source, filetype="pdf") as document:
            if document.needs_pass:
                raise _UnreadablePDF("原始 PDF 已加密，无法读取")
            page_count = document.page_count
            if page_count < 1:
                raise _UnreadablePDF("原始 PDF 没有可阅读的页面")
            if page_number > page_count:
                raise _PageOutOfRange
            text = str(document[page_number - 1].get_text("text")).strip() or None
            return page_count, text
    except (OSError, RuntimeError, ValueError) as error:
        raise _UnreadablePDF("原始 PDF 无法读取，请重新上传或联系管理员") from error


@lru_cache(maxsize=16)
def _render_page_png(source: Path, mtime_ns: int, size: int, page_number: int) -> bytes:
    # The file signature is part of the cache key. Authorization is checked before
    # reaching this function on every request, including hits.
    del mtime_ns, size
    pymupdf = _pymupdf()
    try:
        with pymupdf.open(source, filetype="pdf") as document:
            if document.needs_pass:
                raise _UnreadablePDF("原始 PDF 已加密，无法读取")
            if page_number > document.page_count:
                raise _PageOutOfRange
            page = document[page_number - 1]
            width, height = float(page.rect.width), float(page.rect.height)
            if not (math.isfinite(width) and math.isfinite(height)) or min(width, height) <= 0:
                raise _UnreadablePDF("原始 PDF 页面尺寸无效")
            scale = min(2.0, 3500 / max(width, height), math.sqrt(9_000_000 / (width * height)))
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            return bytes(pixmap.tobytes("png"))
    except (OSError, RuntimeError, ValueError) as error:
        raise _UnreadablePDF("原始 PDF 无法渲染，请重新上传或联系管理员") from error


def _ocr_text(repository: SQLiteOCRJobRepository, book_id: str, page_number: int) -> str | None:
    job = repository.get_job(book_id)
    if job is None:
        return None
    pages_path = job.output_dir / "normalized" / "pages.jsonl"
    try:
        with pages_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    page = json.loads(line)
                    if int(page.get("page_number", 0)) != page_number:
                        continue
                    value = page.get("cleaned_text") or page.get("text") or page.get("raw_text")
                    return (str(value).strip() or None) if value is not None else None
                except (ValueError, TypeError, AttributeError):
                    continue
    except OSError:
        return None
    return None


def _source_book(
    owner: str,
    book_id: str,
    repo: CommunityRepository,
    repository: SQLiteOCRJobRepository,
) -> StoredBook:
    asset = repo.asset(book_id)
    if asset is not None and repo.owns(owner, book_id):
        # A claimed duplicate has its own upload ID. Everyone with the canonical
        # shelf entry reads the same canonical PDF, not another user's upload.
        source_id = str(asset["canonical"])
    elif asset is None and repo.owns_upload(owner, book_id):
        source_id = book_id
    else:
        raise HTTPException(status_code=403, detail="请先将这本书加入自己的书架")

    stored = repository.get_book(source_id)
    if stored is None or stored.status != "structured" or repository.get_structure(source_id) is None:
        raise HTTPException(status_code=409, detail="原文尚未解析完成，请稍后重试")
    if not stored.file_path.is_file():
        raise HTTPException(status_code=404, detail="原始 PDF 文件不可用，请重新上传或联系管理员")
    return stored


def _raise_page_error(error: Exception) -> NoReturn:
    if isinstance(error, _PageOutOfRange):
        raise HTTPException(status_code=422, detail="PDF 页码超出范围") from error
    if isinstance(error, _UnreadablePDF):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise error


def source_router(
    repo: CommunityRepository,
    jobs: Callable[[], SQLiteOCRJobRepository],
    visitor: Callable[..., str],
) -> APIRouter:
    router = APIRouter(prefix="/books", tags=["source pages"])

    @router.get("/{book_id}/pages/{page_number}", response_model=SourcePageResponse)
    def source_page(
        book_id: str,
        page_number: int = PathParameter(ge=1),
        owner: str = Depends(visitor),
    ) -> SourcePageResponse:
        repository = jobs()
        stored = _source_book(owner, book_id, repo, repository)
        try:
            page_count, pdf_text = _pdf_page(stored.file_path, page_number)
        except (_PageOutOfRange, _UnreadablePDF) as error:
            _raise_page_error(error)
        text = _ocr_text(repository, stored.book_id, page_number) or pdf_text
        image_url = f"/api/books/{quote(book_id, safe='')}/pages/{page_number}/image"
        return SourcePageResponse(
            book_id=book_id,
            page_number=page_number,
            page_count=page_count,
            text=text,
            image_url=image_url,
            printed_page_number=None,
        )

    @router.get("/{book_id}/pages/{page_number}/image", response_class=Response)
    def source_page_image(
        book_id: str,
        page_number: int = PathParameter(ge=1),
        owner: str = Depends(visitor),
    ) -> Response:
        stored = _source_book(owner, book_id, repo, jobs())
        try:
            stat = stored.file_path.stat()
            image = _render_page_png(stored.file_path, stat.st_mtime_ns, stat.st_size, page_number)
        except OSError as error:
            raise HTTPException(status_code=404, detail="原始 PDF 文件不可用") from error
        except (_PageOutOfRange, _UnreadablePDF) as error:
            _raise_page_error(error)
        return Response(
            content=image,
            media_type="image/png",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    return router
