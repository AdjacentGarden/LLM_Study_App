"""Demo chapter reading is exposed only from the owned, matching PDF pack."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from adaptive_learning.api.imported_assets_routes import imported_assets_router
from adaptive_learning.community import CommunityRepository
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft


def test_demo_teaching_pages_keep_introduction_media_and_verified_blocks(tmp_path: Path) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    book_id = "book_biology_2"
    book = tmp_path / "books" / book_id
    book.mkdir(parents=True)
    pdf = book / "source.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((35, 50), "The chromosome duplicates once.")
        document.save(pdf)
    source_sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    jobs = SQLiteOCRJobRepository(tmp_path / "state" / "ocr_jobs.sqlite3")
    jobs.register_book(book_id=book_id, original_name="biology.pdf", file_path=pdf, source_sha256=source_sha)
    jobs.save_structure(
        book_id,
        BookStructure(
            title="Biology", summary="", source_page_count=1,
            chapters=[
                ChapterDraft(chapter_id="c1s1", order=1, title="Introduction", start_page=0, end_page=0, summary="", knowledge_points=[], source_block_ids=[], has_supplementary_content=True),
                ChapterDraft(chapter_id="c2s1", order=2, title="Chromosomes", start_page=1, end_page=1, summary="", knowledge_points=[], source_block_ids=[]),
            ],
        ),
    )
    normalized = book / "ocr" / "normalized"
    normalized.mkdir(parents=True)
    normalized.joinpath("pages.jsonl").write_text(json.dumps({"page_number": 1, "text": "The chromosome duplicates once."}) + "\n", encoding="utf-8")
    imported = book / "imported"
    imported.mkdir()
    imported.joinpath("assets", "lesson").mkdir(parents=True)
    imported.joinpath("assets", "lesson", "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    imported.joinpath("assets.json").write_text(json.dumps({"intro": {"path": "assets/lesson/diagram.png", "kind": "image", "caption": "Chapter diagram", "source_kind": "lesson"}}), encoding="utf-8")
    imported.joinpath("teaching.json").write_text(json.dumps({
        "schema_version": 1,
        "source_book_id": book_id,
        "source_pdf_sha256": source_sha,
        "lessons": [
            {"book_id": book_id, "chapter_id": "c1s1", "page_start": 0, "page_end": 0, "status": "supplemental_draft", "title": "Introduction", "summary": "Learn the basics.", "objectives": ["Read the question"], "asset_ids": ["intro"], "blocks": [{"title": "Observe", "content": "Look at the pattern.", "asset_ids": ["intro"]}]},
            {"book_id": book_id, "chapter_id": "c2s1", "page_start": 1, "page_end": 1, "status": "source_aligned", "title": "Chromosomes", "summary": "Follow the chromosome.", "objectives": ["Find the source"], "blocks": [{"title": "Replication", "content": "It duplicates once.", "citations": [{"page_start": 1, "page_end": 1, "quote": "The chromosome duplicates once.", "source_metadata": {"source_pdf_sha256": source_sha}}]}]},
        ],
    }), encoding="utf-8")
    repo = CommunityRepository(tmp_path / "state" / "community.sqlite3")
    repo.register_asset({"book_id": book_id, "title": "Biology"}, "biology-sha", None)
    repo.add_book("alice", book_id)
    app = FastAPI()
    def visitor(request: Request) -> str:
        return request.cookies.get("owner") or "guest"
    app.include_router(imported_assets_router(repo, tmp_path, visitor, lambda: jobs), prefix="/api")
    client = TestClient(app)
    first_url = f"/api/books/{book_id}/teaching-lessons/c1s1"
    assert client.get(first_url).status_code == 403
    client.cookies.set("owner", "alice")
    first = client.get(first_url)
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "private, no-store"
    assert first.json()["objectives"] == ["Read the question"]
    assert first.json()["introduction_media"][0]["url"].endswith("/imported-assets/intro")
    assert first.json()["blocks"][0]["content"] == "Look at the pattern."
    assert first.json()["source_available"] is False

    sourced = client.get(f"/api/books/{book_id}/teaching-lessons/c2s1")
    assert sourced.status_code == 200, sourced.text
    assert sourced.json()["blocks"][0]["citations"][0]["page_number"] == 1
    assert sourced.json()["source_available"] is True

    with pymupdf.open() as document:
        document.new_page().insert_text((35, 50), "An updated source page.")
        document.save(book / "replacement.pdf")
    (book / "replacement.pdf").replace(pdf)
    assert client.get(first_url).status_code == 409
