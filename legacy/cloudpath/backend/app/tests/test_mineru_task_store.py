from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.document.mineru.exceptions import MinerUProtocolError, MinerUStaleResultError
from app.document.mineru.task_store import MinerUTaskStore, make_idempotency_key


ENDPOINT = "http://127.0.0.1:8001"
FILE_SHA = "a" * 64
OPTIONS_SHA = "b" * 64


def _store() -> MinerUTaskStore:
    return MinerUTaskStore(namespace=f"mineru_tasks_test_{uuid4().hex}")


def _begin(
    store: MinerUTaskStore,
    *,
    book_id: str = "book_1",
    file_sha256: str = FILE_SHA,
    options_sha256: str = OPTIONS_SHA,
):
    return store.begin_or_resume(
        book_id=book_id,
        file_sha256=file_sha256,
        options_sha256=options_sha256,
        endpoint=ENDPOINT,
    )


def _attach(store: MinerUTaskStore, *, book_id: str = "book_1", task_id: str = "remote-1"):
    record = store.get_current(book_id)
    assert record is not None
    store.mark_submitting(book_id, record.parse_generation)
    return store.attach_remote_task(book_id, record.parse_generation, task_id, queued_ahead=2)


def test_idempotency_key_normalizes_endpoint_trailing_slash() -> None:
    without_slash = make_idempotency_key("book", FILE_SHA, OPTIONS_SHA, ENDPOINT)
    with_slash = make_idempotency_key("book", FILE_SHA, OPTIONS_SHA, ENDPOINT + "/")

    assert without_slash == with_slash
    assert without_slash != make_idempotency_key("book", "c" * 64, OPTIONS_SHA, ENDPOINT)


def test_created_record_is_reused_but_submission_claim_is_single_use() -> None:
    store = _store()
    first = _begin(store)
    second = _begin(store)

    assert first.resumed is False
    assert second.resumed is True
    assert second.record.record_id == first.record.record_id
    assert second.record.parse_generation == 1

    claimed = store.mark_submitting("book_1", first.record.parse_generation)
    assert claimed.status == "submitting"

    with pytest.raises(MinerUProtocolError, match="claim has already been consumed"):
        store.mark_submitting("book_1", first.record.parse_generation)

    after_claim = _begin(store)
    assert after_claim.resumed is True
    assert after_claim.record.status == "submitting"
    assert after_claim.record.parse_generation == 1
    with pytest.raises(MinerUProtocolError):
        store.mark_submitting("book_1", after_claim.record.parse_generation)


def test_submission_claim_is_atomic_between_two_threads() -> None:
    store = _store()
    generation = _begin(store).record.parse_generation

    def claim() -> str:
        try:
            store.mark_submitting("book_1", generation)
            return "claimed"
        except MinerUProtocolError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: claim(), range(2)))

    assert results.count("claimed") == 1
    assert results.count("rejected") == 1
    assert store.get_current("book_1").status == "submitting"  # type: ignore[union-attr]


def test_artifact_commit_runs_only_for_current_generation() -> None:
    store = _store()
    first = _begin(store).record
    committed: list[int] = []

    store.commit_if_current("book_1", first.parse_generation, lambda: committed.append(first.parse_generation))
    assert committed == [1]

    second = _begin(store, file_sha256="c" * 64).record
    assert second.parse_generation == 2
    with pytest.raises(MinerUStaleResultError):
        store.commit_if_current("book_1", first.parse_generation, lambda: committed.append(first.parse_generation))

    assert committed == [1]


def test_generation_reservation_waits_for_local_artifact_commit_lock() -> None:
    from threading import Event

    store = _store()
    first = _begin(store).record
    entered = Event()
    release = Event()

    def commit() -> None:
        def action() -> None:
            entered.set()
            assert release.wait(timeout=2)

        store.commit_if_current("book_1", first.parse_generation, action)

    with ThreadPoolExecutor(max_workers=2) as executor:
        commit_future = executor.submit(commit)
        assert entered.wait(timeout=2)
        reservation_future = executor.submit(_begin, store, file_sha256="d" * 64)
        assert not reservation_future.done()
        release.set()
        commit_future.result(timeout=2)
        second = reservation_future.result(timeout=2)

    assert second.record.parse_generation == 2


