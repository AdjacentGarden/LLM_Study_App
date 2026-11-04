"""Durable lifecycle and budget ledger for phone-driven assistant tool runs.

The phone owns tool execution and note writes. This ledger owns every model POST
made on a run's behalf: a step is claimed before sending, fenced by its attempt
number, and its final assistant message is stored before it is returned. Retries
read the stored state first and only resend when no input can have been
processed. Usage is counted per run and never reset by a retry or restart. These cost
counters do not limit the number of steps or the capacity of a model request.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .database import Connection, connect

#: Sends of one step whose earlier attempts provably never reached the model.
MAX_ATTEMPTS = 4
#: A claimed step whose owner has not reported by then is treated as uncertain.
LEASE_GRACE_SECONDS = 30.0


class LedgerConflict(ValueError):
    """The request contradicts the stored run or step and must not be sent."""


@dataclass(frozen=True, slots=True)
class Budget:
    steps: int
    posts: int
    input_tokens: int
    output_tokens: int
    model_seconds: float
    #: Input tokens the provider served from its prompt cache; not a limit.
    cached_input_tokens: int = 0

    def exhausted(self, *, new_step: bool = True) -> str | None:
        return None

    def public(self) -> dict[str, Any]:
        return {
            "steps": self.steps, "posts": self.posts,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "model_seconds": round(self.model_seconds, 3),
            "cached_input_tokens": self.cached_input_tokens,
        }


@dataclass(frozen=True, slots=True)
class StepRecord:
    run_id: str
    step_id: str
    step_index: int
    request_digest: str
    #: submitting, completed, retryable, uncertain or failed.
    state: str
    attempt: int
    message: dict[str, Any] | None
    finish_reason: str | None
    error: str | None
    retry_after: float | None
    lease_until: float


@dataclass(frozen=True, slots=True)
class Claim:
    """Result of asking to send a step. ``send`` is True only for the new owner."""

    step: StepRecord
    budget: Budget
    send: bool
    #: Set when nothing was claimed: "cancelled" or a Budget.exhausted() name.
    refused: str | None = None


class AssistantLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS assistant_runs (
                    owner TEXT NOT NULL, run_id TEXT NOT NULL, context_digest TEXT NOT NULL,
                    state TEXT NOT NULL, posts INTEGER NOT NULL,
                    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                    model_seconds REAL NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(owner,run_id));
                CREATE TABLE IF NOT EXISTS assistant_steps (
                    owner TEXT NOT NULL, run_id TEXT NOT NULL, step_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL, request_digest TEXT NOT NULL,
                    state TEXT NOT NULL, attempt INTEGER NOT NULL, lease_until REAL NOT NULL,
                    message_json TEXT, finish_reason TEXT, error TEXT, retry_after REAL,
                    estimated_input_tokens INTEGER NOT NULL DEFAULT 0,
                    expire_charged_input_tokens INTEGER NOT NULL DEFAULT 0,
                    expire_charged_seconds REAL NOT NULL DEFAULT 0,
                    expire_charged_posts INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL, updated REAL NOT NULL,
                    PRIMARY KEY(owner,run_id,step_id),
                    UNIQUE(owner,run_id,step_index),
                    FOREIGN KEY(owner,run_id) REFERENCES assistant_runs(owner,run_id));
            """)
            if not db.postgres:
                columns = {
                    row["name"]
                    for row in db.execute("PRAGMA table_info(assistant_runs)").fetchall()
                }
                if "cached_input_tokens" not in columns:
                    db.execute(
                        "ALTER TABLE assistant_runs "
                        "ADD COLUMN cached_input_tokens INTEGER NOT NULL DEFAULT 0"
                    )
                step_columns = {
                    row["name"]
                    for row in db.execute("PRAGMA table_info(assistant_steps)").fetchall()
                }
                if "estimated_input_tokens" not in step_columns:
                    db.execute(
                        "ALTER TABLE assistant_steps "
                        "ADD COLUMN estimated_input_tokens INTEGER NOT NULL DEFAULT 0"
                    )
                if "expire_charged_input_tokens" not in step_columns:
                    db.execute(
                        "ALTER TABLE assistant_steps "
                        "ADD COLUMN expire_charged_input_tokens INTEGER NOT NULL DEFAULT 0"
                    )
                if "expire_charged_seconds" not in step_columns:
                    db.execute(
                        "ALTER TABLE assistant_steps "
                        "ADD COLUMN expire_charged_seconds REAL NOT NULL DEFAULT 0"
                    )
                if "expire_charged_posts" not in step_columns:
                    db.execute(
                        "ALTER TABLE assistant_steps "
                        "ADD COLUMN expire_charged_posts INTEGER NOT NULL DEFAULT 0"
                    )

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        with connect(self.path) as db:
            yield db

    def step(self, owner: str, run_id: str, step_id: str) -> tuple[StepRecord, Budget] | None:
        """Read-only status. An expired lease is reported as uncertain."""
        with self.connect() as db:
            db.begin_write()
            run = self._run(db, owner, run_id)
            row = self._step_row(db, owner, run_id, step_id)
            if run is None or row is None:
                return None
            step = self._expire(db, owner, row)
            return step, self._budget(db, owner, run_id)

    def history(self, owner: str, run_id: str, step_index: int) -> list[dict[str, Any]]:
        """Stored assistant messages of steps 0..step_index-1, all completed."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT step_index,state,message_json FROM assistant_steps "
                "WHERE owner=? AND run_id=? AND step_index<? ORDER BY step_index",
                (owner, run_id, step_index),
            ).fetchall()
        if [r["step_index"] for r in rows] != list(range(step_index)):
            raise LedgerConflict("steps must be sent in order without gaps")
        if any(r["state"] != "completed" for r in rows):
            raise LedgerConflict("an earlier step has not completed")
        return [json.loads(r["message_json"]) for r in rows]

    def claim(
        self, owner: str, run_id: str, step_id: str, step_index: int, *,
        context_digest: str, digest: str, lease_seconds: float, estimated_input_tokens: int,
    ) -> Claim:
        """Atomically decide whether this call may POST the step to the model.

        A new step requires every earlier step to be completed. Completed steps
        are immutable, so a history read beforehand is still the history here.
        """
        now = time.time()
        with self.connect() as db:
            db.begin_write()
            run = self._run(db, owner, run_id)
            if run is None:
                if step_index != 0:
                    raise LedgerConflict("run does not exist; the first step must have index 0")
                self._insert_run(db, owner, run_id, context_digest, "active", now)
            elif run["context_digest"] and run["context_digest"] != context_digest:
                raise LedgerConflict("run context differs from the context it was started with")
            row = self._step_row(db, owner, run_id, step_id)
            if row is not None:
                if row["request_digest"] != digest or row["step_index"] != step_index:
                    raise LedgerConflict("step ID was already used for a different request")
                step = self._expire(db, owner, row)
                if step.state == "retryable" and step.attempt >= MAX_ATTEMPTS:
                    db.execute(
                        "UPDATE assistant_steps SET state='failed',updated=? "
                        "WHERE owner=? AND run_id=? AND step_id=?", (now, owner, run_id, step_id))
                    row = self._step_row(db, owner, run_id, step_id)
                    step = self._record(row)
                waiting = step.state == "retryable" and row["updated"] + (step.retry_after or 0) > now
                if step.state != "retryable" or waiting:
                    return Claim(step, self._budget(db, owner, run_id), send=False)
            cancelled = run is not None and run["state"] == "cancelled"
            budget = self._budget(db, owner, run_id)
            if row is None:
                prior = db.execute(
                    "SELECT step_index,state FROM assistant_steps "
                    "WHERE owner=? AND run_id=? ORDER BY step_index",
                    (owner, run_id),
                ).fetchall()
                if [r["step_index"] for r in prior] != list(range(step_index)):
                    raise LedgerConflict("steps must be sent in order without gaps")
                if any(r["state"] != "completed" for r in prior):
                    raise LedgerConflict("an earlier step has not completed")
            refused = "cancelled" if cancelled else budget.exhausted(new_step=row is None)
            if refused is not None:
                if row is None:
                    return Claim(self._absent(run_id, step_id, step_index, digest), budget,
                                 send=False, refused=refused)
                return Claim(self._record(row), budget, send=False, refused=refused)
            lease_until = now + lease_seconds + LEASE_GRACE_SECONDS
            if row is None:
                db.execute(
                    "INSERT INTO assistant_steps(owner,run_id,step_id,step_index,request_digest,"
                    "state,attempt,lease_until,estimated_input_tokens,created,updated) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (owner, run_id, step_id, step_index, digest, "submitting", 1, lease_until,
                     max(0, estimated_input_tokens), now, now),
                )
            else:
                db.execute(
                    "UPDATE assistant_steps SET state='submitting',attempt=attempt+1,lease_until=?,"
                    "estimated_input_tokens=?,error=NULL,retry_after=NULL,"
                    "expire_charged_input_tokens=0,expire_charged_seconds=0,"
                    "expire_charged_posts=0,updated=? "
                    "WHERE owner=? AND run_id=? AND step_id=?",
                    (lease_until, max(0, estimated_input_tokens), now, owner, run_id, step_id),
                )
            claimed = self._step_row(db, owner, run_id, step_id)
            assert claimed is not None
            return Claim(self._record(claimed), self._budget(db, owner, run_id), send=True)

    def finish(
        self, owner: str, run_id: str, step_id: str, attempt: int, *, state: str,
        posted: bool, elapsed: float, input_tokens: int = 0, output_tokens: int = 0,
        cached_input_tokens: int = 0, message: dict[str, Any] | None = None, finish_reason: str | None = None,
        error: str | None = None, retry_after: float | None = None,
    ) -> tuple[StepRecord, Budget]:
        """Record the owner's outcome; a stale attempt cannot overwrite a newer one."""
        if state not in {"completed", "retryable", "uncertain", "failed"}:
            raise ValueError(f"invalid step outcome {state!r}")
        now = time.time()
        with self.connect() as db:
            db.begin_write()
            row = self._step_row(db, owner, run_id, step_id)
            if row is None:
                raise LedgerConflict("step does not exist")
            if row["attempt"] == attempt and row["state"] in {"submitting", "uncertain"}:
                # An expired lease may already be marked uncertain without usage.
                # The owner's real result still lands, except that an already
                # charged POST is not charged twice.
                charged = row["state"] == "uncertain"
                # Only _expire sets these markers, so an uncertain row written by
                # the owner itself is not refunded. Subtract the exact expire
                # charge before adding real usage, clamped at zero.
                refunded_input = max(0, int(row["expire_charged_input_tokens"]))
                refunded_seconds = max(0.0, float(row["expire_charged_seconds"]))
                # _expire charged one POST because the request may have reached
                # the provider. A late owner that reports no POST refunds that
                # charge; a reported POST keeps it counted exactly once.
                refunded_posts = 0 if posted else max(0, int(row["expire_charged_posts"]))
                db.execute(
                    "UPDATE assistant_steps SET state=?,message_json=?,finish_reason=?,error=?,"
                    "retry_after=?,expire_charged_input_tokens=0,expire_charged_seconds=0,"
                    "expire_charged_posts=0,updated=? "
                    "WHERE owner=? AND run_id=? AND step_id=? AND attempt=?",
                    (state, json.dumps(message, ensure_ascii=False) if message is not None else None,
                     finish_reason, error, retry_after, now, owner, run_id, step_id, attempt),
                )
                db.execute(
                    "UPDATE assistant_runs SET "
                    "posts=CASE WHEN posts>? THEN posts-? ELSE 0 END+?,"
                    "input_tokens=CASE WHEN input_tokens>? THEN input_tokens-? ELSE 0 END +?,"
                    "output_tokens=output_tokens+?,"
                    "model_seconds=CASE WHEN model_seconds>? THEN model_seconds-? ELSE 0 END +?,"
                    "cached_input_tokens=cached_input_tokens+?,updated=? "
                    "WHERE owner=? AND run_id=?",
                    (refunded_posts, refunded_posts, int(posted and not charged),
                     refunded_input, refunded_input,
                     max(0, input_tokens), max(0, output_tokens),
                     refunded_seconds, refunded_seconds, max(0.0, elapsed),
                     max(0, min(cached_input_tokens, input_tokens)), now, owner, run_id),
                )
            current = self._step_row(db, owner, run_id, step_id)
            assert current is not None
            return self._record(current), self._budget(db, owner, run_id)

    def cancel(self, owner: str, run_id: str) -> Budget | None:
        """Refuse every later claim. An in-flight step still records its result."""
        now = time.time()
        with self.connect() as db:
            db.begin_write()
            if self._run(db, owner, run_id) is None:
                # A cancel that wins the race against the first step fences it too.
                self._insert_run(db, owner, run_id, "", "cancelled", now)
            else:
                db.execute(
                    "UPDATE assistant_runs SET state='cancelled',updated=? WHERE owner=? AND run_id=?",
                    (now, owner, run_id),
                )
            return self._budget(db, owner, run_id)

    def _expire(self, db: Connection, owner: str, row: Any) -> StepRecord:
        now = time.time()
        if row["state"] == "submitting" and row["lease_until"] <= now:
            # The owner vanished mid-call. The POST may have been processed, so
            # the step is never resent; it is charged one POST conservatively,
            # plus the input estimate and the time its lease was held. The exact
            # charge is stored so a late finish can refund it.
            elapsed = max(0.0, now - float(row["updated"]))
            charged_input = max(0, int(row["estimated_input_tokens"]))
            changed = db.execute(
                "UPDATE assistant_steps SET state='uncertain',error=?,updated=?,"
                "expire_charged_input_tokens=?,expire_charged_seconds=?,expire_charged_posts=1 "
                "WHERE owner=? AND run_id=? AND step_id=? AND attempt=? AND state='submitting'",
                ("model request owner stopped before reporting", now, charged_input, elapsed,
                 owner, row["run_id"], row["step_id"], row["attempt"]),
            ).rowcount
            if changed:
                db.execute(
                    "UPDATE assistant_runs SET posts=posts+1,input_tokens=input_tokens+?,"
                    "model_seconds=model_seconds+?,updated=? WHERE owner=? AND run_id=?",
                    (charged_input, elapsed, now, owner, row["run_id"]),
                )
            row = self._step_row(db, owner, row["run_id"], row["step_id"])
        return self._record(row)

    @staticmethod
    def _insert_run(
        db: Connection, owner: str, run_id: str, context_digest: str, state: str, now: float
    ) -> None:
        db.execute(
            "INSERT INTO assistant_runs(owner,run_id,context_digest,state,posts,input_tokens,"
            "output_tokens,model_seconds,created,updated) VALUES(?,?,?,?,0,0,0,0,?,?)",
            (owner, run_id, context_digest, state, now, now),
        )

    @staticmethod
    def _run(db: Connection, owner: str, run_id: str) -> Any:
        return db.execute(
            "SELECT * FROM assistant_runs WHERE owner=? AND run_id=?", (owner, run_id)
        ).fetchone()

    @staticmethod
    def _step_row(db: Connection, owner: str, run_id: str, step_id: str) -> Any:
        return db.execute(
            "SELECT * FROM assistant_steps WHERE owner=? AND run_id=? AND step_id=?",
            (owner, run_id, step_id),
        ).fetchone()

    @staticmethod
    def _budget(db: Connection, owner: str, run_id: str) -> Budget:
        run = db.execute(
            "SELECT posts,input_tokens,output_tokens,model_seconds,cached_input_tokens "
            "FROM assistant_runs "
            "WHERE owner=? AND run_id=?", (owner, run_id),
        ).fetchone()
        steps = db.execute(
            "SELECT count(*) FROM assistant_steps WHERE owner=? AND run_id=?", (owner, run_id)
        ).fetchone()[0]
        if run is None:
            return Budget(int(steps), 0, 0, 0, 0.0)
        return Budget(int(steps), int(run["posts"]), int(run["input_tokens"]),
                      int(run["output_tokens"]), float(run["model_seconds"]),
                      int(run["cached_input_tokens"]))

    @staticmethod
    def _record(row: Any) -> StepRecord:
        return StepRecord(
            run_id=row["run_id"], step_id=row["step_id"], step_index=int(row["step_index"]),
            request_digest=row["request_digest"], state=row["state"], attempt=int(row["attempt"]),
            message=json.loads(row["message_json"]) if row["message_json"] else None,
            finish_reason=row["finish_reason"], error=row["error"],
            retry_after=row["retry_after"], lease_until=float(row["lease_until"]),
        )

    @staticmethod
    def _absent(run_id: str, step_id: str, step_index: int, digest: str) -> StepRecord:
        return StepRecord(run_id, step_id, step_index, digest, "refused", 0,
                          None, None, None, None, 0.0)
