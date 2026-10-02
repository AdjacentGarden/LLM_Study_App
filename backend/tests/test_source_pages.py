"""Source pages use real PDFs and isolated account/library storage."""

from __future__ import annotations

import hashlib
import io
import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageChops

from adaptive_learning.accounts import COOKIE
from adaptive_learning.api import source_routes
from adaptive_learning.api.community_routes import community_router
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.community import CommunityRepository
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository
from adaptive_learning.ingestion.models import BookStructure
from adaptive_learning.studio import get_studio


def _make_pdf(path: Path, labels: tuple[str, ...]) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document:
        for label in labels:
            page = document.new_page(width=300, height=400)
            page.insert_text((35, 55), label, fontsize=18)
        document.save(path)


def _register_book(
    jobs: SQLiteOCRJobRepository,
    root: Path,
    book_id: str,
    labels: tuple[str, ...],
    *,
    structured: bool = True,
) -> Path:
    path = root / "books" / book_id / "source.pdf"
    _make_pdf(path, labels)
    jobs.register_book(
        book_id=book_id,
        original_name=f"{book_id}.pdf",
        file_path=path,
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    if structured:
        jobs.save_structure(
            book_id,
            BookStructure(title=book_id, summary="", chapters=[], source_page_count=len(labels)),
        )
    return path


def _account(repo: CommunityRepository, client: TestClient, owner: str) -> None:
    token = f"session-{owner}"
    with repo.connect() as db:
        db.execute(
            "INSERT INTO accounts VALUES (?,?,?,?,?,?,?)",
            (owner, f"{owner}@example.com", "student", "[]", "learn", 0, time.time()),
        )
        db.execute(
            "INSERT INTO account_sessions VALUES (?,?,?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), owner, time.time(), time.time() + 3600),
        )
    client.cookies.set(COOKIE, token)


@pytest.fixture
def source_api(tmp_path: Path):
    pytest.importorskip("pymupdf")
    jobs = SQLiteOCRJobRepository(tmp_path / "state" / "ocr_jobs.sqlite3")
    assessments = SQLiteAssessmentRepository(tmp_path / "state" / "assessments.sqlite3")
    repo = CommunityRepository(tmp_path / "state" / "community.sqlite3")
    app = FastAPI()
    app.include_router(community_router(tmp_path, lambda: [], lambda: jobs, lambda: assessments))
    with TestClient(app) as alice, TestClient(app) as bob:
        # Initializes the same account schema and visitor dependency used by the app.
        assert alice.get("/api/library").status_code == 200
        assert bob.get("/api/library").status_code == 200
        _account(repo, alice, "alice")
        _account(repo, bob, "bob")
        yield tmp_path, jobs, repo, alice, bob


def test_real_page_metadata_image_bounds_and_cache(source_api) -> None:
    root, jobs, repo, alice, bob = source_api
    _register_book(jobs, root, "math", ("Math first page", "Math second page"))
    repo.register_asset({"book_id": "math", "title": "Math"}, "math-sha", None)
    repo.add_book("alice", "math")

    first = alice.get("/api/books/math/pages/1")
    assert first.status_code == 200, first.text
    assert first.json() == {
        "book_id": "math",
        "page_number": 1,
        "page_count": 2,
        "text": "Math first page",
        "image_url": "/api/books/math/pages/1/image",
        "printed_page_number": None,
    }
    assert first.headers["cache-control"] == "private, no-store"
    second = alice.get("/api/books/math/pages/2").json()
    assert second["text"] == "Math second page"

    image = alice.get(first.json()["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "private, no-store"
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(image.content)) as png:
        assert png.size == (600, 800)
    assert alice.get(second["image_url"]).content != image.content

    hits_before = source_routes._render_page_png.cache_info().hits
    assert alice.get(first.json()["image_url"]).content == image.content
    assert source_routes._render_page_png.cache_info().hits == hits_before + 1

    for page_number in (0, 3):
        assert alice.get(f"/api/books/math/pages/{page_number}").status_code == 422
        assert alice.get(f"/api/books/math/pages/{page_number}/image").status_code == 422
    assert bob.get("/api/books/math/pages/1").status_code == 403
    assert bob.get("/api/books/math/pages/1/image").status_code == 403
    repo.remove_book("alice", "math")
    assert alice.get(first.json()["image_url"]).status_code == 403


def test_source_page_ink_is_private_versioned_and_bound_to_one_pdf_page(source_api) -> None:
    root, jobs, repo, alice, bob = source_api
    source = _register_book(jobs, root, "biology", ("First source page", "Second source page"))
    repo.register_asset({"book_id": "biology", "title": "Biology"}, "biology-sha", None)
    repo.add_book("alice", "biology")

    url = "/api/studio/source-page?book_id=biology&page_number=1"
    assert bob.get(url).status_code == 403
    starter_response = alice.get(url)
    assert starter_response.status_code == 200, starter_response.text
    starter = starter_response.json()
    assert starter["revision"] == 0
    assert starter["surface"] == "source_page"
    assert starter["pages"] == [1]
    assert starter["source_page_number"] == 1
    assert starter["source_pdf_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()

    stroke = {
        "points": [{"x": 100, "y": 120, "p": 0.5}, {"x": 180, "y": 190, "p": 0.7}],
        "width": 4,
        "color": "#243148",
    }
    payload = {**starter, "strokes": [stroke], "excerpt": "First source page"}
    saved_response = alice.post("/api/studio/notes", json=payload)
    assert saved_response.status_code == 200, saved_response.text
    saved = saved_response.json()
    assert saved["revision"] == 1
    assert alice.get(url).json()["strokes"] == [stroke]
    assert alice.get("/api/studio/source-page?book_id=biology&page_number=2").json()["id"] != saved["id"]
    assert bob.get(f"/api/studio/notes/{saved['id']}").status_code == 404

    # A lost save response can be retried, while stale edits and forged page identity are rejected.
    assert alice.post("/api/studio/notes", json=payload).json()["revision"] == 1
    stale = {**payload, "title": "changed"}
    assert alice.post("/api/studio/notes", json=stale).status_code == 409
    assert alice.post("/api/studio/notes", json={**starter, "id": "note_forged", "strokes": [stroke]}).status_code == 422
    assert alice.post("/api/studio/notes", json={**starter, "surface": "blank", "source_page_number": None, "source_pdf_sha256": None, "strokes": [stroke]}).status_code == 422
    assert alice.post("/api/studio/notes", json={**starter, "source_pdf_sha256": "0" * 64, "strokes": [stroke]}).status_code == 409
    assert alice.post("/api/studio/notes", json={**starter, "pages": [2], "strokes": [stroke]}).status_code == 422

    images = get_studio(root).source_note_images(saved)
    assert len(images) == 2
    with Image.open(io.BytesIO(images[0][1])) as ink, Image.open(io.BytesIO(images[1][1])) as composite:
        assert ink.size == composite.size
        assert ink.getpixel((0, 0)) == (255, 255, 255)
        assert ImageChops.difference(ink, composite).getbbox() is not None

    replacement = root / "replacement.pdf"
    _make_pdf(replacement, ("Updated first page", "Updated second page"))
    replacement.replace(source)
    assert alice.get(url).status_code == 409
    assert alice.post("/api/studio/notes", json={**payload, "revision": 1}).status_code == 409
    with jobs._connect() as db:
        db.execute(
            "UPDATE books SET source_sha256=? WHERE book_id=?",
            (hashlib.sha256(source.read_bytes()).hexdigest(), "biology"),
        )
    new_note = alice.get(url)
    assert new_note.status_code == 200
    assert new_note.json()["id"] != saved["id"]
    assert new_note.json()["strokes"] == []


def test_cache_refreshes_when_source_pdf_changes(source_api) -> None:
    root, jobs, repo, alice, _ = source_api
    source = _register_book(jobs, root, "geography", ("Old page",))
    repo.register_asset({"book_id": "geography", "title": "Geography"}, "geography-sha", None)
    repo.add_book("alice", "geography")
    url = "/api/books/geography/pages/1/image"
    old_image = alice.get(url).content

    replacement = root / "replacement.pdf"
    _make_pdf(replacement, ("Updated page content",))
    replacement.replace(source)

    assert alice.get("/api/books/geography/pages/1").json()["text"] == "Updated page content"
    assert alice.get(url).content != old_image


def test_ocr_text_preferred_and_missing_source_is_recoverable(source_api) -> None:
    root, jobs, repo, alice, _ = source_api
    source = _register_book(jobs, root, "history", ("Embedded text",))
    job = jobs.enqueue(book_id="history", output_dir=source.parent / "ocr", max_attempts=1)
    jobs.save_structure(
        "history", BookStructure(title="History", summary="", chapters=[], source_page_count=1)
    )
    normalized = job.output_dir / "normalized"
    normalized.mkdir(parents=True)
    (normalized / "pages.jsonl").write_text(
        json.dumps({"page_number": 1, "text": "Recognized source text"}) + "\n",
        encoding="utf-8",
    )
    repo.register_asset({"book_id": "history", "title": "History"}, "history-sha", None)
    repo.add_book("alice", "history")
    assert alice.get("/api/books/history/pages/1").json()["text"] == "Recognized source text"

    source.unlink()
    for suffix in ("", "/image"):
        response = alice.get(f"/api/books/history/pages/1{suffix}")
        assert response.status_code == 404
        assert "PDF" in response.json()["detail"]
        assert str(root) not in response.text

    source.write_bytes(b"not a PDF")
    assert alice.get("/api/books/history/pages/1").status_code == 422
    assert alice.get("/api/books/history/pages/1/image").status_code == 422


def test_page_without_extractable_text_has_no_invented_text(source_api) -> None:
    root, jobs, repo, alice, _ = source_api
    _register_book(jobs, root, "blank", ("",))
    repo.register_asset({"book_id": "blank", "title": "Blank"}, "blank-sha", None)
    repo.add_book("alice", "blank")
    page = alice.get("/api/books/blank/pages/1")
    assert page.status_code == 200
    assert page.json()["text"] is None
    assert page.json()["printed_page_number"] is None


def test_upload_owner_parse_gate_and_shared_canonical_access(source_api) -> None:
    root, jobs, repo, alice, bob = source_api
    _register_book(jobs, root, "pending", ("Not parsed yet",), structured=False)
    assert alice.get("/api/books/pending/pages/1").status_code == 403
    assert repo.bind_upload("alice", "pending")
    assert alice.get("/api/books/pending/pages/1").status_code == 409
    assert bob.get("/api/books/pending/pages/1").status_code == 403

    _register_book(jobs, root, "canonical", ("Canonical source",))
    _register_book(jobs, root, "duplicate", ("Another upload",))
    repo.register_asset({"book_id": "canonical", "title": "Canonical"}, "hash-one", "same-text")
    assert (
        repo.register_asset({"book_id": "duplicate", "title": "Duplicate"}, "hash-two", "same-text")
        == "canonical"
    )
    repo.add_book("bob", "canonical")
    assert bob.get("/api/books/canonical/pages/1").json()["text"] == "Canonical source"
    assert bob.get("/api/books/duplicate/pages/1").json()["text"] == "Canonical source"
    assert alice.get("/api/books/canonical/pages/1").status_code == 403
    assert alice.get("/api/books/duplicate/pages/1/image").status_code == 403


def test_cached_image_still_checks_account_and_dependency_error(source_api, monkeypatch) -> None:
    root, jobs, repo, alice, _ = source_api
    _register_book(jobs, root, "physics", ("Physics source",))
    repo.register_asset({"book_id": "physics", "title": "Physics"}, "physics-sha", None)
    repo.add_book("alice", "physics")
    url = "/api/books/physics/pages/1/image"
    assert alice.get(url).status_code == 200
    alice.cookies.set(COOKIE, "session-bob")
    assert alice.get(url).status_code == 403

    alice.cookies.set(COOKIE, "session-alice")
    def unavailable() -> None:
        raise source_routes.HTTPException(503, "PDF renderer unavailable")

    monkeypatch.setattr(source_routes, "_pymupdf", unavailable)
    assert alice.get("/api/books/physics/pages/1").status_code == 503
    # A cached image remains valid without calling the optional renderer; a
    # different page/source signature still reports its absence accurately.
    source_routes._render_page_png.cache_clear()
    assert alice.get(url).status_code == 503
