"""The shelf reflects the usable diagnostic bank, not claim-time catalog metadata."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaptive_learning.api.community_routes import community_router
from adaptive_learning.assessment.item_generation import structure_fingerprint
from adaptive_learning.assessment.models import DiagnosticItem, ResponseType
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    monkeypatch.delenv("RAG_BOOK_INDEX_MANIFEST", raising=False)
    jobs = SQLiteOCRJobRepository(tmp_path / "jobs.sqlite3")
    assessments = SQLiteAssessmentRepository(tmp_path / "assessments.sqlite3")
    app = FastAPI()
    app.include_router(
        community_router(tmp_path, lambda: [], lambda: jobs, lambda: assessments)
    )
    with TestClient(app) as owner, TestClient(app) as other:
        yield owner, other, jobs, assessments, tmp_path


def structure(title="Geometry"):
    return BookStructure(
        title=title,
        summary="A structured book",
        source_page_count=12,
        chapters=[
            ChapterDraft(
                chapter_id="chapter-1",
                order=1,
                title="Chapter 1",
                start_page=1,
                end_page=12,
                summary="Chapter summary",
                knowledge_points=["Shapes"],
                source_block_ids=["block-1"],
            )
        ],
    )


def register_upload(jobs, tmp_path, book_id, source_hash, book_structure):
    source = tmp_path / f"{book_id}.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")
    jobs.register_book(
        book_id=book_id,
        original_name=f"{book_id}.pdf",
        file_path=source,
        source_sha256=source_hash,
    )
    jobs.save_structure(book_id, book_structure)


def claim(client, book_id):
    assert client.post(f"/api/library/books/{book_id}/bind-upload").status_code == 200
    response = client.post(f"/api/library/books/{book_id}/claim")
    assert response.status_code == 200, response.text
    return response.json()


def single_choice():
    return DiagnosticItem(
        item_id="choice-1",
        chapter_id="chapter-1",
        knowledge_point_ids=["shape"],
        prompt="Which shape has three sides?",
        response_type=ResponseType.SINGLE_CHOICE,
        options=["Triangle", "Square"],
        correct_option_ids=["Triangle"],
    )


def shelf_book(client):
    response = client.get("/api/library")
    assert response.status_code == 200, response.text
    books = response.json()
    assert len(books) == 1
    return books[0]


def test_claimed_book_becomes_ready_only_after_matching_choice_bank(shelf):
    owner, other, jobs, assessments, tmp_path = shelf
    original = structure()
    register_upload(jobs, tmp_path, "geometry", "geometry-hash", original)

    assert owner.get("/api/library").json() == []
    assert claim(owner, "geometry")["diagnostics_ready"] is False
    assert shelf_book(owner)["diagnostics_ready"] is False
    assert other.get("/api/library").json() == []

    assessments.save_bank(
        book_id="geometry",
        structure_fingerprint=structure_fingerprint(original),
        items=[single_choice()],
    )
    assert shelf_book(owner)["diagnostics_ready"] is True

    revised = structure("Geometry, revised")
    jobs.save_structure("geometry", revised)
    assert shelf_book(owner)["diagnostics_ready"] is False

    # A matching legacy bank with only typed answers cannot start the current
    # choice-only diagnostic flow.
    open_item = single_choice().model_copy(
        update={"response_type": ResponseType.SHORT_ANSWER}
    )
    assessments.save_bank(
        book_id="geometry",
        structure_fingerprint=structure_fingerprint(revised),
        items=[open_item],
    )
    assert shelf_book(owner)["diagnostics_ready"] is False

    assessments.save_bank(
        book_id="geometry",
        structure_fingerprint=structure_fingerprint(revised),
        items=[single_choice()],
    )
    assert shelf_book(owner)["diagnostics_ready"] is True


def test_deduplicated_upload_uses_canonical_structure_and_bank(shelf):
    owner, other, jobs, assessments, tmp_path = shelf
    canonical = structure("Canonical geometry")
    alias = structure("Re-uploaded geometry")
    register_upload(jobs, tmp_path, "canonical", "same-pdf-hash", canonical)
    register_upload(jobs, tmp_path, "alias", "same-pdf-hash", alias)

    assert claim(owner, "canonical")["book_id"] == "canonical"
    assert claim(other, "alias")["book_id"] == "canonical"
    assert shelf_book(other)["book_id"] == "canonical"

    assessments.save_bank(
        book_id="alias",
        structure_fingerprint=structure_fingerprint(alias),
        items=[single_choice()],
    )
    assert shelf_book(other)["diagnostics_ready"] is False

    assessments.save_bank(
        book_id="canonical",
        structure_fingerprint=structure_fingerprint(canonical),
        items=[single_choice()],
    )
    assert shelf_book(other)["diagnostics_ready"] is True
    assert shelf_book(owner)["diagnostics_ready"] is True
