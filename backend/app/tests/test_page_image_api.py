from __future__ import annotations

from fastapi.testclient import TestClient
import fitz

from app.core.config import get_settings
from app.main import app
from app.services.storage import original_file_path


def _create_pdf(path) -> None:
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "Page one")
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Page two")
    doc.save(path)
    doc.close()


def test_book_page_image_renders_pdf_page(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    book_id = "book_page_image"
    original = original_file_path(book_id, "source.pdf")
    _create_pdf(original)
    client = TestClient(app)

    response = client.get(f"/api/books/{book_id}/pages/2/image")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
    assert (tmp_path / "books" / book_id / "artifacts" / "page_images" / "page_002.png").exists()
    get_settings.cache_clear()


def test_book_page_image_rejects_out_of_range_page(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    book_id = "book_page_image_out_of_range"
    original = original_file_path(book_id, "source.pdf")
    _create_pdf(original)
    client = TestClient(app)

    response = client.get(f"/api/books/{book_id}/pages/99/image")

    assert response.status_code == 404
    assert response.json()["code"] == "page_out_of_range"
    get_settings.cache_clear()
