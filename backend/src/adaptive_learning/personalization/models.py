from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from ..learning_preferences import LearningDesignConfig

SUMMARY_MAX_CHARS = 1000


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
    title: str = Field(min_length=1, max_length=180)
    explanation: str = Field(min_length=1, max_length=1200)
    importance: str
    mastery: float = Field(ge=0, le=1)
    state: str
    citations: list[SourceCitation] = Field(default_factory=list)


class Flashcard(BaseModel):
    card_id: str
    front: str = Field(min_length=1, max_length=120)
    back: str = Field(min_length=1, max_length=360)
    reason_for_user: str = Field(min_length=1, max_length=180)
    point_id: str = ""
    citations: list[SourceCitation] = Field(default_factory=list)
    source: SourceCitation | None = None


class LessonSection(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    content: str = Field(min_length=1, max_length=1200)
    purpose: str = Field(min_length=1, max_length=240)
    citations: list[SourceCitation] = Field(default_factory=list)
    kind: str = "explanation"
    point_id: str = ""


class LearningUnit(BaseModel):
    unit_id: str
    title: str
    objectives: list[str]
    knowledge_point_ids: list[str]
    sections: list[LessonSection]
    flashcard_ids: list[str]
    practice_item_ids: list[str]
    estimated_minutes: int = Field(ge=1)
    prerequisite_check: str | None = None


class PracticeItem(BaseModel):
    item_id: str
    point_id: str
    prompt: str = Field(min_length=1, max_length=300)
    response_type: str = "explanation"
    options: list[str] = Field(default_factory=list)
    correct_option_ids: list[str] = Field(default_factory=list)
    expected_answer: str = Field(min_length=1, max_length=800)
    rubric: list[str] = Field(default_factory=list, max_length=6)
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
    explanation: str = Field(min_length=1, max_length=600)
    presentation_order: list[str] = Field(default_factory=list)
    support_actions: list[str] = Field(default_factory=list)


class StudyUnit(BaseModel):
    unit_id: str
    title: str
    point_ids: list[str] = Field(min_length=1)
    estimated_minutes: int = Field(ge=1, le=600)


class ChapterLearningBundle(BaseModel):
    course_id: str
    version: int = Field(ge=1)
    chapter_id: str
    chapter_title: str
    decision: PersonalizationDecision
    opening: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=SUMMARY_MAX_CHARS)
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
    design_config: LearningDesignConfig | None = None
    design_fingerprint: str = ""
    units: list[LearningUnit] = Field(default_factory=list)
    coverage_point_ids: list[str] = Field(default_factory=list)
    study_units: list[StudyUnit] = Field(default_factory=list)
    teaching_quality_version: str = ""
    teaching_evidence_fingerprint: str = ""
    teaching_review: dict[str, object] = Field(default_factory=dict)
    teaching_fact_review: dict[str, object] = Field(default_factory=dict)
    # Untrusted planning hints, not approval; intentionally absent from public output.
    teaching_scope_version: str = ""
    teaching_scope_plan: dict[str, list[dict[str, object]]] = Field(default_factory=dict)


def reviewed_learning_units(bundle: ChapterLearningBundle) -> list[LearningUnit]:
    """Project reviewed content into the existing native layout without new inference.

    Preserve the compiler's grouping, unit identities and section order. No draft
    prose, stale questions or answer keys are copied from the original sections.
    """
    if not bundle.units:
        return []
    points = {point.point_id: point for point in bundle.knowledge_points}
    cards = {card.point_id: card for card in bundle.flashcards}
    practices = {item.point_id: item for item in bundle.practice_items}
    if (
        len(points) != len(bundle.knowledge_points)
        or len(cards) != len(bundle.flashcards)
        or len(practices) != len(bundle.practice_items)
        or set(cards) != set(points)
        or set(practices) != set(points)
    ):
        raise ValueError("reviewed unit content must cover each point exactly once")
    covered = [point_id for unit in bundle.units for point_id in unit.knowledge_point_ids]
    if len(covered) != len(points) or set(covered) != set(points):
        raise ValueError("native learning units must cover every reviewed point exactly once")
    rewritten = []
    explanation_kinds = {"key_points", "explanation", "evidence_example", "steps"}
    for unit in bundle.units:
        if not unit.knowledge_point_ids:
            raise ValueError("native learning unit has no reviewed points")
        sections = []
        for section in unit.sections:
            if section.point_id not in unit.knowledge_point_ids:
                raise ValueError("native section refers to a point outside its unit")
            point = points[section.point_id]
            if section.kind in explanation_kinds:
                # The reviewed explanation is already complete. Do not fabricate
                # a new example or derivation to fill a presentation preference.
                content, citations = point.explanation, point.citations
            elif section.kind == "checkpoint":
                practice = practices[section.point_id]
                content, citations = practice.prompt, practice.citations
            elif section.kind == "recall":
                card = cards[section.point_id]
                content, citations = card.front, card.citations
            else:
                raise ValueError("native learning unit has an unsupported section kind")
            sections.append(section.model_copy(update={
                "content": content, "citations": citations,
            }, deep=True))
        chosen = [points[point_id] for point_id in unit.knowledge_point_ids]
        estimate = sum(
            2 + (practices[point.point_id].estimated_seconds + 59) // 60
            + len(point.explanation) // 150
            + bool(bundle.design_config and "examples" in bundle.design_config.guidance_styles)
            for point in chosen
        )
        rewritten.append(unit.model_copy(update={
            "title": chosen[0].title if len(chosen) == 1 else unit.title,
            "objectives": [point.explanation for point in chosen],
            "sections": sections,
            "flashcard_ids": [cards[point.point_id].card_id for point in chosen],
            "practice_item_ids": [practices[point.point_id].item_id for point in chosen],
            "estimated_minutes": estimate,
        }, deep=True))
    return rewritten


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
    design_config: LearningDesignConfig | None = None
    design_fingerprint: str = ""
    units: list[LearningUnit] = Field(default_factory=list)
    coverage_point_ids: list[str] = Field(default_factory=list)
    study_units: list[StudyUnit] = Field(default_factory=list)

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
            design_config=bundle.design_config,
            design_fingerprint=bundle.design_fingerprint,
            units=reviewed_learning_units(bundle)
            if bundle.teaching_quality_version else bundle.units,
            coverage_point_ids=bundle.coverage_point_ids,
            study_units=bundle.study_units,
        )