def test_submission_uncertain_is_reused_without_automatic_resubmission() -> None:
    store = _store()
    record = _begin(store).record
    store.mark_submitting("book_1", record.parse_generation)
    uncertain = store.mark_submission_uncertain(
        "book_1",
        record.parse_generation,
        detail=r"connection dropped after reading D:\private\lesson.pdf",
    )

    assert uncertain.status == "submission_uncertain"
    assert uncertain.error_code == "mineru_submission_uncertain"
    assert "D:\\private" not in (uncertain.error_detail or "")
    assert "[local-path]" in (uncertain.error_detail or "")
    assert store.list_resumable() == []

    resumed = _begin(store)
    assert resumed.resumed is True
    assert resumed.record.record_id == record.record_id
    assert resumed.record.status == "submission_uncertain"
    with pytest.raises(MinerUProtocolError):
        store.mark_submitting("book_1", record.parse_generation)


def test_remote_task_with_id_is_resumable_and_attach_is_idempotent() -> None:
    store = _store()
    record = _begin(store).record
    attached = _attach(store)

    assert attached.status == "submitted"
    assert attached.mineru_task_id == "remote-1"
    assert attached.queued_ahead == 2
    assert [item.record_id for item in store.list_resumable()] == [record.record_id]

    duplicate = store.attach_remote_task("book_1", record.parse_generation, "remote-1", queued_ahead=99)
    assert duplicate == attached

    resumed = _begin(store)
    assert resumed.resumed is True
    assert resumed.record.mineru_task_id == "remote-1"
    assert resumed.record.parse_generation == record.parse_generation


def test_remote_status_without_task_id_becomes_uncertain_not_resubmittable() -> None:
    store = _store()
    original = _begin(store).record

    def corrupt(raw_state: dict | None) -> dict:
        state = dict(raw_state or {})
        records = dict(state["records"])
        current = dict(records[state["current_record_id"]])
        current["status"] = "pending"
        current["mineru_task_id"] = None
        records[current["record_id"]] = current
        return {**state, "records": records}

    # Simulate a legacy/crash-written record that advanced without persisting
    # the remote id.  The safe response is to block, never to POST again.
    store._kv.update_in_place("book_1", corrupt)  # noqa: SLF001
    resumed = _begin(store)

    assert resumed.resumed is True
    assert resumed.record.record_id == original.record_id
    assert resumed.record.status == "submission_uncertain"
    assert resumed.record.error_code == "mineru_task_id_missing"
    assert store.list_resumable() == []
    with pytest.raises(MinerUProtocolError):
        store.mark_submitting("book_1", original.parse_generation)


def test_new_generation_discards_old_and_rejects_late_updates() -> None:
    store = _store()
    first = _begin(store).record
    _attach(store)
    store.update_remote_status("book_1", first.parse_generation, "processing")

    second = _begin(store, options_sha256="c" * 64).record

    assert second.parse_generation == first.parse_generation + 1
    assert store.is_current("book_1", second.parse_generation)
    old = store.get_record("book_1", first.parse_generation)
    assert old is not None
    assert old.status == "discarded_stale"
    assert old.error_code == "mineru_stale_generation"
    with pytest.raises(MinerUStaleResultError):
        store.update_remote_status("book_1", first.parse_generation, "completed")
    with pytest.raises(MinerUStaleResultError):
        store.accept_result("book_1", first.parse_generation, "d" * 64)


def test_result_digest_is_idempotent_but_conflicts_are_rejected() -> None:
    store = _store()
    record = _begin(store).record
    _attach(store)
    store.update_remote_status("book_1", record.parse_generation, "completed")

    first = store.accept_result("book_1", record.parse_generation, "D" * 64)
    duplicate = store.accept_result("book_1", record.parse_generation, "d" * 64)

    assert first.status == "completed"
    assert first.result_digest == "d" * 64
    assert duplicate == first
    with pytest.raises(MinerUProtocolError, match="conflicting results"):
        store.accept_result("book_1", record.parse_generation, "e" * 64)


