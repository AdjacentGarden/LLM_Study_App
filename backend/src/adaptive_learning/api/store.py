from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..assessment.models import DiagnosticItem, InterviewSession
from ..ingestion.models import BookReconstruction


@dataclass(slots=True)
class BookRecord:
    book_id: str
    original_name: str
    file_path: Path
    status: str = "uploaded"
    progress: float = 0
    current_step: str = "等待文档重建"
    reconstruction: BookReconstruction | None = None
    diagnostic_items: list[DiagnosticItem] = field(default_factory=list)


class MemoryStore:
    def __init__(self) -> None:
        self.books: dict[str, BookRecord] = {}
        self.interviews: dict[str, InterviewSession] = {}

    def book(self, book_id: str) -> BookRecord:
        if book_id not in self.books:
            raise KeyError(book_id)
        return self.books[book_id]

    def interview(self, session_id: str) -> InterviewSession:
        if session_id not in self.interviews:
            raise KeyError(session_id)
        return self.interviews[session_id]
