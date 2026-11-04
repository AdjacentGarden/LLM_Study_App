"""Private PDF identity checks use temporary databases and no model calls."""

from __future__ import annotations

import asyncio
import hashlib
import io
import multiprocessing
import sqlite3
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import Response, UploadFile
from native_harness import api, structure
from starlette.requests import Request
from test_native_api import native as native

from adaptive_learning.api.store import MemoryStore
from adaptive_learning.community import CommunityRepository
from adaptive_learning.ingestion.jobs import OCRRunResult, SQLiteOCRJobRepository

PDF = b"%PDF-1.7\nSame private source bytes\n%%EOF"


def upload(client, *, name="original.pdf", payload=PDF, route="/api/native/uploads"):
    response = client.post(route, files={"file": (name, payload, "application/pdf")})
    assert response.status_code == 200, response.text
    assert {"book_id", "filename", "status", "next"} <= response.json().keys()
    return response.json()


@pytest.mark.parametrize("route", ["/api/books", "/api/native/uploads"])
def test_same_owner_reuses_renamed_pdf_and_completed_ocr_after_restart(native, monkeypatch, route):
    owner, _, root = native
    covers = []
    monkeypatch.setattr(api, "_enqueue_cover", covers.append)
    first = upload(owner, route=route)
    book_id = first["book_id"]
    jobs = api.job_repository
    jobs.enqueue(book_id=book_id, output_dir=root / "books" / book_id / "ocr", max_attempts=3)
    job = jobs.claim_next(owner="offline-fixture", lease_seconds=60)
    assert job is not None
    job.output_dir.mkdir(parents=True)
    artifact = job.output_dir / "existing-result.txt"
    artifact.write_text("preserve this OCR output")
    jobs.succeed(job_id=job.job_id, owner="offline-fixture", result=OCRRunResult(1, False, 1))
    jobs.save_structure(book_id, structure())
    original_job = jobs.get_job(book_id)
    original_structure = jobs.get_structure(book_id)

    monkeypatch.setattr(api, "job_repository", SQLiteOCRJobRepository(jobs.database_path))
    monkeypatch.setattr(api, "store", MemoryStore())
    duplicate = upload(owner, name="renamed.pdf", route=route)
    assert duplicate == {
        "book_id": book_id,
        "filename": "original.pdf",
        "status": "structured",
        "next": f"POST /api/books/{book_id}/process",
    }
    assert api.job_repository.get_job(book_id) == original_job
    assert api.job_repository.get_structure(book_id) == original_structure
    assert owner.post(f"/api/books/{book_id}/process").json()["status"] == "structured"
    assert api.job_repository.get_job(book_id) == original_job
    assert artifact.read_text() == "preserve this OCR output"
    assert covers == [book_id]
    assert [path.name for path in (root / "books").iterdir()] == [book_id]
    with jobs._connect() as db:
        assert db.execute("SELECT count(*) FROM books").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM ocr_jobs").fetchone()[0] == 1
    with api.account_repository.repo.connect() as db:
        assert db.execute("SELECT count(*) FROM upload_owners").fetchone()[0] == 1


def test_same_filename_with_different_pdf_bytes_creates_distinct_books(native):
    owner, _, _ = native
    first = upload(owner)
    second = upload(owner, payload=PDF + b"\nDifferent content")
    assert first["book_id"] != second["book_id"]
    assert api.job_repository.source_fingerprint(first["book_id"]) == hashlib.sha256(PDF).hexdigest()
    assert api.job_repository.source_fingerprint(second["book_id"]) != hashlib.sha256(PDF).hexdigest()


def test_same_pdf_in_another_account_never_reuses_or_exposes_original_book(native):
    owner, other, _ = native
    first = upload(owner, name="private-title.pdf")
    second = upload(other, name="other-title.pdf")
    assert second["book_id"] != first["book_id"]
    assert second["filename"] == "other-title.pdf"
    assert other.get(f"/api/books/{first['book_id']}/source").status_code == 403
    assert owner.get(f"/api/books/{second['book_id']}/source").status_code == 403
    actor = api.account_repository.session_owner(other.cookies.get("zhiwo_account"))
    assert api.account_repository.repo.owns_upload(actor, second["book_id"])
    assert not api.account_repository.repo.owns_upload(actor, first["book_id"])


