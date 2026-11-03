"""Account-scoped incremental assistant history and monotonic context capacity.

Temporary conversations and their model ledger exist only in process memory.
They never create database rows or recoverable chat archives.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .assistant_ledger import AssistantLedger, LedgerConflict
from .database import Connection

WINDOW = 1_000_000
THRESHOLD = 900_000
HISTORY_LIMIT = 6


class MemoryLedger(AssistantLedger):
    def __init__(self) -> None:
        self.raw = sqlite3.connect(":memory:", check_same_thread=False)
        self.raw.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        super().__init__(Path("."))

    @contextmanager
    def connect(self):
        with self.lock, self.raw:
            yield Connection(self.raw, False)


class AssistantSessions:
    def __init__(self, ledger: AssistantLedger) -> None:
        self.ledger = ledger
        self.lock = threading.RLock()
        self.temporary: dict[tuple[str, str], dict[str, Any]] = {}
        self.memory_ledgers: dict[tuple[str, str], MemoryLedger] = {}
        with ledger.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS assistant_history_initializations (
                    owner TEXT PRIMARY KEY,initialized REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS assistant_conversations (
                    owner TEXT NOT NULL,session_id TEXT NOT NULL,title TEXT NOT NULL,
                    state TEXT NOT NULL,input_tokens INTEGER NOT NULL,estimated INTEGER NOT NULL,
                    active_run_id TEXT,reason TEXT,created REAL NOT NULL,updated REAL NOT NULL,
                    PRIMARY KEY(owner,session_id));
                CREATE TABLE IF NOT EXISTS assistant_conversation_events (
                    owner TEXT NOT NULL,session_id TEXT NOT NULL,event_id TEXT NOT NULL,
                    payload TEXT NOT NULL,created REAL NOT NULL,
                    PRIMARY KEY(owner,session_id,event_id));
                CREATE TABLE IF NOT EXISTS assistant_conversation_runs (
                    owner TEXT NOT NULL,session_id TEXT NOT NULL,run_id TEXT NOT NULL,
                    PRIMARY KEY(owner,run_id));
            """)

    def migrate_history(self, owner: str, sessions: list[dict[str, Any]]) -> bool:
        """Import pre-upgrade archives once, before this account starts v2.

        Existing over-quota archives are retained. The initialization marker is
        sealed by ordinary first sends too, so migration cannot add a seventh
        new conversation after the normal quota has been established.
        """
        with self.ledger.connect() as db:
            db.begin_write()
            initialized = db.execute(
                "SELECT 1 FROM assistant_history_initializations WHERE owner=?", (owner,)
            ).fetchone()
            existing = db.execute(
                "SELECT count(*) FROM assistant_conversations WHERE owner=?", (owner,)
            ).fetchone()[0]
            if initialized or existing:
                return False
            now = time.time()
            for session in sessions:
                state = session.get("state", "open")
                tokens = session.get("input_tokens", 0)
                active = session.get("active_run_id")
                if state == "read_only" or tokens >= WINDOW or state == "finishing" and not active:
                    state = "archived"
                elif tokens >= THRESHOLD and state == "open":
                    state = "finishing" if active else "archived"
                db.execute(
                    "INSERT INTO assistant_conversations VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        owner,
                        session["session_id"],
                        session["title"],
                        state,
                        tokens,
                        int(session.get("estimated", True)),
                        active,
                        "context_capacity" if state != "open" else None,
                        session.get("created") or now,
                        now,
                    ),
                )
                for index, message in enumerate(session.get("messages", [])):
                    payload = {"role": message["role"], "content": message["text"]}
                    if message["role"] == "user":
                        payload["display_text"] = message["text"]
                    db.execute(
                        "INSERT INTO assistant_conversation_events VALUES(?,?,?,?,?)",
                        (
                            owner,
                            session["session_id"],
                            f"migration:{index}",
                            json.dumps(payload, ensure_ascii=False),
                            now + index / 1000000,
                        ),
                    )
                if active:
                    db.execute(
                        "INSERT INTO assistant_conversation_runs VALUES(?,?,?) ON CONFLICT DO NOTHING",
                        (owner, session["session_id"], active),
                    )
            db.execute("INSERT INTO assistant_history_initializations VALUES(?,?)", (owner, now))
            return True

    def begin(
        self, owner: str, session: str, run: str, title: str, ephemeral: bool
    ) -> dict[str, Any]:
        with self.lock:
            if ephemeral:
                key = owner, session
                if key not in self.temporary:
                    self.temporary[key] = dict(
                        owner=owner,
                        session_id=session,
                        title=title,
                        state="open",
                        input_tokens=0,
                        estimated=True,
                        active_run_id=None,
                        reason=None,
                        events={},
                    )
                    self.memory_ledgers[key] = MemoryLedger()
                value = self.temporary[key]
                self._check(value, run)
                value["active_run_id"] = run
                return self.public(value)
            with self.ledger.connect() as db:
                db.begin_write()
                db.execute(
                    "INSERT INTO assistant_history_initializations VALUES(?,?) ON CONFLICT DO NOTHING",
                    (owner, time.time()),
                )
                row = db.execute(
                    "SELECT * FROM assistant_conversations WHERE owner=? AND session_id=?",
                    (owner, session),
                ).fetchone()
                if row is None:
                    if (
                        db.execute(
                            "SELECT count(*) FROM assistant_conversations WHERE owner=?", (owner,)
                        ).fetchone()[0]
                        >= HISTORY_LIMIT
                    ):
                        raise LedgerConflict("已达会话数上限，请删除无用历史会话")
                    now = time.time()
                    db.execute(
                        "INSERT INTO assistant_conversations VALUES(?,?,?,'open',0,1,NULL,NULL,?,?)",
                        (owner, session, title[:200], now, now),
                    )
                    row = db.execute(
                        "SELECT * FROM assistant_conversations WHERE owner=? AND session_id=?",
                        (owner, session),
                    ).fetchone()
                self._check(dict(row), run)
                bound = db.execute(
                    "SELECT session_id FROM assistant_conversation_runs WHERE owner=? AND run_id=?",
                    (owner, run),
                ).fetchone()
                if bound and bound[0] != session:
                    raise LedgerConflict("任务属于另一会话")
                db.execute(
                    "INSERT INTO assistant_conversation_runs VALUES(?,?,?) ON CONFLICT DO NOTHING",
                    (owner, session, run),
                )
                db.execute(
                    "UPDATE assistant_conversations SET active_run_id=?,updated=? WHERE owner=? AND session_id=?",
                    (run, time.time(), owner, session),
                )
                return self.public(dict(row))

    @staticmethod
    def _check(value: dict[str, Any], run: str) -> None:
        if (
            value["state"] == "archived"
            or value["state"] == "finishing"
            and value["active_run_id"] != run
        ):
            raise LedgerConflict("此对话已达到上下文上限，无法继续对话。请新建对话。")
        if value["active_run_id"] and value["active_run_id"] != run:
            raise LedgerConflict("当前会话仍有任务执行中，请先完成或取消")

    def events(self, owner: str, session: str, ephemeral: bool) -> list[dict[str, Any]]:
        return [event for _, event in self.event_items(owner, session, ephemeral)]

    def event_items(self, owner: str, session: str, ephemeral: bool):
        if ephemeral:
            with self.lock:
                return list(self.temporary[(owner, session)]["events"].items())
        with self.ledger.connect() as db:
            return [
                (r[0], json.loads(r[1]))
                for r in db.execute(
                    "SELECT event_id,payload FROM assistant_conversation_events "
                    "WHERE owner=? AND session_id=? ORDER BY created,event_id",
                    (owner, session),
                )
            ]

    def model_history(self, owner, session, ephemeral, ledger, operation_lookup):
        """Pair a cancelled step with known receipts without replaying its writes.

        A receipt can arrive after cancellation or even after the next user message.
        Put it next to the original model call in the wire history.
        """
        items = self.event_items(owner, session, ephemeral)
        with ledger.connect() as db:
            cancelled = {r[0] for r in db.execute(
                "SELECT run_id FROM assistant_runs WHERE owner=? AND state='cancelled'", (owner,)
            )}
        receipts = dict(items)
        messages = []
        for key, event in items:
            run, _, _ = key.rpartition(":tool:")
            if event["role"] == "tool" and run in cancelled:
                continue
            messages.append(event)
            run, _, _ = key.rpartition(":assistant:")
            if event["role"] != "assistant" or run not in cancelled:
                continue
            for call in event.get("tool_calls", []):
                call_id = call["id"]
                receipt = receipts.get(f"{run}:tool:{call_id}")
                if receipt is None:
                    result = operation_lookup(owner, f"{run}.{call_id}") or {
                        "status": "cancelled_without_receipt",
                        "message": "任务已取消，操作结果未确认。不要假定成功或重新执行。",
                    }
                    receipt = {"role": "tool", "tool_call_id": call_id,
                               "content": json.dumps(result, ensure_ascii=False)}
                messages.append(receipt)
        return messages

    def append(
        self, owner: str, session: str, event_id: str, payload: dict[str, Any], ephemeral: bool
    ) -> None:
        with self.lock:
            if ephemeral:
                events = self.temporary[(owner, session)]["events"]
                if event_id in events and events[event_id] != payload:
                    raise LedgerConflict("消息 ID 已绑定其他内容")
                events[event_id] = payload
                return
            encoded = json.dumps(payload, ensure_ascii=False)
            with self.ledger.connect() as db:
                db.begin_write()
                old = db.execute(
                    "SELECT payload FROM assistant_conversation_events WHERE owner=? AND session_id=? AND event_id=?",
                    (owner, session, event_id),
                ).fetchone()
                if old and old[0] != encoded:
                    raise LedgerConflict("消息 ID 已绑定其他内容")
                db.execute(
                    "INSERT INTO assistant_conversation_events VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING",
                    (owner, session, event_id, encoded, time.time()),
                )

    def capacity(
        self,
        owner: str,
        session: str,
        tokens: int,
        estimated: bool,
        run: str,
        ephemeral: bool,
        done: bool = False,
    ) -> dict[str, Any]:
        with self.lock:
            value = self.get(owner, session, ephemeral)
            state = value["state"]
            if tokens >= WINDOW:
                state = "archived"
            elif tokens >= THRESHOLD and state == "open":
                state = "finishing" if not done else "archived"
            elif done and state == "finishing":
                state = "archived"
            reason = "context_capacity" if state != "open" else None
            active = None if done else run
            if ephemeral:
                self.temporary[(owner, session)].update(
                    state=state,
                    input_tokens=tokens,
                    estimated=estimated,
                    active_run_id=active,
                    reason=reason,
                )
            else:
                with self.ledger.connect() as db:
                    db.execute(
                        "UPDATE assistant_conversations SET state=?,input_tokens=?,estimated=?,active_run_id=?,reason=?,updated=? WHERE owner=? AND session_id=?",
                        (
                            state,
                            tokens,
                            int(estimated),
                            active,
                            reason,
                            time.time(),
                            owner,
                            session,
                        ),
                    )
            return dict(
                input_tokens=tokens,
                estimated=estimated,
                window_tokens=WINDOW,
                new_turn_threshold=THRESHOLD,
                state=state,
                active_run_id=active,
                reason=reason,
            )

    def get(self, owner: str, session: str, ephemeral: bool = False) -> dict[str, Any]:
        if ephemeral:
            value = self.temporary.get((owner, session))
        else:
            with self.ledger.connect() as db:
                value = db.execute(
                    "SELECT * FROM assistant_conversations WHERE owner=? AND session_id=?",
                    (owner, session),
                ).fetchone()
        if value is None:
            raise KeyError(session)
        return dict(value)

    def list(self, owner: str) -> list[dict[str, Any]]:
        with self.ledger.connect() as db:
            return [
                self.public(dict(r))
                for r in db.execute(
                    "SELECT * FROM assistant_conversations WHERE owner=? ORDER BY updated DESC",
                    (owner,),
                )
            ]

    def close_temporary(self, owner: str, session: str) -> None:
        with self.lock:
            self.temporary.pop((owner, session), None)
            self.memory_ledgers.pop((owner, session), None)

    def delete(self, owner: str, session: str) -> None:
        with self.lock:
            self.temporary.pop((owner, session), None)
            self.memory_ledgers.pop((owner, session), None)
            with self.ledger.connect() as db:
                db.begin_write()
                runs = list(
                    db.execute(
                        "SELECT run_id FROM assistant_conversation_runs WHERE owner=? AND session_id=?",
                        (owner, session),
                    )
                )
                for row in runs:
                    db.execute(
                        "DELETE FROM assistant_steps WHERE owner=? AND run_id=?", (owner, row[0])
                    )
                    db.execute(
                        "DELETE FROM assistant_runs WHERE owner=? AND run_id=?", (owner, row[0])
                    )
                db.execute(
                    "DELETE FROM assistant_conversation_runs WHERE owner=? AND session_id=?",
                    (owner, session),
                )
                db.execute(
                    "DELETE FROM assistant_conversation_events WHERE owner=? AND session_id=?",
                    (owner, session),
                )
                db.execute(
                    "DELETE FROM assistant_conversations WHERE owner=? AND session_id=?",
                    (owner, session),
                )

    @staticmethod
    def public(value: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in value.items() if k not in {"owner", "events"}}
