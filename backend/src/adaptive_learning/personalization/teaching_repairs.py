"""Durable course drafts, exact review proofs and fenced model submissions.

Only the ordinary teaching validators can approve a component. Audit imports
are drafts; neither persisted model responses nor draft rows are approvals.
"""
from __future__ import annotations

import json
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

from ..database import connect
from ..llm.client import (
    LLMCooldownError, LLMNotSubmittedError, LLMOutputLimitError, LLMRefusalError,
    no_ambiguous_model_retries,
)
from .teaching_review_reuse import fingerprint


class TeachingRepairPending(RuntimeError):
    def __init__(self, retry_after: float = 5, *, blocked: bool = False) -> None:
        super().__init__("课程正在准备中，已有内容已保留。")
        self.retry_after = max(1, retry_after)
        self.blocked = blocked


current_repair: ContextVar[TeachingRepairRun | None] = ContextVar("course_repair_run", default=None)
last_submission: ContextVar[str | None] = ContextVar("course_last_submission", default=None)
current_unit: ContextVar[str] = ContextVar("course_current_unit", default="")


class TeachingRepairStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with connect(path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS teaching_course_repairs (
                    run_key TEXT PRIMARY KEY, scope TEXT NOT NULL, chapter_id TEXT NOT NULL,
                    identity_json TEXT NOT NULL, state_json TEXT NOT NULL DEFAULT '{}',
                    updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS teaching_course_scope ON teaching_course_repairs(scope,chapter_id,updated);
                CREATE TABLE IF NOT EXISTS teaching_course_units (
                    run_key TEXT NOT NULL, unit_id TEXT NOT NULL, candidate_json TEXT NOT NULL,
                    candidate_hash TEXT NOT NULL, status TEXT NOT NULL,
                    feedback_json TEXT NOT NULL DEFAULT '[]', attempts INTEGER NOT NULL DEFAULT 0,
                    next_retry_at REAL NOT NULL DEFAULT 0, updated REAL NOT NULL,
                    PRIMARY KEY(run_key,unit_id));
                CREATE TABLE IF NOT EXISTS teaching_course_proofs (
                    run_key TEXT NOT NULL, proof_key TEXT NOT NULL, proof_json TEXT NOT NULL,
                    updated REAL NOT NULL, PRIMARY KEY(run_key,proof_key));
                CREATE TABLE IF NOT EXISTS teaching_course_submissions (
                    run_key TEXT NOT NULL, request_key TEXT NOT NULL, token TEXT NOT NULL,
                    status TEXT NOT NULL, response_json TEXT, updated REAL NOT NULL,
                    PRIMARY KEY(run_key,request_key));
            """)
            if not db.postgres:
                for table, name, definition in (
                    ('teaching_course_units', 'work_json', "TEXT NOT NULL DEFAULT '{}'"),
                    ('teaching_course_submissions', 'unit_id', "TEXT NOT NULL DEFAULT ''"),
                ):
                    columns = {row['name'] for row in db.execute('PRAGMA table_info(' + table + ')').fetchall()}
                    if name not in columns:
                        db.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + name + ' ' + definition)
            db.executescript('''CREATE TABLE IF NOT EXISTS teaching_course_epochs (
                scope TEXT NOT NULL, chapter_id TEXT NOT NULL, epoch TEXT NOT NULL,
                PRIMARY KEY(scope,chapter_id));''')
            db.executescript('''CREATE INDEX IF NOT EXISTS teaching_unit_submissions
                ON teaching_course_submissions(run_key,unit_id,status);''')

    def open(self, scope: str, chapter_id: str, identity: dict[str, Any]) -> TeachingRepairRun:
        with connect(self.path) as db:
            epoch = db.execute('SELECT epoch FROM teaching_course_epochs WHERE scope=? AND chapter_id=?',
                               (scope, chapter_id)).fetchone()
        identity = {**identity, 'epoch': epoch['epoch'] if epoch else 'initial'}
        key = fingerprint({"scope": scope, "chapter": chapter_id, "identity": identity})
        with connect(self.path) as db:
            db.execute("""INSERT INTO teaching_course_repairs
                (run_key,scope,chapter_id,identity_json,state_json,updated) VALUES(?,?,?,?,?,?)
                ON CONFLICT(run_key) DO NOTHING""",
                (key, scope, chapter_id, json.dumps(identity, ensure_ascii=False), '{}', time.time()))
        return TeachingRepairRun(self, key, scope, chapter_id, identity)

    def recover(self) -> None:
        # Called with the queue's exclusive worker lock. A lost POST response
        # cannot become permission to submit that question again after restart.
        with connect(self.path) as db:
            db.execute("UPDATE teaching_course_submissions SET status='unknown' WHERE status='running'")
            db.execute("UPDATE teaching_course_units SET status='pending' WHERE status='running'")

    def progress(self, scope: str, chapter_id: str) -> dict[str, int]:
        with connect(self.path) as db:
            epoch = db.execute('SELECT epoch FROM teaching_course_epochs WHERE scope=? AND chapter_id=?',
                               (scope, chapter_id)).fetchone()
            runs = db.execute("""SELECT run_key,identity_json FROM teaching_course_repairs
                WHERE scope=? AND chapter_id=? AND run_key NOT LIKE 'seed:%'
                ORDER BY updated DESC""", (scope, chapter_id)).fetchall()
            run = next((row for row in runs if not epoch or
                        json.loads(row['identity_json']).get('epoch') == epoch['epoch']), None)
            rows = db.execute("SELECT status FROM teaching_course_units WHERE run_key=?",
                (run['run_key'],)).fetchall() if run else []
        return {'completed': sum(row['status'] == 'approved' for row in rows), 'total': len(rows)}


class TeachingRepairRun:
    def __init__(self, store: TeachingRepairStore, key: str, scope: str,
                 chapter_id: str, identity: dict[str, Any]) -> None:
        self.store, self.key, self.scope, self.chapter_id = store, key, scope, chapter_id
        self.identity = identity

    def load(self) -> dict[str, Any]:
        with connect(self.store.path) as db:
            row = db.execute("SELECT state_json FROM teaching_course_repairs WHERE run_key=?", (self.key,)).fetchone()
            state = json.loads(row['state_json'])
            if not state and not self.identity.get('engine'):
                seed = db.execute("SELECT state_json FROM teaching_course_repairs WHERE run_key=?",
                    ('seed:' + self.scope + ':' + self.chapter_id,)).fetchone()
                if seed:
                    draft = json.loads(seed['state_json'])
                    if draft.get('evidence_fingerprint') == self.identity['evidence_fingerprint']:
                        state = {'seed_candidate': draft['candidate']}
            return state

    def save(self, state: dict[str, Any], units: dict[str, Any], pending: set[str],
             feedback: Any, attempt: int, delay: float = 0) -> None:
        now = time.time()
        with connect(self.store.path) as db:
            db.begin_write()
            db.execute("UPDATE teaching_course_repairs SET state_json=?,updated=? WHERE run_key=?",
                (json.dumps(state, ensure_ascii=False, allow_nan=False), now, self.key))
            for unit_id, candidate in units.items():
                db.execute("""INSERT INTO teaching_course_units
                    (run_key,unit_id,candidate_json,candidate_hash,status,feedback_json,attempts,next_retry_at,updated)
                    VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(run_key,unit_id) DO UPDATE SET
                    candidate_json=excluded.candidate_json,candidate_hash=excluded.candidate_hash,
                    status=excluded.status,feedback_json=excluded.feedback_json,attempts=excluded.attempts,
                    next_retry_at=excluded.next_retry_at,updated=excluded.updated""",
                    (self.key, unit_id, json.dumps(candidate, ensure_ascii=False), fingerprint(candidate),
                     'repairing' if unit_id in pending else 'approved'
                     if state.get('approved_unit_hashes', {}).get(unit_id) == fingerprint(candidate) else 'draft',
                     json.dumps(feedback if unit_id in pending else [], ensure_ascii=False),
                     attempt, now + delay if unit_id in pending else 0, now))

    def proof_get(self, key: str) -> dict[str, Any] | None:
        with connect(self.store.path) as db:
            row = db.execute("SELECT proof_json FROM teaching_course_proofs WHERE run_key=? AND proof_key=?",
                (self.key, key)).fetchone()
        return json.loads(row['proof_json']) if row else None

    def proof_put(self, key: str, proof: dict[str, Any]) -> None:
        with connect(self.store.path) as db:
            db.execute("""INSERT INTO teaching_course_proofs VALUES(?,?,?,?)
                ON CONFLICT(run_key,proof_key) DO UPDATE SET proof_json=excluded.proof_json,updated=excluded.updated""",
                (self.key, key, json.dumps(proof, ensure_ascii=False), time.time()))

    def invalidate_last_response(self) -> None:
        key = last_submission.get()
        if key:
            with connect(self.store.path) as db:
                db.execute("""UPDATE teaching_course_submissions SET status='invalid',response_json=NULL
                    WHERE run_key=? AND request_key=? AND status='responded'""", (self.key, key))

    def structured(self, call: Callable[[], dict[str, Any]], request: dict[str, Any]) -> dict[str, Any]:
        from ..assessment.item_repairs import _service_retry_delay
        unit_id = current_unit.get()
        key, token = fingerprint({'unit_id': unit_id, **request}), uuid.uuid4().hex
        last_submission.set(key)
        with connect(self.store.path) as db:
            db.begin_write()
            blocked = db.execute("""SELECT 1 FROM teaching_course_submissions
                WHERE run_key=? AND unit_id=? AND status='unknown' LIMIT 1""", (self.key, unit_id)).fetchone()
            if blocked:
                raise TeachingRepairPending(120, blocked=True)
            row = db.execute("SELECT * FROM teaching_course_submissions WHERE run_key=? AND request_key=?",
                (self.key, key)).fetchone()
            if row and row['status'] == 'responded':
                return json.loads(row['response_json'])
            if row and row['status'] in ('running', 'retired_revision'):
                raise TeachingRepairPending(30, blocked=True)
            db.execute("""INSERT INTO teaching_course_submissions
                (run_key,request_key,token,status,response_json,updated,unit_id)
                VALUES(?,?,?,'running',NULL,?,?)
                ON CONFLICT(run_key,request_key) DO UPDATE SET token=excluded.token,
                status='running',response_json=NULL,updated=excluded.updated""", (self.key, key, token, time.time(), unit_id))
        try:
            with no_ambiguous_model_retries():
                response = call()
        except BaseException as error:
            from .teaching_scope import is_structured_response_error
            safe = isinstance(error, (LLMCooldownError, LLMNotSubmittedError, LLMOutputLimitError, LLMRefusalError))
            safe = safe or is_structured_response_error(error)
            service_delay = _service_retry_delay(error)
            with connect(self.store.path) as db:
                db.execute("""UPDATE teaching_course_submissions SET status=?,updated=?
                    WHERE run_key=? AND request_key=? AND token=?""",
                    ('invalid' if safe or service_delay is not None else 'unknown', time.time(), self.key, key, token))
            if isinstance(error, (LLMCooldownError, LLMNotSubmittedError)) or service_delay is not None:
                raise TeachingRepairPending(service_delay or getattr(error, 'retry_after_seconds', 30)) from error
            raise
        with connect(self.store.path) as db:
            db.execute("""UPDATE teaching_course_submissions SET status='responded',response_json=?,updated=?
                WHERE run_key=? AND request_key=? AND token=? AND status='running'""",
                (json.dumps(response, ensure_ascii=False, allow_nan=False), time.time(), self.key, key, token))
        return response

    def initialize_units(self, specs: list[dict[str, Any]]) -> None:
        with connect(self.store.path) as db:
            db.begin_write()
            for spec in specs:
                work = {**spec, 'revision': 0, 'failures': 0, 'repair_failures': 0,
                        'mode': 'generate', 'history': [], 'feedback': []}
                db.execute('''INSERT INTO teaching_course_units
                    (run_key,unit_id,candidate_json,candidate_hash,status,updated,work_json)
                    VALUES(?,?,'{}','','pending',?,?) ON CONFLICT(run_key,unit_id) DO NOTHING''',
                    (self.key, spec['unit_id'], time.time(), json.dumps(work, ensure_ascii=False)))

    def units(self) -> list[dict[str, Any]]:
        with connect(self.store.path) as db:
            rows = db.execute('SELECT * FROM teaching_course_units WHERE run_key=? ORDER BY unit_id',
                              (self.key,)).fetchall()
        return [{**dict(row), 'candidate': json.loads(row['candidate_json']),
                 'work': json.loads(row['work_json'])} for row in rows]

    def submission_blocked(self, unit_id: str) -> bool:
        with connect(self.store.path) as db:
            return db.execute("""SELECT 1 FROM teaching_course_submissions
                WHERE run_key=? AND unit_id=? AND status IN ('unknown','running') LIMIT 1""",
                (self.key, unit_id)).fetchone() is not None

    def retire_lost_review(self, unit_id: str) -> bool:
        """Retain an unrecoverable old review; never allow its request to replay.

        The caller must abandon its known input and enforce a different new
        design. This grants a new question, not permission to retry a lost POST.
        A still-running local call can never be retired here.
        """
        with connect(self.store.path) as db:
            db.begin_write()
            if db.execute("""SELECT 1 FROM teaching_course_submissions WHERE run_key=?
                AND unit_id=? AND status='running' LIMIT 1""", (self.key, unit_id)).fetchone():
                return False
            unknown = db.execute("""SELECT updated FROM teaching_course_submissions WHERE run_key=?
                AND unit_id=? AND status='unknown'""", (self.key, unit_id)).fetchall()
            if not unknown or any(row['updated'] > time.time() - 60 for row in unknown):
                return False
            db.execute("""UPDATE teaching_course_submissions SET status='retired_revision'
                WHERE run_key=? AND unit_id=? AND status='unknown'""", (self.key, unit_id))
        return True

    def write_unit(self, unit: dict[str, Any], *, status: str, candidate: Any = None,
                   delay: float = 0) -> None:
        draft = unit['candidate'] if candidate is None else candidate
        now = time.time()
        with connect(self.store.path) as db:
            db.begin_write()
            db.execute('''UPDATE teaching_course_units SET candidate_json=?,candidate_hash=?,status=?,
                feedback_json=?,attempts=?,next_retry_at=?,updated=?,work_json=? WHERE run_key=? AND unit_id=?''',
                (json.dumps(draft, ensure_ascii=False, allow_nan=False), fingerprint(draft), status,
                 json.dumps(unit['work'].get('feedback', []), ensure_ascii=False), unit['attempts'],
                 now + delay if delay else 0, now, json.dumps(unit['work'], ensure_ascii=False, allow_nan=False),
                 self.key, unit['unit_id']))
            db.execute('UPDATE teaching_course_repairs SET updated=? WHERE run_key=?', (now, self.key))
        unit.update(candidate=draft, status=status, next_retry_at=now + delay if delay else 0)

    def save_state(self, state: dict[str, Any]) -> None:
        with connect(self.store.path) as db:
            db.execute('UPDATE teaching_course_repairs SET state_json=?,updated=? WHERE run_key=?',
                       (json.dumps(state, ensure_ascii=False, allow_nan=False), time.time(), self.key))


def invalidate_repair_response() -> None:
    run = current_repair.get()
    if run:
        run.invalidate_last_response()
