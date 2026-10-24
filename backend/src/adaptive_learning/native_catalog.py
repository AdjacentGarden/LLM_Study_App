"""Curated existing course snapshots, independent of live OCR and generation services."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from .community import CommunityRepository
from .ingestion.models import BookStructure, ChapterDraft
from .rag.index import RAGIndexError
from .rag.upload_index import build_upload_index

BOOK_ID = "book_biology_2"
CATALOG_ID = "community_genetics"


@lru_cache(maxsize=1)
def qa_pages() -> bytes:
    """The shipped PDF page map, never course summaries or a different biology edition."""
    corpus = json.loads((Path(__file__).parent / "data/native_biology_qa.json").read_text("utf-8"))
    pages = corpus["pages"]
    if (corpus["book_id"] != BOOK_ID
            or corpus["page_count"] != snapshot()["source"]["summary"]["page_count"]
            or [p["page_number"] for p in pages] != list(range(1, corpus["page_count"] + 1))
            or any(hashlib.sha256(p["raw_text"].encode()).hexdigest() != p["text_sha256"] for p in pages)):
        raise RAGIndexError("invalid built-in textbook page map")
    return ("\n".join(json.dumps(p, ensure_ascii=False) for p in pages) + "\n").encode()


def prepare_qa_index(data_dir: Path, encoder: Any) -> Path:
    payload = qa_pages()
    fingerprint = hashlib.sha256(payload).hexdigest()
    directory = data_dir / "catalog" / BOOK_ID / fingerprint
    source = directory / "ocr/normalized/pages.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        staging = source.with_name(f"pages-{os.urandom(8).hex()}.tmp")
        try:
            staging.write_bytes(payload)
            os.replace(staging, source)
        finally:
            staging.unlink(missing_ok=True)
    return build_upload_index(directory, encoder)


@lru_cache(maxsize=1)
def snapshot() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((Path(__file__).parent / "data/native_biology.json").read_text("utf-8")),
    )


def catalog(
    *, repository: CommunityRepository | None = None, owner: str | None = None
) -> list[dict[str, Any]]:
    """Server-owned catalog: verified shipped provenance and active public book shares.

    No enrollment count or personal progress exists in these metadata sources.
    Unknown statistics remain null, never a bundled popularity/learning claim.
    Private assets and withdrawn/non-book posts are never catalog entries.
    """
    entries: list[dict[str, Any]] = []
    try:
        saved = snapshot()
    except (OSError, ValueError):
        saved = None
    if saved is not None:
        entry = copy.deepcopy(saved["entry"])
        entry["learners"] = None
        entry["progress"] = None
        entry["flashcardCount"] = len(saved["source"]["flashcards"])
        entry["catalog_origin"] = {
            "kind": "server_curated_course",
            "book_id": BOOK_ID,
            "source_sha256": saved["provenance"]["sources_sha256"],
        }
        entries.append(entry)
    if repository is None or owner is None:
        return entries
    page = 0
    seen: set[str] = set()
    while True:
        result = repository.list_posts(owner, "book", "", page)
        for post in result["items"]:
            metadata = post.get("book")
            post_id = str(post["id"])
            if not isinstance(metadata, dict) or post_id in seen:
                continue
            seen.add(post_id)
            title = str(post["title"])
            # A book share publishes the actual source, not the publisher's
            # learning history or a newly generated course/flashcard collection.
            entries.append({
                "id": "community_post_" + post_id,
                "title": title,
                "catalogTitle": title,
                "owner": "社区公开分享",
                "cover": str(metadata.get("cover_url") or ""),
                "subject": str(metadata.get("subject") or ""),
                "grade": str(metadata.get("grade") or ""),
                "version": str(metadata.get("version") or ""),
                "volume": metadata.get("volume"),
                "learners": None,
                "progress": None,
                "flashcardCount": 0,
                "recommended": False,
                "tags": [],
                "description": str(post["description"]),
                "chapters": [],
                "catalog_origin": {"kind": "community_book_share", "post_id": post_id,
                                   "book_id": str(post["book_id"])},
            })
        if not result["has_more"]:
            return entries
        page += 1


def structure() -> BookStructure:
    source = snapshot()["source"]
    return BookStructure(
        title=source["summary"]["title"],
        summary=snapshot()["entry"]["description"],
        source_page_count=source["summary"]["page_count"],
        chapters=[
            ChapterDraft(
                chapter_id=c["chapter_id"],
                order=i + 1,
                title=c["source_title"],
                start_page=c["page_start"],
                end_page=c["page_end"],
                summary="",
                knowledge_points=[],
                source_block_ids=[],
            )
            for i, c in enumerate(source["chapters"])
        ],
    )


def public_source(resource_id: str, progress: dict[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = copy.deepcopy(snapshot()["source"])
    value["summary"]["content_mode"] = "catalog"
    for question in value["quizzes"]:
        question["answer"] = ""
        question["explanation"] = ""
    for card in value["flashcards"]:
        card["mastery"] = 0
    for task in value["plan"]["tasks"]:
        task["task_id"] = f"catalog:{resource_id}:{task['task_id']}"
        task["user_id"] = ""
        task["status"] = progress.get("task_status", {}).get(task["task_id"], "pending")
        task["weak_points"] = []
        task.pop("score", None)
    value["plan"]["user_id"] = ""
    return value
