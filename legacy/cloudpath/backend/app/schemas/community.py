from __future__ import annotations

from pydantic import BaseModel, Field


class CommunityBookSummary(BaseModel):
    id: str
    title: str
    catalog_title: str
    author: str
    cover: str
    subject: str
    level: str
    language: str
    edition: str
    page_count: int
    file_size_bytes: int
    source_page_url: str
    license_name: str
    license_url: str
    rights_notice: str
    description: str
    chapters: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    server_cached: bool = False
    imported_book_id: str | None = None


class CommunityImportResponse(BaseModel):
    catalog_id: str
    book_id: str
    filename: str
    size_bytes: int
    status: str
    already_imported: bool = False
