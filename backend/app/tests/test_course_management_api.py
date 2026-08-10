from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.services.storage import original_file_path


def test_delete_course_removes_book_from_storage_and_list(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    book_id = "book_delete_course"
    original = original_file_path(book_id, "source.pdf")
    original.write_bytes(b"%PDF-1.4\n% demo\n")
    client = TestClient(app)

    listed = client.get("/api/books")
    assert listed.status_code == 200
    assert any(item["book_id"] == book_id for item in listed.json())

    response = client.delete(f"/api/books/{book_id}")

    assert response.status_code == 204
    assert not (tmp_path / "books" / book_id).exists()
    listed_after_delete = client.get("/api/books")
    assert all(item["book_id"] != book_id for item in listed_after_delete.json())
    get_settings.cache_clear()


def test_delete_course_returns_404_for_missing_book(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.delete("/api/books/book_missing")

    assert response.status_code == 404
    assert response.json()["code"] == "book_not_found"
    get_settings.cache_clear()
