from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Callable, Literal
import time

from pydantic import BaseModel, ConfigDict

from app.document.mineru.exceptions import MinerUProtocolError, MinerUStaleResultError, safe_error_detail
from app.services.kv_store import _PersistedKVStore


MinerULocalTaskStatus = Literal[
    "created",
    "submitting",
    "submission_uncertain",
    "submitted",
    "pending",
    "processing",
    "remote_completed",
    "completed",
    "failed",
    "timed_out",
    "expired",
    "discarded_stale",
    "abandoned",
]
REMOTE_RESUMABLE_STATUSES = {"submitted", "pending", "processing", "remote_completed"}
SUBMISSION_BLOCKED_STATUSES = {"submitting", "submission_uncertain"}
RESULT_TERMINAL_STATUSES = {
    "failed",
    "timed_out",
    "expired",
    "discarded_stale",
    "abandoned",
}
MAX_RECORDS_PER_BOOK = 20


class MinerUTaskRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    record_id: str
    book_id: str
    parse_generation: int
    idempotency_key: str
    file_sha256: str
    options_sha256: str
    endpoint: str
    status: MinerULocalTaskStatus
    cloudpath_job_id: str | None = None
    worker_claimed_at: float | None = None
    worker_finished_at: float | None = None
    worker_outcome: Literal["succeeded", "failed"] | None = None
    mineru_task_id: str | None = None
    remote_status: str | None = None
    queued_ahead: int | None = None
    error_code: str | None = None
    error_detail: str | None = None
    result_digest: str | None = None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class MinerUTaskBegin:
    record: MinerUTaskRecord
    resumed: bool


def make_idempotency_key(book_id: str, file_sha256: str, options_sha256: str, endpoint: str) -> str:
    payload = "\0".join((book_id, file_sha256, options_sha256, endpoint.rstrip("/")))
    return sha256(payload.encode("utf-8")).hexdigest()


