from __future__ import annotations

from app.core.config import get_settings
from app.services.job_store import JobStore


def test_persisted_running_jobs_are_failed_after_restart(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("BOOKCOURSE_PERSIST_STATE", "true")
    get_settings.cache_clear()
    try:
        store = JobStore()
        pending = store.create("book_pending")
        processing = store.create("book_processing")
        store.update(processing.job_id, status="processing", stage="ocr_running", progress=42)
        done = store.create("book_done")
        store.update(done.job_id, status="done", stage="done", progress=100)

        restarted = JobStore()
        interrupted = restarted.recover_interrupted()

        assert {record.job_id for record in interrupted} == {pending.job_id, processing.job_id}
        assert restarted.get(pending.job_id).error == "worker_restarted"
        assert restarted.get(processing.job_id).status == "failed"
        assert restarted.get(done.job_id).status == "done"
    finally:
        get_settings.cache_clear()
