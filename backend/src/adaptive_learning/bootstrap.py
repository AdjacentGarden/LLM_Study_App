"""Batched native startup assembly; response metadata is separate from content."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Any

from . import native_catalog as curated
from .assessment.models import InterviewSession
from .assessment.repository import SQLiteAssessmentRepository
from .community import CommunityRepository
from .ingestion.jobs import SQLiteOCRJobRepository
from .learning_preferences import LearningDesignOverrides, resolve_learning_design
from .native import NativeRepository
from .personalization.models import ChapterLearningBundle, PublicChapterLearningBundle


@lru_cache(maxsize=1)
def builtin_content_version() -> str:
    return hashlib.sha256(json.dumps(curated.snapshot(), sort_keys=True).encode()).hexdigest()


class BootstrapService:
    def __init__(self, native: NativeRepository, assessments: SQLiteAssessmentRepository,
                 jobs: SQLiteOCRJobRepository, community: CommunityRepository) -> None:
        self.native, self.assessments, self.jobs, self.community = native, assessments, jobs, community

    def assemble(self, owner: str, *, light: bool) -> dict[str, Any]:
        with self.native.connect() as db:
            workspaces = [dict(r) for r in db.execute("SELECT * FROM native_workspaces WHERE owner=? ORDER BY created,id", (owner,))]
            resource_columns = "r.id,r.book_id,r.workspace_id,r.revision,r.session_id" if light else "r.*"
            resources = [dict(r) for r in db.execute(f"SELECT {resource_columns} FROM native_resources r JOIN native_workspaces w ON w.id=r.workspace_id WHERE w.owner=? ORDER BY r.created,r.id", (owner,))]
            progress = {r["resource_id"]: {"revision": r["revision"], **json.loads(r["payload"])} for r in db.execute("SELECT p.* FROM native_progress p JOIN native_resources r ON r.id=p.resource_id JOIN native_workspaces w ON w.id=r.workspace_id WHERE w.owner=?", (owner,))}
            reviews: dict[str, dict[str, Any]] = {}
            for r in db.execute("SELECT p.* FROM native_catalog_practice p JOIN native_resources r ON r.id=p.resource_id JOIN native_workspaces w ON w.id=r.workspace_id WHERE w.owner=? ORDER BY p.rowid", (owner,)):
                event = json.loads(r["payload"])
                if "review_state" in event:
                    reviews.setdefault(r["resource_id"], {})[event["question_id"]] = event["review_state"]
            overrides = {r["workspace_id"]: dict(r) for r in db.execute("SELECT d.* FROM native_design_overrides d JOIN native_workspaces w ON w.id=d.workspace_id WHERE w.owner=?", (owner,))}
            preferences_row = db.execute("SELECT payload FROM native_learner_preferences WHERE owner=?", (owner,)).fetchone()
            selection_row = db.execute("SELECT workspace_id,resource_id FROM native_selection WHERE owner=?", (owner,)).fetchone()
        preferences = json.loads(preferences_row[0]) if preferences_row else None
        library = self.community.library(owner)
        book_ids = sorted({r["book_id"] for r in resources} | {r["book_id"] for r in library})
        stored: dict[str, Any] = {}
        structures: dict[str, Any] = {}
        if book_ids:
            placeholders = ",".join("?" for _ in book_ids)
            with self.jobs._connect() as db:
                stored = {r["book_id"]: dict(r) for r in db.execute(f"SELECT * FROM books WHERE book_id IN ({placeholders})", book_ids)}
                structures = {r["book_id"]: r["updated_at"] if light else json.loads(r["structure_json"]) for r in db.execute(f"SELECT {'book_id,updated_at' if light else '*'} FROM book_structures WHERE book_id IN ({placeholders})", book_ids)}
        sessions = sorted({r["session_id"] for r in resources if r["session_id"]})
        bundles: dict[str, dict[str, ChapterLearningBundle]] = {}
        session_states: dict[str, Any] = {}
        content_versions: dict[str, list[Any]] = {}
        if sessions:
            placeholders = ",".join("?" for _ in sessions)
            with self.assessments._connect() as db:
                columns = "session_id,course_id,chapter_id,version,profile_fingerprint,source_fingerprint,content_digest" if light else "session_id,chapter_id,bundle_json"
                for row in db.execute(f"SELECT {columns} FROM course_bundles WHERE session_id IN ({placeholders}) ORDER BY version,course_id", sessions):
                    if light:
                        content_versions.setdefault(row["session_id"], []).append(list(dict(row).values()))
                    else:
                        bundle = ChapterLearningBundle.model_validate_json(row["bundle_json"])
                        bundles.setdefault(row["session_id"], {})[row["chapter_id"]] = bundle
                for row in db.execute(f"SELECT session_id,session_json FROM interview_sessions WHERE session_id IN ({placeholders})", sessions):
                    session = InterviewSession.model_validate_json(row["session_json"])
                    session_states[row["session_id"]] = {k: v.model_dump(mode="json") for k, v in session.profile.flashcard_reviews.items()}
        assembled: dict[str, list[dict[str, Any]]] = {}
        for row in resources:
            book = stored.get(row["book_id"])
            structure = None if light else (json.loads(row["structure_json"]) if row["structure_json"] else curated.structure().model_dump(mode="json") if row["book_id"] == curated.BOOK_ID else structures.get(row["book_id"]))
            courses = [PublicChapterLearningBundle.from_private(bundle).model_dump(mode="json") for bundle in bundles.get(row["session_id"], {}).values()]
            value = progress.get(row["id"], {"revision": 0, "page": 1, "chapter_id": None, "lesson_id": None, "completed_chapters": [], "task_status": {}, "mastered_ids": []})
            builtin = row["book_id"] == curated.BOOK_ID
            version = builtin_content_version() if builtin else hashlib.sha256(json.dumps([row["revision"], structures.get(row["book_id"]), content_versions.get(row["session_id"], [])], sort_keys=True).encode()).hexdigest()
            resource = {"id": row["id"], "book_id": row["book_id"], "workspace_id": row["workspace_id"], "revision": row["revision"], "session_id": row["session_id"], "filename": book["original_name"] if book else "教材.pdf", "status": "ready" if builtin else book["status"] if book else "missing", "progress": value, "flashcard_reviews": session_states.get(row["session_id"], reviews.get(row["id"], {})), "structure": None if light else structure, "courses": [] if light else courses, "source_content": curated.public_source(row["id"], value) if builtin and not light else None}
            if light:
                resource["content_key"] = ("builtin:" if builtin else row["id"] + ":") + version
                resource["content_path"] = "/api/native/builtin-content/" + version if builtin else f'/api/workspaces/{row["workspace_id"]}/resources/{row["id"]}'
            assembled.setdefault(row["workspace_id"], []).append(resource)
        result = []
        for row in workspaces:
            payload = json.loads(row["preferences"])
            override = overrides.get(row["id"])
            revision = override["revision"] if override else 0
            values = json.loads(override["payload"]) if override else {}
            config = resolve_learning_design(preferences, payload, LearningDesignOverrides.model_validate(values), {}).model_copy(update={"course_revision": revision})
            result.append({k: v for k, v in {**row, "preferences": payload, "resources": assembled.get(row["id"], []), "learning_design": {"revision": revision, "overrides": values, "effective": config.model_dump(), "fingerprint": config.fingerprint}}.items() if k != "owner"})
        return {"workspaces": result, "selection": dict(selection_row) if selection_row else None, "contract_version": 1, "library": [{**r, "structure": None if light else structures.get(r["book_id"])} for r in library], "learner_preferences": preferences, "catalog": curated.catalog(), **({"content_mode": "metadata"} if light else {})}
