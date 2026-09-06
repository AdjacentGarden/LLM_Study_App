from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.community.catalog import (
    community_book_summary,
    community_cover_path,
    get_catalog_entry,
    import_community_book,
    list_community_books,
)
from app.core.auth import Principal, require_api_key
from app.core.errors import AppError
from app.schemas.community import CommunityBookSummary, CommunityImportResponse


router = APIRouter(prefix="/api/community", dependencies=[Depends(require_api_key)])


@router.get("/books", response_model=list[CommunityBookSummary])
def community_books(principal: Principal = Depends(require_api_key)) -> list[CommunityBookSummary]:
    return list_community_books(principal)


@router.get("/books/{catalog_id}", response_model=CommunityBookSummary)
def community_book(catalog_id: str, principal: Principal = Depends(require_api_key)) -> CommunityBookSummary:
    return community_book_summary(get_catalog_entry(catalog_id), principal)


@router.get("/books/{catalog_id}/cover")
def community_book_cover(catalog_id: str) -> FileResponse:
    entry = get_catalog_entry(catalog_id)
    path = community_cover_path(entry)
    if not path.is_file():
        raise AppError("community_cover_missing", "社区教材封面缺失", status_code=404)
    return FileResponse(path, media_type="image/webp", filename=entry.cover_filename)


@router.post("/books/{catalog_id}/import", response_model=CommunityImportResponse)
def community_book_import(
    catalog_id: str,
    principal: Principal = Depends(require_api_key),
) -> CommunityImportResponse:
    return import_community_book(catalog_id, principal)