class MinerUTaskStore:
    """Persist MinerU task identity and parse-generation ownership.

    Atomicity here is intentionally limited to the single-process lock supplied
    by ``_PersistedKVStore``.  Its JSON backend has no inter-process file lock,
    so this store must not be shared by multiple CloudPath worker processes.
    A database-backed compare-and-swap is required before enabling that mode.
    """

    def __init__(self, namespace: str = "mineru_tasks", *, clock: Callable[[], float] = time.time) -> None:
        self._kv = _PersistedKVStore(namespace)
        self._clock = clock

    def begin_or_resume(
        self,
        *,
        book_id: str,
        file_sha256: str,
        options_sha256: str,
        endpoint: str,
        expected_generation: int | None = None,
    ) -> MinerUTaskBegin:
        self._assert_persistence_readable()
        idempotency_key = make_idempotency_key(book_id, file_sha256, options_sha256, endpoint)
        decision: list[MinerUTaskBegin] = []

        def _update(current: dict | None) -> dict:
            state = self._normalize_state(current)
            records = dict(state["records"])
            current_id = state.get("current_record_id")
            current_record = records.get(current_id) if isinstance(current_id, str) else None
            if expected_generation is not None:
                if current_record is None:
                    raise MinerUStaleResultError("Expected MinerU parse generation no longer exists")
                parsed = MinerUTaskRecord.model_validate(current_record)
                if parsed.parse_generation != expected_generation:
                    raise MinerUStaleResultError("MinerU worker belongs to an obsolete parse generation")
                if parsed.idempotency_key != idempotency_key or parsed.endpoint != endpoint.rstrip("/"):
                    raise MinerUStaleResultError("MinerU worker input does not match its reserved parse generation")
                decision.append(MinerUTaskBegin(record=parsed, resumed=True))
                return state
            if current_record:
                parsed = MinerUTaskRecord.model_validate(current_record)
                same_request = (
                    parsed.idempotency_key == idempotency_key
                    and parsed.endpoint == endpoint.rstrip("/")
                )
                if (
                    same_request
                    and parsed.cloudpath_job_id is not None
                    and parsed.worker_finished_at is None
                ):
                    # The API may be retried after MinerU completed but while
                    # mapping/fallback/commit still runs. Keep returning the
                    # already-bound CloudPath job until that worker finishes.
                    decision.append(MinerUTaskBegin(record=parsed, resumed=True))
                    return state
                if (
                    same_request
                    and parsed.status == "created"
                    and parsed.worker_outcome is None
                ):
                    # Nothing has been sent upstream, so reusing this local run
                    # is safe and does not create a duplicate MinerU task.
                    decision.append(MinerUTaskBegin(record=parsed, resumed=True))
                    return state
                if same_request and parsed.status in SUBMISSION_BLOCKED_STATUSES:
                    # Once submission has started, absence of a task id is
                    # ambiguous: MinerU may already have accepted the upload.
                    # Never create a fresh run automatically in this state.
                    decision.append(MinerUTaskBegin(record=parsed, resumed=True))
                    return state
                if same_request and parsed.status in REMOTE_RESUMABLE_STATUSES:
                    if parsed.mineru_task_id:
                        decision.append(MinerUTaskBegin(record=parsed, resumed=True))
                        return state
                    uncertain = parsed.model_copy(
                        update={
                            "status": "submission_uncertain",
                            "error_code": "mineru_task_id_missing",
                            "error_detail": "MinerU task id is missing; automatic resubmission is disabled",
                            "updated_at": self._clock(),
                        }
                    )
                    records[parsed.record_id] = uncertain.model_dump()
                    decision.append(MinerUTaskBegin(record=uncertain, resumed=True))
                    return {**state, "records": records}
                if same_request and parsed.status == "timed_out" and parsed.mineru_task_id:
                    # CloudPath cannot cancel MinerU. The upstream GPU task may
                    # still be running after our deadline, so an identical
                    # automatic retry must not create a second task. A caller
                    # must explicitly invalidate this generation first.
                    decision.append(MinerUTaskBegin(record=parsed, resumed=True))
                    return state

                # A new generation supersedes every previous current record,
                # including terminal records.  Keeping it as discarded_stale
                # makes late callbacks auditable and guarantees they fail CAS.
                records[parsed.record_id] = parsed.model_copy(
                    update={
                        "status": "discarded_stale",
                        "error_code": "mineru_stale_generation",
                        "error_detail": "Superseded by a newer parse generation",
                        "updated_at": self._clock(),
                    }
                ).model_dump()

            generation = int(state.get("generation", 0)) + 1
            now = self._clock()
            record = MinerUTaskRecord(
                record_id=f"g{generation}_{idempotency_key[:16]}",
                book_id=book_id,
                parse_generation=generation,
                idempotency_key=idempotency_key,
                file_sha256=file_sha256,
                options_sha256=options_sha256,
                endpoint=endpoint.rstrip("/"),
                status="created",
                created_at=now,
                updated_at=now,
            )
            records[record.record_id] = record.model_dump()
            records = self._trim_records(records, keep_id=record.record_id)
            decision.append(MinerUTaskBegin(record=record, resumed=False))
            return {
                "generation": generation,
                "current_record_id": record.record_id,
                "records": records,
            }

        self._kv.update_in_place(book_id, _update)
        return decision[0]

    def bind_cloudpath_job(
        self,
        book_id: str,
        parse_generation: int,
        cloudpath_job_id: str,
    ) -> MinerUTaskRecord:
        """Bind exactly one CloudPath job to a reserved parse generation."""

        normalized_job_id = cloudpath_job_id.strip()
        if not normalized_job_id:
            raise MinerUProtocolError("CloudPath job id cannot be empty")

        def _change(record: MinerUTaskRecord) -> MinerUTaskRecord:
            if record.cloudpath_job_id == normalized_job_id:
                return record
            if record.cloudpath_job_id is not None:
                raise MinerUProtocolError("Parse generation is already bound to another CloudPath job")
            return record.model_copy(
                update={
                    "cloudpath_job_id": normalized_job_id,
                    "updated_at": self._clock(),
                }
            )

        return self._update_current(book_id, parse_generation, _change)

    def claim_worker(
        self,
        book_id: str,
        parse_generation: int,
        cloudpath_job_id: str,
    ) -> MinerUTaskRecord:
        """Atomically allow one worker invocation for a generation/job pair."""

        normalized_job_id = cloudpath_job_id.strip()

        def _change(record: MinerUTaskRecord) -> MinerUTaskRecord:
            if not normalized_job_id or record.cloudpath_job_id != normalized_job_id:
                raise MinerUStaleResultError("CloudPath job is not bound to this parse generation")
            if record.worker_claimed_at is not None:
                raise MinerUProtocolError("Parse generation worker has already been claimed")
            return record.model_copy(
                update={
                    "worker_claimed_at": self._clock(),
                    "updated_at": self._clock(),
                }
            )

        return self._update_current(book_id, parse_generation, _change)

    def finish_worker(
        self,
        book_id: str,
        parse_generation: int,
        cloudpath_job_id: str,
        *,
        succeeded: bool,
    ) -> MinerUTaskRecord:
        normalized_job_id = cloudpath_job_id.strip()

        def _change(record: MinerUTaskRecord) -> MinerUTaskRecord:
            if not normalized_job_id or record.cloudpath_job_id != normalized_job_id:
                raise MinerUStaleResultError("CloudPath job is not bound to this parse generation")
            if record.worker_claimed_at is None:
                raise MinerUProtocolError("Parse generation worker was not claimed")
            outcome = "succeeded" if succeeded else "failed"
            if record.worker_finished_at is not None:
                if record.worker_outcome == outcome:
                    return record
                raise MinerUProtocolError("Parse generation worker outcome is conflicting")
            return record.model_copy(
                update={
                    "worker_finished_at": self._clock(),
                    "worker_outcome": outcome,
                    "updated_at": self._clock(),
                }
            )

        return self._update_current(book_id, parse_generation, _change)

    def assert_current(
        self,
        book_id: str,
        parse_generation: int,
        *,
        cloudpath_job_id: str | None = None,
    ) -> MinerUTaskRecord:
        record = self.get_current(book_id)
        if record is None or record.parse_generation != parse_generation or record.status == "discarded_stale":
            raise MinerUStaleResultError("Parse generation is no longer current")
        if cloudpath_job_id is not None and record.cloudpath_job_id != cloudpath_job_id:
            raise MinerUStaleResultError("CloudPath job is not bound to the current parse generation")
        return record

    def commit_if_current(
        self,
        book_id: str,
        parse_generation: int,
        action: Callable[[], object],
        *,
        cloudpath_job_id: str | None = None,
    ) -> object:
        """Execute one local artifact commit under the generation CAS lock."""

        self._assert_persistence_readable()

        def _commit(raw_state: dict | None) -> object:
            state = self._normalize_state(raw_state)
            current_id = state.get("current_record_id")
            raw_record = state["records"].get(current_id) if isinstance(current_id, str) else None
            if raw_record is None:
                raise MinerUStaleResultError("No active parse generation exists at artifact commit")
            record = MinerUTaskRecord.model_validate(raw_record)
            if record.parse_generation != parse_generation or record.status == "discarded_stale":
                raise MinerUStaleResultError("Obsolete parse generation cannot publish artifacts")
            if cloudpath_job_id is not None and record.cloudpath_job_id != cloudpath_job_id:
                raise MinerUStaleResultError("CloudPath job cannot publish another generation's artifacts")
            return action()

        return self._kv.with_value_lock(book_id, _commit)

    def mark_submitting(self, book_id: str, parse_generation: int) -> MinerUTaskRecord:
        def _change(record: MinerUTaskRecord) -> MinerUTaskRecord:
            if record.status != "created":
                raise MinerUProtocolError("MinerU submission claim has already been consumed")
            if record.mineru_task_id:
                raise MinerUProtocolError("A created MinerU task cannot already have a remote task id")
            return record.model_copy(update={"status": "submitting", "updated_at": self._clock()})

        return self._update_current(book_id, parse_generation, _change)

    def mark_submission_uncertain(
        self,
        book_id: str,
        parse_generation: int,
        *,
        detail: object = None,
    ) -> MinerUTaskRecord:
        def _change(record: MinerUTaskRecord) -> MinerUTaskRecord:
            if record.status == "submission_uncertain":
                return record
            if record.status != "submitting":
                raise MinerUProtocolError("Only an unconfirmed MinerU submission can become uncertain")
            return record.model_copy(
                update={
                    "status": "submission_uncertain",
                    "error_code": "mineru_submission_uncertain",
                    "error_detail": safe_error_detail(detail),
                    "updated_at": self._clock(),
                }
            )

        return self._update_current(book_id, parse_generation, _change)

    def attach_remote_task(self, book_id: str, parse_generation: int, task_id: str, *, queued_ahead: int | None = None) -> MinerUTaskRecord:
        normalized_task_id = task_id.strip()
        if not normalized_task_id:
            raise MinerUProtocolError("MinerU returned an empty task id")

        def _change(record: MinerUTaskRecord) -> MinerUTaskRecord:
            if record.mineru_task_id and record.mineru_task_id != normalized_task_id:
                raise MinerUProtocolError("MinerU task id changed for the same parse generation")
            if record.mineru_task_id == normalized_task_id:
                # A duplicate submission response must not regress a task that
                # has already advanced to pending/processing/completed.
                return record
            if record.status != "submitting":
                raise MinerUProtocolError("MinerU task id cannot be attached in the current state")
            return record.model_copy(
                update={
                    "mineru_task_id": normalized_task_id,
                    "status": "submitted",
                    "remote_status": "pending",
                    "queued_ahead": queued_ahead,
                    "updated_at": self._clock(),
                }
            )

        return self._update_current(book_id, parse_generation, _change)

    def update_remote_status(
        self,
        book_id: str,
        parse_generation: int,
        remote_status: str,
        *,
        queued_ahead: int | None = None,
        error_detail: object = None,
    ) -> MinerUTaskRecord:
        status_map: dict[str, MinerULocalTaskStatus] = {
            "pending": "pending",
            "processing": "processing",
            "completed": "remote_completed",
            "failed": "failed",
        }
        if remote_status not in status_map:
            raise MinerUProtocolError("MinerU returned an unknown task status")

        def _change(record: MinerUTaskRecord) -> MinerUTaskRecord:
            if not record.mineru_task_id:
                raise MinerUProtocolError("Cannot poll MinerU without a persisted task id")
            if record.status in RESULT_TERMINAL_STATUSES or record.status == "completed":
                raise MinerUStaleResultError("MinerU status arrived after the local task became terminal")
            return record.model_copy(
                update={
                    "status": status_map[remote_status],
                    "remote_status": remote_status,
                    "queued_ahead": queued_ahead,
                    "error_code": "mineru_task_failed" if remote_status == "failed" else record.error_code,
                    "error_detail": safe_error_detail(error_detail) if remote_status == "failed" else record.error_detail,
                    "updated_at": self._clock(),
                }
            )

        return self._update_current(book_id, parse_generation, _change)

    def mark_timed_out(self, book_id: str, parse_generation: int, *, detail: object = None) -> MinerUTaskRecord:
        return self._update_current(
            book_id,
            parse_generation,
            lambda record: record.model_copy(
                update={
                    "status": "timed_out",
                    "error_code": "mineru_timeout",
                    "error_detail": safe_error_detail(detail),
                    "updated_at": self._clock(),
                }
            ),
        )

    def mark_expired(self, book_id: str, parse_generation: int, *, detail: object = None) -> MinerUTaskRecord:
        return self._update_current(
            book_id,
            parse_generation,
            lambda record: record.model_copy(
                update={
                    "status": "expired",
                    "error_code": "mineru_task_expired",
                    "error_detail": safe_error_detail(detail),
                    "updated_at": self._clock(),
                }
            ),
        )

    def mark_failed(self, book_id: str, parse_generation: int, *, code: str, detail: object = None) -> MinerUTaskRecord:
        return self._update_current(
            book_id,
            parse_generation,
            lambda record: record.model_copy(
                update={
                    "status": "failed",
                    "error_code": code,
                    "error_detail": safe_error_detail(detail),
                    "updated_at": self._clock(),
                }
            ),
        )

    def accept_result(self, book_id: str, parse_generation: int, result_digest: str) -> MinerUTaskRecord:
        normalized_digest = result_digest.strip().lower()
        if not normalized_digest:
            raise MinerUProtocolError("MinerU result digest cannot be empty")

        def _change(record: MinerUTaskRecord) -> MinerUTaskRecord:
            if record.status == "completed":
                if record.result_digest == normalized_digest:
                    return record
                raise MinerUProtocolError("MinerU returned conflicting results for one parse generation")
            if record.status != "remote_completed":
                if record.status in RESULT_TERMINAL_STATUSES:
                    raise MinerUStaleResultError("MinerU result arrived after the local task became terminal")
                raise MinerUProtocolError("MinerU result was accepted before the remote task completed")
            return record.model_copy(
                update={
                    "status": "completed",
                    "remote_status": "completed",
                    "result_digest": normalized_digest,
                    "updated_at": self._clock(),
                }
            )

        return self._update_current(book_id, parse_generation, _change)

    def invalidate(
        self,
        book_id: str,
        parse_generation: int,
        *,
        detail: object = None,
    ) -> MinerUTaskRecord:
        self._assert_persistence_readable()
        invalidated: list[MinerUTaskRecord] = []

        def _update(current: dict | None) -> dict:
            state = self._normalize_state(current)
            current_id = state.get("current_record_id")
            raw_record = state["records"].get(current_id) if isinstance(current_id, str) else None
            if raw_record is None:
                raise MinerUStaleResultError("No active MinerU parse generation exists")
            record = MinerUTaskRecord.model_validate(raw_record)
            if record.parse_generation != parse_generation:
                raise MinerUStaleResultError("Cannot invalidate an obsolete MinerU parse generation")
            if record.status == "discarded_stale":
                return state
            changed = record.model_copy(
                update={
                    "status": "discarded_stale",
                    "error_code": "mineru_stale_generation",
                    "error_detail": safe_error_detail(detail) or "Parse generation was invalidated",
                    "updated_at": self._clock(),
                }
            )
            records = dict(state["records"])
            records[changed.record_id] = changed.model_dump()
            invalidated.append(changed)
            return {**state, "current_record_id": None, "records": records}

        self._kv.update_in_place(book_id, _update)
        if invalidated:
            return invalidated[0]
        record = self.get_record(book_id, parse_generation)
        if record is None:
            raise MinerUStaleResultError("MinerU parse generation no longer exists")
        return record

    def get_current(self, book_id: str) -> MinerUTaskRecord | None:
        self._assert_persistence_readable()
        state = self._kv.get(book_id)
        normalized = self._normalize_state(state)
        current_id = normalized.get("current_record_id")
        record = normalized["records"].get(current_id) if isinstance(current_id, str) else None
        return MinerUTaskRecord.model_validate(record) if record else None

    def get_record(self, book_id: str, parse_generation: int) -> MinerUTaskRecord | None:
        self._assert_persistence_readable()
        state = self._normalize_state(self._kv.get(book_id))
        for raw_record in state["records"].values():
            record = MinerUTaskRecord.model_validate(raw_record)
            if record.parse_generation == parse_generation:
                return record
        return None

    def list_resumable(self) -> list[MinerUTaskRecord]:
        self._assert_persistence_readable()
        resumable: list[MinerUTaskRecord] = []
        for raw_state in self._kv.values():
            state = self._normalize_state(raw_state)
            current_id = state.get("current_record_id")
            raw_record = state["records"].get(current_id) if isinstance(current_id, str) else None
            if raw_record is None:
                continue
            record = MinerUTaskRecord.model_validate(raw_record)
            if record.status in REMOTE_RESUMABLE_STATUSES and record.mineru_task_id:
                resumable.append(record)
        return sorted(resumable, key=lambda record: (record.created_at, record.book_id, record.parse_generation))

    def is_current(
        self,
        book_id: str,
        parse_generation: int,
        *,
        cloudpath_job_id: str | None = None,
    ) -> bool:
        try:
            self.assert_current(
                book_id,
                parse_generation,
                cloudpath_job_id=cloudpath_job_id,
            )
        except MinerUStaleResultError:
            return False
        return True

    def reload(self) -> None:
        self._kv.reload()

    def _update_current(
        self,
        book_id: str,
        parse_generation: int,
        change: Callable[[MinerUTaskRecord], MinerUTaskRecord],
    ) -> MinerUTaskRecord:
        self._assert_persistence_readable()
        updated_record: list[MinerUTaskRecord] = []

        def _update(current: dict | None) -> dict:
            state = self._normalize_state(current)
            current_id = state.get("current_record_id")
            raw_record = state["records"].get(current_id) if isinstance(current_id, str) else None
            if raw_record is None:
                raise MinerUStaleResultError("No active MinerU parse generation exists")
            record = MinerUTaskRecord.model_validate(raw_record)
            if record.parse_generation != parse_generation:
                raise MinerUStaleResultError("MinerU result belongs to an obsolete parse generation")
            if record.status == "discarded_stale":
                raise MinerUStaleResultError("MinerU parse generation has been invalidated")
            changed = change(record)
            records = dict(state["records"])
            records[changed.record_id] = changed.model_dump()
            updated_record.append(changed)
            return {**state, "records": records}

        self._kv.update_in_place(book_id, _update)
        return updated_record[0]

    def _assert_persistence_readable(self) -> None:
        if self._kv.load_failed:
            # Losing task identity can duplicate an already-running GPU task.
            # Fail closed until an operator repairs or explicitly replaces the
            # state file; never reinterpret corrupt state as an empty store.
            raise MinerUProtocolError(
                "Persisted MinerU task state is unreadable; automatic submission is disabled"
            )

    @staticmethod
    def _normalize_state(value: dict | None) -> dict:
        state = dict(value or {})
        records = state.get("records")
        return {
            "generation": int(state.get("generation", 0)),
            "current_record_id": state.get("current_record_id"),
            "records": dict(records) if isinstance(records, dict) else {},
        }

    @staticmethod
    def _trim_records(records: dict[str, dict], *, keep_id: str) -> dict[str, dict]:
        if len(records) <= MAX_RECORDS_PER_BOOK:
            return records
        ordered = sorted(
            records.items(),
            key=lambda item: float(item[1].get("updated_at", 0)),
            reverse=True,
        )
        kept = dict(ordered[:MAX_RECORDS_PER_BOOK])
        if keep_id not in kept:
            kept[keep_id] = records[keep_id]
        return kept


mineru_task_store = MinerUTaskStore()