def test_conflicting_result_claims_are_serialized_atomically() -> None:
    store = _store()
    record = _begin(store).record
    _attach(store)
    store.update_remote_status("book_1", record.parse_generation, "completed")

    def accept(digest: str) -> str:
        try:
            store.accept_result("book_1", record.parse_generation, digest)
            return "accepted"
        except MinerUProtocolError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(accept, ("d" * 64, "e" * 64)))

    assert outcomes.count("accepted") == 1
    assert outcomes.count("conflict") == 1
    stored = store.get_record("book_1", record.parse_generation)
    assert stored is not None
    assert stored.status == "completed"
    assert stored.result_digest in {"d" * 64, "e" * 64}


def test_timeout_is_terminal_and_late_result_is_rejected() -> None:
    store = _store()
    record = _begin(store).record
    _attach(store)
    store.update_remote_status("book_1", record.parse_generation, "processing")

    timed_out = store.mark_timed_out("book_1", record.parse_generation, detail="total timeout")

    assert timed_out.status == "timed_out"
    assert timed_out.error_code == "mineru_timeout"
    assert store.list_resumable() == []
    with pytest.raises(MinerUStaleResultError):
        store.accept_result("book_1", record.parse_generation, "d" * 64)
    with pytest.raises(MinerUStaleResultError):
        store.update_remote_status("book_1", record.parse_generation, "completed")

    blocked_retry = _begin(store)
    assert blocked_retry.resumed is True
    assert blocked_retry.record.parse_generation == record.parse_generation
    assert blocked_retry.record.status == "timed_out"

    store.invalidate("book_1", record.parse_generation, detail="explicit operator retry")
    explicit_retry = _begin(store)
    assert explicit_retry.resumed is False
    assert explicit_retry.record.parse_generation == record.parse_generation + 1


def test_404_expiration_is_terminal_and_not_resumed() -> None:
    store = _store()
    record = _begin(store).record
    _attach(store)

    expired = store.mark_expired("book_1", record.parse_generation, detail="GET status returned HTTP 404")

    assert expired.status == "expired"
    assert expired.error_code == "mineru_task_expired"
    assert "404" in (expired.error_detail or "")
    assert store.list_resumable() == []
    with pytest.raises(MinerUStaleResultError):
        store.accept_result("book_1", record.parse_generation, "d" * 64)


def test_invalidate_clears_current_and_rejects_late_result() -> None:
    store = _store()
    first = _begin(store).record
    _attach(store)
    store.update_remote_status("book_1", first.parse_generation, "completed")

    invalidated = store.invalidate("book_1", first.parse_generation, detail="book was deleted")

    assert invalidated.status == "discarded_stale"
    assert store.get_current("book_1") is None
    assert store.is_current("book_1", first.parse_generation) is False
    assert store.get_record("book_1", first.parse_generation) == invalidated
    with pytest.raises(MinerUStaleResultError):
        store.accept_result("book_1", first.parse_generation, "d" * 64)

    second = _begin(store).record
    assert second.parse_generation == first.parse_generation + 1


