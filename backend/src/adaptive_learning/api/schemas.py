from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..assessment.models import (
    AssessmentResponse,
    FlashcardReviewState,
    InterviewSession,
    InterviewTurn,
    LearnerProfile,
    ScoredEvidence,
)
from ..personalization.models import PublicChapterLearningBundle


class UploadResponse(BaseModel):
    book_id: str
    filename: str
    status: str
    next: str


class BookCatalogItem(BaseModel):
    book_id: str
    title: str
    status: str
    page_count: int | None
    chapter_count: int
    summary: str
    diagnostics_ready: bool = False
    cover_url: str | None = None


class BookStatusResponse(BaseModel):
    book_id: str
    status: str
    progress: float
    current_step: str
    quality_score: float | None = None
    needs_human_review: bool = False
    attempts: int = 0
    max_attempts: int = 0
    page_count: int | None = None
    retryable: bool = False


class StartInterviewRequest(BaseModel):
    user_id: str = "local_user"
    book_id: str


class BookQuestionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=2000)


class ProfileAnswerRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)
    selected_option_ids: list[str] = Field(default_factory=list, max_length=20)


class DiagnosticBankResponse(BaseModel):
    book_id: str
    item_count: int
    chapter_count: int
    choice_count: int
    explanation_count: int
    ready: bool = True


class CourseResponse(PublicChapterLearningBundle):
    pass


class CoursePracticeRequest(BaseModel):
    event_id: str = Field(min_length=8, max_length=120)
    answer: str = Field(min_length=1, max_length=4000)
    selected_option_ids: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0, le=1)
    response_seconds: float = Field(gt=0, le=3600)
    hints_used: int = Field(default=0, ge=0, le=10)
    revisions: int = Field(default=0, ge=0, le=20)


class FlashcardReviewRequest(BaseModel):
    event_id: str = Field(min_length=8, max_length=120)
    rating: str
    response_seconds: float = Field(gt=0, le=3600)


class CourseActivityResponse(BaseModel):
    duplicate: bool = False
    evidence: ScoredEvidence
    profile: LearnerProfile
    course_stale: bool
    review_state: FlashcardReviewState | None = None


class InterviewResponse(BaseModel):
    session_id: str
    phase: str
    turn: InterviewTurn
    profile: LearnerProfile


class DiagnosticAnswerResponse(BaseModel):
    evidence: ScoredEvidence
    next_turn: InterviewTurn
    profile: LearnerProfile


class ProfileConfirmationRequest(BaseModel):
    confirmed: bool


class SessionEnvelope(BaseModel):
    session: InterviewSession


class DiagnosticResponseRequest(AssessmentResponse):
    pass
