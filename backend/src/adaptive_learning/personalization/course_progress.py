"""Small public course phases, separate from private model/audit payloads."""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum

logger = logging.getLogger(__name__)


class CourseStage(StrEnum):
    PLANNING = "planning"
    WRITING = "writing"
    REVIEWING = "reviewing"
    REFINING = "refining"


_observer: ContextVar[Callable[[CourseStage], None] | None] = ContextVar(
    "course_progress_observer", default=None
)


@contextmanager
def observe_course_progress(observer: Callable[[CourseStage], None]) -> Iterator[None]:
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)


def report_course_stage(stage: CourseStage) -> None:
    # Do not accept arbitrary audit text, provider roles, identifiers or payloads.
    if not isinstance(stage, CourseStage):
        return
    observer = _observer.get()
    if observer is None:
        return
    try:
        observer(stage)
    except Exception as error:
        # Telemetry failure is not a content rejection and must never enter the
        # writer/review repair loops or cause another inference/submission.
        logger.warning("course progress unavailable", extra={"error_type": type(error).__name__})
