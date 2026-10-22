"""Curated existing course snapshots, independent of live OCR and generation services."""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from .ingestion.models import BookStructure, ChapterDraft

BOOK_ID = "book_biology_2"
CATALOG_ID = "community_genetics"


@lru_cache(maxsize=1)
def snapshot() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((Path(__file__).parent / "data/native_biology.json").read_text("utf-8")),
    )


def catalog() -> list[dict[str, Any]]:
    return [copy.deepcopy(snapshot()["entry"])]


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
