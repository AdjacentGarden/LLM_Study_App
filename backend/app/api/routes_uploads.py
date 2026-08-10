from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile, File

from app.core.auth import Principal, require_api_key
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


router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])


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
    size = 0
    exceeded_limit = False
    with target.open("wb") as handle:
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
        # On Windows the handle must be closed before unlinking the partial
        # upload; deleting inside the ``with`` block raises WinError 32.
        target.unlink(missing_ok=True)
        raise AppError("file_too_large", "文件超过大小限制", details={"max_upload_bytes": get_settings().max_upload_bytes})
    try:
        validate_saved_upload(target, filename)
    except AppError:
        target.unlink(missing_ok=True)
        raise
    return FileSaveResponse(book_id=book_id, filename=filename, size_bytes=size, status="saved")
