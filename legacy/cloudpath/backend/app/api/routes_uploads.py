from __future__ import annotations

from threading import Lock
from uuid import uuid4

from fastapi import APIRouter, Depends, UploadFile, File
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, require_api_key, require_book_owner_from_path
from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.uploads import UploadInitRequest, UploadInitResponse, FileSaveResponse
from app.services.storage import (
    create_book_id,
    original_file_path,
    sanitize_filename,
    assert_allowed_file,
    write_book_owner,
)
from app.services.upload_validation import validate_declared_content_type, validate_saved_upload


router = APIRouter(
    prefix="/api",
    dependencies=[Depends(require_api_key), Depends(require_book_owner_from_path)],
)
_original_swap_lock = Lock()


@router.post("/uploads/init", response_model=UploadInitResponse)
def init_upload(payload: UploadInitRequest, principal: Principal = Depends(require_api_key)) -> UploadInitResponse:
    safe = sanitize_filename(payload.filename)
    assert_allowed_file(safe)
    validate_declared_content_type(safe, payload.content_type)
    settings = get_settings()
    if payload.size_bytes is not None and payload.size_bytes > settings.max_upload_bytes:
        raise AppError("file_too_large", "文件超过大小限制", details={"max_upload_bytes": settings.max_upload_bytes})
    book_id = create_book_id()
    write_book_owner(book_id, principal.user_id)
    return UploadInitResponse(
        book_id=book_id,
        upload_url=f"/api/books/{book_id}/files",
        max_upload_bytes=settings.max_upload_bytes,
    )


@router.post("/books/{book_id}/files", response_model=FileSaveResponse)
async def save_file(
    book_id: str,
    file: UploadFile = File(...),
    principal: Principal = Depends(require_api_key),
) -> FileSaveResponse:
    filename = sanitize_filename(file.filename or "upload.bin")
    assert_allowed_file(filename)
    validate_declared_content_type(filename, file.content_type)
    target = original_file_path(book_id, filename)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.uploading")
    size = 0
    exceeded_limit = False
    try:
        with temporary.open("xb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > get_settings().max_upload_bytes:
                    exceeded_limit = True
                    break
                handle.write(chunk)
        if exceeded_limit:
            raise AppError(
                "file_too_large",
                "文件超过大小限制",
                details={"max_upload_bytes": get_settings().max_upload_bytes},
            )
        # PDF/OOXML parsing is CPU and I/O heavy. Keep it off the async API
        # event loop so health checks and other learners remain responsive.
        await run_in_threadpool(validate_saved_upload, temporary, filename)
        # Validation succeeded. Atomic replacement preserves a previous valid
        # upload if the new candidate is corrupt or interrupted.
        with _original_swap_lock:
            existing = [
                candidate
                for candidate in target.parent.iterdir()
                if candidate.is_file()
                and not candidate.name.startswith(".")
                and candidate != target
            ]
            if existing:
                raise AppError(
                    "original_already_exists",
                    "该课程已经绑定了另一份原始文件，请新建课程后上传",
                    status_code=409,
                    details={"existing_filename": existing[0].name},
                )
            temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return FileSaveResponse(book_id=book_id, filename=filename, size_bytes=size, status="saved")