def test_reload_recovers_only_current_remote_tasks_with_task_ids(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_PERSIST_STATE", "true")
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    namespace = f"mineru_tasks_reload_{uuid4().hex}"
    original = MinerUTaskStore(namespace=namespace)

    remote = _begin(original, book_id="book_remote").record
    _attach(original, book_id="book_remote", task_id="remote-persisted")
    original.update_remote_status("book_remote", remote.parse_generation, "processing")

    created = _begin(original, book_id="book_created").record

    uncertain = _begin(original, book_id="book_uncertain").record
    original.mark_submitting("book_uncertain", uncertain.parse_generation)
    original.mark_submission_uncertain("book_uncertain", uncertain.parse_generation, detail="unknown acceptance")

    reloaded = MinerUTaskStore(namespace=namespace)
    resumable = reloaded.list_resumable()

    assert [(item.book_id, item.mineru_task_id) for item in resumable] == [
        ("book_remote", "remote-persisted")
    ]
    assert reloaded.get_record("book_remote", remote.parse_generation).status == "processing"  # type: ignore[union-attr]

    reused_created = _begin(reloaded, book_id="book_created")
    assert reused_created.resumed is True
    assert reused_created.record.record_id == created.record_id
    reused_uncertain = _begin(reloaded, book_id="book_uncertain")
    assert reused_uncertain.resumed is True
    assert reused_uncertain.record.status == "submission_uncertain"

    # Exercise the explicit reload method as well as reconstruction.
    reloaded.reload()
    assert reloaded.get_current("book_remote").mineru_task_id == "remote-persisted"  # type: ignore[union-attr]


def test_get_record_returns_none_for_unknown_generation() -> None:
    store = _store()
    _begin(store)

    assert store.get_record("book_1", 999) is None
    assert store.get_record("missing_book", 1) is None


def test_corrupt_persisted_state_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_PERSIST_STATE", "true")
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    namespace = f"mineru_tasks_corrupt_{uuid4().hex}"
    state_path = tmp_path / "_state" / f"{namespace}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{not-valid-json", encoding="utf-8")

    store = MinerUTaskStore(namespace=namespace)
    with pytest.raises(MinerUProtocolError, match="automatic submission is disabled"):
        _begin(store)

    assert state_path.read_text(encoding="utf-8") == "{not-valid-json"


def test_expected_generation_and_cloudpath_job_binding_are_strict() -> None:
    store = _store()
    reserved = _begin(store).record

    bound = store.bind_cloudpath_job("book_1", reserved.parse_generation, "job_one")
    duplicate = store.bind_cloudpath_job("book_1", reserved.parse_generation, "job_one")

    assert bound.cloudpath_job_id == "job_one"
    assert duplicate == bound
    assert store.assert_current(
        "book_1",
        reserved.parse_generation,
        cloudpath_job_id="job_one",
    ) == bound
    with pytest.raises(MinerUProtocolError, match="already bound"):
        store.bind_cloudpath_job("book_1", reserved.parse_generation, "job_two")
    with pytest.raises(MinerUStaleResultError, match="not bound"):
        store.assert_current(
            "book_1",
            reserved.parse_generation,
            cloudpath_job_id="job_two",
        )
    with pytest.raises(MinerUStaleResultError, match="obsolete"):
        store.begin_or_resume(
            book_id="book_1",
            file_sha256=FILE_SHA,
            options_sha256=OPTIONS_SHA,
            endpoint=ENDPOINT,
            expected_generation=reserved.parse_generation + 1,
        )
    with pytest.raises(MinerUStaleResultError, match="input does not match"):
        store.begin_or_resume(
            book_id="book_1",
            file_sha256="c" * 64,
            options_sha256=OPTIONS_SHA,
            endpoint=ENDPOINT,
            expected_generation=reserved.parse_generation,
        )


def test_only_one_worker_claims_a_bound_generation() -> None:
    store = _store()
    reserved = _begin(store).record
    store.bind_cloudpath_job("book_1", reserved.parse_generation, "job_one")

    def claim() -> str:
        try:
            store.claim_worker("book_1", reserved.parse_generation, "job_one")
            return "claimed"
        except MinerUProtocolError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: claim(), range(2)))

    assert outcomes.count("claimed") == 1
    assert outcomes.count("rejected") == 1
    finished = store.finish_worker(
        "book_1",
        reserved.parse_generation,
        "job_one",
        succeeded=True,
    )
    assert finished.worker_outcome == "succeeded"
    assert finished.worker_finished_at is not None

    # A completed CloudPath worker makes an identical explicit parse request
    # a fresh generation instead of permanently reusing the old job.
    next_run = _begin(store)
    assert next_run.resumed is False
    assert next_run.record.parse_generation == reserved.parse_generation + 1
