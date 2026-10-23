from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from adaptive_learning.api.community_routes import community_router
from adaptive_learning.api.schemas import BookCatalogItem
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.community import CommunityRepository
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository


@pytest.fixture
def source_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("RAG_BOOK_INDEX_MANIFEST", raising=False)
    jobs = SQLiteOCRJobRepository(tmp_path / "jobs.sqlite3")
    assessments = SQLiteAssessmentRepository(tmp_path / "assessments.sqlite3")
    entries = []
    files = {}
    for index in range(7):
        book_id = f"book-{index}"
        path = tmp_path / f"original-{index}.pdf"
        path.write_bytes(b"%PDF-1.7\n" + f"registered document {index}\n".encode())
        files[book_id] = path
        jobs.register_book(book_id=book_id, original_name=f"教材{index}.pdf", file_path=path,
                           source_sha256=f"hash-{5 if index == 6 else index}")
        entries.append(BookCatalogItem(book_id=book_id, title=f"教材{index}", status="structured",
            page_count=1, chapter_count=1, summary="有来源的教材", diagnostics_ready=False))
    app = FastAPI()
    app.include_router(community_router(tmp_path, lambda: entries, lambda: jobs, lambda: assessments))
    repo = CommunityRepository(tmp_path / "state/community.sqlite3")
    with TestClient(app) as owner, TestClient(app) as stranger:
        owner.get("/api/library")
        owner_id = repo.visitor(owner.cookies.get("zhiwo_visitor"), [])[0]
        repo.add_book(owner_id, "book-5")
        yield owner, stranger, repo, files


def test_original_pdf_requires_ownership_and_removal_revokes_access(source_environment):
    owner, stranger, repo, files = source_environment
    denied = stranger.get("/api/library/books/book-5/source")
    assert denied.status_code == 403 and b"%PDF" not in denied.content
    response = owner.get("/api/library/books/book-5/source")
    assert response.status_code == 200 and response.content == files["book-5"].read_bytes()
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert owner.post("/api/library/books/book-5/remove").status_code == 200
    assert owner.get("/api/library/books/book-5/source").status_code == 403


def test_alias_reads_the_registered_canonical_pdf_and_supports_range_requests(source_environment):
    owner, _, _, files = source_environment
    response = owner.get("/api/library/books/book-6/source", headers={"Range": "bytes=0-8"})
    assert response.status_code == 206
    assert response.content == files["book-5"].read_bytes()[:9]
    assert response.headers["content-range"].startswith("bytes 0-8/")
    assert owner.get("/api/library/books/book-6/source").content == files["book-5"].read_bytes()


def test_client_cannot_choose_an_arbitrary_file_or_walk_a_path(source_environment, tmp_path):
    owner, _, _, files = source_environment
    secret = tmp_path / "private-unregistered.pdf"
    secret.write_bytes(b"%PDF-1.7\nPRIVATE_UNREGISTERED")
    response = owner.get("/api/library/books/book-5/source", params={"path": str(secret), "filename": str(secret)})
    assert response.status_code == 200 and response.content == files["book-5"].read_bytes()
    assert b"PRIVATE_UNREGISTERED" not in response.content
    response = owner.get("/api/library/books/..%2Fprivate-unregistered.pdf/source")
    assert response.status_code in {403, 404}
    assert b"PRIVATE_UNREGISTERED" not in response.content


@pytest.mark.parametrize("fault", ["missing", "not_pdf", "directory"])
def test_missing_or_invalid_registered_sources_fail_without_exposing_local_paths(source_environment, fault):
    owner, _, _, files = source_environment
    path = files["book-5"]
    if fault == "missing":
        path.unlink()
    elif fault == "not_pdf":
        path.write_bytes(b"PRIVATE_CREDENTIAL masquerading as PDF")
    else:
        path.unlink()
        path.mkdir()
    response = owner.get("/api/library/books/book-5/source")
    assert response.status_code == 404
    assert str(path) not in response.text and "PRIVATE" not in response.text


def test_first_source_read_keeps_the_new_visitor_cookie(source_environment):
    _, stranger, _, _ = source_environment
    response = stranger.get("/api/library/books/book-0/source")
    assert response.status_code == 200
    assert "zhiwo_visitor=" in response.headers["set-cookie"]
    assert stranger.cookies.get("zhiwo_visitor")


def test_private_upload_survives_router_restart_with_no_public_catalog(source_environment):
    owner, _, repo, files = source_environment
    root = files['book-5'].parent
    app = FastAPI()
    # A private uploaded asset is persisted but never appears in the public
    # catalog. Reconstruct the router exactly as a server restart does.
    app.include_router(community_router(root, lambda: [],
        lambda: SQLiteOCRJobRepository(root / 'jobs.sqlite3'),
        lambda: SQLiteAssessmentRepository(root / 'assessments.sqlite3')))
    with TestClient(app) as recovered, TestClient(app) as stranger:
        recovered.cookies.update(owner.cookies)
        assert recovered.get('/api/library/books/book-5/source').content == files['book-5'].read_bytes()
        assert recovered.get('/api/studio/notes', params={'book_id':'book-5'}).status_code == 200
        assert stranger.get('/api/library/books/book-5/source').status_code == 403
        assert stranger.get('/api/studio/notes', params={'book_id':'book-5'}).status_code == 403
        assert recovered.post('/api/library/books/book-5/remove').status_code == 200
        assert recovered.get('/api/library/books/book-5/source').status_code == 403
