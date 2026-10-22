from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from ..assessment.item_generation import stable_knowledge_point_id
from ..assessment.models import LearnerProfile, MasteryPosterior
from ..ingestion.models import ChapterDraft, SourceQuote
from ..learning_preferences import LearningDesignConfig
from .models import (
    ChapterLearningBundle,
    Flashcard,
    KnowledgePoint,
    LearningUnit,
    LessonSection,
    PersonalizationDecision,
    PracticeItem,
    SourceCitation,
    TeachingDepth,
)


def profile_fingerprint(profile: LearnerProfile) -> str:
    included = {
        "goal",
        "focus_chapter_ids",
        "background_level",
        "constraints",
        "chapter_mastery",
        "knowledge_mastery",
        "misconception_candidates",
        "confidence_brier_sum",
        "confidence_observations",
    }
    if profile.learning_design is not None:
        included.add("learning_design")
        # Provenance is audit metadata; identical effective preferences reuse a version.
        profile = profile.model_copy(
            update={
                "learning_design": profile.learning_design.model_copy(
                    update={"sources": {}, "course_revision": 0}
                )
            }
        )
    payload = profile.model_dump_json(
        include=included,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def chapter_fingerprint(chapter: ChapterDraft) -> str:
    # Invalidate compiled drafts when teaching-text semantics change.
    return hashlib.sha256(
        ("complete-coverage-units-v3:" + chapter.model_dump_json()).encode()
    ).hexdigest()


class ChapterCourseCompiler:
    """Compiles a profile-specific course from verified chapter evidence."""

    def compile(
        self,
        *,
        chapter: ChapterDraft,
        profile: LearnerProfile,
        decision: PersonalizationDecision,
        version: int = 1,
        instance_key: str = "",
    ) -> ChapterLearningBundle:
        if version < 1:
            raise ValueError("course version must be positive")
        candidates = self._rank_points(chapter, profile)
        if not candidates:
            raise ValueError("chapter has no evidence-backed knowledge points")
        # Time controls the number of points in a unit, never silently drops the rest.
        selected = candidates
        design = profile.learning_design
        source_hash = chapter_fingerprint(chapter)
        profile_hash = profile_fingerprint(profile)
        course_digest = hashlib.sha256(
            f"{instance_key}:{chapter.chapter_id}:{source_hash}:{profile_hash}".encode()
        ).hexdigest()[:20]
        course_id = f"course_{course_digest}"

        knowledge_points: list[KnowledgePoint] = []
        flashcards: list[Flashcard] = []
        practices: list[PracticeItem] = []
        reading: list[LessonSection] = []
        examples: list[LessonSection] = []
        seen_quotes: set[tuple[int, str]] = set()

        for point_id, label, quotes, posterior in selected:
            stable_digest = hashlib.sha256(f"{instance_key}:{point_id}".encode()).hexdigest()[:20]
            citations = [_citation(quote) for quote in quotes]
            knowledge_points.append(
                KnowledgePoint(
                    point_id=point_id,
                    title=_point_title(label),
                    explanation=label,
                    importance="优先"
                    if posterior.evidence_count == 0 or posterior.mean < 0.55
                    else "复习",
                    mastery=posterior.mean,
                    state=_mastery_state(posterior),
                    citations=citations,
                )
            )
            flashcards.append(
                Flashcard(
                    card_id=f"card_{stable_digest}",
                    point_id=point_id,
                    front=_question_for(label, decision.depth),
                    back=label,
                    reason_for_user=_card_reason(posterior, decision),
                    citations=citations,
                    source=citations[0],
                )
            )
            practices.append(
                PracticeItem(
                    item_id=f"practice_{stable_digest}",
                    point_id=point_id,
                    prompt=_designed_practice(label, decision.depth, design),
                    expected_answer=label,
                    rubric=_rubric(label),
                    difficulty={
                        TeachingDepth.FOUNDATION: -0.5,
                        TeachingDepth.STANDARD: 0.2,
                        TeachingDepth.ADVANCED: 0.9,
                    }[decision.depth],
                    estimated_seconds={
                        TeachingDepth.FOUNDATION: 50,
                        TeachingDepth.STANDARD: 70,
                        TeachingDepth.ADVANCED: 100,
                    }[decision.depth],
                    citations=citations,
                )
            )
            for quote in quotes:
                key = (quote.page_number, quote.quote)
                if key in seen_quotes:
                    continue
                seen_quotes.add(key)
                reading.append(
                    LessonSection(
                        title=f"第 {quote.page_number} 页 · 可核验原文",
                        content=quote.quote,
                        purpose=f"用于理解“{_point_title(label)}”",
                        citations=[_citation(quote)],
                    )
                )
            if quotes and len(examples) < 3:
                quote = quotes[0]
                examples.append(
                    LessonSection(
                        title=f"证据拆解 {len(examples) + 1}",
                        content=f"先阅读第 {quote.page_number} 页这段原文，再用自己的话说明它如何支持“{_point_title(label)}”。",
                        purpose="把记忆陈述转成可解释的证据关系",
                        citations=[_citation(quote)],
                    )
                )

        # All cited reading remains available across units, including later units.
        estimated = max(
            5,
            round(sum(len(section.content) for section in reading) / 300)
            + round(len(flashcards) * 0.7)
            + round(len(practices) * 1.5),
        )
        warnings = []
        if len(selected) < len(chapter.knowledge_points):
            warnings.append(
                f"{len(chapter.knowledge_points) - len(selected)} 个知识点缺少可核验原文，保留为待补证内容。"
            )
        point_limit = self._point_limit(profile, decision.depth, len(candidates))
        units = _learning_units(
            chapter,
            knowledge_points,
            flashcards,
            practices,
            design or LearningDesignConfig(minutes_per_day=profile.constraints.minutes_per_day),
            point_limit,
        )
        return ChapterLearningBundle(
            course_id=course_id,
            version=version,
            chapter_id=chapter.chapter_id,
            chapter_title=chapter.title,
            decision=decision,
            opening=_opening(profile, decision, len(selected)),
            summary=chapter.summary,
            original_reading=reading,
            knowledge_points=knowledge_points,
            flashcards=flashcards,
            worked_examples=examples,
            checkpoint_questions=[item.prompt for item in practices[:3]],
            practice_items=practices,
            generated_from_block_ids=chapter.source_block_ids,
            profile_fingerprint=profile_hash,
            source_fingerprint=source_hash,
            estimated_minutes=min(600, estimated),
            created_at=datetime.now(UTC),
            unresolved_source_warnings=warnings,
            design_config=design,
            design_fingerprint=design.fingerprint if design else "",
            units=units,
            coverage_point_ids=[p.point_id for p in knowledge_points],
        )

    @staticmethod
    def _rank_points(
        chapter: ChapterDraft, profile: LearnerProfile
    ) -> list[tuple[str, str, list[SourceQuote], MasteryPosterior]]:
        ranked: list[tuple[str, str, list[SourceQuote], MasteryPosterior]] = []
        for label in chapter.knowledge_points:
            quotes = chapter.knowledge_point_evidence.get(label, [])
            if not quotes:
                continue
            point_id = stable_knowledge_point_id(chapter.chapter_id, label)
            posterior = profile.knowledge_mastery.get(point_id, MasteryPosterior())
            ranked.append((point_id, label, quotes, posterior))
        return sorted(
            ranked,
            key=lambda item: (
                _priority_band(item[3]),
                item[3].mean,
                -len(item[2]),
                item[1],
            ),
        )

    @staticmethod
    def _point_limit(profile: LearnerProfile, depth: TeachingDepth, available: int) -> int:
        depth_limit = {
            TeachingDepth.FOUNDATION: 12,
            TeachingDepth.STANDARD: 8,
            TeachingDepth.ADVANCED: 6,
        }[depth]
        time_limit = max(3, profile.constraints.minutes_per_day // 4)
        return min(available, depth_limit, time_limit)


def _designed_practice(
    label: str, depth: TeachingDepth, design: LearningDesignConfig | None
) -> str:
    if design is None:
        return _practice_prompt(label, depth)
    subject = f"“{label}”" if len(label) <= 100 else "本节知识点"
    if design.primary_goal == "exam":
        return f"依据教材说明{subject}的关键条件，并指出解题时应避免的误读。"
    if design.primary_goal == "growth":
        return f"结合本节给出的原文情境，解释{subject}的含义和适用条件。"
    if design.primary_goal == "interest":
        return f"用自己的话说明{subject}，并指出原文中最让你好奇的一处。"
    return _practice_prompt(label if len(label) <= 100 else "本节知识点", depth)


def _learning_units(
    chapter: ChapterDraft,
    points: list[KnowledgePoint],
    cards: list[Flashcard],
    practices: list[PracticeItem],
    design: LearningDesignConfig | None,
    point_limit: int,
) -> list[LearningUnit]:
    config = design or LearningDesignConfig()
    cap = config.segment_minutes
    units: list[LearningUnit] = []
    groups: list[list[int]] = []
    current: list[int] = []
    cost = 0
    estimates = [
        2
        + (practices[i].estimated_seconds + 59) // 60
        + len(p.explanation) // 150
        + ("examples" in config.guidance_styles)
        for i, p in enumerate(points)
    ]
    for index, minutes in enumerate(estimates):
        if current and (cost + minutes > cap or len(current) >= point_limit):
            groups.append(current)
            current, cost = [], 0
        current.append(index)
        cost += minutes
    if current:
        groups.append(current)
    selected_kinds = {"key_points", "checkpoint"}
    selected_kinds.update(
        {
            "keyPoints": "key_points",
            "principles": "explanation",
            "examples": "evidence_example",
            "stepByStep": "steps",
        }[s]
        for s in config.guidance_styles
    )
    if not config.guidance_styles:
        selected_kinds.add("explanation")
    for indices in groups:
        chosen = [points[i] for i in indices]
        point_ids = [p.point_id for p in chosen]
        digest = hashlib.sha256(
            (chapter.chapter_id + ":" + ":".join(point_ids)).encode()
        ).hexdigest()[:20]
        sections: list[LessonSection] = []
        for kind in config.presentation_order:
            if kind not in selected_kinds:
                continue
            for i in indices:
                point, question = points[i], practices[i]
                quote = point.citations[0].quote
                content = {
                    "key_points": point.explanation,
                    "explanation": f"对照原文，区分主体、成立条件与结论，再说明它们之间的关系。\n{quote}",
                    "evidence_example": f"先从这段教材原文入手，说明它如何支持本节知识点：\n{quote}",
                    "steps": f"1. 阅读原文：{quote}\n2. 找出支持本节知识点的条件和关系。\n3. 合上原文，用自己的话复述。",
                    "checkpoint": question.prompt,
                }[kind]
                title = {
                    "key_points": "本节重点",
                    "explanation": "原理与条件",
                    "evidence_example": "原文例证",
                    "steps": "分步理解",
                    "checkpoint": "检查理解",
                }[kind]
                sections.append(
                    LessonSection(
                        title=title,
                        content=content,
                        purpose=title,
                        citations=point.citations,
                        kind=kind,
                        point_id=point.point_id,
                    )
                )
        if "memory" in config.learning_barriers:
            for point in chosen:
                sections.append(
                    LessonSection(
                        title="主动回忆",
                        content="不看教材，复述本节知识点，再打开原文核对遗漏的条件。",
                        purpose="检查是否能独立回忆",
                        citations=point.citations,
                        kind="recall",
                        point_id=point.point_id,
                    )
                )
        units.append(
            LearningUnit(
                unit_id=f"unit_{digest}",
                title=chosen[0].title
                if len(chosen) == 1
                else f"{chapter.title} · 单元 {len(units) + 1}",
                objectives=[p.explanation for p in chosen],
                knowledge_point_ids=point_ids,
                sections=sections,
                flashcard_ids=[cards[i].card_id for i in indices],
                practice_item_ids=[practices[i].item_id for i in indices],
                estimated_minutes=sum(estimates[i] for i in indices),
                prerequisite_check="先确认能说明原文中的基本术语；不清楚时回看相邻原文。"
                if "understanding" in config.learning_barriers
                else None,
            )
        )
    return units


def _citation(quote: SourceQuote) -> SourceCitation:
    return SourceCitation(page_number=quote.page_number, quote=quote.quote)


def _point_title(label: str) -> str:
    # A comma/colon can be part of a complete concept, formula, or condition.
    # Layout wraps the full source label; never silently cut meaning for aesthetics.
    return label.strip().rstrip("。") or "本章知识点"


def _question_for(label: str, depth: TeachingDepth) -> str:
    # Draft only: never cut a concept at a verb or an arbitrary character count.
    # API publication requires evidence-grounded LLM repair and review.
    subject = label.strip().rstrip("。！？")
    subject = subject or _point_title(label)
    if depth == TeachingDepth.FOUNDATION:
        return f"{subject}是什么？说出最关键的一点。"
    if depth == TeachingDepth.ADVANCED:
        return f"不看原文，说明{subject}的关键关系与成立条件。"
    return f"请用自己的话解释{subject}。"


def _practice_prompt(label: str, depth: TeachingDepth) -> str:
    title = _point_title(label)
    if depth == TeachingDepth.FOUNDATION:
        return f"请解释“{title}”，至少说出一个关键特征。"
    if depth == TeachingDepth.ADVANCED:
        return f"请完整说明“{title}”中的关键关系，并指出容易混淆之处。"
    return f"请用自己的话完整说明“{title}”。"


def _rubric(label: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"[，；。]", label) if part.strip()]
    return (parts or [label.strip()])[:4]


def _mastery_state(posterior: MasteryPosterior) -> str:
    if posterior.evidence_count == 0:
        return "尚未验证"
    if posterior.mean < 0.45:
        return "需要补强"
    if posterior.mean < 0.7:
        return "正在掌握"
    return "较为熟悉"


def _priority_band(posterior: MasteryPosterior) -> int:
    if posterior.evidence_count > 0 and posterior.mean < 0.45:
        return 0
    if posterior.evidence_count == 0:
        return 1
    if posterior.mean < 0.7:
        return 2
    return 3


def _card_reason(posterior: MasteryPosterior, decision: PersonalizationDecision) -> str:
    if posterior.evidence_count == 0:
        return "这部分还没有实际作答证据，先用闪卡建立基线。"
    if posterior.mean < 0.55:
        return "当前回答显示这里需要优先巩固。"
    if "信心校准" in decision.emphasis:
        return "用短回忆检查掌握感是否与实际表现一致。"
    return "用间隔回忆保持已经形成的理解。"


def _opening(profile: LearnerProfile, decision: PersonalizationDecision, point_count: int) -> str:
    depth_label = {
        TeachingDepth.FOUNDATION: "从术语和关键步骤开始",
        TeachingDepth.STANDARD: "把核心概念连接成结构",
        TeachingDepth.ADVANCED: "减少重复讲解，重点检查边界与迁移",
    }[decision.depth]
    goal = f"围绕你的目标“{profile.goal[:50]}”" if profile.goal else "围绕本章主线"
    return f"这次会{goal}，{depth_label}，先处理 {point_count} 个最值得学习的知识点。"


ChapterBundleGenerator = ChapterCourseCompiler