@pytest.mark.parametrize("invalid_source", ["missing", "empty", "directory"])
def test_invalid_original_source_is_not_reused(native, invalid_source):
    owner, _, _ = native
    first = upload(owner)
    source = api.job_repository.get_book(first["book_id"]).file_path
    source.unlink()
    if invalid_source == "empty":
        source.touch()
    elif invalid_source == "directory":
        source.mkdir()
    second = upload(owner, name="recovered.pdf")
    assert second["book_id"] != first["book_id"]
    assert api.job_repository.get_book(second["book_id"]).file_path.read_bytes() == PDF
    assert upload(owner, name="again.pdf")["book_id"] == second["book_id"]


def test_concurrent_native_uploads_return_one_book_without_scheduling_ocr(native, monkeypatch):
    owner, _, root = native
    covers = []
    monkeypatch.setattr(api, "_enqueue_cover", covers.append)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda index: upload(owner, name=f"copy-{index}.pdf"), range(8)))
    assert len({receipt["book_id"] for receipt in receipts}) == 1
    assert len(covers) == 1
    assert len(list((root / "books").iterdir())) == 1
    with api.job_repository._connect() as db:
        assert db.execute("SELECT count(*) FROM books").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM ocr_jobs").fetchone()[0] == 0


def _register_in_process(arguments):
    root, index = arguments
    root = Path(root)
    jobs = SQLiteOCRJobRepository(root / "jobs.sqlite3")
    book, duplicate = jobs.register_owned_book(
        owner="same-account",
        ownership_database_path=root / "community.sqlite3",
        book_id=f"candidate-{index}",
        original_name=f"copy-{index}.pdf",
        file_path=root / f"candidate-{index}.pdf",
        source_sha256=hashlib.sha256(PDF).hexdigest(),
    )
    return book.book_id, duplicate


def test_duplicate_registration_is_atomic_between_api_processes(tmp_path):
    jobs = SQLiteOCRJobRepository(tmp_path / "jobs.sqlite3")
    owners = CommunityRepository(tmp_path / "community.sqlite3")
    for index in range(8):
        (tmp_path / f"candidate-{index}.pdf").write_bytes(PDF)
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
        receipts = list(pool.map(_register_in_process, [(str(tmp_path), index) for index in range(8)]))
    assert len({book_id for book_id, _ in receipts}) == 1
    assert [duplicate for _, duplicate in receipts].count(False) == 1
    with jobs._connect() as db:
        assert db.execute("SELECT count(*) FROM books").fetchone()[0] == 1
    with owners.connect() as db:
        assert db.execute("SELECT count(*) FROM upload_owners").fetchone()[0] == 1


def test_ownership_registration_failure_rolls_back_the_new_book(tmp_path):
    jobs = SQLiteOCRJobRepository(tmp_path / "jobs.sqlite3")
    owners = CommunityRepository(tmp_path / "community.sqlite3")
    owners.bind_upload("another-account", "candidate-0")
    (tmp_path / "candidate-0.pdf").write_bytes(PDF)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        _register_in_process((str(tmp_path), 0))
    assert jobs.get_book("candidate-0") is None
    assert owners.owns_upload("another-account", "candidate-0")


def test_upload_hashes_stream_once_without_rereading_saved_pdf(native, monkeypatch):
    owner, _, _ = native
    payload = PDF + b"x" * (1024 * 1024)
    account_cookie = owner.cookies.get("zhiwo_account")
    request = Request({
        "type": "http", "method": "POST", "path": "/api/books", "scheme": "http",
        "headers": [(b"cookie", f"zhiwo_account={account_cookie}".encode())],
        "server": ("testserver", 80), "client": ("127.0.0.1", 1),
    })
    original_open = Path.open

    def no_source_reread(path, mode="r", *args, **kwargs):
        assert not (path.name == "source.pdf" and "r" in mode), "saved PDF was read twice"
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_source_reread)
    stream = io.BytesIO(payload)
    result = asyncio.run(api.upload_book(UploadFile(filename="single-pass.pdf", file=stream), request, Response()))
    assert stream.tell() == len(payload)
    assert api.job_repository.source_fingerprint(result.book_id) == hashlib.sha256(payload).hexdigest()
