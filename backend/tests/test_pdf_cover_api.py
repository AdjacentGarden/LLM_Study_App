import asyncio
import io
import sqlite3
from dataclasses import replace
from importlib import import_module
from types import SimpleNamespace

from fastapi.testclient import TestClient
from fastapi import Response, UploadFile
from starlette.requests import Request

from adaptive_learning.api.store import MemoryStore

api = import_module("adaptive_learning.api.app")


def test_private_cover_requires_upload_or_shelf_ownership(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF_COVER_ENABLED", "false")
    monkeypatch.setattr(api, "settings", replace(api.settings, data_dir=tmp_path, published_book_ids=("published",)))
    monkeypatch.setattr(api, "_backfill_active_covers", lambda: None)
    repo = SimpleNamespace(
        is_public_book=lambda book: False,
        owns_upload=lambda owner, book: owner == "uploader" and book == "private",
        owns=lambda owner, book: owner == "reader" and book == "private",
    )
    monkeypatch.setattr(api, "account_repository", SimpleNamespace(
        repo=repo, request_owner=lambda request: request.headers.get("x-fixture-owner")))
    directory = tmp_path / "covers"
    directory.mkdir()
    for book in ("private", "published"):
        (directory / f"{book}.jpg").write_bytes(b"offline-jpeg-fixture")
    with TestClient(api.app) as client:
        assert client.get("/api/books/private/cover").status_code == 404
        assert client.get("/api/books/private/cover", headers={"x-fixture-owner": "other"}).status_code == 404
        for owner in ("uploader", "reader"):
            response = client.get("/api/books/private/cover?v=example", headers={"x-fixture-owner": owner})
            assert response.status_code == 200 and response.content == b"offline-jpeg-fixture"
            assert response.headers["cache-control"] == "private, no-store"
            assert "Cookie" in response.headers["vary"].split(", ")
            assert response.headers["x-content-type-options"] == "nosniff"
        assert client.get("/api/books/published/cover").headers["cache-control"].startswith("public")


def test_enqueue_uses_current_repository_and_data_directory(tmp_path, monkeypatch):
    repository, calls = object(), []
    monkeypatch.setattr(api, "settings", replace(api.settings, data_dir=tmp_path))
    monkeypatch.setattr(api, "job_repository", repository)

    def current_service(directory, jobs):
        assert directory == tmp_path and jobs is repository
        return SimpleNamespace(enqueue=lambda book: calls.append(book))

    monkeypatch.setattr(api, "get_cover_service", current_service)
    api._enqueue_cover("uploaded-book")
    assert calls == ["uploaded-book"]


def test_startup_backfill_only_considers_active_resources_and_published_books(tmp_path, monkeypatch):
    seen = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, query):
            assert "native_resources" in query and "JOIN native_workspaces" in query
            return [("active-upload",)]

    monkeypatch.setattr(api, "settings", replace(api.settings, data_dir=tmp_path, published_book_ids=("published",)))
    monkeypatch.setattr(api, "native_repository", SimpleNamespace(connect=Connection))
    monkeypatch.setattr(api, "_enqueue_cover", seen.append)
    api._backfill_active_covers()
    assert set(seen) == {"published", "active-upload"}


def test_cover_database_failure_does_not_fail_successfully_registered_upload(tmp_path, monkeypatch):
    registered, bound = [], []
    monkeypatch.setattr(api, "settings", replace(api.settings, data_dir=tmp_path))
    monkeypatch.setattr(api, "store", MemoryStore())
    monkeypatch.setattr(api, "job_repository", SimpleNamespace(register_book=lambda **kwargs: registered.append(kwargs)))
    monkeypatch.setattr(api, "account_repository", SimpleNamespace(
        request_owner=lambda request: "uploader", repo=SimpleNamespace(
            bind_upload=lambda owner, book_id: bound.append((owner, book_id)))))

    def unavailable(book_id):
        raise sqlite3.OperationalError("offline cover storage failure")

    monkeypatch.setattr(api, "get_cover_service", lambda *args: SimpleNamespace(enqueue=unavailable))
    request = Request({"type": "http", "method": "POST", "path": "/api/books", "headers": [],
                       "scheme": "http", "server": ("testserver", 80), "client": ("127.0.0.1", 1)})
    upload = UploadFile(filename="textbook.pdf", file=io.BytesIO(b"%PDF-1.7\nOffline upload fixture"))
    result = asyncio.run(api.upload_book(upload, request, Response()))
    assert result.status == "uploaded"
    assert registered[0]["book_id"] == result.book_id
    assert registered[0]["file_path"].is_file()
    assert bound == [("uploader", result.book_id)]
