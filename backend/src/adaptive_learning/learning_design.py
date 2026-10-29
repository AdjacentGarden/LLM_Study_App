"""Account-owned course design, durable daily plans and reversible feedback suggestions."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from . import native_catalog
from .assessment.models import EvidenceKind, InterviewSession, LearnerProfile
from .learning_preferences import (
    GOAL_LABELS,
    LearningDesignConfig,
    LearningDesignOverrides,
    resolve_learning_design,
)
from .native import NativeConflict
from .personalization.review import review_state_for_point


def resolved_profile(services: Any, session: InterviewSession) -> LearnerProfile:
    context = services.native_repository.design_context(session.session_id)
    if context is None:
        return session.profile
    config = NativeLearningDesign(services).config(
        context["owner"], context["workspace_id"], session.profile
    )
    profile = session.profile.model_copy(deep=True)
    profile.learning_design = config
    if config.sources.get("primary_goal") != "interview":
        profile.goal = GOAL_LABELS[config.primary_goal]
    profile.constraints.minutes_per_day = config.minutes_per_day
    return profile


class NativeLearningDesign:
    def __init__(self, services: Any):
        self.services = services
        self.repo = services.native_repository

    def config(
        self, owner: str, wid: str, profile: LearnerProfile | None = None
    ) -> LearningDesignConfig:
        course = self.repo.workspace(owner, wid)
        stored = self.repo.design_overrides(owner, wid)
        declarations = {}
        field_names = {"goal": "primary_goal", "minutes_per_day": "minutes_per_day"}
        if profile:
            for evidence in profile.evidence:
                if evidence.kind != EvidenceKind.DECLARED:
                    continue
                newer_override = (
                    stored["revision"] > evidence.design_revision
                    and stored["overrides"].get(field_names.get(evidence.field, "")) is not None
                )
                if not newer_override:
                    declarations[evidence.field] = evidence.value
        config = resolve_learning_design(
            self.repo.learner_preferences(owner),
            course["preferences"],
            LearningDesignOverrides.model_validate(stored["overrides"]),
            declarations,
        )
        return config.model_copy(update={"course_revision": stored["revision"]})

    def state(self, owner: str, wid: str) -> dict[str, Any]:
        config = self.config(owner, wid)
        return {
            **self.repo.design_overrides(owner, wid),
            "effective": config.model_dump(),
            "fingerprint": config.fingerprint,
        }

    def queue(self, owner: str, wid: str, day: date) -> list[dict[str, Any]]:
        config = self.config(owner, wid)
        completed = self.repo.completed_tasks(owner, wid)
        tasks: list[dict[str, Any]] = []
        for resource in self.repo.resources(owner, wid):
            rid = resource["id"]
            progress = self.repo.progress(rid)
            if resource["book_id"] == native_catalog.BOOK_ID:
                source = native_catalog.public_source(rid, progress)
                for original in source["plan"]["tasks"]:
                    total = max(1, original["minutes"])
                    count = math.ceil(total / 5)
                    previous_id = None
                    for index in range(count):
                        tid = f"{original['task_id']}:segment:{index}"
                        done = original["status"] in {"done", "completed"} or tid in completed
                        tasks.append(
                            {
                                "task_id": tid,
                                "resource_id": rid,
                                "book_id": resource["book_id"],
                                "chapter_id": original.get("chapter_id"),
                                "lesson_id": original.get("lesson_id"),
                                "title": original["title"]
                                + (f" · 第 {index + 1}/{count} 段" if count > 1 else ""),
                                "kind": "practice"
                                if any(s in original["title"] for s in ("练习", "测验"))
                                else "learn",
                                "minutes": min(5, total - index * 5),
                                "status": "done" if done else "pending",
                                "requires": [previous_id] if previous_id else [],
                                "source_task_id": original["task_id"],
                                "reason": self._reason(config),
                            }
                        )
                        previous_id = tid
                if "memory" in config.learning_barriers and config.review_mode != "free":
                    learned_chapters = set(progress["completed_chapters"])
                    for card in source["flashcards"]:
                        prefix = f"recall:{rid}:{card['card_id']}:"
                        review_id = prefix + day.isoformat()
                        if card["chapter_id"] not in learned_chapters:
                            continue
                        if not _recall_due(completed, prefix, day) and review_id not in completed:
                            continue
                        tasks.append(
                            {
                                "task_id": review_id,
                                "resource_id": rid,
                                "book_id": resource["book_id"],
                                "chapter_id": card["chapter_id"],
                                "card_id": card["card_id"],
                                "title": "原课闪卡回忆",
                                "kind": "recall",
                                "minutes": 2,
                                "status": "done" if review_id in completed else "pending",
                                "requires": [],
                                "reason": "依据记忆支持偏好复习已经学习的原课程内容",
                            }
                        )
                continue
            if not resource["session_id"]:
                continue
            session = self.services.assessment_repository.get_session(resource["session_id"])
            bundles: dict[str, Any] = {}
            history = self.services.assessment_repository.courses_for_session(
                resource["session_id"]
            )
            for bundle in history:
                if (
                    bundle.chapter_id not in bundles
                    or bundles[bundle.chapter_id].version < bundle.version
                ):
                    bundles[bundle.chapter_id] = bundle
            for bundle in bundles.values():
                by_point = {p.point_id: p for p in bundle.knowledge_points}
                unit_for = {
                    pid: unit.unit_id for unit in bundle.units for pid in unit.knowledge_point_ids
                }
                previous_id = None
                for point in by_point.values():
                    tid = f"learn:{rid}:{point.point_id}"
                    done = tid in completed or bundle.chapter_id in progress["completed_chapters"]
                    tasks.append(
                        {
                            "task_id": tid,
                            "resource_id": rid,
                            "book_id": resource["book_id"],
                            "chapter_id": bundle.chapter_id,
                            "point_id": point.point_id,
                            "unit_id": unit_for.get(point.point_id),
                            "title": point.title,
                            "kind": "learn",
                            "minutes": min(config.segment_minutes, 4),
                            "status": "done" if done else "pending",
                            "requires": [previous_id] if previous_id else [],
                            "reason": self._reason(config),
                        }
                    )
                    previous_id = tid
                    practice = next(
                        (q for q in bundle.practice_items if q.point_id == point.point_id), None
                    )
                    practice_id = f"practice:{rid}:{point.point_id}"
                    answered = bool(
                        session
                        and practice
                        and any(
                            o.item_id == practice.item_id
                            for o in session.profile.diagnostic_observations
                        )
                    )
                    if practice:
                        tasks.append(
                            {
                                "task_id": practice_id,
                                "resource_id": rid,
                                "book_id": resource["book_id"],
                                "chapter_id": bundle.chapter_id,
                                "point_id": point.point_id,
                                "course_id": bundle.course_id,
                                "practice_item_id": practice.item_id,
                                "title": f"检查理解：{point.title}",
                                "kind": "practice",
                                "minutes": max(1, math.ceil(practice.estimated_seconds / 60)),
                                "status": "done"
                                if practice_id in completed or answered
                                else "pending",
                                "requires": [tid],
                                "reason": "根据实际作答检查理解，不以偏好推断能力",
                            }
                        )
                    card = next(
                        (c for c in bundle.flashcards if c.point_id == point.point_id), None
                    )
                    review = (
                        review_state_for_point(session.profile, point.point_id, history)
                        if session and card
                        else None
                    )
                    due = bool(review and review.due_at and review.due_at.date() <= day)
                    prefix = f"recall:{rid}:{point.point_id}:"
                    review_id = prefix + day.isoformat()
                    first_recall = (
                        not review
                        and done
                        and "memory" in config.learning_barriers
                        and _recall_due(completed, prefix, day)
                    )
                    if config.review_mode != "free" and (
                        due or first_recall or review_id in completed
                    ):
                        tasks.append(
                            {
                                "task_id": review_id,
                                "resource_id": rid,
                                "book_id": resource["book_id"],
                                "chapter_id": bundle.chapter_id,
                                "point_id": point.point_id,
                                "card_id": card.card_id if card else None,
                                "title": f"回忆：{point.title}",
                                "kind": "recall",
                                "minutes": 2,
                                "status": "done" if review_id in completed else "pending",
                                "requires": [tid],
                                "reason": "已到复习时间" if due else "根据记忆支持偏好安排主动回忆",
                            }
                        )
        return tasks

    @staticmethod
    def _reason(config: LearningDesignConfig) -> str:
        return (
            f"{GOAL_LABELS[config.primary_goal]}；单次聚焦一个小目标"
            if {"starting", "focus"} & set(config.learning_barriers)
            else f"按{GOAL_LABELS[config.primary_goal]}与每日 {config.minutes_per_day} 分钟安排"
        )

    def plan(self, owner: str, wid: str, day: date) -> dict[str, Any]:
        config = self.config(owner, wid)
        queue = self.queue(owner, wid, day)
        completed = self.repo.completed_tasks(owner, wid) | {
            t["task_id"] for t in queue if t["status"] == "done"
        }
        existing = self.repo.daily_plan(owner, wid, day.isoformat())
        if existing and (
            existing.get("confirmed") or any(t["task_id"] in completed for t in existing["tasks"])
        ):
            proposed = existing
        else:
            remaining = config.minutes_per_day
            selected: list[dict[str, Any]] = []
            available = set(completed)
            pending = {t["task_id"]: (i, t) for i, t in enumerate(queue) if t["status"] != "done"}
            priority = {
                "recall": 4,
                "learn": 2,
                "practice": 3 if config.primary_goal in {"exam", "growth"} else 1,
            }
            while pending and remaining > 0:
                ready = [
                    (i, t)
                    for i, t in pending.values()
                    if set(t["requires"]) <= available and t["minutes"] <= remaining
                ]
                if not ready:
                    break
                _, task = min(ready, key=lambda item: (-priority.get(item[1]["kind"], 0), item[0]))
                selected.append(task)
                available.add(task["task_id"])
                del pending[task["task_id"]]
                remaining -= task["minutes"]
                if config.planning_support == "explore" or remaining <= 0:
                    break
            identity = f"{wid}:{day}:{config.fingerprint}:" + ":".join(
                t["task_id"] for t in selected
            )
            proposed = {
                "plan_id": "plan_" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                "day": day.isoformat(),
                "design_config": config.model_dump(),
                "design_fingerprint": config.fingerprint,
                "budget_minutes": config.minutes_per_day,
                "planning_support": config.planning_support,
                "needs_confirmation": config.planning_support == "collaborative",
                "confirmed": False,
                "revision": 0,
                "assignment_mode": {
                    "selfDirected": "choices",
                    "collaborative": "proposal",
                    "structured": "checklist",
                    "explore": "next_step",
                }[config.planning_support],
                "tasks": selected,
                "estimated_minutes": sum(t["minutes"] for t in selected),
            }
            proposed = self.repo.save_daily_plan(owner, wid, day.isoformat(), proposed)
        result = {
            **proposed,
            "tasks": [
                {**t, "status": "done" if t["task_id"] in completed else t["status"]}
                for t in proposed["tasks"]
            ],
            "remaining_task_count": sum(t["status"] != "done" for t in queue),
            "preferences_changed": proposed["design_fingerprint"] != config.fingerprint,
            "queue": queue,
            "adjustment_suggestions": self.feedback(owner, wid),
        }
        return result

    def confirm_plan(
        self, owner: str, wid: str, day: date, plan_id: str, task_ids: list[str], revision: int = 0
    ) -> dict[str, Any]:
        current = self.plan(owner, wid, day)
        if current["plan_id"] != plan_id:
            raise NativeConflict("今日计划已改变，请重新加载")
        if current.get("revision", 0) != revision:
            if (
                current.get("confirmed")
                and current.get("revision") == revision + 1
                and set(task_ids) == {t["task_id"] for t in current["tasks"]}
            ):
                return current
            raise NativeConflict("今日计划已在另一端修改，请重新加载")
        choices = {t["task_id"]: t for t in current["queue"]}
        if len(task_ids) != len(set(task_ids)) or any(t not in choices for t in task_ids):
            raise ValueError("计划中含有重复或不存在的任务")
        selected = [task for task in current["queue"] if task["task_id"] in set(task_ids)]
        satisfied = {tid for tid, task in choices.items() if task["status"] == "done"} | set(
            task_ids
        )
        if any(not set(t["requires"]) <= satisfied for t in selected):
            raise ValueError("请先安排该任务依赖的前序内容")
        if sum(t["minutes"] for t in selected) > current["budget_minutes"]:
            raise ValueError("所选任务超出每日时间预算")
        payload = {
            k: v
            for k, v in current.items()
            if k
            not in {
                "queue",
                "adjustment_suggestions",
                "remaining_task_count",
                "preferences_changed",
            }
        }
        payload.update(
            tasks=selected,
            estimated_minutes=sum(t["minutes"] for t in selected),
            confirmed=True,
            needs_confirmation=False,
            revision=revision + 1,
        )
        self.repo.save_daily_plan(
            owner,
            wid,
            day.isoformat(),
            payload,
            expected_plan_id=plan_id,
            expected_revision=revision,
        )
        return self.plan(owner, wid, day)

    def feedback(self, owner: str, wid: str) -> list[dict[str, Any]]:
        config = self.config(owner, wid)
        saved = {a["suggestion_id"]: a for a in self.repo.adjustments(owner, wid)}
        suggestions: list[dict[str, Any]] = []
        for resource in self.repo.resources(owner, wid):
            groups: dict[str, list[tuple[str, float]]] = defaultdict(list)
            if resource["book_id"] == native_catalog.BOOK_ID:
                for record in reversed(self.repo.catalog_practice(resource["id"])):
                    groups[record["chapter"]].append((record["id"], record["score"]))
            elif resource["session_id"]:
                session = self.services.assessment_repository.get_session(resource["session_id"])
                if session:
                    for index, observation in enumerate(session.profile.diagnostic_observations):
                        if (
                            observation.scoring_confidence >= 0.5
                            and observation.evidence_weight > 0
                        ):
                            for point in observation.knowledge_point_ids:
                                groups[point].append(
                                    (f"{session.session_id}:{index}", observation.score)
                                )
            for point, attempts in groups.items():
                recent = attempts[-3:]
                if len(recent) < 3 or any(score >= 0.5 for _, score in recent):
                    continue
                evidence_ids = [eid for eid, _ in recent]
                key = (
                    "suggestion_"
                    + hashlib.sha256(
                        f"{resource['id']}:{point}:{evidence_ids}".encode()
                    ).hexdigest()[:24]
                )
                if key in saved:
                    suggestions.append(saved[key])
                elif config.guidance_styles != ["examples", "stepByStep"]:
                    suggestions.append(
                        {
                            "suggestion_id": key,
                            "resource_id": resource["id"],
                            "point_id": point,
                            "state": "proposed",
                            "reason": "该知识点最近三次有可靠评分的作答均需补强，建议改用原文示例和分步解释。",
                            "evidence_ids": evidence_ids,
                            "proposed_overrides": {"guidance_styles": ["examples", "stepByStep"]},
                            "requires_confirmation": True,
                        }
                    )
        return suggestions


def _recall_due(completed: set[str], prefix: str, day: date) -> bool:
    previous = [date.fromisoformat(t[len(prefix) :]) for t in completed if t.startswith(prefix)]
    # Initial suggestion only; a real flashcard review uses its FSRS due date instead.
    return not previous or day >= max(previous) + timedelta(days=3)
