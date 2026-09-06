from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class TeachingDepth(StrEnum):
    FOUNDATION = "foundation"
    STANDARD = "standard"
    ADVANCED = "advanced"


class SourceCitation(BaseModel):
    page_number: int
    block_id: str = ""
    quote: str = Field(min_length=1, max_length=500)


class KnowledgePoint(BaseModel):
    point_id: str
    title: str
    explanation: str
    importance: str
    mastery: float = Field(ge=0, le=1)
    state: str
    citations: list[SourceCitation] = Field(default_factory=list)


class Flashcard(BaseModel):
    card_id: str
    front: str
    back: str
    reason_for_user: str
    point_id: str = ""
    citations: list[SourceCitation] = Field(default_factory=list)
    source: SourceCitation | None = None


class LessonSection(BaseModel):
    title: str
    content: str
    purpose: str
    citations: list[SourceCitation] = Field(default_factory=list)


class PracticeItem(BaseModel):
    item_id: str
    point_id: str
    prompt: str
    response_type: str = "explanation"
    options: list[str] = Field(default_factory=list)
    correct_option_ids: list[str] = Field(default_factory=list)
    expected_answer: str
    rubric: list[str] = Field(default_factory=list)
    difficulty: float = Field(ge=-3, le=3)
    estimated_seconds: int = Field(ge=10, le=600)
    citations: list[SourceCitation] = Field(min_length=1)


class PublicPracticeItem(BaseModel):
    item_id: str
    point_id: str
    prompt: str
    response_type: str
    options: list[str]
    estimated_seconds: int

    @classmethod
    def from_private(cls, item: PracticeItem) -> PublicPracticeItem:
        return cls(
            item_id=item.item_id,
            point_id=item.point_id,
            prompt=item.prompt,
            response_type=item.response_type,
            options=item.options,
            estimated_seconds=item.estimated_seconds,
        )


class PersonalizationDecision(BaseModel):
    chapter_id: str
    depth: TeachingDepth
    assumed_mastery: float = Field(ge=0, le=1)
    evidence_confidence: float = Field(ge=0, le=1)
    emphasis: list[str] = Field(default_factory=list)
    scaffolds: list[str] = Field(default_factory=list)
    exercise_difficulty: float = Field(ge=0, le=1)
    explanation: str


class ChapterLearningBundle(BaseModel):
    course_id: str
    version: int = Field(ge=1)
    chapter_id: str
    chapter_title: str
    decision: PersonalizationDecision
    opening: str
    summary: str
    original_reading: list[LessonSection]
    knowledge_points: list[KnowledgePoint]
    flashcards: list[Flashcard]
    worked_examples: list[LessonSection]
    checkpoint_questions: list[str]
    practice_items: list[PracticeItem]
    generated_from_block_ids: list[str]
    profile_fingerprint: str
    source_fingerprint: str
    estimated_minutes: int = Field(ge=1, le=600)
    created_at: datetime
    unresolved_source_warnings: list[str] = Field(default_factory=list)


class PublicChapterLearningBundle(BaseModel):
    course_id: str
    version: int
    chapter_id: str
    chapter_title: str
    decision: PersonalizationDecision
    opening: str
    summary: str
    original_reading: list[LessonSection]
    knowledge_points: list[KnowledgePoint]
    flashcards: list[Flashcard]
    worked_examples: list[LessonSection]
    checkpoint_questions: list[str]
    practice_items: list[PublicPracticeItem]
    estimated_minutes: int
    created_at: datetime
    unresolved_source_warnings: list[str]

    @classmethod
    def from_private(cls, bundle: ChapterLearningBundle) -> PublicChapterLearningBundle:
        return cls(
            course_id=bundle.course_id,
            version=bundle.version,
            chapter_id=bundle.chapter_id,
            chapter_title=bundle.chapter_title,
            decision=bundle.decision,
            opening=bundle.opening,
            summary=bundle.summary,
            original_reading=bundle.original_reading,
            knowledge_points=bundle.knowledge_points,
            flashcards=bundle.flashcards,
            worked_examples=bundle.worked_examples,
            checkpoint_questions=bundle.checkpoint_questions,
            practice_items=[
                PublicPracticeItem.from_private(item) for item in bundle.practice_items
            ],
            estimated_minutes=bundle.estimated_minutes,
            created_at=bundle.created_at,
            unresolved_source_warnings=bundle.unresolved_source_warnings,
        )
