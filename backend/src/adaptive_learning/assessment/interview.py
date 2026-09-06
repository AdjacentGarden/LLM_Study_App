from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from typing import Protocol

from .engine import AdaptiveAssessmentEngine, score_choice
from .models import (
    AssessmentResponse,
    DiagnosticItem,
    EvidenceKind,
    InterviewPhase,
    InterviewSession,
    InterviewTurn,
    LearnerConstraints,
    LearnerProfile,
    MasteryPosterior,
    ProfileEvidence,
    PublicDiagnosticItem,
    ResponseType,
    ScoredEvidence,
)
from .question_quality import has_answer_polarity_conflict


class AnswerScorer(Protocol):
    def score(self, item: DiagnosticItem, response: AssessmentResponse) -> ScoredEvidence: ...


def goal_options() -> list[dict[str, str]]:
    return [
        {"id": "exam", "label": "准备考试或测验"},
        {"id": "foundation", "label": "从基础开始系统掌握"},
        {"id": "overview", "label": "快速理解全书重点"},
        {"id": "application", "label": "解决实际问题或迁移应用"},
    ]


class InterviewOrchestrator:
    """A conversational shell around a deterministic, auditable diagnostic engine."""

    def __init__(
        self, engine: AdaptiveAssessmentEngine, open_answer_scorer: AnswerScorer | None = None
    ):
        self.engine = engine
        self.open_answer_scorer = open_answer_scorer

    def start(
        self,
        *,
        user_id: str,
        book_id: str,
        book_title: str,
        chapter_titles: list[str],
        items: list[DiagnosticItem],
        chapter_options: list[dict[str, str]] | None = None,
    ) -> InterviewSession:
        now = datetime.now(UTC)
        profile = LearnerProfile(user_id=user_id, book_id=book_id)
        for item in items:
            profile.chapter_mastery.setdefault(item.chapter_id, MasteryPosterior())
            for point_id in item.knowledge_point_ids:
                profile.knowledge_mastery.setdefault(point_id, MasteryPosterior())
        overview = "、".join(chapter_titles[:5])
        suffix = f"等 {len(chapter_titles)} 章" if len(chapter_titles) > 5 else ""
        turn = self._turn(
            phase=InterviewPhase.BOOK_BRIEFING,
            message=f"我已读完《{book_title}》的结构。全书主要包括 {overview}{suffix}。接下来我会用几次简短交流了解你的目标和基础，再为每章调整讲解深度。",
            question="你这次学习这本书的主要目标是什么？",
            response_type=ResponseType.SINGLE_CHOICE,
            options=goal_options(),
            why="目标会决定案例、练习和章节优先级。",
            progress=0.05,
        )
        return InterviewSession(
            session_id=f"interview_{uuid.uuid4().hex}",
            profile=profile,
            pending_turn=turn,
            chapter_options=chapter_options
            or [
                {"id": f"chapter_{index + 1}", "label": title}
                for index, title in enumerate(chapter_titles)
            ],
            created_at=now,
            updated_at=now,
        )

    def answer_profile_question(
        self,
        session: InterviewSession,
        answer: str,
        *,
        chapter_options: list[dict[str, str]] | None = None,
        selected_option_ids: list[str] | None = None,
    ) -> InterviewTurn:
        if session.phase == InterviewPhase.BOOK_BRIEFING:
            goals = {option["id"]: option["label"] for option in goal_options()}
            selected = (selected_option_ids or [])[:1]
            goal_id = selected[0] if selected else ""
            if goal_id not in goals:
                raise ValueError("a learning goal option is required")
            goal = goals[goal_id]
            session.profile.goal = goal
            self._declared_fact(session.profile, "goal", goal_id, confidence=1)
            session.phase = InterviewPhase.GOAL_DISCOVERY
            turn = self._turn(
                phase=session.phase,
                message="明白了。我会把与你目标最相关的章节放到前面。",
                question="你最想优先学习哪些章节？可以多选，也可以选“由我判断”。",
                response_type=ResponseType.MULTIPLE_CHOICE,
                options=[
                    *(chapter_options or session.chapter_options),
                    {"id": "auto", "label": "由我判断"},
                ],
                why="聚焦章节会获得更细的诊断和更丰富的练习。",
                progress=0.12,
            )
        elif session.phase == InterviewPhase.GOAL_DISCOVERY:
            allowed = {option["id"] for option in (chapter_options or session.chapter_options)}
            selected = selected_option_ids or [part.strip() for part in answer.split(",")]
            session.profile.focus_chapter_ids = [
                part for part in selected if part in allowed and part != "auto"
            ]
            self._declared_fact(
                session.profile,
                "focus_chapters",
                ",".join(session.profile.focus_chapter_ids) or "auto",
                confidence=1,
            )
            session.phase = InterviewPhase.BACKGROUND_DISCOVERY
            turn = self._turn(
                phase=session.phase,
                message="最后确认一下你的起点。这里没有好坏之分，只用于避免内容过浅或过难。",
                question="你过去对这本书涉及的内容接触到什么程度？",
                response_type=ResponseType.SINGLE_CHOICE,
                options=[
                    {"id": "new", "label": "基本没学过"},
                    {"id": "some", "label": "零散接触过"},
                    {"id": "systematic", "label": "系统学过"},
                    {"id": "applied", "label": "能在实际中运用"},
                ],
                why="自述只作为轻量先验，后面的实际回答会校正它。",
                progress=0.17,
            )
        elif session.phase == InterviewPhase.BACKGROUND_DISCOVERY:
            selected = (selected_option_ids or [answer.strip()])[:1]
            level = selected[0] if selected else "unknown"
            if level not in {"new", "some", "systematic", "applied"}:
                level = "unknown"
            session.profile.background_level = level
            session.profile.declared_background = answer.strip()
            self._apply_background_prior(session.profile, level)
            self._declared_fact(session.profile, "background", level, confidence=0.7)
            session.phase = InterviewPhase.CONSTRAINT_DISCOVERY
            turn = self._turn(
                phase=session.phase,
                message="了解。这个判断不是成绩，只是让我先从更合适的位置开始。",
                question="你通常每天愿意为这本书留出多少时间？",
                response_type=ResponseType.SINGLE_CHOICE,
                options=[
                    {"id": "15", "label": "15 分钟"},
                    {"id": "30", "label": "30 分钟"},
                    {"id": "45", "label": "45 分钟"},
                    {"id": "60", "label": "60 分钟以上"},
                ],
                why="可用时间决定每次学习单元的长度，不会改变知识判断。",
                progress=0.21,
            )
        elif session.phase == InterviewPhase.CONSTRAINT_DISCOVERY:
            selected = (selected_option_ids or [answer.strip()])[:1]
            match = re.search(r"\d+", selected[0] if selected else answer)
            minutes = min(360, max(5, int(match.group()) if match else 30))
            session.profile.constraints.minutes_per_day = minutes
            self._declared_fact(session.profile, "minutes_per_day", str(minutes), confidence=1)
            session.phase = InterviewPhase.ADAPTIVE_DIAGNOSIS
            turn = self._turn(
                phase=session.phase,
                message="接下来只需做选择题。我会先从基础题开始，答得稳再逐步加深；遇到困难就回查基础，最后为你推荐需要重点学习的章节和闪卡。不确定时请如实标注信心。",
                question=None,
                response_type=None,
                why="动态诊断用于定位真正需要讲解的知识，不用于给你排名。",
                progress=0.25,
            )
        else:
            raise ValueError(f"phase {session.phase} does not accept profile answers")
        session.pending_turn = turn
        session.updated_at = datetime.now(UTC)
        return turn

    def next_diagnostic(
        self,
        session: InterviewSession,
        items: list[DiagnosticItem],
    ) -> InterviewTurn:
        if session.phase != InterviewPhase.ADAPTIVE_DIAGNOSIS:
            raise ValueError("session is not in adaptive diagnosis")
        if session.pending_turn is not None and session.pending_turn.item is not None:
            return session.pending_turn
        if self.engine.should_stop(session.profile, len(session.asked_item_ids)):
            return self._profile_confirmation(session)
        valid_items = [item for item in items if not has_answer_polarity_conflict(item)]
        item = self.engine.select_next(session.profile, valid_items, set(session.asked_item_ids))
        if item is None:
            return self._profile_confirmation(session)
        session.asked_item_ids.append(item.item_id)
        progress = 0.25 + 0.6 * min(1, len(session.asked_item_ids) / self.engine.max_items)
        ordered_options = sorted(
            enumerate(item.options),
            key=lambda pair: hashlib.sha256(
                f"{session.session_id}:{item.item_id}:{pair[0]}".encode()
            ).digest(),
        )
        public_item = PublicDiagnosticItem.from_private(item)
        public_item.options = [option for _, option in ordered_options]
        turn = self._turn(
            phase=session.phase,
            message="这道题会帮助我缩小当前最不确定的部分。",
            question=item.prompt,
            response_type=item.response_type,
            options=[
                {"id": str(index), "label": option} for index, option in ordered_options
            ],
            item=public_item,
            why="这道题能补足当前最缺少依据的知识区域，并兼顾你的学习重点。",
            progress=progress,
        )
        session.pending_turn = turn
        session.updated_at = datetime.now(UTC)
        return turn

    def record_diagnostic(
        self,
        session: InterviewSession,
        response: AssessmentResponse,
        items: list[DiagnosticItem],
    ) -> ScoredEvidence:
        pending = session.pending_turn
        if pending is None or pending.item is None or pending.item.item_id != response.item_id:
            raise ValueError("response does not match the pending diagnostic item")
        item = next(
            (candidate for candidate in items if candidate.item_id == response.item_id), None
        )
        if item is None:
            raise ValueError("private diagnostic item is unavailable")
        if has_answer_polarity_conflict(item):
            # A previously saved pending question may predate the quality gate.
            # Let the session advance, but never count this answer as evidence.
            evidence = ScoredEvidence(score=0.5, scoring_confidence=0, needs_follow_up=False)
        elif item.response_type in {ResponseType.SINGLE_CHOICE, ResponseType.MULTIPLE_CHOICE}:
            evidence = score_choice(item, response)
        elif self.open_answer_scorer is not None:
            evidence = self.open_answer_scorer.score(item, response)
        else:
            evidence = ScoredEvidence(score=0.5, scoring_confidence=0, needs_follow_up=True)
        if evidence.scoring_confidence > 0:
            self.engine.update_profile(session.profile, item, response, evidence)
        session.responses.append(response)
        if evidence.needs_follow_up and item.item_id not in session.follow_up_item_ids:
            session.follow_up_item_ids.append(item.item_id)
            session.pending_turn = self._turn(
                phase=InterviewPhase.ADAPTIVE_DIAGNOSIS,
                message="这段回答有不止一种合理理解，我还不能可靠地把它写进你的画像。",
                question=evidence.follow_up_question
                or "能否再举一个例子，说明你判断中最关键的一步？",
                response_type=ResponseType.EXPLANATION,
                item=PublicDiagnosticItem.from_private(item),
                why="澄清一次比把模型的猜测当成你的真实掌握更可靠。",
                progress=pending.progress,
            )
        else:
            session.pending_turn = None
        session.updated_at = datetime.now(UTC)
        return evidence

    def confirm_profile(self, session: InterviewSession) -> InterviewTurn:
        if session.phase != InterviewPhase.PROFILE_CONFIRMATION:
            raise ValueError("profile is not ready for confirmation")
        session.phase = InterviewPhase.COMPLETE
        turn = self._turn(
            phase=session.phase,
            message="画像已确认。后续每次练习和提问都会继续更新掌握度，课程不是一次生成后就固定不变。",
            progress=1,
        )
        session.pending_turn = turn
        session.updated_at = datetime.now(UTC)
        return turn

    def revise_profile(self, session: InterviewSession) -> InterviewTurn:
        if session.phase != InterviewPhase.PROFILE_CONFIRMATION:
            raise ValueError("profile is not ready for revision")
        session.phase = InterviewPhase.BOOK_BRIEFING
        turn = self._turn(
            phase=session.phase,
            message="可以。我们从学习目标重新选择，之前的小测证据会保留，不需要重复作答。",
            question="你这次学习这本书的主要目标是什么？",
            response_type=ResponseType.SINGLE_CHOICE,
            options=goal_options(),
            why="重新选择目标会调整章节优先级，但不会抹掉已经完成的小测结果。",
            progress=0.05,
        )
        session.pending_turn = turn
        session.updated_at = datetime.now(UTC)
        return turn

    def _profile_confirmation(self, session: InterviewSession) -> InterviewTurn:
        session.phase = InterviewPhase.PROFILE_CONFIRMATION
        known = sum(
            value.evidence_count > 0 for value in session.profile.knowledge_mastery.values()
        )
        total = max(1, len(session.profile.knowledge_mastery))
        labels = {option["id"]: option["label"] for option in session.chapter_options}
        measured = [
            (labels.get(chapter_id, "相关章节"), posterior.mean)
            for chapter_id, posterior in session.profile.chapter_mastery.items()
            if posterior.evidence_count > 0
        ]
        stronger = [label for label, mean in measured if mean >= 0.65][:3]
        priorities = [
            label for label, mean in sorted(measured, key=lambda pair: pair[1]) if mean < 0.55
        ][:3]
        findings: list[str] = []
        if stronger:
            findings.append(f"目前表现较稳的是{'、'.join(stronger)}")
        if priorities:
            findings.append(f"建议优先补强{'、'.join(priorities)}")
        if not priorities:
            findings.append("暂未发现明显薄弱章，后续练习会继续验证")
        unknown = max(0, total - known)
        if unknown:
            findings.append(f"另有 {unknown} 个知识点会在正式学习中继续校准")
        finding_text = "；".join(findings)
        turn = self._turn(
            phase=session.phase,
            message=f"初步画像已形成：已采集 {known}/{total} 个关键知识点的作答证据。{finding_text}。请确认目标、背景和掌握判断；任何一项都可以修改。",
            question="这个画像符合你的实际情况吗？",
            response_type=ResponseType.SINGLE_CHOICE,
            options=[
                {"id": "confirm", "label": "符合，生成课程"},
                {"id": "edit", "label": "我要修改"},
            ],
            why="用户确认是防止系统把推测当事实的最后一道门。",
            progress=0.9,
        )
        session.pending_turn = turn
        session.updated_at = datetime.now(UTC)
        return turn

    @staticmethod
    def _turn(
        *,
        phase: InterviewPhase,
        message: str,
        question: str | None = None,
        response_type: ResponseType | None = None,
        options: list[dict[str, str]] | None = None,
        item: PublicDiagnosticItem | None = None,
        why: str | None = None,
        progress: float = 0,
    ) -> InterviewTurn:
        return InterviewTurn(
            turn_id=f"turn_{uuid.uuid4().hex}",
            phase=phase,
            message=message,
            question=question,
            response_type=response_type,
            options=options or [],
            item=item,
            why_asked=why,
            progress=progress,
        )

    @staticmethod
    def _declared_fact(
        profile: LearnerProfile, field: str, value: str, *, confidence: float
    ) -> None:
        profile.evidence.append(
            ProfileEvidence(
                evidence_id=f"evidence_{uuid.uuid4().hex}",
                kind=EvidenceKind.DECLARED,
                field=field,
                value=value,
                confidence=confidence,
                observed_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _apply_background_prior(profile: LearnerProfile, level: str) -> None:
        priors = {
            "new": (1.0, 1.8),
            "some": (1.2, 1.2),
            "systematic": (1.6, 1.0),
            "applied": (1.8, 1.0),
            "unknown": (1.0, 1.0),
        }
        alpha, beta = priors[level]
        for posterior in [
            *profile.knowledge_mastery.values(),
            *profile.chapter_mastery.values(),
        ]:
            if posterior.evidence_count == 0:
                posterior.alpha = alpha
                posterior.beta = beta


def parse_constraints(minutes_per_day: int, target_date: str | None = None) -> LearnerConstraints:
    return LearnerConstraints(minutes_per_day=minutes_per_day, target_date=target_date)
