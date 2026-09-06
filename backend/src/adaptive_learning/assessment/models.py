from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class InterviewPhase(StrEnum):
    BOOK_BRIEFING = "book_briefing"
    GOAL_DISCOVERY = "goal_discovery"
    BACKGROUND_DISCOVERY = "background_discovery"
    CONSTRAINT_DISCOVERY = "constraint_discovery"
    ADAPTIVE_DIAGNOSIS = "adaptive_diagnosis"
    PROFILE_CONFIRMATION = "profile_confirmation"
    COMPLETE = "complete"


class ResponseType(StrEnum):
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    SHORT_ANSWER = "short_answer"
    EXPLANATION = "explanation"
    SELF_REPORT = "self_report"


class EvidenceKind(StrEnum):
    DECLARED = "declared"
    DIAGNOSTIC = "diagnostic"
    BEHAVIORAL = "behavioral"


class MasteryPosterior(BaseModel):
    alpha: float = Field(default=1.0, gt=0)
    beta: float = Field(default=1.0, gt=0)
    evidence_count: int = 0
    tracked_mastery: float | None = Field(default=None, ge=0, le=1)
    last_updated_at: datetime | None = None

    @property
    def mean(self) -> float:
        """Best current mastery estimate, including sequential knowledge tracing."""
        if self.tracked_mastery is not None:
            return self.tracked_mastery
        return self.beta_mean

    @property
    def beta_mean(self) -> float:
        """Evidence-counting estimate used for uncertainty calculations."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def variance(self) -> float:
        total = self.alpha + self.beta
        return (self.alpha * self.beta) / (total * total * (total + 1))


class DiagnosticItem(BaseModel):
    item_id: str
    chapter_id: str
    knowledge_point_ids: list[str] = Field(min_length=1, max_length=4)
    knowledge_point_labels: list[str] = Field(default_factory=list, max_length=4)
    prompt: str
    response_type: ResponseType
    options: list[str] = Field(default_factory=list)
    correct_option_ids: list[str] = Field(default_factory=list)
    expected_answer: str | None = None
    rubric: list[str] = Field(default_factory=list)
    difficulty: float = Field(default=0, ge=-3, le=3)
    discrimination: float = Field(default=1, gt=0, le=3)
    estimated_seconds: int = Field(default=45, ge=10, le=600)
    source_block_ids: list[str] = Field(default_factory=list)
    source_pages: list[int] = Field(default_factory=list)
    prerequisite_ids: list[str] = Field(default_factory=list)


class PublicDiagnosticItem(BaseModel):
    item_id: str
    chapter_id: str
    knowledge_point_labels: list[str] = Field(default_factory=list, max_length=4)
    prompt: str
    response_type: ResponseType
    options: list[str] = Field(default_factory=list)
    estimated_seconds: int

    @field_validator("knowledge_point_labels", mode="before")
    @classmethod
    def hide_answer_bearing_labels(cls, value: object) -> list[str]:
        # Generated labels may be verbatim correct propositions. Also sanitize
        # persisted pending turns when they are restored, not just new items.
        return []

    @classmethod
    def from_private(cls, item: DiagnosticItem) -> PublicDiagnosticItem:
        return cls(
            item_id=item.item_id,
            chapter_id=item.chapter_id,
            knowledge_point_labels=item.knowledge_point_labels,
            prompt=item.prompt,
            response_type=item.response_type,
            options=item.options,
            estimated_seconds=item.estimated_seconds,
        )


class AssessmentResponse(BaseModel):
    item_id: str
    answer: str
    selected_option_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    response_seconds: float = Field(gt=0, le=3600)
    hints_used: int = Field(default=0, ge=0, le=10)
    revisions: int = Field(default=0, ge=0, le=20)


class ScoredEvidence(BaseModel):
    score: float = Field(ge=0, le=1)
    scoring_confidence: float = Field(ge=0, le=1)
    misconception_candidates: list[str] = Field(default_factory=list)
    matched_rubric: list[str] = Field(default_factory=list)
    missing_rubric: list[str] = Field(default_factory=list)
    needs_follow_up: bool = False
    follow_up_question: str | None = None


class ProfileEvidence(BaseModel):
    evidence_id: str
    kind: EvidenceKind
    field: str
    value: str
    confidence: float = Field(ge=0, le=1)
    item_id: str | None = None
    observed_at: datetime


class DiagnosticObservation(BaseModel):
    item_id: str
    chapter_id: str
    knowledge_point_ids: list[str]
    score: float = Field(ge=0, le=1)
    evidence_weight: float = Field(ge=0)
    scoring_confidence: float = Field(ge=0, le=1)
    self_confidence: float = Field(ge=0, le=1)
    response_seconds: float = Field(gt=0)
    hints_used: int = Field(ge=0)
    item_difficulty: float = Field(default=0, ge=-3, le=3)
    observed_at: datetime
    # Keep the content actually seen by the learner when item banks are refreshed.
    question_snapshot: str | None = None
    answer_snapshot: str | None = None
    source_pages: list[int] = Field(default_factory=list)
    knowledge_labels: list[str] = Field(default_factory=list)
    activity_kind: str | None = None


class LearnerConstraints(BaseModel):
    minutes_per_day: int = Field(default=30, ge=5, le=360)
    target_date: str | None = None
    preferred_language: str = "zh-CN"
    accessibility_needs: list[str] = Field(default_factory=list)


class FlashcardReviewState(BaseModel):
    algorithm: str = "legacy-sm2"
    repetitions: int = Field(default=0, ge=0)
    ease_factor: float = Field(default=2.5, ge=1.3, le=3.5)
    interval_days: float = Field(default=0, ge=0)
    due_at: datetime | None = None
    last_rating: str | None = None
    updated_at: datetime | None = None
    card_id: int | None = None
    memory_state: int | None = Field(default=None, ge=1, le=3)
    learning_step: int | None = Field(default=None, ge=0)
    stability: float | None = Field(default=None, ge=0)
    difficulty: float | None = Field(default=None, ge=1, le=10)
    last_review_at: datetime | None = None
    desired_retention: float = Field(default=0.9, gt=0, lt=1)


class LearnerProfile(BaseModel):
    user_id: str
    book_id: str
    goal: str = ""
    focus_chapter_ids: list[str] = Field(default_factory=list)
    declared_background: str = ""
    background_level: str = "unknown"
    constraints: LearnerConstraints = Field(default_factory=LearnerConstraints)
    chapter_mastery: dict[str, MasteryPosterior] = Field(default_factory=dict)
    knowledge_mastery: dict[str, MasteryPosterior] = Field(default_factory=dict)
    confidence_brier_sum: float = 0
    confidence_observations: int = 0
    misconception_candidates: list[str] = Field(default_factory=list)
    evidence: list[ProfileEvidence] = Field(default_factory=list)
    diagnostic_observations: list[DiagnosticObservation] = Field(default_factory=list)
    flashcard_reviews: dict[str, FlashcardReviewState] = Field(default_factory=dict)
    profile_confidence: float = Field(default=0, ge=0, le=1)

    @property
    def calibration_error(self) -> float | None:
        if self.confidence_observations == 0:
            return None
        return self.confidence_brier_sum / self.confidence_observations


class InterviewTurn(BaseModel):
    turn_id: str
    phase: InterviewPhase
    message: str
    question: str | None = None
    response_type: ResponseType | None = None
    options: list[dict[str, str]] = Field(default_factory=list)
    item: PublicDiagnosticItem | None = None
    why_asked: str | None = None
    progress: float = Field(default=0, ge=0, le=1)


class InterviewSession(BaseModel):
    session_id: str
    profile: LearnerProfile
    phase: InterviewPhase = InterviewPhase.BOOK_BRIEFING
    asked_item_ids: list[str] = Field(default_factory=list)
    follow_up_item_ids: list[str] = Field(default_factory=list)
    responses: list[AssessmentResponse] = Field(default_factory=list)
    pending_turn: InterviewTurn | None = None
    chapter_options: list[dict[str, str]] = Field(default_factory=list)
    revision: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime
