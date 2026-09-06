from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import routes_books
from app.core.config import get_settings
from app.document.mineru.task_store import mineru_task_store
from app.main import app
from app.services.job_store import JobRecord, job_store
from app.services.storage import original_file_path


def test_job_record_keeps_generation_fields_backward_compatible() -> None:
    legacy = JobRecord.from_dict(
        {
            "job_id": "job_legacy",
            "book_id": "book_legacy",
            "status": "pending",
            "stage": "queued",
            "progress": 0,
        }
    )

    assert legacy.parse_generation is None
    assert legacy.mineru_record_id is None


def test_parse_api_binds_one_job_and_enqueues_one_worker_per_generation(
    monkeypatch,
    tmp_path,
) -> None:
    book_id = "book_generation_binding"
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_USE_WORKER", "true")
    get_settings.cache_clear()
    original_file_path(book_id, "source.pdf").write_bytes(b"%PDF-1.4\nreserved parse")
    enqueued: list[tuple[object, tuple[object, ...]]] = []

    def capture_enqueue(fn, *args) -> None:
        enqueued.append((fn, args))

    monkeypatch.setattr(routes_books.task_queue, "enqueue", capture_enqueue)
    client = TestClient(app)

    first = client.post(f"/api/books/{book_id}/parse", json={})
    second = client.post(f"/api/books/{book_id}/parse", json={})

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["job_id"] == first.json()["job_id"]
    assert len(enqueued) == 1

    current = mineru_task_store.get_current(book_id)
    assert current is not None
    assert current.cloudpath_job_id == first.json()["job_id"]
    job = job_store.get(first.json()["job_id"])
    assert job is not None
    assert job.parse_generation == current.parse_generation
    assert job.mineru_record_id == current.record_id
    _, worker_args = enqueued[0]
    assert worker_args == (job.job_id, book_id, current.parse_generation)


def test_parse_worker_carries_generation_and_job_guard_once(monkeypatch, tmp_path) -> None:
    book_id = "book_generation_worker"
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    original = original_file_path(book_id, "source.pdf")
    original.write_bytes(b"%PDF-1.4\nworker guard")
    reservation = routes_books._reserve_parse_generation(book_id, original)  # noqa: SLF001
    job = job_store.create(
        book_id,
        stage="queued",
        parse_generation=reservation.record.parse_generation,
        mineru_record_id=reservation.record.record_id,
    )
    mineru_task_store.bind_cloudpath_job(
        book_id,
        reservation.record.parse_generation,
        job.job_id,
    )
    calls: list[tuple[int | None, str | None]] = []

    def fake_parse_document(
        received_book_id,
        received_original,
        received_artifacts,
        on_progress=None,
        *,
        expected_generation=None,
        cloudpath_job_id=None,
    ):
        assert received_book_id == book_id
        assert received_original == original
        assert received_artifacts.name == "artifacts"
        calls.append((expected_generation, cloudpath_job_id))
        assert on_progress is not None
        on_progress("mapper_guard", 50, "guarded")

    monkeypatch.setattr(routes_books, "parse_document", fake_parse_document)

    routes_books.run_parse_job(job.job_id, book_id, reservation.record.parse_generation)
    # A duplicated queue delivery cannot execute the same generation twice.
    routes_books.run_parse_job(job.job_id, book_id, reservation.record.parse_generation)

    assert calls == [(reservation.record.parse_generation, job.job_id)]
    stored_job = job_store.get(job.job_id)
    assert stored_job is not None
    assert stored_job.status == "done"
    record = mineru_task_store.get_record(book_id, reservation.record.parse_generation)
    assert record is not None
    assert record.worker_outcome == "succeeded"


def test_completed_parse_reuses_persisted_backend_cache(monkeypatch, tmp_path) -> None:
    book_id = "book_completed_cache"
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_USE_WORKER", "true")
    get_settings.cache_clear()
    original = original_file_path(book_id, "source.pdf")
    original.write_bytes(b"%PDF-1.4\npersisted parse cache")
    enqueued: list[tuple[object, tuple[object, ...]]] = []

    monkeypatch.setattr(routes_books.task_queue, "enqueue", lambda fn, *args: enqueued.append((fn, args)))
    client = TestClient(app)
    first = client.post(f"/api/books/{book_id}/parse", json={})
    assert first.status_code == 200, first.text
    assert len(enqueued) == 1

    current = mineru_task_store.get_current(book_id)
    assert current is not None and current.cloudpath_job_id
    mineru_task_store.claim_worker(book_id, current.parse_generation, current.cloudpath_job_id)
    mineru_task_store.finish_worker(
        book_id,
        current.parse_generation,
        current.cloudpath_job_id,
        succeeded=True,
    )
    job_store.update(
        current.cloudpath_job_id,
        status="done",
        stage="done",
        progress=100,
        message="解析完成",
    )
    monkeypatch.setattr(routes_books, "_parse_cache_artifacts_ready", lambda candidate: candidate == book_id)

    second = client.post(f"/api/books/{book_id}/parse", json={})

    assert second.status_code == 200, second.text
    assert second.json() == {"book_id": book_id, "job_id": first.json()["job_id"], "status": "done"}
    assert len(enqueued) == 1
    assert mineru_task_store.get_current(book_id).parse_generation == current.parse_generation  # type: ignore[union-attr]
    assert job_store.get(current.cloudpath_job_id).message == "已命中后端解析缓存，无需重复解析"  # type: ignore[union-attr]
