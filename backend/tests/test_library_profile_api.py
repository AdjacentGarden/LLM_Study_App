from dataclasses import replace
from importlib import import_module

from fastapi.testclient import TestClient
from test_assessment_repository import session

from adaptive_learning.assessment.repository import SQLiteAssessmentRepository

api_module = import_module("adaptive_learning.api.app")


def test_covers_only_serve_published_existing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(
        api_module,
        "settings",
        replace(
            api_module.settings,
            data_dir=tmp_path,
            published_book_ids=("published", "missing"),
        ),
    )
    covers = tmp_path / "covers"
    covers.mkdir()
    image = b"\xff\xd8fixture\xff\xd9"
    (covers / "published.jpg").write_bytes(image)
    (covers / "private.jpg").write_bytes(image)
    with TestClient(api_module.app) as client:
        response = client.get("/api/books/published/cover")
        assert response.status_code == 200
        assert response.content == image
        assert response.headers["content-type"] == "image/jpeg"
        assert "max-age" in response.headers["cache-control"]
        assert client.get("/api/books/private/cover").status_code == 404
        assert client.get("/api/books/missing/cover").status_code == 404


def test_learning_record_endpoint_is_read_only_and_handles_missing_session(tmp_path, monkeypatch):
    repository = SQLiteAssessmentRepository(tmp_path / "records.sqlite3")
    value = session()
    repository.create_session(value)
    monkeypatch.setattr(api_module, "assessment_repository", repository)
    monkeypatch.setattr(api_module, "_items_for_book", lambda book_id: [])
    before = repository.get_session(value.session_id).model_dump_json()
    with TestClient(api_module.app) as client:
        response = client.get(f"/api/interviews/{value.session_id}/learning-records")
        assert response.status_code == 200
        assert response.json() == {
            "book_id": value.profile.book_id,
            "knowledge": [],
            "evidence": [],
            "flashcards": [],
        }
        assert client.get("/api/interviews/missing/learning-records").status_code == 404
    assert repository.get_session(value.session_id).model_dump_json() == before
