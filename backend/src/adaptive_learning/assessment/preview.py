"""Source-grounded course previews without claiming an assessment took place."""

import uuid
from datetime import UTC, datetime

from ..ingestion.models import BookStructure
from ..learning_preferences import GOAL_LABELS, LearningDesignConfig
from .item_generation import stable_knowledge_point_id
from .models import (
    InterviewPhase,
    InterviewSession,
    InterviewTurn,
    LearnerProfile,
    MasteryPosterior,
)


def create_preview_session(
    *,
    user_id: str,
    structure: BookStructure,
    book_id: str,
    learning_design: LearningDesignConfig,
) -> InterviewSession:
    now = datetime.now(UTC)
    profile = LearnerProfile(user_id=user_id, book_id=book_id, learning_design=learning_design)
    profile.goal = GOAL_LABELS[learning_design.primary_goal]
    profile.constraints.minutes_per_day = learning_design.minutes_per_day
    for chapter in structure.chapters:
        profile.chapter_mastery[chapter.chapter_id] = MasteryPosterior()
        for point in chapter.knowledge_points:
            profile.knowledge_mastery[stable_knowledge_point_id(chapter.chapter_id, point)] = (
                MasteryPosterior()
            )
    return InterviewSession(
        session_id="preview_" + uuid.uuid4().hex,
        purpose="preview",
        phase=InterviewPhase.PREVIEW_READY,
        profile=profile,
        pending_turn=InterviewTurn(
            turn_id="turn_" + uuid.uuid4().hex,
            phase=InterviewPhase.PREVIEW_READY,
            message="课程预览：未做诊断，掌握度未知。课程依据教材原文与课程偏好生成，后续实际练习会逐步校准掌握情况。",
            progress=1,
        ),
        chapter_options=[
            {"id": chapter.chapter_id, "label": chapter.title} for chapter in structure.chapters
        ],
        created_at=now,
        updated_at=now,
    )
