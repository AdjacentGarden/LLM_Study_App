from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app.assignments.service import list_mistakes
from app.core.errors import AppError
from app.schemas.books import LearningState, StudyPlan, StudyPlanRequest, StudyTask, StudyTaskUpdate
from app.services.artifact_store import read_chapters, read_lessons
from app.services.kv_store import _PersistedKVStore


_plans_kv = _PersistedKVStore("study_plans")
_tasks_kv = _PersistedKVStore("study_tasks")


def _plan_key(user_id: str, book_id: str) -> str:
    return f"{user_id}::{book_id}"


def _bounded_positive(value: int, fallback: int, minimum: int, maximum: int) -> int:
    if value < minimum:
        return fallback
    return min(value, maximum)


def _task_prefix(user_id: str, book_id: str) -> str:
    safe_user = "".join(char if char.isalnum() else "_" for char in user_id)[:40] or "anonymous"
    return f"task_{safe_user}_{book_id}"


def create_plan(book_id: str, payload: StudyPlanRequest) -> StudyPlan:
    chapters = read_chapters(book_id)
    lessons = read_lessons(book_id)
    user_id = payload.user_id or "anonymous"
    days = _bounded_positive(payload.days, fallback=14, minimum=1, maximum=90)
    daily_minutes = _bounded_positive(payload.daily_minutes, fallback=30, minimum=5, maximum=240)
    lesson_minutes = max(10, int(daily_minutes * 0.7))
    review_minutes = max(5, daily_minutes - lesson_minutes)

    tasks: list[StudyTask] = []
    day = 1
    if lessons:
        lesson_items = [
            {
                "key": lesson.chapter_id,
                "lesson_id": lesson.lesson_id,
                "title": lesson.title,
                "review_target": lesson.chapter_id,
            }
            for lesson in lessons
        ]
    else:
        lesson_items = [
            {
                "key": chapter.chapter_id,
                "lesson_id": f"lesson_{chapter.chapter_id}",
                "title": chapter.ai_title or chapter.source_title,
                "review_target": chapter.chapter_id,
            }
            for chapter in chapters
            if chapter.level <= 2
        ]

    for item in lesson_items:
        tasks.append(
            StudyTask(
                task_id=f"{_task_prefix(user_id, book_id)}_{item['key']}_lesson",
                user_id=user_id,
                book_id=book_id,
                day=day,
                title=item["title"],
                task_type="lesson_source_qa",
                minutes=lesson_minutes,
                lesson_id=item["lesson_id"],
            )
        )
        tasks.append(
            StudyTask(
                task_id=f"{_task_prefix(user_id, book_id)}_{item['key']}_review",
                user_id=user_id,
                book_id=book_id,
                day=min(day + 1, days),
                title=f"Review: {item['title']}",
                task_type="mistake_review",
                minutes=review_minutes,
                lesson_id=item["lesson_id"],
                review_target=item["review_target"],
            )
        )
        day = day + 1 if day < days else 1

    plan = StudyPlan(user_id=user_id, book_id=book_id, days=days, daily_minutes=daily_minutes, tasks=tasks)
    _plans_kv.upsert(_plan_key(user_id, book_id), plan.model_dump(mode="json"))
    for task in tasks:
        _tasks_kv.upsert(task.task_id, task.model_dump(mode="json"))
    return plan


def get_plan(book_id: str, user_id: str = "anonymous") -> StudyPlan | None:
    user_id = user_id or "anonymous"
    data = _plans_kv.get(_plan_key(user_id, book_id))
    if data is None:
        return None
    return StudyPlan.model_validate(data)


def _find_plan_for_task(task_id: str) -> tuple[StudyPlan, str, str] | None:
    data = _tasks_kv.get(task_id)
    if data is None:
        return None
    user_id = data.get("user_id", "")
    book_id = data.get("book_id", "")
    plan_data = _plans_kv.get(_plan_key(user_id, book_id))
    if plan_data is None:
        return None
    return StudyPlan.model_validate(plan_data), user_id, book_id


def update_task(task_id: str, payload: StudyTaskUpdate) -> StudyTask:
    located = _find_plan_for_task(task_id)
    if located is None:
        raise AppError("study_task_not_found", "study task not found", status_code=404)
    plan, user_id, book_id = located

    def _patch_task(current: dict | None) -> dict:
        data = dict(current or {})
        update = payload.model_dump(exclude_none=True)
        if not payload.weak_points:
            update.pop("weak_points", None)
        data.update(update)
        return data

    updated_data = _tasks_kv.update_in_place(task_id, _patch_task)
    # Save updated plan back: patch in-place
    for index, task in enumerate(plan.tasks):
        if task.task_id == task_id:
            updated = StudyTask.model_validate(updated_data)
            plan.tasks[index] = _append_adjustment_task_once(book_id, plan, updated, index)
            break
    _plans_kv.upsert(_plan_key(user_id, book_id), plan.model_dump(mode="json"))
    return StudyTask.model_validate(updated_data)


def _append_adjustment_task_once(book_id: str, plan: StudyPlan, task: StudyTask, _index: int) -> StudyTask:
    if task.score is None or task.score >= 60 or not task.weak_points:
        return task
    adjustment_id = f"{task.task_id}_adjustment"
    if any(existing.task_id == adjustment_id for existing in plan.tasks):
        return task
    adjustment = StudyTask(
        task_id=adjustment_id,
        user_id=task.user_id,
        book_id=task.book_id,
        day=min(task.day + 1, plan.days),
        title=f"Review weak points: {', '.join(task.weak_points[:3])}",
        task_type="mistake_review",
        minutes=max(5, int(plan.daily_minutes * 0.4)),
        lesson_id=task.lesson_id,
        review_target=task.review_target or task.lesson_id,
        status="pending",
        weak_points=task.weak_points,
        adjustment_reason="quiz_score_below_60",
    )
    plan.tasks.append(adjustment)
    _tasks_kv.upsert(adjustment_id, adjustment.model_dump(mode="json"))
    return task


def learning_state(user_id: str) -> LearningState:
    all_tasks: list[StudyTask] = [StudyTask.model_validate(item) for item in _tasks_kv.values() if item.get("user_id") == user_id]
    completed = [task for task in all_tasks if task.status == "done"]
    scored = [task.score for task in all_tasks if task.score is not None]
    weak_points = list(dict.fromkeys(point for task in all_tasks for point in task.weak_points))
    user_mistakes = list_mistakes(user_id=user_id)
    weak_points.extend(point for mistake in user_mistakes for point in mistake.knowledge_points if point not in weak_points)
    return LearningState(
        user_id=user_id,
        completed_tasks=len(completed),
        pending_tasks=len([task for task in all_tasks if task.status != "done"]),
        average_score=(sum(scored) / len(scored)) if scored else None,
        weak_points=weak_points,
        mistake_count=len(user_mistakes),
    )


def task_owner(task_id: str) -> str | None:
    data = _tasks_kv.get(task_id)
    if data is None:
        return None
    return data.get("user_id")


def clear_for_test() -> None:
    for key in list(_plans_kv.keys()):
        _plans_kv.remove(key)
    for key in list(_tasks_kv.keys()):
        _tasks_kv.remove(key)
    _plans_kv.clear_cache()
    _tasks_kv.clear_cache()