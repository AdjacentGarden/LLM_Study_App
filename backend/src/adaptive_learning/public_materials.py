"""Versioned shared textbook materials; never personal courses or learner data."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any

from .assessment.item_generation import diagnostic_bank_key
from .ingestion.figure_layout import file_digest

_LOCK = threading.RLock()
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GROUP = re.compile(r"^[A-Za-z0-9_-]{8,100}$")


class MaterialsPending(ValueError):
    pass


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def public_group_contains_book(community: Any, book_id: str) -> bool:
    """Read-only, revocable cover permission; never initializes or publishes groups."""
    if not hasattr(community, "connect"):
        return False
    with community.connect() as db:
        if db.postgres:
            exists = db.execute("SELECT to_regclass('public_course_groups')").fetchone()[0]
        else:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='public_course_groups'").fetchone()
        if not exists:
            return False
        rows = list(db.execute("""SELECT g.book_ids FROM public_course_groups g
            LEFT JOIN posts p ON p.id=g.legacy_post
            WHERE g.active=1 AND g.status='ready'
            AND (g.legacy_post IS NULL OR (p.active=1 AND p.kind='book'))"""))
    for row in rows:
        try:
            ids = json.loads(row["book_ids"])
            if isinstance(ids, list) and book_id in ids:
                return True
        except (TypeError, ValueError):
            continue
    return False


class PublicMaterials:
    def __init__(self, services: Any) -> None:
        self.services = services
        self.community = services.account_repository.repo
        self.root = Path(services.settings.data_dir) / "public-materials"
        self.blobs = self.root / "blobs"
        with self.community.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS public_course_groups (
                    id TEXT PRIMARY KEY,owner TEXT NOT NULL,title TEXT NOT NULL,
                    description TEXT NOT NULL,book_ids TEXT NOT NULL,status TEXT NOT NULL,
                    manifest TEXT,error TEXT,active INTEGER NOT NULL DEFAULT 1,
                    legacy_post TEXT,created REAL NOT NULL,updated REAL NOT NULL);
            """)

    def _row(self, group_id: str) -> dict[str, Any]:
        if not _GROUP.fullmatch(group_id):
            raise KeyError("group")
        with self.community.connect() as db:
            row = db.execute("SELECT * FROM public_course_groups WHERE id=? AND active=1", (group_id,)).fetchone()
        if row is None:
            raise KeyError("group")
        value = dict(row)
        if value["legacy_post"]:
            with self.community.connect() as db:
                live = db.execute("SELECT 1 FROM posts WHERE id=? AND active=1 AND kind='book'", (value["legacy_post"],)).fetchone()
            if live is None:
                raise KeyError("group")
        return value

    @staticmethod
    def describe(row: dict[str, Any]) -> dict[str, Any]:
        manifest = json.loads(row["manifest"]) if row.get("manifest") else None
        return {"id": row["id"], "title": row["title"], "description": row["description"],
                "book_ids": json.loads(row["book_ids"]), "status": row["status"],
                "version": manifest["version"] if manifest else None,
                "manifest_url": f'/api/public/library/course-groups/{row["id"]}/manifest',
                "cover_url": f'/api/books/{json.loads(row["book_ids"])[0]}/cover' if json.loads(row["book_ids"]) else None}

    def register(self, actor: str, *, title: str, book_ids: list[str], description: str = "",
                 group_id: str | None = None) -> dict[str, Any]:
        if not book_ids or len(book_ids) != len(set(book_ids)) or len(book_ids) > 50:
            raise ValueError("invalid_book_list")
        for book in book_ids:
            if self.services.job_repository.get_book(book) is None or not (
                    self.community.owns_upload(actor, book) or self.community.owns(actor, book)):
                raise PermissionError("book_unavailable")
        group_id = group_id or "public_group_" + hashlib.sha256(json_bytes([actor, book_ids])).hexdigest()[:32]
        if not _GROUP.fullmatch(group_id):
            raise ValueError("invalid_group_id")
        with _LOCK, self.community.connect() as db:
            db.begin_write()
            old = db.execute("SELECT owner,book_ids,title,description,active FROM public_course_groups WHERE id=?", (group_id,)).fetchone()
            if old is not None and old["owner"] != actor:
                raise PermissionError("group_owned_elsewhere")
            unchanged = old is not None and old["active"] == 1 and (old["book_ids"], old["title"], old["description"]) == (
                json.dumps(book_ids), title, description)
            if not unchanged:
                now = time.time()
                db.execute("""INSERT INTO public_course_groups
                    (id,owner,title,description,book_ids,status,manifest,error,active,legacy_post,created,updated)
                    VALUES(?,?,?,?,?,'preparing',NULL,NULL,1,NULL,?,?)
                    ON CONFLICT(id) DO UPDATE SET title=excluded.title,description=excluded.description,
                    book_ids=excluded.book_ids,status='preparing',manifest=NULL,error=NULL,active=1,legacy_post=NULL,updated=excluded.updated""",
                    (group_id, actor, title, description, json.dumps(book_ids), now, now))
        # Preparation owns shared diagnostics; this never writes personal preferences.
        queue = getattr(self.services, "preparation_queue", None)
        if queue is not None:
            for book in book_ids:
                stored = self.services.job_repository.get_book(book)
                if stored.status in {"ocr_ready", "structured", "ready"}:
                    queue.enqueue(book)
        self.refresh(group_id)
        return self.describe(self._row(group_id))

    def discover_legacy(self) -> None:
        """Only existing explicit public shares; never discover unshared uploads."""
        with self.community.connect() as db:
            rows = list(db.execute("SELECT id,owner,book_id,title,description,created FROM posts WHERE kind='book' AND active=1"))
            for row in rows:
                group_id = "community_post_" + str(row["id"])
                db.execute("""INSERT INTO public_course_groups
                    (id,owner,title,description,book_ids,status,active,legacy_post,created,updated)
                    VALUES(?,?,?,?,?,'preparing',1,?,?,?) ON CONFLICT(id) DO NOTHING""",
                    (group_id, row["owner"], row["title"], row["description"], json.dumps([row["book_id"]]),
                     row["id"], row["created"], time.time()))
        # Configured public source IDs are an independent, explicit publication list.
        for book in getattr(self.services.settings, "published_book_ids", ()):
            structure = self.services.job_repository.get_structure(book)
            stored = self.services.job_repository.get_book(book)
            if structure is None or stored is None:
                continue
            group_id = "published_" + hashlib.sha256(book.encode()).hexdigest()[:32]
            with self.community.connect() as db:
                db.execute("""INSERT INTO public_course_groups
                    (id,owner,title,description,book_ids,status,active,created,updated)
                    VALUES(?,'server-public',?,?,?,'preparing',1,?,?) ON CONFLICT(id) DO NOTHING""",
                    (group_id, structure.title, structure.summary[:500], json.dumps([book]), time.time(), time.time()))

    def list_ready(self) -> list[dict[str, Any]]:
        self.discover_legacy()
        with self.community.connect() as db:
            ids = [str(row["id"]) for row in db.execute(
                "SELECT id FROM public_course_groups WHERE active=1 ORDER BY created,id")]
        result = []
        for group in ids:
            try:
                self.refresh(group)
                row = self._row(group)
                if row["status"] == "ready":
                    result.append(self.describe(row))
            except KeyError:
                continue
        return result

    def refresh_for_book(self, book_id: str) -> None:
        with self.community.connect() as db:
            rows = list(db.execute("SELECT id,book_ids FROM public_course_groups WHERE active=1 AND status!='ready'"))
        for row in rows:
            if book_id in json.loads(row["book_ids"]):
                self.refresh(str(row["id"]))

    def refresh(self, group_id: str) -> None:
        with _LOCK:
            row = self._row(group_id)
            if row["status"] == "ready":
                return
            try:
                books = [self._book(group_id, book) for book in json.loads(row["book_ids"])]
                payload: dict[str, Any] = {"schema_version": 1, "group_id": group_id, "title": row["title"],
                                          "description": row["description"], "books": books}
                payload["version"] = hashlib.sha256(json_bytes(payload)).hexdigest()
                encoded = json_bytes(payload).decode()
                with self.community.connect() as db:
                    db.execute("UPDATE public_course_groups SET status='ready',manifest=?,error=NULL,updated=? WHERE id=?",
                               (encoded, time.time(), group_id))
            except (MaterialsPending, OSError, ValueError, TypeError, KeyError) as error:
                # Keep full failures internal; API status has bounded reason codes only.
                reason = str(error) if isinstance(error, MaterialsPending) else "material_artifact_invalid"
                with self.community.connect() as db:
                    db.execute("UPDATE public_course_groups SET status='preparing',error=?,updated=? WHERE id=?",
                               (reason[:200], time.time(), group_id))

    def manifest(self, group_id: str) -> dict[str, Any]:
        self.refresh(group_id)
        row = self._row(group_id)
        if row["status"] != "ready" or not row["manifest"]:
            raise MaterialsPending(row.get("error") or "materials_preparing")
        return json.loads(row["manifest"])

    def status(self, group_id: str, actor: str) -> dict[str, Any]:
        row = self._row(group_id)
        if row["owner"] != actor and row["status"] != "ready":
            raise KeyError("group")
        self.refresh(group_id)
        row = self._row(group_id)
        result = self.describe(row)
        if row["owner"] == actor and row.get("error"):
            result["pending_reason"] = row["error"]
        return result

    def withdraw(self, group_id: str, actor: str) -> None:
        row = self._row(group_id)
        if row["owner"] != actor:
            raise PermissionError("group_owned_elsewhere")
        with self.community.connect() as db:
            db.execute("UPDATE public_course_groups SET active=0,status='withdrawn',updated=? WHERE id=?", (time.time(), group_id))

    def _store_bytes(self, group_id: str, kind: str, data: bytes, media_type: str) -> dict[str, Any]:
        digest = hashlib.sha256(data).hexdigest()
        self.blobs.mkdir(parents=True, exist_ok=True)
        path = self.blobs / digest
        if not path.is_file() or file_digest(path) != digest:
            with tempfile.NamedTemporaryFile(dir=self.blobs, delete=False) as stream:
                stream.write(data)
                temporary = Path(stream.name)
            try:
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return {"asset_id": digest, "kind": kind, "sha256": digest, "bytes": len(data), "media_type": media_type,
                "download_url": f"/api/public/library/course-groups/{group_id}/assets/{digest}"}

    def _store_file(self, group_id: str, kind: str, source: Path, media_type: str) -> dict[str, Any]:
        # PDFs are copied in bounded chunks; a library of books is never one RAM blob.
        self.blobs.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as incoming, tempfile.NamedTemporaryFile(dir=self.blobs, delete=False) as outgoing:
            temporary = Path(outgoing.name)
            for block in iter(lambda: incoming.read(1024 * 1024), b""):
                outgoing.write(block)
                digest.update(block)
                size += len(block)
        checksum = digest.hexdigest()
        target = self.blobs / checksum
        try:
            if target.is_symlink() or not target.is_file() or file_digest(target) != checksum:
                os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return {"asset_id": checksum, "kind": kind, "sha256": checksum, "bytes": size, "media_type": media_type,
                "download_url": f"/api/public/library/course-groups/{group_id}/assets/{checksum}"}

    def _book(self, group_id: str, book_id: str) -> dict[str, Any]:
        stored = self.services.job_repository.get_book(book_id)
        structure = self.services.job_repository.get_structure(book_id)
        if stored is None or structure is None:
            raise MaterialsPending("ocr_or_structure_preparing")
        source = stored.file_path
        if not source.is_file():
            raise MaterialsPending("original_pdf_unavailable")
        with source.open("rb") as stream:
            if not stream.read(1024).lstrip().startswith(b"%PDF-"):
                raise MaterialsPending("original_pdf_unavailable")
        expected_source = self.services.job_repository.source_fingerprint(book_id)
        if expected_source is None or file_digest(source) != expected_source:
            raise MaterialsPending("original_source_changed")
        normalized = source.parent / "ocr" / "normalized"
        quality_report = normalized / "quality_report.json"
        if quality_report.is_file():
            recorded_source = json.loads(quality_report.read_text()).get("source_sha256")
            if recorded_source is not None and recorded_source != expected_source:
                raise MaterialsPending("ocr_source_version_mismatch")
        path = normalized / "pages.jsonl"
        if not path.is_file():
            raise MaterialsPending("ocr_text_unavailable")
        if path.stat().st_size > 128 * 1024 * 1024:
            raise MaterialsPending("ocr_artifact_exceeds_limit")
        pages = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            page = json.loads(line)
            number = page["page_number"]
            if type(number) is not int or not 1 <= number <= structure.source_page_count:
                raise ValueError("invalid_ocr_page")
            blocks = []
            for index, block in enumerate(page.get("blocks", [])):
                if not isinstance(block, dict):
                    continue
                item = {"block_id": str(block.get("block_id") or f"page_{number}_block_{index}"),
                        "text": str(block.get("text") or ""), "block_type": str(block.get("block_type") or block.get("type") or "text")}
                bbox = block.get("bbox")
                if isinstance(bbox, list) and len(bbox) == 4:
                    item["bbox"] = bbox
                blocks.append(item)
            text = next((page[k] for k in ("cleaned_text", "raw_text", "text") if isinstance(page.get(k), str)),
                        "\n".join(block["text"] for block in blocks))
            pages.append({"page_number": number, "raw_text": text, "cleaned_text": text, "blocks": blocks})
        if [p["page_number"] for p in pages] != list(range(1, structure.source_page_count+1)):
            raise MaterialsPending("ocr_page_coverage_incomplete")
        bank = self.services.assessment_repository.get_bank(diagnostic_bank_key(book_id),
                expected_fingerprint=self.services.diagnostic_fingerprint(structure))
        if not bank:
            raise MaterialsPending("shared_diagnostics_preparing")
        public_structure = {"title": structure.title, "summary": structure.summary,
                            "source_page_count": structure.source_page_count, "chapters": []}
        for chapter in structure.chapters:
            value = chapter.model_dump(mode="json")
            public_structure["chapters"].append({key: value[key] for key in (
                "chapter_id", "order", "title", "start_page", "end_page", "summary", "knowledge_points", "source_block_ids",
                "evidence", "knowledge_point_evidence") if key in value})
        diagnostics = [{key: value for key, value in item.model_dump(mode="json").items()
                        if key in {"item_id", "chapter_id", "knowledge_point_ids", "knowledge_point_labels", "prompt",
                                   "response_type", "options", "estimated_seconds", "source_block_ids", "source_pages"}} for item in bank]
        grading = [{"item_id": item.item_id, "correct_option_ids": item.correct_option_ids,
                    "expected_answer": item.expected_answer, "rubric": item.rubric} for item in bank]
        files = [self._store_file(group_id, "pdf", source, "application/pdf"),
                 self._store_bytes(group_id, "ocr_pages", b"".join(json_bytes(p)+b"\n" for p in pages), "application/x-ndjson"),
                 self._store_bytes(group_id, "structure", json_bytes(public_structure), "application/json"),
                 self._store_bytes(group_id, "diagnostics", json_bytes({"items": diagnostics}), "application/json"),
                 self._store_bytes(group_id, "grading", json_bytes({"items": grading}), "application/json")]
        figures = []
        figure_status: dict[str, Any] = {"status": "not_extracted", "reason": "historical_manifest_absent"}
        manifest_path = normalized / "figure_manifest.json"
        if manifest_path.is_file():
            try:
                candidate_status = json.loads(manifest_path.read_text())
                if not isinstance(candidate_status, dict) or not isinstance(candidate_status.get("figures", []), list):
                    raise ValueError("invalid_figure_manifest")
                figure_status = candidate_status
            except (OSError, ValueError, TypeError):
                figure_status = {"status": "unavailable", "reason": "figure_manifest_invalid"}
            if figure_status.get("source_sha256") != files[0]["sha256"] and figure_status.get("reason") != "figure_manifest_invalid":
                figure_status = {"status": "unavailable", "reason": "figure_source_mismatch"}
            for image in figure_status.get("figures", []):
                try:
                    image_path = (normalized / image["file"]).resolve(strict=True)
                    if not image_path.is_relative_to((normalized / "figures").resolve()) or not image_path.is_file():
                        continue
                    if image.get("label") not in {"image", "chart"}:
                        continue
                    if file_digest(image_path) != image.get("sha256"):
                        continue
                    asset = self._store_file(group_id, "figure", image_path, str(image["media_type"]))
                    figures.append({"asset_id": image["asset_id"], "file_asset_id": asset["asset_id"], "page_number": image["page_number"],
                                    "bbox": image.get("bbox"), "caption": image.get("caption", ""),
                                    **{key: asset[key] for key in ("sha256", "bytes", "media_type", "download_url")}})
                except (OSError, ValueError, KeyError, TypeError):
                    continue  # Per-image failure never blocks shared material publication.
        # Status remains diagnostic data; UI need not show an extra warning.
        public_figures = {"schema_version": 1, "figures": figures, "extraction": {
            key: figure_status[key] for key in ("status", "reason", "model_revision", "model_sha256", "filter_version", "filter_sha256", "skipped_candidates")
            if key in figure_status}}
        files.append(self._store_bytes(group_id, "figure_manifest", json_bytes(public_figures), "application/json"))
        if files[0]["sha256"] != expected_source or file_digest(source) != expected_source:
            raise MaterialsPending("original_source_changed_during_publication")
        version = hashlib.sha256(json_bytes([public_structure, files, figures])).hexdigest()
        return {"book_id": book_id, "title": structure.title, "content_version": version,
                "cover_url": f"/api/books/{book_id}/cover",
                "structure": public_structure, "files": files, "figures": figures}

    def asset(self, group_id: str, digest: str) -> tuple[Path, dict[str, Any]]:
        if not _DIGEST.fullmatch(digest):
            raise KeyError("asset")
        manifest = self.manifest(group_id)
        files = [file for book in manifest["books"] for file in [*book["files"], *book["figures"]]]
        allowed = next((file for file in files if file["sha256"] == digest), None)
        if allowed is None:
            raise KeyError("asset")
        path = self.blobs / digest
        if path.is_symlink() or not path.is_file() or path.stat().st_size != allowed["bytes"] or file_digest(path) != digest:
            raise KeyError("asset")
        return path, allowed
