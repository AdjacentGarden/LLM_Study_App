"""Read-only, content-addressed migration of an account's legacy private data.

New phone data is authoritative. Later exports are archive versions, never a
server-to-phone overwrite instruction. No delete/purge operation exists here.
"""
from __future__ import annotations
import base64
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from ..accounts import COOKIE
from ..database import connect, database_url


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def serial(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"encoding": "base64", "data": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, dict):
        return {str(k): serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    return value


def exists(db, name):
    if db.postgres:
        return db.execute("SELECT to_regclass(?)", (name,)).fetchone()[0] is not None
    return db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def export_snapshot(services, actor):
    root = Path(services.settings.data_dir).resolve()
    records = []
    owned_sessions = set()
    studio_ids = set()
    owned_books = set()
    sources = []
    # Every query is account-scoped directly or joined through owned parent rows.
    def read(dbfile, table, predicate, parameters, keys):
        path = root / "state" / dbfile
        if not database_url() and not path.is_file():
            return []
        with connect(path) as db:
            if not exists(db, table):
                return []
            rows = [serial(dict(row)) for row in db.execute("SELECT * FROM " + table + " WHERE " + predicate, parameters).fetchall()]
        sources.append(table)
        for row in rows:
            identity = {k: row[k] for k in keys if k in row}
            if not identity:
                raise HTTPException(500, "旧数据导出标识不完整")
            records.append({"kind": table, "id": table + ":" + sha(identity), "revision": sha(row), "payload": row, "deleted": table.endswith("tombstones") or table == "removed_books"})
        return rows

    owner_tables = {
        "native_workspaces": ("id",), "native_selection": ("owner",), "native_learner_preferences": ("owner",),
        "native_learning_objects": ("id",), "native_workspace_tombstones": ("owner", "workspace_id"),
        "native_assistant_operations": ("owner", "operation_id"),
    }
    native_file = Path(services.native_repository.path).name
    for table, keys in owner_tables.items():
        read(native_file, table, "owner=?", (actor,), keys)
    workspace_predicate = "workspace_id IN (SELECT id FROM native_workspaces WHERE owner=?)"
    resource_predicate = "resource_id IN (SELECT id FROM native_resources WHERE " + workspace_predicate + ")"
    for table, keys in {
        "native_resources": ("id",), "native_creation_drafts": ("workspace_id",),
        "native_design_overrides": ("workspace_id",), "native_daily_plans": ("workspace_id", "day"),
        "native_task_completions": ("workspace_id", "event_id"), "native_design_adjustments": ("workspace_id", "adjustment_id"),
    }.items():
        rows = read(native_file, table, workspace_predicate, (actor,), keys)
        if table == "native_resources":
            owned_books.update(row["book_id"] for row in rows)
            owned_sessions.update(row["session_id"] for row in rows if row.get("session_id"))
    for table, keys in {"native_progress": ("resource_id",), "native_mutations": ("resource_id", "mutation_id"),
                        "native_catalog_practice": ("resource_id", "event_id"), "native_session_context": ("session_id",)}.items():
        read(native_file, table, resource_predicate, (actor,), keys)
    community_file = Path(services.account_repository.repo.path).name
    for table, keys in {
        "user_profiles": ("owner",), "resources": ("id",), "library_books": ("owner", "canonical"),
        "removed_books": ("owner", "canonical"), "acquisitions": ("owner", "post_id"),
        "session_owners": ("session_id",), "upload_owners": ("book_id",),
        "studio_notes": ("id",), "studio_jobs": ("id",), "studio_note_logical_inputs": ("note_id",),
        "studio_note_request_aliases": ("owner", "request_id"),
        "studio_note_editions": ("job_id",), "reading_doubts": ("id",),
    }.items():
        rows = read(community_file, table, "owner=?", (actor,), keys)
        if table == "upload_owners":
            owned_books.update(row["book_id"] for row in rows)
        if table == "session_owners":
            owned_sessions.update(row["session_id"] for row in rows)
        if table in {"studio_notes", "studio_jobs"}:
            studio_ids.update(row["id"] for row in rows)
    with services.account_repository.repo.connect() as db:
        if exists(db, "accounts"):
            row = db.execute("SELECT owner,stage,interests,goal FROM accounts WHERE owner=?", (actor,)).fetchone()
            if row is not None:
                payload = serial(dict(row))
                records.append({"kind": "account_learning_preferences", "id": "account-preferences:" + sha({"owner": actor}),
                                "revision": sha(payload), "payload": payload, "deleted": False})
        if exists(db, "assets") and exists(db, "library_books"):
            owned_books.update(row[0] for row in db.execute("SELECT a.book_id FROM assets a JOIN library_books l ON l.canonical=a.canonical WHERE l.owner=?", (actor,)).fetchall())
    assessment_file = Path(services.assessment_repository.database_path).name
    rows = read(assessment_file, "interview_sessions", "user_id=?", (actor,), ("session_id",))
    owned_sessions.update(row["session_id"] for row in rows)
    if owned_sessions:
        placeholders = ",".join("?" for _ in owned_sessions)
        params = tuple(sorted(owned_sessions))
        scope = "session_id IN (" + placeholders + ")"
        for table, keys in {"course_bundles": ("course_id",), "learning_events": ("event_id",)}.items():
            read(assessment_file, table, scope, params, keys)
        for table, keys in {"course_jobs": ("job_id",), "course_job_requests": ("session_id", "request_id")}.items():
            read("course_jobs.sqlite3", table, scope, params, keys)
        for table, keys in {"teaching_course_repairs": ("run_key",), "teaching_course_epochs": ("scope", "chapter_id")}.items():
            read("course_jobs.sqlite3", table, "scope IN (" + placeholders + ")", params, keys)
        for table, keys in {"teaching_course_units": ("run_key", "unit_id"), "teaching_course_proofs": ("run_key", "proof_key"),
                            "teaching_course_submissions": ("run_key", "request_key")}.items():
            read("course_jobs.sqlite3", table, "run_key IN (SELECT run_key FROM teaching_course_repairs WHERE scope IN (" + placeholders + "))", params, keys)
    ledger_file = Path(services.assistant_ledger.path).name
    for table, keys in {
        "assistant_history_initializations": ("owner",),
        "assistant_conversations": ("owner", "session_id"), "assistant_conversation_events": ("owner", "session_id", "event_id"),
        "assistant_conversation_runs": ("owner", "run_id"), "assistant_runs": ("owner", "run_id"),
        "assistant_steps": ("owner", "run_id", "step_id"),
    }.items():
        predicate = "owner=?"
        if table in {"assistant_runs", "assistant_steps"}:
            predicate += " AND run_id NOT LIKE 'private:%' AND run_id NOT LIKE 'private-model:%'"
        read(ledger_file, table, predicate, (actor,), keys)
    files = []
    paths = {}
    def add_file(path, *, kind, book_id=None):
        resolved = Path(path).resolve()
        if not resolved.is_file() or not resolved.is_relative_to(root):
            return False
        relative = str(resolved.relative_to(root))
        file_id = hashlib.sha256(relative.encode()).hexdigest()
        if file_id in paths:
            return True
        checksum = hashlib.sha256()
        with resolved.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                checksum.update(block)
        paths[file_id] = resolved
        files.append({"id": file_id, "relative_path": relative, "download_url": "/api/private/export/files/" + file_id,
                      "sha256": checksum.hexdigest(), "bytes": resolved.stat().st_size, "kind": kind, "book_id": book_id})
        return True
    def missing_file(book_id, kind, reason, identifier=""):
        payload = {"book_id": book_id, "kind": kind, "reason": reason, "identifier": identifier}
        records.append({"kind": "migration_missing_file", "id": "missing-file:" + sha({"book": book_id, "kind": kind, "id": identifier}),
                        "revision": sha(payload), "payload": payload, "deleted": False})
    # Media may be references in legacy JSON, or inline image bodies in exported
    # events. Inline bodies are included verbatim in their account-scoped record.
    media = root / "learning-studio"
    if media.is_dir():
        voice_scope = hashlib.sha256(actor.encode()).hexdigest()[:24]
        for path in media.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(media)
            # Actual Studio writers use exact UUID media stems, owner-hashed
            # voice scope, or job-id + random UUID trace filenames.
            owned = len(relative.parts) == 1 and path.suffix in {".jpg", ".mp4"} and path.stem in studio_ids
            owned = owned or (len(relative.parts) == 3 and relative.parts[:2] == ("private-voice-notes", voice_scope)
                              and path.suffix == ".audio" and path.stem in studio_ids)
            if len(relative.parts) == 2 and relative.parts[0] in {"private-media-traces", "private-note-traces"}:
                match = re.fullmatch(r"(.+)-[a-f0-9]{32}\.json", path.name)
                owned = owned or (match is not None and match.group(1) in studio_ids)
            if owned:
                if not add_file(path, kind="private_media"):
                    missing_file("", "private_media", "unsafe_file_reference", str(relative))
    for book_id in sorted(owned_books):
        stored = services.job_repository.get_book(book_id)
        if stored is None or not add_file(stored.file_path, kind="original_pdf", book_id=book_id):
            missing_file(book_id, "original_pdf", "source_file_unavailable_or_unsafe")
        # Only normalized OCR text/manifests and independent crop paths named in
        # the manifest are allowed. Never export saved OCR full-page bitmaps.
        normalized = root / "books" / book_id / "ocr" / "normalized"
        for name in ("pages.jsonl", "full_text.md", "quality_report.json", "quality_report.md", "figure_manifest.json", "SHA256SUMS"):
            add_file(normalized / name, kind="ocr_text", book_id=book_id)
        manifest = normalized / "figure_manifest.json"
        if manifest.is_file():
            payload = json.loads(manifest.read_text())
            for item in payload.get("figures", []):
                path = (normalized / item.get("file", "")).resolve()
                if not path.is_relative_to((normalized / "figures").resolve()) or not add_file(path, kind="independent_figure", book_id=book_id):
                    missing_file(book_id, "independent_figure", "missing_or_unsafe_figure_reference", str(item.get("asset_id") or item.get("file")))
    records.sort(key=lambda row: (row["kind"], row["id"]))
    files.sort(key=lambda row: row["id"])
    revision = sha({"records": [(r["id"], r["revision"]) for r in records], "files": [(f["id"], f["sha256"]) for f in files]})
    return records, files, paths, revision, sorted(set(sources))


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    record_count: int = Field(ge=0)
    file_count: int = Field(ge=0)


def private_export_router(services):
    router = APIRouter(prefix="/api/private/export", tags=["private-export"])
    with services.native_repository.connect() as db:
        db.executescript("CREATE TABLE IF NOT EXISTS private_export_checkpoints (owner TEXT PRIMARY KEY,snapshot_revision TEXT NOT NULL,digest TEXT NOT NULL,record_count INTEGER NOT NULL,file_count INTEGER NOT NULL,updated REAL NOT NULL)")
    def owner(request: Request, response: Response):
        actor = services.account_repository.session_owner(request.cookies.get(COOKIE))
        if actor is None:
            raise HTTPException(401, "请先登录学习账号")
        response.headers["Cache-Control"] = "private, no-store"
        return str(actor)

    @router.get("")
    def export(cursor: str | None = None, limit: int = Query(default=100, ge=1, le=500), actor: str = Depends(owner)):
        records, files, _, revision, sources = export_snapshot(services, actor)
        offset = 0
        if cursor:
            try:
                previous, index = cursor.split(":", 1)
                offset = int(index)
                if previous != revision:
                    raise HTTPException(409, "旧版数据已变化，请重新扫描并归档增量")
                if offset < 0 or offset > len(records):
                    raise ValueError()
            except ValueError as error:
                raise HTTPException(422, "导出游标无效") from error
        end = min(len(records), offset + limit)
        return {"schema_version": 1, "cursor": cursor, "next_cursor": revision + ":" + str(end) if end < len(records) else None,
                "records": records[offset:end], "files": files if offset == 0 else [], "snapshot_revision": revision,
                "record_count": len(records), "file_count": len(files), "sources": sources,
                "complete": not any(row["kind"] == "migration_missing_file" for row in records),
                "legacy_writes_active": True, "merge_policy": "local_authoritative_legacy_changes_archive_only"}

    @router.get("/files/{file_id}")
    def download(file_id: str, actor: str = Depends(owner)):
        _, _, paths, _, _ = export_snapshot(services, actor)
        path = paths.get(file_id)
        if path is None:
            raise HTTPException(404, "导出文件不存在或不属于当前账号")
        return FileResponse(path, headers={"Cache-Control": "private, no-store"})

    @router.post("/checkpoint")
    def checkpoint(body: Checkpoint, actor: str = Depends(owner)):
        records, files, _, revision, _ = export_snapshot(services, actor)
        expected = sha({"records": [(r["id"], r["revision"]) for r in records], "files": [(f["id"], f["sha256"]) for f in files]})
        if body.snapshot_revision != revision or body.digest != expected or body.record_count != len(records) or body.file_count != len(files):
            raise HTTPException(409, "导出校验不一致，请重新扫描")
        with services.native_repository.connect() as db:
            db.execute("INSERT INTO private_export_checkpoints VALUES(?,?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET snapshot_revision=excluded.snapshot_revision,digest=excluded.digest,record_count=excluded.record_count,file_count=excluded.file_count,updated=excluded.updated",
                       (actor, revision, body.digest, body.record_count, body.file_count, time.time()))
        return {"verified": True, "legacy_writes_active": True, "purged": False}
    return router
