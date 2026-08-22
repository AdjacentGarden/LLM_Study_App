from __future__ import annotations

from hashlib import sha256
from multiprocessing import get_context
import os
from threading import Event, Thread

import fitz
from fastapi.testclient import TestClient

from app.community import catalog as catalog_service
from app.main import app
from app.services.storage import find_original_file, list_book_ids, storage_root


def _hold_import_lock_in_process(
    root: str,
    acquired,
    release,
) -> None:
    os.environ["BOOKCOURSE_STORAGE_ROOT"] = root
    from app.core.config import get_settings
    from app.community.catalog import _cross_process_import_lock

    get_settings.cache_clear()
    with _cross_process_import_lock("same-user", "same-catalog"):
        acquired.set()
        release.wait(timeout=10)


def _real_pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 96), "Calculus Made Easy - verified community import")
    page.insert_text((72, 132), "CHAPTER I. DIFFERENTIAL CALCULUS")
    payload = document.tobytes()
    document.close()
    return payload


def test_catalog_exposes_real_sources_and_real_cover_assets() -> None:
    response = TestClient(app).get("/api/community/books")
    assert response.status_code == 200
    books = response.json()
    assert [book["id"] for book in books] == [entry.id for entry in catalog_service.CATALOG]
    assert all(book["page_count"] > 100 for book in books)
    assert all(book["file_size_bytes"] > 500_000 for book in books)
    assert all("public-domain" in book["license_name"] for book in books)
    assert all(book["imported_book_id"] is None for book in books)

    cover = TestClient(app).get(f"/api/community/books/{books[0]['id']}/cover")
    assert cover.status_code == 200
    assert cover.headers["content-type"].startswith("image/webp")
    assert len(cover.content) > 5_000


def test_import_persists_a_real_pdf_and_is_idempotent(monkeypatch) -> None:
    pdf_bytes = _real_pdf_bytes()

    def write_verified_fixture(_entry, destination) -> None:
        destination.write_bytes(pdf_bytes)

    monkeypatch.setattr(catalog_service, "_stream_verified_pdf", write_verified_fixture)
    client = TestClient(app)
    catalog_id = catalog_service.CATALOG[0].id

    imported = client.post(f"/api/community/books/{catalog_id}/import")
    assert imported.status_code == 200
    payload = imported.json()
    assert payload["already_imported"] is False
    original = find_original_file(payload["book_id"])
    assert original is not None
    assert sha256(original.read_bytes()).hexdigest() == sha256(pdf_bytes).hexdigest()
    cached = catalog_service.cached_community_source(catalog_service.CATALOG[0])
    assert cached.is_file()
    assert cached.parent.parent == storage_root() / catalog_service._LIBRARY_DIRECTORY_NAME
    assert cached.parent.parent != storage_root() / "books"

    repeated = client.post(f"/api/community/books/{catalog_id}/import")
    assert repeated.status_code == 200
    assert repeated.json()["book_id"] == payload["book_id"]
    assert repeated.json()["already_imported"] is True

    refreshed_catalog = client.get("/api/community/books").json()
    refreshed = next(book for book in refreshed_catalog if book["id"] == catalog_id)
    assert refreshed["imported_book_id"] == payload["book_id"]

    courses = client.get("/api/books").json()
    course = next(item for item in courses if item["book_id"] == payload["book_id"])
    assert course["title"] == catalog_service.CATALOG[0].title
    assert course["page_count"] == catalog_service.CATALOG[0].page_count
    assert course["source_catalog_id"] == catalog_id
    assert course["cover_url"].endswith(f"/{catalog_id}/cover")


def test_failed_import_removes_partial_course(monkeypatch) -> None:
    def reject_source(_entry, destination) -> None:
        destination.write_bytes(b"not a pdf")
        raise catalog_service.AppError(
            "community_source_integrity_failed",
            "社区教材完整性校验失败，已拒绝导入",
            status_code=409,
        )

    monkeypatch.setattr(catalog_service, "_stream_verified_pdf", reject_source)
    client = TestClient(app)
    response = client.post(f"/api/community/books/{catalog_service.CATALOG[1].id}/import")
    assert response.status_code == 409
    assert client.get("/api/books").json() == []
    staging = storage_root() / catalog_service._STAGING_DIRECTORY_NAME
    assert list(staging.iterdir()) == []


def test_in_progress_import_is_not_visible_as_a_ghost_course(monkeypatch) -> None:
    pdf_bytes = _real_pdf_bytes()
    download_started = Event()
    finish_download = Event()
    responses = []

    def blocked_download(_entry, destination) -> None:
        download_started.set()
        assert finish_download.wait(timeout=5)
        destination.write_bytes(pdf_bytes)

    monkeypatch.setattr(catalog_service, "_stream_verified_pdf", blocked_download)
    catalog_id = catalog_service.CATALOG[0].id

    def import_in_background() -> None:
        responses.append(TestClient(app).post(f"/api/community/books/{catalog_id}/import"))

    worker = Thread(target=import_in_background)
    worker.start()
    assert download_started.wait(timeout=5)
    try:
        assert list_book_ids() == []
        assert TestClient(app).get("/api/books").json() == []
        catalog = TestClient(app).get("/api/community/books").json()
        pending = next(book for book in catalog if book["id"] == catalog_id)
        assert pending["imported_book_id"] is None
    finally:
        finish_download.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert responses[0].status_code == 200
    assert len(list_book_ids()) == 1


def test_metadata_failure_never_publishes_a_partial_course(monkeypatch) -> None:
    pdf_bytes = _real_pdf_bytes()

    def write_verified_fixture(_entry, destination) -> None:
        destination.write_bytes(pdf_bytes)

    def fail_metadata(_path, _entry) -> None:
        raise RuntimeError("simulated process failure before atomic publish")

    monkeypatch.setattr(catalog_service, "_stream_verified_pdf", write_verified_fixture)
    monkeypatch.setattr(catalog_service, "_write_community_source_metadata", fail_metadata)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(f"/api/community/books/{catalog_service.CATALOG[2].id}/import")

    assert response.status_code == 500
    assert list_book_ids() == []
    assert client.get("/api/books").json() == []
    staging = storage_root() / catalog_service._STAGING_DIRECTORY_NAME
    assert list(staging.iterdir()) == []


def test_import_lock_serializes_independent_worker_processes() -> None:
    context = get_context("spawn")
    first_acquired = context.Event()
    first_release = context.Event()
    second_acquired = context.Event()
    second_release = context.Event()
    root = str(storage_root())
    first = context.Process(
        target=_hold_import_lock_in_process,
        args=(root, first_acquired, first_release),
    )
    second = context.Process(
        target=_hold_import_lock_in_process,
        args=(root, second_acquired, second_release),
    )
    first.start()
    assert first_acquired.wait(timeout=10)
    second.start()
    try:
        assert not second_acquired.wait(timeout=0.5)
        first_release.set()
        assert second_acquired.wait(timeout=10)
    finally:
        first_release.set()
        second_release.set()
        first.join(timeout=10)
        second.join(timeout=10)
        if first.is_alive():
            first.terminate()
        if second.is_alive():
            second.terminate()

    assert first.exitcode == 0
    assert second.exitcode == 0
