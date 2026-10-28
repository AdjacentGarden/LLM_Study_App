"""One bounded pass of durable single-item work; the preparation queue repeats forever.

No loop holds a model slot while waiting for another repair. Exact approvals and
submission tokens use the existing cache, while feedback and delays survive restart.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

import httpx
from openai import APIStatusError

from ..llm.book_parallel import ordered_book_map
from ..llm.client import (
    LLMCooldownError,
    LLMError,
    LLMNotSubmittedError,
    LLMOutputLimitError,
    LLMRefusalError,
    no_ambiguous_model_retries,
)
from .diagnostic_cache import (
    DiagnosticChapterSubmissionBlocked,
    DiagnosticRepairPending,
    validate_chapter_items,
)
from .models import DiagnosticItem

if TYPE_CHECKING:
    from ..ingestion.models import BookStructure, ChapterDraft, SourceQuote
    from .item_generation import DiagnosticItemGenerator

stop_requested: ContextVar[Callable[[], bool]] = ContextVar("diagnostic_stop_requested", default=lambda: False)


def _service_retry_delay(error: BaseException) -> float | None:
    """Only a received rate/service rejection, never an ambiguous timeout."""
    seen: set[int] = set()
    while id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, (httpx.HTTPStatusError, APIStatusError)):
            status = error.response.status_code
            if status == 429 or status == 503:
                try:
                    return max(5, float(error.response.headers.get("Retry-After", "30")))
                except ValueError:
                    return 30
            return None
        if error.__cause__ is None:
            return None
        error = error.__cause__
    return None


def _identity(generator: DiagnosticItemGenerator, structure: BookStructure, chapter: ChapterDraft,
              points: list[tuple[str, str, list[SourceQuote]]], index: int, scope: str) -> dict[str, Any]:
    from .item_generation import (
        _COGNITIVE_TARGETS,
        _RELAXED_REVIEW_INSTRUCTIONS,
        _TARGETED_REPAIR_INSTRUCTIONS,
    )
    identity = generator._chapter_identity(structure, chapter, points, scope)
    identity["point_ids"] = [points[index][0]]
    identity["protocol_fingerprint"] = hashlib.sha256(json.dumps({
        "base": identity["protocol_fingerprint"], "policy": "single-item-continuous-v1",
        "writer": _TARGETED_REPAIR_INSTRUCTIONS,
        "target": _COGNITIVE_TARGETS[index % len(_COGNITIVE_TARGETS)],
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    identity["review_fingerprint"] = hashlib.sha256(
        (identity["review_fingerprint"] + _RELAXED_REVIEW_INSTRUCTIONS).encode()).hexdigest()
    return identity


def advance_diagnostic_items(generator: DiagnosticItemGenerator, structure: BookStructure,
                             cache: Any, scope: str) -> list[DiagnosticItem]:
    from .item_generation import _COGNITIVE_TARGETS, DiagnosticGenerationError, ItemDraft
    if not generator.verify_items or not scope.strip():
        raise ValueError("continuous item generation requires independent review and a book scope")

    def advance_chapter(chapter: ChapterDraft) -> tuple[list[DiagnosticItem], int, list[float], bool, bool]:
        points = generator._select_points(chapter)
        if not points:
            return [], 0, [], False, False
        # Previously completed chapters were independently reviewed under the
        # stricter policy. Reuse them without spending new model calls.
        existing = cache.lookup(generator._chapter_identity(structure, chapter, points, scope))
        if existing is not None:
            by_id = {point[0]: point for point in points}
            for index, item in enumerate(existing):
                application = _COGNITIVE_TARGETS[index % len(_COGNITIVE_TARGETS)]["id"] == "source_application"
                expected = generator._validate_item(ItemDraft.model_validate(generator._candidate_item(item)),
                    chapter, by_id, allow_rewrite=generator.allow_grounded_answer_rewrite or application,
                    structured_application=application)
                if expected != item:
                    raise DiagnosticChapterSubmissionBlocked("saved chapter approval does not match its source")
            return existing, len(points), [], False, False
        approved: list[DiagnosticItem] = []
        delays: list[float] = []
        waiting = blocked = False
        for index, point in enumerate(points):
            if stop_requested.get()():
                delays.append(1)
                break
            identity = _identity(generator, structure, chapter, points, index, scope)
            state = cache.repair_state(identity)
            now = time.time()
            due = float(state.get("next_retry_at", 0))
            if due > now:
                delays.append(due - now)
                waiting = waiting or bool(state.get("waiting_for_service"))
                continue
            claim = cache.claim(identity)
            if claim["status"] == "approved":
                items = claim["items"]
                validate_chapter_items(identity, items)
                item = items[0]
                application = _COGNITIVE_TARGETS[index % len(_COGNITIVE_TARGETS)]["id"] == "source_application"
                candidate = generator._candidate_item(item)
                expected = generator._validate_item(ItemDraft.model_validate(candidate), chapter,
                    {point[0]: point}, allow_rewrite=generator.allow_grounded_answer_rewrite or application,
                    structured_application=application)
                if expected != item:
                    raise DiagnosticChapterSubmissionBlocked("saved item approval does not match its source")
                approved.append(item)
                continue
            if claim["status"] != "claimed":
                # Live/unknown submissions are never interpreted as a quality
                # rejection. GET polling cannot authorize a duplicate inference.
                blocked = True
                continue
            token = claim["token"]
            attempt = int(state.get("attempt", 0)) + 1
            context = dict(state.get("context", {}))
            context["generation_strategy"] = "redesign" if attempt > 1 and (attempt - 1) % 3 == 0 else "repair"
            audit = {"book_id": scope, "chapter_id": chapter.chapter_id,
                     "knowledge_point_id": point[0], "repair_round": attempt}
            generator._audit(audit, "item_attempt_started")
            try:
                with no_ambiguous_model_retries():
                    items = generator._generate_chapter(structure.title, chapter, [point],
                        audit_context=audit, point_offset=index, repair_context=context, targeted=True)
                cache.approve(identity, token, items)
                approved.extend(items)
                generator._audit(audit, "item_approval_saved")
            except (DiagnosticGenerationError, LLMRefusalError, LLMOutputLimitError) as error:
                delay = min(120, 5 * 2 ** min(attempt - 1, 5))
                next_context = getattr(error, "repair_context", {}) or {
                    "repair_feedback": {"stage": "model_response", "repair": "重新设计有原文依据的完整选择题"}}
                next_context.pop("_pending_candidate", None)
                cache.defer(identity, token, {"attempt": attempt, "context": next_context,
                    "next_retry_at": time.time() + delay, "waiting_for_service": False})
                delays.append(delay)
                generator._audit(audit, "item_repair_scheduled", retry_after_seconds=delay)
            except (LLMCooldownError, LLMNotSubmittedError) as error:
                delay = max(5, getattr(error, "retry_after_seconds", 30))
                cache.defer(identity, token, {"attempt": attempt - 1,
                    "context": getattr(error, "repair_context", context),
                    "next_retry_at": time.time() + delay, "waiting_for_service": True}, "not_admitted")
                delays.append(delay)
                waiting = True
            except LLMError as error:
                delay = _service_retry_delay(error)
                if delay is None:
                    cache.fail(identity, token, "unknown")
                    raise
                cache.defer(identity, token, {"attempt": attempt - 1,
                    "context": getattr(error, "repair_context", context),
                    "next_retry_at": time.time() + delay, "waiting_for_service": True})
                delays.append(delay)
                waiting = True
            except BaseException:
                cache.fail(identity, token, "unknown")
                raise
        return approved, len(points), delays, waiting, blocked

    batches = ordered_book_map(advance_chapter, structure.chapters)
    items = [item for batch, _, _, _, _ in batches for item in batch]
    total = sum(count for _, count, _, _, _ in batches)
    if any(blocked for _, _, _, _, blocked in batches):
        raise DiagnosticChapterSubmissionBlocked("an item submission is running or needs reconciliation")
    if len(items) < total:
        delays = [delay for _, _, pending, _, _ in batches for delay in pending]
        raise DiagnosticRepairPending(completed=len(items), total=total,
            retry_after=min(delays, default=5),
            waiting_for_service=any(waiting for _, _, _, waiting, _ in batches))
    if not items:
        raise DiagnosticGenerationError("structure contains no evidence-backed knowledge points")
    from .item_generation import structure_fingerprint
    return generator._balance_options(items, seed=structure_fingerprint(structure))


def diagnostic_item_progress(generator: DiagnosticItemGenerator, structure: BookStructure,
                              cache: Any, scope: str) -> dict[str, int]:
    """Read exact current identities only; progress observation never claims work."""
    completed = total = 0
    for chapter in structure.chapters:
        points = generator._select_points(chapter)
        total += len(points)
        if not points:
            continue
        existing = cache.lookup(generator._chapter_identity(structure, chapter, points, scope))
        if existing is not None:
            completed += len(existing)
        else:
            completed += sum(cache.lookup(_identity(generator, structure, chapter, points, index, scope)) is not None
                             for index in range(len(points)))
    return {"diagnostics_completed": completed, "diagnostics_total": total}
