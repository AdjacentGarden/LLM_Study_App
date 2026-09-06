from __future__ import annotations

from pydantic import BaseModel


class UploadInitRequest(BaseModel):
    filename: str
    content_type: str | None = None
    size_bytes: int | None = None


class UploadInitResponse(BaseModel):
    book_id: str
    upload_url: str
    max_upload_bytes: int


class FileSaveResponse(BaseModel):
    book_id: str
    filename: str
    size_bytes: int
    status: str


class ParseRequest(BaseModel):
    force: bool = False


class ParseJobResponse(BaseModel):
    book_id: str
    job_id: str
    status: str
