from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.schemas.books import Chapter, Chunk
from app.services.artifact_store import read_lessons, write_chapters, write_chunks


def _seed_lesson_book(monkeypatch, tmp_path) -> str:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_LLM_PROVIDER", "template")
    get_settings.cache_clear()
    book_id = "book_lesson_api"
    write_chapters(
        book_id,
        [
            Chapter(
                chapter_id="c1",
                level=1,
                source_title="Chapter 1",
                ai_title="Lesson 1",
                page_start=1,
                page_end=3,
                confidence=90,
                status="confirmed",
                source="test",
            )
        ],
    )
    write_chunks(
        book_id,
        [
            Chunk(
                chunk_id="chunk_001",
                book_id=book_id,
                chapter_id="c1",
                page_start=1,
                page_end=1,
                content_type="text",
                text="Concept A is the first important idea in this chapter.",
            ),
            Chunk(
                chunk_id="chunk_002",
                book_id=book_id,
                chapter_id="c1",
                page_start=2,
                page_end=2,
                content_type="text",
                text="Concept B expands the chapter and gives a comparison.",
            ),
        ],
    )
    return book_id


def test_lesson_build_job_and_read_api(monkeypatch, tmp_path) -> None:
    book_id = _seed_lesson_book(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.post(f"/api/books/{book_id}/lessons/build", json={"chapter_ids": ["c1"]})

    assert response.status_code == 200
    job_id = response.json()["job_id"]
    job = client.get(f"/api/lesson-generation/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["status"] == "done"
    assert job.json()["lessons"][0]["lesson_id"] == "lesson_c1"
    assert job.json()["chapter_results"][0]["status"] == "done"

    lessons = client.get(f"/api/books/{book_id}/lessons")
    assert lessons.status_code == 200
    assert lessons.json()[0]["blocks"]
    lesson = client.get(f"/api/books/{book_id}/lessons/lesson_c1")
    assert lesson.status_code == 200
    assert lesson.json()["source_chunk_ids"] == ["chunk_001", "chunk_002"]
    assert read_lessons(book_id)[0].lesson_id == "lesson_c1"
    get_settings.cache_clear()


def test_flashcards_and_quizzes_are_generated_from_lessons(monkeypatch, tmp_path) -> None:
    book_id = _seed_lesson_book(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(f"/api/books/{book_id}/lessons/build", json={"chapter_ids": ["c1"]})

    cards = client.post(f"/api/books/{book_id}/flashcards/build", json={"chapter_ids": ["c1"]})
    quizzes = client.post(f"/api/books/{book_id}/quizzes/build", json={"chapter_ids": ["c1"]})

    assert cards.status_code == 200
    assert cards.json()
    assert cards.json()[0]["lesson_id"] == "lesson_c1"
    assert cards.json()[0]["source_chunk_ids"]
    assert cards.json()[0]["front"].startswith("解释：")
    assert quizzes.status_code == 200
    assert quizzes.json()[0]["source_chunk_ids"]
    assert quizzes.json()[0]["answer"] == "正确"

    saved_cards = client.get(f"/api/books/{book_id}/flashcards")
    saved_quizzes = client.get(f"/api/books/{book_id}/quizzes")
    assert saved_cards.json()[0]["card_id"].startswith("card_c1")
    assert saved_quizzes.json()[0]["question_id"].startswith("quiz_c1")
    get_settings.cache_clear()


def test_lesson_build_requires_chunks(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    book_id = "book_lesson_missing_chunks"
    write_chapters(
        book_id,
        [
            Chapter(
                chapter_id="c1",
                level=1,
                source_title="Chapter 1",
                ai_title="Lesson 1",
                page_start=1,
                page_end=1,
                confidence=90,
                status="confirmed",
                source="test",
            )
        ],
    )
    client = TestClient(app)

    response = client.post(f"/api/books/{book_id}/lessons/build", json={})

    assert response.status_code == 404
    assert response.json()["code"] == "artifact_missing"
    get_settings.cache_clear()
