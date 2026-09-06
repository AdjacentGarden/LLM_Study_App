from __future__ import annotations

from ..assessment.models import LearnerProfile, MasteryPosterior
from .models import PersonalizationDecision, TeachingDepth


class PersonalizationPolicy:
    """Pure policy layer: the same evidence always produces the same learning strategy."""

    def decide(self, profile: LearnerProfile, chapter_id: str) -> PersonalizationDecision:
        posterior = profile.chapter_mastery.get(chapter_id, MasteryPosterior())
        mastery = posterior.mean
        confidence = self._posterior_confidence(posterior)
        is_focus = chapter_id in profile.focus_chapter_ids
        calibration_error = profile.calibration_error or 0

        if mastery < 0.42 or confidence < 0.28:
            depth = TeachingDepth.FOUNDATION
            scaffolds = ["先备知识", "分步解释", "术语随文解释", "低门槛检验"]
            difficulty = 0.3
        elif mastery < 0.76:
            depth = TeachingDepth.STANDARD
            scaffolds = ["概念联系", "典型例题", "即时检验"]
            difficulty = 0.55
        else:
            depth = TeachingDepth.ADVANCED
            scaffolds = ["边界条件", "反例辨析", "迁移任务"]
            difficulty = 0.78

        emphasis: list[str] = []
        if is_focus:
            emphasis.append("用户重点章节")
        if calibration_error > 0.22:
            emphasis.append("信心校准")
        if profile.misconception_candidates:
            emphasis.append("易错观念澄清")
        if profile.goal:
            emphasis.append(f"贴合目标：{profile.goal[:40]}")
        if not emphasis:
            emphasis.append("建立章节整体结构")

        reason = (
            f"章节掌握估计为 {mastery:.0%}，证据置信度为 {confidence:.0%}；"
            f"因此采用{self._depth_label(depth)}内容，并按可用时间控制单元长度。"
        )
        return PersonalizationDecision(
            chapter_id=chapter_id,
            depth=depth,
            assumed_mastery=mastery,
            evidence_confidence=confidence,
            emphasis=emphasis,
            scaffolds=scaffolds,
            exercise_difficulty=difficulty,
            explanation=reason,
        )

    @staticmethod
    def _posterior_confidence(posterior: MasteryPosterior) -> float:
        concentration = posterior.alpha + posterior.beta
        evidence_signal = min(1.0, posterior.evidence_count / 3)
        concentration_signal = min(1.0, max(0.0, (concentration - 2) / 5))
        return 0.65 * evidence_signal + 0.35 * concentration_signal

    @staticmethod
    def _depth_label(depth: TeachingDepth) -> str:
        return {
            TeachingDepth.FOUNDATION: "基础支架型",
            TeachingDepth.STANDARD: "结构理解型",
            TeachingDepth.ADVANCED: "迁移挑战型",
        }[depth]
