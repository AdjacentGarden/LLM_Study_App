"""Account-scoped native workspaces. Shared textbooks never contain personal progress."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .assessment.models import FlashcardReviewState
from .database import Connection, connect
from .ingestion.models import BookStructure
from .personalization.review import rating_score, schedule_review


class NativeConflict(ValueError):
    pass


class NativeRepository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS native_workspaces (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                    preferences TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS native_workspace_owner ON native_workspaces(owner);
                CREATE TABLE IF NOT EXISTS native_resources (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES native_workspaces(id) ON DELETE CASCADE,
                    book_id TEXT NOT NULL, structure_json TEXT, session_id TEXT,
                    revision INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
                    UNIQUE(workspace_id,book_id));
                CREATE TABLE IF NOT EXISTS native_session_context (
                    session_id TEXT PRIMARY KEY, resource_id TEXT NOT NULL,
                    structure_json TEXT NOT NULL, items_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS native_progress (
                    resource_id TEXT PRIMARY KEY REFERENCES native_resources(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL, payload TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS native_mutations (
                    resource_id TEXT NOT NULL REFERENCES native_resources(id) ON DELETE CASCADE,
                    mutation_id TEXT NOT NULL, PRIMARY KEY(resource_id,mutation_id));
                CREATE TABLE IF NOT EXISTS native_selection (
                    owner TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, resource_id TEXT);
                CREATE TABLE IF NOT EXISTS native_learner_preferences (
                    owner TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS native_catalog_practice (
                    resource_id TEXT NOT NULL REFERENCES native_resources(id) ON DELETE CASCADE,
                    event_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(resource_id,event_id));
                CREATE TABLE IF NOT EXISTS native_design_overrides (
                    workspace_id TEXT PRIMARY KEY REFERENCES native_workspaces(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS native_daily_plans (
                    workspace_id TEXT NOT NULL REFERENCES native_workspaces(id) ON DELETE CASCADE,
                    day TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(workspace_id,day));
                CREATE TABLE IF NOT EXISTS native_task_completions (
                    workspace_id TEXT NOT NULL REFERENCES native_workspaces(id) ON DELETE CASCADE,
                    event_id TEXT NOT NULL, task_id TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(workspace_id,event_id));
                CREATE TABLE IF NOT EXISTS native_design_adjustments (
                    workspace_id TEXT NOT NULL REFERENCES native_workspaces(id) ON DELETE CASCADE,
                    adjustment_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(workspace_id,adjustment_id));
            """)

    def learner_preferences(self, owner: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM native_learner_preferences WHERE owner=?", (owner,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_learner_preferences(self, owner: str, value: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO native_learner_preferences VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET payload=excluded.payload",
                (owner, json.dumps(value, ensure_ascii=False)),
            )

    def design_overrides(self, owner: str, wid: str) -> dict[str, Any]:
        self.workspace(owner, wid)
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM native_design_overrides WHERE workspace_id=?", (wid,)
            ).fetchone()
        return (
            {"revision": row["revision"], "overrides": json.loads(row["payload"])}
            if row
            else {"revision": 0, "overrides": {}}
        )

    def save_design_overrides(
        self,
        owner: str,
        wid: str,
        revision: int,
        payload: dict[str, Any],
        *,
        adjustment: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.workspace(owner, wid)
        with self.connect() as db:
            db.begin_write()
            row = db.execute(
                "SELECT revision FROM native_design_overrides WHERE workspace_id=?", (wid,)
            ).fetchone()
            if (row[0] if row else 0) != revision:
                raise NativeConflict("课程学习偏好已更新，请重新加载")
            db.execute(
                "INSERT INTO native_design_overrides VALUES(?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload",
                (wid, revision + 1, json.dumps(payload, ensure_ascii=False)),
            )
            if adjustment is not None:
                db.execute(
                    "INSERT INTO native_design_adjustments VALUES(?,?,?) ON CONFLICT(workspace_id,adjustment_id) DO UPDATE SET payload=excluded.payload",
                    (wid, adjustment["suggestion_id"], json.dumps(adjustment, ensure_ascii=False)),
                )
        return {"revision": revision + 1, "overrides": payload}

    def design_context(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT w.id AS workspace_id,w.owner,w.preferences,r.id AS resource_id FROM native_resources r JOIN native_workspaces w ON w.id=r.workspace_id WHERE r.session_id=?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def daily_plan(self, owner: str, wid: str, day: str) -> dict[str, Any] | None:
        self.workspace(owner, wid)
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM native_daily_plans WHERE workspace_id=? AND day=?", (wid, day)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_daily_plan(
        self,
        owner: str,
        wid: str,
        day: str,
        payload: dict[str, Any],
        *,
        expected_plan_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        self.workspace(owner, wid)
        with self.connect() as db:
            db.begin_write()
            row = db.execute(
                "SELECT payload FROM native_daily_plans WHERE workspace_id=? AND day=?", (wid, day)
            ).fetchone()
            previous: dict[str, Any] | None = json.loads(row[0]) if row else None
            if expected_plan_id is not None:
                if previous is None or previous["plan_id"] != expected_plan_id:
                    raise NativeConflict("今日计划已改变，请重新加载后确认")
                if (
                    expected_revision is not None
                    and previous.get("revision", 0) != expected_revision
                ):
                    raise NativeConflict("今日计划已在另一端修改，请重新加载")
            elif previous and (
                previous.get("confirmed") or previous["plan_id"] == payload["plan_id"]
            ):
                return previous
            db.execute(
                "INSERT INTO native_daily_plans VALUES(?,?,?) ON CONFLICT(workspace_id,day) DO UPDATE SET payload=excluded.payload",
                (wid, day, json.dumps(payload, ensure_ascii=False)),
            )
        return payload

    def completed_tasks(self, owner: str, wid: str) -> set[str]:
        self.workspace(owner, wid)
        with self.connect() as db:
            return {
                row[0]
                for row in db.execute(
                    "SELECT task_id FROM native_task_completions WHERE workspace_id=?", (wid,)
                )
            }

    def complete_task(self, owner: str, wid: str, event_id: str, task_id: str) -> bool:
        self.workspace(owner, wid)
        with self.connect() as db:
            db.begin_write()
            row = db.execute(
                "SELECT task_id FROM native_task_completions WHERE workspace_id=? AND event_id=?",
                (wid, event_id),
            ).fetchone()
            if row:
                if row[0] != task_id:
                    raise NativeConflict("重复提交的任务不一致")
                return False
            db.execute(
                "INSERT INTO native_task_completions VALUES(?,?,?,?)",
                (wid, event_id, task_id, time.time()),
            )
        return True

    def adjustments(self, owner: str, wid: str) -> list[dict[str, Any]]:
        self.workspace(owner, wid)
        with self.connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM native_design_adjustments WHERE workspace_id=?", (wid,)
                )
            ]

    def save_adjustment(
        self, owner: str, wid: str, adjustment_id: str, payload: dict[str, Any]
    ) -> None:
        self.workspace(owner, wid)
        with self.connect() as db:
            db.execute(
                "INSERT INTO native_design_adjustments VALUES(?,?,?) ON CONFLICT(workspace_id,adjustment_id) DO UPDATE SET payload=excluded.payload",
                (wid, adjustment_id, json.dumps(payload, ensure_ascii=False)),
            )

    def catalog_practice(self, resource_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM native_catalog_practice WHERE resource_id=? ORDER BY rowid DESC",
                    (resource_id,),
                )
            ]

    def save_catalog_practice(
        self, resource_id: str, event_id: str, payload: dict[str, Any]
    ) -> bool:
        with self.connect() as db:
            return bool(
                db.execute(
                    'INSERT INTO native_catalog_practice(resource_id,event_id,payload) VALUES(?,?,?) ON CONFLICT DO NOTHING',
                    (resource_id, event_id, json.dumps(payload, ensure_ascii=False)),
                ).rowcount
            )

    def catalog_review_states(self, resource_id: str) -> dict[str, Any]:
        return {record["question_id"]: record["review_state"]
                for record in reversed(self.catalog_practice(resource_id))
                if "review_state" in record}

    def review_catalog_card(
        self, resource_id: str, event_id: str, card: dict[str, Any],
        rating: str, response_seconds: float,
    ) -> dict[str, Any]:
        # Serialize scheduling and event insertion: concurrent reviews must use
        # the last committed FSRS state, and retries must not advance it twice.
        with self.connect() as db:
            db.begin_write()
            rows = [json.loads(r[0]) for r in db.execute(
                "SELECT payload FROM native_catalog_practice WHERE resource_id=? ORDER BY rowid DESC",
                (resource_id,),
            )]
            duplicate = next((r for r in rows if r["id"] == event_id), None)
            if duplicate:
                if (duplicate.get("review_state") is None
                        or duplicate["question_id"] != card["card_id"]
                        or duplicate["answer"] != rating
                        or duplicate["response_seconds"] != response_seconds):
                    raise NativeConflict("重复提交的内容不一致")
                return {"duplicate": True, "review_state": duplicate["review_state"]}
            previous = next((r["review_state"] for r in rows
                             if r["question_id"] == card["card_id"] and "review_state" in r), None)
            state = schedule_review(FlashcardReviewState.model_validate(previous) if previous else None,
                                    rating, response_seconds=response_seconds)
            assert state.updated_at is not None
            record = {
                "id": event_id, "question_id": card["card_id"], "title": card["front"],
                "chapter": card["concept"], "chapter_id": card["chapter_id"],
                "pages": [card["page_start"]] if card["page_start"] else [],
                "answer": rating, "response_seconds": response_seconds,
                "score": rating_score(rating), "at": state.updated_at.isoformat(),
                "review_state": state.model_dump(mode="json"), "kind": "flashcard_review",
            }
            db.execute("INSERT INTO native_catalog_practice(resource_id,event_id,payload) VALUES(?,?,?)",
                       (resource_id, event_id, json.dumps(record, ensure_ascii=False)))
            return {"duplicate": False, "review_state": record["review_state"]}

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        with connect(self.path) as db:
            yield db

    def workspace(self, owner: str, key: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM native_workspaces WHERE owner=? AND id=?", (owner, key)
            ).fetchone()
        if row is None:
            raise KeyError(key)
        return {**dict(row), "preferences": json.loads(row["preferences"])}

    def workspaces(self, owner: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [{**dict(row), "preferences": json.loads(row["preferences"])} for row in
                    db.execute("SELECT * FROM native_workspaces WHERE owner=? ORDER BY created,id", (owner,))]

    def save_workspace(
        self, owner: str, key: str, name: str, preferences: dict[str, Any]
    ) -> dict[str, Any]:
        with self.connect() as db:
            db.begin_write()
            row = db.execute("SELECT owner FROM native_workspaces WHERE id=?", (key,)).fetchone()
            if row and row[0] != owner:
                raise KeyError(key)
            now = time.time()
            db.execute(
                "INSERT INTO native_workspaces VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,preferences=excluded.preferences,updated=excluded.updated",
                (key, owner, name, json.dumps(preferences, ensure_ascii=False), now, now),
            )
        return self.workspace(owner, key)

    def delete_workspace(self, owner: str, key: str) -> None:
        self.workspace(owner, key)
        with self.connect() as db:
            db.execute("DELETE FROM native_workspaces WHERE id=? AND owner=?", (key, owner))
            db.execute(
                "DELETE FROM native_selection WHERE owner=? AND workspace_id=?", (owner, key)
            )

    def resources(self, owner: str, workspace_id: str) -> list[dict[str, Any]]:
        self.workspace(owner, workspace_id)
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM native_resources WHERE workspace_id=? ORDER BY created,id",
                    (workspace_id,),
                )
            ]

    def resource(self, owner: str, workspace_id: str, key: str) -> dict[str, Any]:
        self.workspace(owner, workspace_id)
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM native_resources WHERE workspace_id=? AND id=?", (workspace_id, key)
            ).fetchone()
        if row is None:
            raise KeyError(key)
        return dict(row)

    def add_resource(self, owner: str, workspace_id: str, book_id: str) -> dict[str, Any]:
        self.workspace(owner, workspace_id)
        with self.connect() as db:
            db.execute(
                'INSERT INTO native_resources(id,workspace_id,book_id,created) VALUES(?,?,?,?) ON CONFLICT DO NOTHING',
                ("resource_" + uuid.uuid4().hex, workspace_id, book_id, time.time()),
            )
            key = db.execute(
                "SELECT id FROM native_resources WHERE workspace_id=? AND book_id=?",
                (workspace_id, book_id),
            ).fetchone()[0]
        return self.resource(owner, workspace_id, key)

    def remove_resource(self, owner: str, workspace_id: str, key: str) -> None:
        self.resource(owner, workspace_id, key)
        with self.connect() as db:
            db.execute("DELETE FROM native_resources WHERE id=?", (key,))

    def save_structure(
        self, owner: str, workspace_id: str, key: str, revision: int, structure: BookStructure
    ) -> None:
        self.resource(owner, workspace_id, key)
        encoded = structure.model_dump_json()
        with self.connect() as db:
            db.begin_write()
            row = db.execute(
                "SELECT revision,structure_json FROM native_resources WHERE id=?", (key,)
            ).fetchone()
            if row["structure_json"] == encoded:
                return
            if row["revision"] != revision:
                raise NativeConflict("目录已更新，请重新加载后再保存")
            db.execute(
                "UPDATE native_resources SET structure_json=?,revision=revision+1,session_id=NULL WHERE id=?",
                (encoded, key),
            )
            db.execute("DELETE FROM native_progress WHERE resource_id=?", (key,))
            db.execute("DELETE FROM native_mutations WHERE resource_id=?", (key,))

    def attach_session(
        self,
        resource_id: str,
        revision: int,
        session_id: str,
        structure: BookStructure,
        items_json: str,
    ) -> str:
        with self.connect() as db:
            db.begin_write()
            row = db.execute(
                "SELECT session_id,revision FROM native_resources WHERE id=?", (resource_id,)
            ).fetchone()
            if row is None or row["revision"] != revision:
                raise NativeConflict("目录已改变，请重新开始诊断")
            if row["session_id"]:
                return str(row["session_id"])
            db.execute(
                "INSERT INTO native_session_context VALUES(?,?,?,?)",
                (session_id, resource_id, structure.model_dump_json(), items_json),
            )
            db.execute(
                "UPDATE native_resources SET session_id=? WHERE id=?", (session_id, resource_id)
            )
        return session_id

    def session_context(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM native_session_context WHERE session_id=?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def reconcile_book_structure(self, book_id: str, before: BookStructure, after: BookStructure,
                                 published_sessions: set[str]) -> None:
        """Refresh generated outlines without clearing progress or completed courses."""
        expected = [(c.chapter_id, c.start_page, c.end_page) for c in before.chapters]
        encoded = after.model_dump_json()

        def matches(value: str | None) -> bool:
            if not value:
                return False
            structure = BookStructure.model_validate_json(value)
            return [(c.chapter_id, c.start_page, c.end_page) for c in structure.chapters] == expected

        with self.connect() as db:
            db.begin_write()
            resources = db.execute("SELECT id,structure_json FROM native_resources WHERE book_id=?", (book_id,)).fetchall()
            for resource in resources:
                if matches(resource["structure_json"]):
                    db.execute("UPDATE native_resources SET structure_json=?,revision=revision+1 WHERE id=?",
                               (encoded, resource["id"]))
                contexts = db.execute("SELECT session_id,structure_json FROM native_session_context WHERE resource_id=?",
                                      (resource["id"],)).fetchall()
                for context in contexts:
                    if context["session_id"] not in published_sessions and matches(context["structure_json"]):
                        db.execute("UPDATE native_session_context SET structure_json=? WHERE session_id=?",
                                   (encoded, context["session_id"]))

    def progress(self, resource_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM native_progress WHERE resource_id=?", (resource_id,)
            ).fetchone()
        return (
            {"revision": row["revision"], **json.loads(row["payload"])}
            if row
            else {
                "revision": 0,
                "page": 1,
                "chapter_id": None,
                "lesson_id": None,
                "completed_chapters": [],
                "task_status": {},
                "mastered_ids": [],
            }
        )

    def save_progress(
        self,
        owner: str,
        workspace_id: str,
        key: str,
        revision: int,
        mutation_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.resource(owner, workspace_id, key)
        with self.connect() as db:
            db.begin_write()
            if db.execute(
                "SELECT 1 FROM native_mutations WHERE resource_id=? AND mutation_id=?",
                (key, mutation_id),
            ).fetchone():
                return self.progress(key)
            old = db.execute(
                "SELECT revision FROM native_progress WHERE resource_id=?", (key,)
            ).fetchone()
            if (old[0] if old else 0) != revision:
                raise NativeConflict("学习进度已在其他设备更新")
            db.execute(
                "INSERT INTO native_progress VALUES(?,?,?,?) ON CONFLICT(resource_id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,updated=excluded.updated",
                (key, revision + 1, json.dumps(payload, ensure_ascii=False), time.time()),
            )
            db.execute("INSERT INTO native_mutations VALUES(?,?)", (key, mutation_id))
        return self.progress(key)

    def selection(self, owner: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT workspace_id,resource_id FROM native_selection WHERE owner=?", (owner,)
            ).fetchone()
        return dict(row) if row else None

    def save_selection(self, owner: str, workspace_id: str, resource_id: str) -> None:
        self.resource(owner, workspace_id, resource_id)
        with self.connect() as db:
            db.execute(
                "INSERT INTO native_selection VALUES(?,?,?) ON CONFLICT(owner) DO UPDATE SET workspace_id=excluded.workspace_id,resource_id=excluded.resource_id",
                (owner, workspace_id, resource_id),
            )
