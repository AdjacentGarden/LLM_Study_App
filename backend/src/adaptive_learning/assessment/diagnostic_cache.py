"""Private chapter approvals and submission fencing, never a public item bank."""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from .models import DiagnosticItem, ResponseType

if TYPE_CHECKING:
    from .repository import SQLiteAssessmentRepository


class DiagnosticChapterCacheError(RuntimeError):
    pass


class DiagnosticChapterSubmissionBlocked(DiagnosticChapterCacheError):
    """A prior submission has no confirmed reusable result; do not submit again."""


def chapter_identity(identity: dict[str, Any]) -> tuple[str, str]:
    fields = {"scope", "chapter_id", "source_fingerprint", "protocol_fingerprint",
              "review_fingerprint", "point_ids"}
    if set(identity) != fields:
        raise ValueError("chapter approval requires its complete exact identity")
    for key in fields - {"point_ids"}:
        value = identity[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            raise ValueError("invalid chapter approval identity")
    points = identity["point_ids"]
    if (not isinstance(points, list) or not 1 <= len(points) <= 6
        or any(not isinstance(point, str) or not point.strip() or len(point) > 200 for point in points)
        or len(points) != len(set(points))):
        raise ValueError("chapter approval requires exact unique point IDs")
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest(), payload


def validate_chapter_items(identity: dict[str, Any], items: list[DiagnosticItem]) -> None:
    chapter_identity(identity)
    expected = identity["point_ids"]
    if len(items) != len(expected) or [item.knowledge_point_ids for item in items] != [[point] for point in expected]:
        raise DiagnosticChapterCacheError("chapter approval must contain every exact selected point once in order")
    if len({item.item_id for item in items}) != len(items):
        raise DiagnosticChapterCacheError("chapter approval item IDs are not unique")
    for item in items:
        if (item.chapter_id != identity["chapter_id"] or item.response_type != ResponseType.SINGLE_CHOICE
            or len(item.options) != 4 or len(set(item.options)) != 4
            or any(not option.strip() for option in item.options)
            or len(item.correct_option_ids) != 1 or item.correct_option_ids[0] not in {"0", "1", "2", "3"}
            or item.expected_answer != item.options[int(item.correct_option_ids[0])]):
            raise DiagnosticChapterCacheError("chapter approval contains an invalid private choice item")


class RepositoryDiagnosticChapterCache:
    def __init__(self, repository: SQLiteAssessmentRepository) -> None:
        self.repository = repository

    def claim(self, identity: dict[str, Any]) -> dict[str, Any]:
        return self.repository.claim_diagnostic_chapter(identity)

    def approve(self, identity: dict[str, Any], token: str, items: list[DiagnosticItem]) -> None:
        self.repository.approve_diagnostic_chapter(identity, token, items)

    def fail(self, identity: dict[str, Any], token: str, outcome: str) -> None:
        self.repository.fail_diagnostic_chapter(identity, token, outcome)
