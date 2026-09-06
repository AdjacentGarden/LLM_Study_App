from __future__ import annotations

from io import BytesIO

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import get_settings
from app.core.limits import heavy_task_limiter
from app.main import app, create_app
from app.schemas.books import Asset
from app.services.artifact_store import read_assets, write_assets
from app.services.storage import original_file_path


@pytest.fixture(autouse=True)
def reset_security_state():
    yield
    heavy_task_limiter.reset()
    get_settings.cache_clear()


def _png_bytes(width: int = 1, height: int = 1) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color=(255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf_bytes(page_count: int = 1) -> bytes:
    document = fitz.open()
    for _ in range(page_count):
        document.new_page(width=72, height=72)
    payload = document.tobytes()
    document.close()
    return payload


def test_business_api_key_is_optional_by_default_and_health_stays_public(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    health = client.get("/api/health")
    response = client.post("/api/uploads/init", json={"filename": "source.pdf", "size_bytes": 12})

    assert health.status_code == 200
    assert response.status_code == 200


def test_business_api_key_is_required_when_configured(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_API_KEY", "secret-key")
    get_settings.cache_clear()
    client = TestClient(app)

    health = client.get("/api/health")
    blocked = client.get("/api/books")
    allowed = client.get("/api/books", headers={"X-BookCourse-Api-Key": "secret-key"})

    assert health.status_code == 200
    assert blocked.status_code == 401
    assert blocked.json()["code"] == "invalid_api_key"
    assert allowed.status_code == 200


def test_cors_credentials_are_configurable(monkeypatch) -> None:
    monkeypatch.delenv("BOOKCOURSE_ALLOW_CREDENTIALS", raising=False)
    get_settings.cache_clear()
    default_client = TestClient(create_app())
    default_response = default_client.options(
        "/api/health",
        headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "GET"},
    )

    monkeypatch.setenv("BOOKCOURSE_ALLOW_CREDENTIALS", "true")
    get_settings.cache_clear()
    credentialed_client = TestClient(create_app())
    credentialed_response = credentialed_client.options(
        "/api/health",
        headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "GET"},
    )

    assert "access-control-allow-credentials" not in default_response.headers
    assert credentialed_response.headers["access-control-allow-credentials"] == "true"


def test_upload_rejects_extension_content_mismatch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)
    init = client.post("/api/uploads/init", json={"filename": "fake.pdf", "size_bytes": 9})
    book_id = init.json()["book_id"]

    response = client.post(
        f"/api/books/{book_id}/files",
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "file_content_mismatch"


def test_upload_init_rejects_mismatched_declared_content_type(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.post("/api/uploads/init", json={"filename": "source.pdf", "content_type": "image/png", "size_bytes": 12})

    assert response.status_code == 400
    assert response.json()["code"] == "file_content_type_mismatch"


def test_upload_save_rejects_mismatched_declared_content_type(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)
    init = client.post("/api/uploads/init", json={"filename": "image.png", "content_type": "application/octet-stream", "size_bytes": 128})
    book_id = init.json()["book_id"]

    response = client.post(
        f"/api/books/{book_id}/files",
        files={"file": ("image.png", _png_bytes(), "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "file_content_type_mismatch"


def test_upload_rejects_pdf_over_page_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_MAX_PDF_PAGES", "1")
    get_settings.cache_clear()
    client = TestClient(app)
    init = client.post("/api/uploads/init", json={"filename": "too-long.pdf", "size_bytes": 128})
    book_id = init.json()["book_id"]

    response = client.post(
        f"/api/books/{book_id}/files",
        files={"file": ("too-long.pdf", _pdf_bytes(2), "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "pdf_page_limit_exceeded"


def test_upload_rejects_image_over_pixel_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_MAX_IMAGE_PIXELS", "1")
    get_settings.cache_clear()
    client = TestClient(app)
    init = client.post("/api/uploads/init", json={"filename": "large.png", "size_bytes": 128})
    book_id = init.json()["book_id"]

    response = client.post(
        f"/api/books/{book_id}/files",
        files={"file": ("large.png", _png_bytes(width=2, height=1), "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "image_pixel_limit_exceeded"


def test_parse_entry_returns_app_error_when_task_limit_is_reached(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_PARSE_MAX_CONCURRENT", "1")
    get_settings.cache_clear()
    original_file_path("book_limited", "source.pdf").write_bytes(b"%PDF-1.4\n% placeholder")
    heavy_task_limiter.start("parse", max_concurrent=1, max_per_minute=60)
    client = TestClient(app)

    response = client.post("/api/books/book_limited/parse", json={})

    assert response.status_code == 429
    assert response.json()["code"] == "heavy_task_concurrency_limited"


def test_public_asset_response_hides_generation_prompt(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    book_id = "book_assets_public"
    write_assets(
        book_id,
        [
            Asset(
                asset_id="ai_fig_001",
                book_id=book_id,
                chapter_id="c1",
                source_type="ai_generated",
                page=None,
                type="diagram",
                caption="AI figure",
                bbox=None,
                image_url="/api/books/book_assets_public/assets/ai_fig_001/file",
                thumbnail_url="/api/books/book_assets_public/assets/ai_fig_001/thumbnail",
                source_page_image_url=None,
                source_chunk_ids=["chunk_001"],
                concepts=["concept"],
                generation_provider="mock",
                generation_prompt="internal prompt should not leave the API",
                review_status="pending",
            )
        ],
    )
    client = TestClient(app)

    response = client.get(f"/api/books/{book_id}/assets")

    assert response.status_code == 200
    assert "generation_prompt" not in response.json()[0]
    assert read_assets(book_id)[0].generation_prompt == "internal prompt should not leave the API"
