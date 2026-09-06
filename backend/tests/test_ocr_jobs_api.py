from __future__ import annotations

from dataclasses import replace
from importlib import import_module
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository

api_module = import_module("adaptive_learning.api.app")


def configure_temporary_queue(tmp_path: Path, monkeypatch: MonkeyPatch) -> SQLiteOCRJobRepository:
    repository = SQLiteOCRJobRepository(tmp_path / "state" / "jobs.sqlite3")
    monkeypatch.setattr(api_module, "job_repository", repository)
    monkeypatch.setattr(
        api_module,
        "settings",
        replace(api_module.settings, data_dir=tmp_path, ocr_worker_enabled=False),
    )
    return repository


def test_upload_enqueue_status_and_restart_hydration(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    configure_temporary_queue(tmp_path, monkeypatch)
    with TestClient(api_module.app) as client:
        upload = client.post(
            "/api/books",
            files={"file": ("教材.pdf", b"%PDF-1.7\nfixture", "application/pdf")},
        )
        assert upload.status_code == 200
        book_id = upload.json()["book_id"]

        first = client.post(f"/api/books/{book_id}/process")
        repeated = client.post(f"/api/books/{book_id}/process")

        assert first.status_code == 200
        assert first.json()["status"] == "queued"
        assert first.json()["max_attempts"] == 3
        assert repeated.json() == first.json()

        del api_module.store.books[book_id]
        hydrated = client.get(f"/api/books/{book_id}/status")

    assert hydrated.status_code == 200
    assert hydrated.json()["book_id"] == book_id
    assert hydrated.json()["status"] == "queued"


def test_retry_endpoint_only_accepts_terminal_retryable_state(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    repository = configure_temporary_queue(tmp_path, monkeypatch)
    with TestClient(api_module.app) as client:
        upload = client.post(
            "/api/books",
            files={"file": ("教材.pdf", b"%PDF-1.7\nfixture", "application/pdf")},
        )
        book_id = upload.json()["book_id"]
        client.post(f"/api/books/{book_id}/process")

        premature = client.post(f"/api/books/{book_id}/process/retry")
        assert premature.status_code == 409

        claimed = repository.claim_next(owner="test", lease_seconds=10)
        assert claimed is not None
        repository.fail(
            job_id=claimed.job_id,
            owner="test",
            error="fixture failure",
            retry_delay_seconds=0,
        )
        # Exhaust the configured attempt budget without running an OCR subprocess.
        for _ in range(2):
            claimed = repository.claim_next(owner="test", lease_seconds=10)
            assert claimed is not None
            repository.fail(
                job_id=claimed.job_id,
                owner="test",
                error="fixture failure",
                retry_delay_seconds=0,
            )

        failed = client.get(f"/api/books/{book_id}/status")
        retried = client.post(f"/api/books/{book_id}/process/retry")

    assert failed.json()["status"] == "failed"
    assert failed.json()["retryable"] is True
    assert retried.status_code == 200
    assert retried.json()["status"] == "queued"
    assert retried.json()["attempts"] == 0
