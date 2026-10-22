"""Shared learning preferences, independent of assessment and course compilation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LearningGoal = Literal["systematic", "exam", "growth", "interest"]
LearningBarrier = Literal["starting", "focus", "understanding", "memory"]
GuidanceStyle = Literal["keyPoints", "principles", "examples", "stepByStep"]
PlanningSupport = Literal["selfDirected", "collaborative", "structured", "explore"]

GOAL_LABELS = {
    "systematic": "系统掌握知识",
    "exam": "备考提升成绩",
    "growth": "提升实际能力",
    "interest": "探索兴趣内容",
}
TIME_BUDGETS = {"under30": 20, "30to60": 45, "1to2h": 90, "over2h": 120}
INTERVIEW_GOALS = {
    "foundation": "systematic",
    "exam": "exam",
    "application": "growth",
    "overview": "interest",
}


class LearningDesignOverrides(BaseModel):
    """Null inherits the account default; an empty list explicitly clears a preference."""

    model_config = ConfigDict(extra="forbid")
    primary_goal: LearningGoal | None = None
    minutes_per_day: int | None = Field(default=None, ge=5, le=360)
    learning_barriers: list[LearningBarrier] | None = Field(default=None, max_length=2)
    guidance_styles: list[GuidanceStyle] | None = Field(default=None, max_length=2)
    planning_support: PlanningSupport | None = None
    time_pattern: Literal["block", "fragmented"] | None = None
    review_mode: Literal["scheduled", "free"] | None = None

    @field_validator("learning_barriers", "guidance_styles")
    @classmethod
    def distinct(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("不能重复选择同一项")
        return value


class LearningDesignConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    primary_goal: LearningGoal = "systematic"
    minutes_per_day: int = Field(default=45, ge=5, le=360)
    learning_barriers: list[LearningBarrier] = Field(default_factory=list, max_length=2)
    guidance_styles: list[GuidanceStyle] = Field(default_factory=list, max_length=2)
    planning_support: PlanningSupport = "collaborative"
    time_pattern: Literal["block", "fragmented"] = "block"
    review_mode: Literal["scheduled", "free"] = "scheduled"
    sources: dict[str, str] = Field(default_factory=dict)
    course_revision: int = 0

    @field_validator("learning_barriers", "guidance_styles")
    @classmethod
    def canonical_choices(cls, value: list[str]) -> list[str]:
        # These are unranked multi-select questions. Click order is not a priority.
        order = [
            "starting",
            "focus",
            "understanding",
            "memory",
            "keyPoints",
            "principles",
            "examples",
            "stepByStep",
        ]
        return sorted(set(value), key=order.index)

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(exclude={"sources", "course_revision"})
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @property
    def segment_minutes(self) -> int:
        short = self.time_pattern == "fragmented" or bool(
            {"starting", "focus"} & set(self.learning_barriers)
        )
        return min(self.minutes_per_day, 8 if short else 15)

    @property
    def presentation_order(self) -> list[str]:
        styles = self.guidance_styles
        first = {
            "keyPoints": "key_points",
            "principles": "explanation",
            "examples": "evidence_example",
            "stepByStep": "steps",
        }
        order = [
            first[style]
            for style in ("keyPoints", "examples", "stepByStep", "principles")
            if style in styles
        ]
        order += ["key_points", "explanation", "evidence_example", "steps", "checkpoint"]
        return list(dict.fromkeys(order))

    @property
    def support_actions(self) -> list[str]:
        names = {
            "starting": "给出可立即开始的小任务",
            "focus": "每次聚焦一个学习目标",
            "understanding": "补充前置概念检查和解释步骤",
            "memory": "安排主动回忆与后续复习",
        }
        return [names[value] for value in self.learning_barriers]

    def qa_preferences(self) -> dict[str, Any]:
        return {
            "goal": GOAL_LABELS[self.primary_goal],
            "guidance_styles": self.guidance_styles,
            "support_actions": self.support_actions,
            "presentation_order": self.presentation_order,
        }


def resolve_learning_design(
    account: Mapping[str, Any] | None = None,
    course: Mapping[str, list[str]] | None = None,
    overrides: LearningDesignOverrides | None = None,
    declarations: Mapping[str, str] | None = None,
) -> LearningDesignConfig:
    """Precedence: defaults < account < course questionnaire < course override < interview."""
    values = LearningDesignConfig().model_dump()
    sources: dict[str, str] = {}

    def assign(key: str, value: Any, source: str) -> None:
        values[key] = value
        sources[key] = source

    account = account or {}
    for source, target, allowed in [
        ("primaryGoal", "primary_goal", GOAL_LABELS),
        (
            "planningSupport",
            "planning_support",
            {s: s for s in ("selfDirected", "collaborative", "structured", "explore")},
        ),
    ]:
        if account.get(source) in allowed:
            assign(target, account[source], "account")
    if account.get("dailyTime") in TIME_BUDGETS:
        assign("minutes_per_day", TIME_BUDGETS[account["dailyTime"]], "account")
    for source, target, allowed_many in [
        ("learningBarriers", "learning_barriers", {"starting", "focus", "understanding", "memory"}),
        (
            "guidanceStyles",
            "guidance_styles",
            {"keyPoints", "principles", "examples", "stepByStep"},
        ),
    ]:
        if source in account:
            selected = [v for v in account[source] or [] if v in allowed_many]
            assign(target, list(dict.fromkeys(selected))[:2], "account")

    course = course or {}
    goals = {**{k: k for k in GOAL_LABELS}, "professional": "growth"}
    if course.get("goals") and course["goals"][0] in goals:
        assign("primary_goal", goals[course["goals"][0]], "course")
    if course.get("timePattern") and course["timePattern"][0] in {"block", "fragmented"}:
        assign("time_pattern", course["timePattern"][0], "course")
    if course.get("reviews"):
        assign("review_mode", "free" if "free" in course["reviews"] else "scheduled", "course")
    foci = {"keyPoints": "keyPoints", "principles": "principles", "practice": "examples"}
    styles = [foci[v] for v in course.get("contentFoci", []) if v in foci]
    if styles:
        assign("guidance_styles", list(dict.fromkeys(styles))[:2], "course")
    if overrides:
        for key, value in overrides.model_dump(exclude_none=True).items():
            assign(key, value, "course_override")

    declarations = declarations or {}
    if declarations.get("goal") in INTERVIEW_GOALS:
        assign("primary_goal", INTERVIEW_GOALS[declarations["goal"]], "interview")
    if declarations.get("minutes_per_day", "").isdigit():
        assign(
            "minutes_per_day", min(360, max(5, int(declarations["minutes_per_day"]))), "interview"
        )
    values["sources"] = sources
    return LearningDesignConfig.model_validate(values)
