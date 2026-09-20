"""Bounded, versioned contracts for private learning media and digital ink."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class Anchor(Strict):
    book_id: str = Field(min_length=1, max_length=100)
    chapter_id: str = Field(default="", max_length=150)
    chapter_title: str = Field(default="", max_length=250)
    excerpt: str = Field(default="", max_length=3000)
    pages: list[int] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def valid_pages(self) -> Anchor:
        if any(p < 1 or p > 100000 for p in self.pages):
            raise ValueError("invalid page")
        return self


class MediaInput(Anchor):
    kind: Literal["image", "video"]
    goal: Literal["意思", "原因", "过程"] = "意思"
    level: Literal["入门", "标准", "进阶"] = "标准"
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    consent: Literal[True]

    @model_validator(mode="after")
    def selected(self) -> MediaInput:
        if len(self.excerpt) < 4:
            raise ValueError("select text first")
        return self


class Point(Strict):
    x: float = Field(ge=0, le=1000)
    y: float = Field(ge=0, le=1400)
    p: float = Field(default=0.5, ge=0, le=1)


class Stroke(Strict):
    points: list[Point] = Field(min_length=1, max_length=3000)
    color: Literal["#243148", "#7655c9", "#23836e"] = "#243148"
    width: int = Field(default=4, ge=2, le=14)


class NoteInput(Anchor):
    id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    revision: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=120)
    input_mode: Literal["ink", "voice"] = "ink"
    strokes: list[Stroke] = Field(default_factory=list, max_length=1500)
    # These are server-owned fields. They are present in the shared response shape,
    # but save_note always preserves or resets them instead of trusting the client.
    audio_ready: bool = False
    audio_mime: str = Field(default="", max_length=80)
    audio_duration_seconds: float = Field(default=0, ge=0, le=602)
    audio_sha256: str = Field(default="", max_length=64)

    @model_validator(mode="after")
    def bounded_points(self) -> NoteInput:
        if sum(len(s.points) for s in self.strokes) > 60000:
            raise ValueError("note is full; create another page")
        return self


class NoteAction(Strict):
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    revision: int = Field(ge=1)
    action: Literal["recognize", "improve", "complete"]
    transcript: str = Field(default="", max_length=12000)
    consent: Literal[True]


class DiagramFact(Strict):
    subject: str = Field(min_length=1, max_length=36)
    relation: str = Field(min_length=1, max_length=20)
    object: str = Field(min_length=1, max_length=36)


class TeachingPlan(Strict):
    title: str = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=2000)
    points: list[str] = Field(min_length=1, max_length=5)
    visual_prompt: str = Field(min_length=10, max_length=1200)
    caution: str = Field(max_length=500)
    supported: bool
    video_suitable: bool
    visual_checks: list[str] = Field(default_factory=list, max_length=5)
    visual_scope: str = Field(default="", max_length=250)
    visual_mode: Literal["illustration", "diagram"] = "illustration"
    diagram_facts: list[DiagramFact] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def has_diagram(self) -> TeachingPlan:
        if self.visual_mode == "diagram" and not self.diagram_facts:
            raise ValueError("diagram facts required")
        return self


class MediaReview(Strict):
    observations: str = Field(min_length=1, max_length=2000)
    objects_correct: bool
    relationships_correct: bool
    no_unwanted_text: bool
    useful: bool
    temporal_consistency: bool
    reason: str = Field(max_length=2000)

    @property
    def passed(self) -> bool:
        return all(
            (
                self.objects_correct,
                self.relationships_correct,
                self.no_unwanted_text,
                self.useful,
                self.temporal_consistency,
            )
        )


class Recognition(Strict):
    transcript: str = Field(max_length=12000)
    uncertain: list[str] = Field(default_factory=list, max_length=30)


class Suggestion(Strict):
    original: str = Field(max_length=2000)
    kind: Literal["需核对", "缺少条件", "可以补充"]
    suggestion: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(max_length=2500)
    page: int = Field(ge=1, le=100000)


class Improvement(Strict):
    summary: str = Field(min_length=1, max_length=1000)
    suggestions: list[Suggestion] = Field(max_length=12)
    polished: str = Field(min_length=1, max_length=16000)


class Review(Strict):
    passed: bool
    reason: str = Field(max_length=1000)
