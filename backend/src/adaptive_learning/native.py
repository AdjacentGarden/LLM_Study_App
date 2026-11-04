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
                CREATE TABLE IF NOT EXISTS native_creation_drafts (
                    workspace_id TEXT PRIMARY KEY REFERENCES native_workspaces(id) ON DELETE CASCADE,
                    payload TEXT NOT NULL, updated REAL NOT NULL);
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
                CREATE TABLE IF NOT EXISTS native_learning_objects (
                    id TEXT PRIMARY KEY,owner TEXT NOT NULL,
                    workspace_id TEXT NOT NULL REFERENCES native_workspaces(id) ON DELETE CASCADE,
                    course_resource_id TEXT,kind TEXT NOT NULL,revision INTEGER NOT NULL,
                    payload TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS native_workspace_tombstones (
                    owner TEXT NOT NULL,workspace_id TEXT NOT NULL,deleted REAL NOT NULL,
                    PRIMARY KEY(owner,workspace_id));
                CREATE TABLE IF NOT EXISTS native_assistant_operations (
                    owner TEXT NOT NULL,operation_id TEXT NOT NULL,digest TEXT NOT NULL,
                    receipt TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(owner,operation_id));
            """)

    def assistant_objects(
        self, owner: str, course_id: str | None = None, kind: str | None = None
    ) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM native_learning_objects WHERE owner=? ORDER BY created,id", (owner,)
            )
            return [
                {**dict(r), "data": json.loads(r["payload"])}
                for r in rows
                if (course_id is None or r["workspace_id"] == course_id)
                and (kind is None or r["kind"] == kind)
            ]

    def assistant_object(self, owner: str, object_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM native_learning_objects WHERE owner=? AND id=?", (owner, object_id)
            ).fetchone()
        if row is None:
            raise KeyError(object_id)
        return {**dict(row), "data": json.loads(row["payload"])}

    def assistant_operation(self, owner: str, operation_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT receipt FROM native_assistant_operations WHERE owner=? AND operation_id=?",
                (owner, operation_id),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def execute_assistant_operation(
        self, owner: str, operation_id: str, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        import hashlib

        encoded = json.dumps(
            {"tool": tool, "arguments": arguments}, ensure_ascii=False, sort_keys=True
        )
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.connect() as db:
            db.begin_write()
            old = db.execute(
                "SELECT * FROM native_assistant_operations WHERE owner=? AND operation_id=?",
                (owner, operation_id),
            ).fetchone()
            if old:
                if old["digest"] != digest:
                    raise NativeConflict("操作 ID 已用于不同参数")
                return json.loads(old["receipt"])
            wid = arguments.get("course_id")
            workspace = db.execute(
                "SELECT * FROM native_workspaces WHERE owner=? AND id=?", (owner, wid)
            ).fetchone()
            if workspace is None:
                raise KeyError(wid)
            rid = arguments.get("course_resource_id")
            if (
                rid
                and not db.execute(
                    "SELECT 1 FROM native_resources WHERE workspace_id=? AND id=?", (wid, rid)
                ).fetchone()
            ):
                raise KeyError(rid)
            now = time.time()
            if tool == "remove_course":
                impact = self._deletion_impact(db, owner, wid)
                if arguments.get("confirmed") is not True:
                    return {
                        "state": "needs_confirmation",
                        "operation_id": operation_id,
                        "impact": impact,
                    }
                if arguments.get("impact_version") != impact["version"]:
                    raise NativeConflict("课程关联数据已改变，请重新确认影响清单")
                resources = [
                    r[0]
                    for r in db.execute(
                        "SELECT id FROM native_resources WHERE workspace_id=?", (wid,)
                    )
                ]
                for resource in resources:
                    db.execute(
                        "DELETE FROM native_session_context WHERE resource_id=?", (resource,)
                    )
                studio_scope = self._studio_namespace(db)
                if studio_scope is not None:
                    resource_ids = set(resources)
                    notes = [
                        r["id"]
                        for r in db.execute(
                            f"SELECT id,data FROM {studio_scope}studio_notes WHERE owner=?",
                            (owner,),
                        )
                        if json.loads(r["data"]).get("course_resource_id") in resource_ids
                    ]
                    jobs = [
                        r["id"]
                        for r in db.execute(
                            f"SELECT id,data FROM {studio_scope}studio_jobs WHERE owner=?", (owner,)
                        )
                        if json.loads(r["data"]).get("note_id") in set(notes)
                        or json.loads(r["data"]).get("course_resource_id") in resource_ids
                    ]
                    for job_id in jobs:
                        db.execute(
                            f"DELETE FROM {studio_scope}studio_note_request_aliases WHERE owner=? AND job_id=?",
                            (owner, job_id),
                        )
                        db.execute(
                            f"DELETE FROM {studio_scope}studio_note_editions WHERE job_id=?",
                            (job_id,),
                        )
                        db.execute(
                            f"DELETE FROM {studio_scope}studio_jobs WHERE owner=? AND id=?",
                            (owner, job_id),
                        )
                    for note_id in notes:
                        db.execute(
                            f"DELETE FROM {studio_scope}studio_note_logical_inputs WHERE note_id=?",
                            (note_id,),
                        )
                        db.execute(
                            f"DELETE FROM {studio_scope}studio_note_editions WHERE note_id=?",
                            (note_id,),
                        )
                        db.execute(
                            f"DELETE FROM {studio_scope}studio_notes WHERE owner=? AND id=?",
                            (owner, note_id),
                        )
                assessment_scope = self._assessment_namespace(db)
                if assessment_scope is not None:
                    sessions = [
                        r[0]
                        for r in db.execute(
                            "SELECT session_id FROM native_resources WHERE workspace_id=? AND session_id IS NOT NULL",
                            (wid,),
                        )
                    ]
                    for sid in sessions:
                        if db.execute(
                            "SELECT 1 FROM native_resources WHERE workspace_id<>? AND session_id=?",
                            (wid, sid),
                        ).fetchone():
                            continue
                        for table in ("learning_events", "course_bundles", "interview_sessions"):
                            db.execute(
                                f"DELETE FROM {assessment_scope}{table} WHERE session_id=?", (sid,)
                            )
                db.execute(
                    "INSERT INTO native_workspace_tombstones VALUES(?,?,?) ON CONFLICT DO NOTHING",
                    (owner, wid, now),
                )
                db.execute("DELETE FROM native_workspaces WHERE owner=? AND id=?", (owner, wid))
                db.execute(
                    "DELETE FROM native_selection WHERE owner=? AND workspace_id=?", (owner, wid)
                )
                receipt = {
                    "state": "completed",
                    "operation_id": operation_id,
                    "course_id": wid,
                    "deleted": True,
                    "impact": impact,
                }
            elif tool == "review_flashcard":
                key = arguments.get("object_id")
                row = db.execute(
                    "SELECT * FROM native_learning_objects WHERE owner=? AND workspace_id=? AND id=?",
                    (owner, wid, key),
                ).fetchone()
                if row is None or row["kind"] != "flashcard":
                    raise KeyError(key)
                data = json.loads(row["payload"])
                rating = arguments["rating"]
                previous = data.get("review_state")
                review = schedule_review(
                    FlashcardReviewState.model_validate(previous) if previous else None,
                    rating,
                    response_seconds=arguments["seconds"],
                )
                state = review.model_dump(mode="json")
                data.update(
                    mastery=0 if rating == "again" else 1,
                    review_state=state,
                    rating=rating,
                    due=state["due_at"],
                )
                data.setdefault("review_history", []).append(
                    {
                        "operation_id": operation_id,
                        "rating": rating,
                        "seconds": arguments["seconds"],
                        "at": state["updated_at"],
                    }
                )
                revision = row["revision"] + 1
                db.execute(
                    "UPDATE native_learning_objects SET payload=?,revision=? WHERE owner=? AND id=?",
                    (json.dumps(data, ensure_ascii=False), revision, owner, key),
                )
                receipt = {
                    "state": "completed",
                    "operation_id": operation_id,
                    "object_id": key,
                    "revision": revision,
                    "review_state": state,
                    "duplicate": False,
                }
            elif tool == "complete_learning_task":
                key = arguments.get("object_id")
                row = db.execute(
                    "SELECT * FROM native_learning_objects WHERE owner=? AND workspace_id=? AND id=?",
                    (owner, wid, key),
                ).fetchone()
                if row is None or row["kind"] not in {"review_task", "plan"}:
                    raise KeyError(key)
                data = json.loads(row["payload"])
                task_id = arguments.get("task_id")
                if row["kind"] == "review_task" or not data.get("tasks"):
                    if task_id and task_id != key:
                        raise KeyError(task_id)
                    data["status"] = "done"
                else:
                    matched = False
                    for index, task in enumerate(data["tasks"]):
                        identities = {str(index), f"{key}.{index}", task.get("id")}
                        if len(data["tasks"]) == 1:
                            identities.add(key)
                        if task_id is None or task_id in identities:
                            task["status"] = "done"
                            matched = True
                    if not matched:
                        raise KeyError(task_id)
                    if all(task.get("status") == "done" for task in data["tasks"]):
                        data["status"] = "done"
                revision = row["revision"] + 1
                db.execute(
                    "UPDATE native_learning_objects SET payload=?,revision=? WHERE owner=? AND id=?",
                    (json.dumps(data, ensure_ascii=False), revision, owner, key),
                )
                receipt = {
                    "state": "completed",
                    "operation_id": operation_id,
                    "object_id": key,
                    "revision": revision,
                    "data": data,
                }
            elif tool in {"create_flashcards", "create_review_task", "update_study_plan"}:
                kind = {
                    "create_flashcards": "flashcard",
                    "create_review_task": "review_task",
                    "update_study_plan": "plan",
                }[tool]
                if tool == "create_flashcards":
                    values = arguments.get("cards") or [
                        {"front": arguments.get("front"), "back": arguments.get("back")}
                    ]
                    if (
                        not isinstance(values, list)
                        or not values
                        or any(
                            not isinstance(v, dict) or not v.get("front") or not v.get("back")
                            for v in values
                        )
                    ):
                        raise NativeConflict("闪卡必须包含 front 和 back")
                else:
                    from datetime import date

                    try:
                        date.fromisoformat(arguments.get("date", ""))
                    except (TypeError, ValueError) as error:
                        raise NativeConflict("请指定有效日期（YYYY-MM-DD）") from error
                    if tool == "create_review_task" and not arguments.get("title"):
                        raise NativeConflict("复习任务必须有标题")
                    if tool == "update_study_plan" and (
                        not isinstance(arguments.get("tasks"), list)
                        or any(
                            not isinstance(task, dict) or not (task.get("id") or task.get("title"))
                            for task in arguments.get("tasks", [])
                        )
                    ):
                        raise NativeConflict("学习计划必须包含有标题或已有 ID 的 tasks 数组")
                    values = [arguments]
                objects = []
                for index, value in enumerate(values):
                    key = (
                        "agent_"
                        + hashlib.sha256(f"{owner}:{operation_id}:{index}".encode()).hexdigest()[
                            :32
                        ]
                    )
                    revision = 1
                    target_id = arguments.get("object_id")
                    if target_id and len(values) == 1:
                        previous = db.execute(
                            "SELECT * FROM native_learning_objects WHERE owner=? AND workspace_id=? AND id=?",
                            (owner, wid, target_id),
                        ).fetchone()
                        if previous is None or previous["kind"] != kind:
                            raise KeyError(target_id)
                        expected = arguments.get("expected_revision", arguments.get("revision"))
                        if expected != previous["revision"]:
                            raise NativeConflict("学习对象已有新版本，请重新加载后编辑")
                        key = target_id
                        revision = previous["revision"] + 1
                        data = json.loads(previous["payload"])
                        if kind == "plan" and "tasks" in value and not arguments.get("replace_all"):
                            tasks = list(data.get("tasks", []))
                            for patch in value["tasks"]:
                                matched = next(
                                    (
                                        index
                                        for index, task in enumerate(tasks)
                                        if patch.get("id") and task.get("id") == patch["id"]
                                    ),
                                    None,
                                )
                                if matched is None:
                                    tasks.append(
                                        {
                                            **patch,
                                            "id": patch.get("id") or f"{key}.task.{len(tasks)}",
                                        }
                                    )
                                else:
                                    tasks[matched] = {**tasks[matched], **patch}
                            value = {**value, "tasks": tasks}
                        data.update(value)
                        value = data
                        db.execute(
                            "UPDATE native_learning_objects SET revision=?,payload=? WHERE owner=? AND id=?",
                            (revision, json.dumps(value, ensure_ascii=False), owner, key),
                        )
                    else:
                        if kind == "plan":
                            value = {
                                **value,
                                "tasks": [
                                    {**task, "id": task.get("id") or f"{key}.task.{index}"}
                                    for index, task in enumerate(value.get("tasks", []))
                                ],
                            }
                        db.execute(
                            "INSERT INTO native_learning_objects VALUES(?,?,?,?,?,?,?,?)",
                            (
                                key,
                                owner,
                                wid,
                                rid,
                                kind,
                                revision,
                                json.dumps(value, ensure_ascii=False),
                                now,
                            ),
                        )
                    objects.append(
                        {
                            "id": key,
                            "revision": revision,
                            "kind": kind,
                            "course_id": wid,
                            "course_resource_id": rid,
                            "data": value,
                        }
                    )
                receipt = {
                    "state": "completed",
                    "operation_id": operation_id,
                    "objects": objects,
                    "course_id": wid,
                }
            else:
                raise NativeConflict("不支持此云端操作")
            db.execute(
                "INSERT INTO native_assistant_operations VALUES(?,?,?,?,?)",
                (owner, operation_id, digest, json.dumps(receipt, ensure_ascii=False), now),
            )
            return receipt

    def deletion_impact(self, owner: str, wid: str) -> dict[str, Any]:
        self.workspace(owner, wid)
        with self.connect() as db:
            return self._deletion_impact(db, owner, wid)

    def _studio_namespace(self, db: Connection) -> str | None:
        if db.postgres:
            return ""
        path = self.path.with_name("community.sqlite3")
        if not path.exists():
            return None
        aliases = {r[1] for r in db.execute("PRAGMA database_list")}
        if "assistant_studio" not in aliases:
            db.execute("ATTACH DATABASE ? AS assistant_studio", (str(path),))
        if not db.execute(
            "SELECT 1 FROM assistant_studio.sqlite_master WHERE type='table' AND name='studio_notes'"
        ).fetchone():
            return None
        return "assistant_studio."

    def _assessment_namespace(self, db: Connection) -> str | None:
        if db.postgres:
            return ""
        path = self.path.with_name("assessments.sqlite3")
        if not path.exists():
            return None
        aliases = {r[1] for r in db.execute("PRAGMA database_list")}
        if "assistant_assessments" not in aliases:
            db.execute("ATTACH DATABASE ? AS assistant_assessments", (str(path),))
        return "assistant_assessments."

    def _deletion_impact(self, db: Connection, owner: str, wid: str) -> dict[str, Any]:
        import hashlib

        workspace = db.execute(
            "SELECT * FROM native_workspaces WHERE owner=? AND id=?", (owner, wid)
        ).fetchone()
        resources = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM native_resources WHERE workspace_id=? ORDER BY id", (wid,)
            )
        ]
        objects = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM native_learning_objects WHERE owner=? AND workspace_id=? ORDER BY id",
                (owner, wid),
            )
        ]
        learning = []
        for resource in resources:
            learning.extend(
                [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM native_catalog_practice WHERE resource_id=? ORDER BY event_id",
                        (resource["id"],),
                    )
                ]
            )
            learning.extend(
                [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM native_progress WHERE resource_id=?", (resource["id"],)
                    )
                ]
            )
        assessment_scope = self._assessment_namespace(db)
        session_ids = [r["session_id"] for r in resources if r["session_id"]]
        bundles = []
        generated_cards = set()
        plans = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM native_daily_plans WHERE workspace_id=? ORDER BY day", (wid,)
            )
        ]
        planned_tasks = set()
        for plan in plans:
            for index, task in enumerate(json.loads(plan["payload"]).get("tasks", [])):
                planned_tasks.add(task.get("task_id") or task.get("id") or f"{plan['day']}:{index}")
        learning.extend(
            [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM native_task_completions WHERE workspace_id=? ORDER BY event_id",
                    (wid,),
                )
            ]
        )
        if assessment_scope is not None:
            for sid in session_ids:
                learning.extend(
                    [
                        dict(r)
                        for r in db.execute(
                            f"SELECT * FROM {assessment_scope}learning_events WHERE session_id=? ORDER BY event_id",
                            (sid,),
                        )
                    ]
                )
                bundles.extend(
                    [
                        dict(r)
                        for r in db.execute(
                            f"SELECT * FROM {assessment_scope}course_bundles WHERE session_id=? ORDER BY course_id",
                            (sid,),
                        )
                    ]
                )
            for bundle in bundles:
                for index, card in enumerate(
                    json.loads(bundle["bundle_json"]).get("flashcards", [])
                ):
                    generated_cards.add(
                        card.get("card_id") or card.get("id") or f"{bundle['course_id']}:{index}"
                    )
        studio_scope = self._studio_namespace(db)
        owned_notes = []
        unassigned_notes = 0
        if studio_scope is not None:
            resource_ids = {r["id"] for r in resources}
            for row in db.execute(
                f"SELECT * FROM {studio_scope}studio_notes WHERE owner=? ORDER BY id", (owner,)
            ):
                data = json.loads(row["data"])
                if data.get("course_resource_id") in resource_ids:
                    owned_notes.append(dict(row))
                elif not data.get("course_resource_id"):
                    unassigned_notes += 1
        counts = {
            "notes": len(owned_notes) + sum(r["kind"] == "note" for r in objects),
            "flashcards": len(generated_cards) + sum(r["kind"] == "flashcard" for r in objects),
            "review_tasks": len(planned_tasks)
            + sum(r["kind"] == "review_task" for r in objects)
            + sum(
                len(json.loads(r["payload"]).get("tasks", []))
                for r in objects
                if r["kind"] == "plan"
            ),
            "learning_records": len(learning),
        }
        version = hashlib.sha256(
            json.dumps(
                [dict(workspace), resources, objects, learning, owned_notes, bundles, plans],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return {
            "course_id": wid,
            "name": workspace["name"],
            "course_resource_ids": [r["id"] for r in resources],
            "counts": counts,
            "version": version,
            "scope": "cloud",
            "local_counts_required": True,
            "unassigned_legacy_notes_preserved": unassigned_notes,
        }

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
                    "INSERT INTO native_catalog_practice(resource_id,event_id,payload) VALUES(?,?,?) ON CONFLICT DO NOTHING",
                    (resource_id, event_id, json.dumps(payload, ensure_ascii=False)),
                ).rowcount
            )

    def catalog_review_states(self, resource_id: str) -> dict[str, Any]:
        return {
            record["question_id"]: record["review_state"]
            for record in reversed(self.catalog_practice(resource_id))
            if "review_state" in record
        }

    def review_catalog_card(
        self,
        resource_id: str,
        event_id: str,
        card: dict[str, Any],
        rating: str,
        response_seconds: float,
    ) -> dict[str, Any]:
        # Serialize scheduling and event insertion: concurrent reviews must use
        # the last committed FSRS state, and retries must not advance it twice.
        with self.connect() as db:
            db.begin_write()
            rows = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM native_catalog_practice WHERE resource_id=? ORDER BY rowid DESC",
                    (resource_id,),
                )
            ]
            duplicate = next((r for r in rows if r["id"] == event_id), None)
            if duplicate:
                if (
                    duplicate.get("review_state") is None
                    or duplicate["question_id"] != card["card_id"]
                    or duplicate["answer"] != rating
                    or duplicate["response_seconds"] != response_seconds
                ):
                    raise NativeConflict("重复提交的内容不一致")
                return {"duplicate": True, "review_state": duplicate["review_state"]}
            previous = next(
                (
                    r["review_state"]
                    for r in rows
                    if r["question_id"] == card["card_id"] and "review_state" in r
                ),
                None,
            )
            state = schedule_review(
                FlashcardReviewState.model_validate(previous) if previous else None,
                rating,
                response_seconds=response_seconds,
            )
            assert state.updated_at is not None
            record = {
                "id": event_id,
                "question_id": card["card_id"],
                "title": card["front"],
                "chapter": card["concept"],
                "chapter_id": card["chapter_id"],
                "pages": [card["page_start"]] if card["page_start"] else [],
                "answer": rating,
                "response_seconds": response_seconds,
                "score": rating_score(rating),
                "at": state.updated_at.isoformat(),
                "review_state": state.model_dump(mode="json"),
                "kind": "flashcard_review",
            }
            db.execute(
                "INSERT INTO native_catalog_practice(resource_id,event_id,payload) VALUES(?,?,?)",
                (resource_id, event_id, json.dumps(record, ensure_ascii=False)),
            )
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
            return [
                {**dict(row), "preferences": json.loads(row["preferences"])}
                for row in db.execute(
                    "SELECT * FROM native_workspaces WHERE owner=? ORDER BY created,id", (owner,)
                )
            ]

    def save_workspace(
        self,
        owner: str,
        key: str,
        name: str,
        preferences: dict[str, Any],
        *,
        explicit_reacquire: bool = False,
    ) -> dict[str, Any]:
        with self.connect() as db:
            db.begin_write()
            if explicit_reacquire:
                db.execute(
                    "DELETE FROM native_workspace_tombstones WHERE owner=? AND workspace_id=?",
                    (owner, key),
                )
            if db.execute(
                "SELECT 1 FROM native_workspace_tombstones WHERE owner=? AND workspace_id=?",
                (owner, key),
            ).fetchone():
                raise NativeConflict("此课程已删除，旧端同步不能恢复已删除课程")
            row = db.execute("SELECT owner FROM native_workspaces WHERE id=?", (key,)).fetchone()
            if row and row[0] != owner:
                raise KeyError(key)
            now = time.time()
            db.execute(
                "INSERT INTO native_workspaces VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,preferences=excluded.preferences,updated=excluded.updated",
                (key, owner, name, json.dumps(preferences, ensure_ascii=False), now, now),
            )
        return self.workspace(owner, key)

    def save_creation_draft(self, owner: str, key: str, payload: dict[str, Any]) -> None:
        self.workspace(owner, key)
        with self.connect() as db:
            db.execute(
                "INSERT INTO native_creation_drafts VALUES(?,?,?) ON CONFLICT(workspace_id) "
                "DO UPDATE SET payload=excluded.payload,updated=excluded.updated",
                (key, json.dumps(payload, ensure_ascii=False), time.time()),
            )

    def confirm_creation_draft(self, owner: str, key: str, request_id: str) -> None:
        self.workspace(owner, key)
        with self.connect() as db:
            db.begin_write()
            row = db.execute("SELECT payload FROM native_creation_drafts WHERE workspace_id=?", (key,)).fetchone()
            saved = json.loads(row[0]) if row else None
            if saved is None or saved["request_id"] != request_id:
                raise NativeConflict("课程草稿已更新，请先同步最新修改")
            saved["confirmed"] = True
            db.execute("UPDATE native_creation_drafts SET payload=?,updated=? WHERE workspace_id=?",
                       (json.dumps(saved, ensure_ascii=False), time.time(), key))

    def creation_draft(self, owner: str, key: str) -> dict[str, Any] | None:
        self.workspace(owner, key)
        with self.connect() as db:
            row = db.execute("SELECT payload FROM native_creation_drafts WHERE workspace_id=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def delete_workspace(self, owner: str, key: str) -> dict[str, Any]:
        # Manual removal and assistant confirmation use the same transactional
        # cascade, receipt and synchronization tombstone.
        impact = self.deletion_impact(owner, key)
        return self.execute_assistant_operation(
            owner,
            "manual_remove_" + impact["version"],
            "remove_course",
            {"course_id": key, "confirmed": True, "impact_version": impact["version"]},
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
                "INSERT INTO native_resources(id,workspace_id,book_id,created) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
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

    def reconcile_book_structure(
        self,
        book_id: str,
        before: BookStructure,
        after: BookStructure,
        published_sessions: set[str],
    ) -> None:
        """Refresh generated outlines without clearing progress or completed courses."""
        expected = [(c.chapter_id, c.start_page, c.end_page) for c in before.chapters]
        encoded = after.model_dump_json()

        def matches(value: str | None) -> bool:
            if not value:
                return False
            structure = BookStructure.model_validate_json(value)
            return [
                (c.chapter_id, c.start_page, c.end_page) for c in structure.chapters
            ] == expected

        with self.connect() as db:
            db.begin_write()
            resources = db.execute(
                "SELECT id,structure_json FROM native_resources WHERE book_id=?", (book_id,)
            ).fetchall()
            for resource in resources:
                if matches(resource["structure_json"]):
                    db.execute(
                        "UPDATE native_resources SET structure_json=?,revision=revision+1 WHERE id=?",
                        (encoded, resource["id"]),
                    )
                contexts = db.execute(
                    "SELECT session_id,structure_json FROM native_session_context WHERE resource_id=?",
                    (resource["id"],),
                ).fetchall()
                for context in contexts:
                    if context["session_id"] not in published_sessions and matches(
                        context["structure_json"]
                    ):
                        db.execute(
                            "UPDATE native_session_context SET structure_json=? WHERE session_id=?",
                            (encoded, context["session_id"]),
                        )

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
