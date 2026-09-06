from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime

from .models import (
    AssessmentResponse,
    DiagnosticItem,
    DiagnosticObservation,
    EvidenceKind,
    LearnerProfile,
    MasteryPosterior,
    ProfileEvidence,
    ResponseType,
    ScoredEvidence,
)


def _logistic(value: float) -> float:
    if value >= 0:
        exponent = math.exp(-value)
        return 1 / (1 + exponent)
    exponent = math.exp(value)
    return exponent / (1 + exponent)


def irt_probability(theta: float, item: DiagnosticItem) -> float:
    return _logistic(item.discrimination * (theta - item.difficulty))


def irt_fisher_information(theta: float, item: DiagnosticItem) -> float:
    probability = irt_probability(theta, item)
    return item.discrimination**2 * probability * (1 - probability)


def posterior_expected_variance(posterior: MasteryPosterior) -> float:
    probability = posterior.beta_mean
    success = MasteryPosterior(alpha=posterior.alpha + 1, beta=posterior.beta)
    failure = MasteryPosterior(alpha=posterior.alpha, beta=posterior.beta + 1)
    return probability * success.variance + (1 - probability) * failure.variance


def expected_information_gain(posterior: MasteryPosterior) -> float:
    return max(0.0, posterior.variance - posterior_expected_variance(posterior))


def update_knowledge_trace(
    posterior: MasteryPosterior,
    *,
    score: float,
    evidence_weight: float,
    learning_rate: float = 0.12,
    guess_rate: float = 0.2,
    slip_rate: float = 0.1,
) -> float:
    """Apply a soft-evidence Bayesian Knowledge Tracing update.

    The Beta posterior remains the auditable evidence counter. This sequential
    estimate adds the distinction BKT needs between a lucky guess, a slip and
    genuine learning after an attempt.
    """

    prior = posterior.mean
    known_likelihood = score * (1 - slip_rate) + (1 - score) * slip_rate
    unknown_likelihood = score * guess_rate + (1 - score) * (1 - guess_rate)
    normalizer = prior * known_likelihood + (1 - prior) * unknown_likelihood
    observed = prior if normalizer <= 0 else prior * known_likelihood / normalizer
    reliability = 1 - math.exp(-max(0.0, evidence_weight))
    blended = prior + reliability * (observed - prior)
    learned = blended + (1 - blended) * learning_rate * reliability
    posterior.tracked_mastery = min(0.995, max(0.005, learned))
    return posterior.tracked_mastery


class AdaptiveAssessmentEngine:
    """Interpretable CAT/Bayesian hybrid for cold-start learner diagnosis."""

    def __init__(
        self,
        *,
        min_items: int = 5,
        max_items: int = 12,
        stop_uncertainty: float = 0.035,
    ) -> None:
        self.min_items = min_items
        self.max_items = max_items
        self.stop_uncertainty = stop_uncertainty

    def estimate_theta(self, profile: LearnerProfile, chapter_id: str | None = None) -> float:
        if chapter_id is not None:
            chapter = profile.chapter_mastery.get(chapter_id)
            values = [chapter.mean] if chapter is not None and chapter.evidence_count else []
        else:
            values = [posterior.mean for posterior in profile.knowledge_mastery.values()]
        if not values:
            return 0.0
        mean = sum(values) / len(values)
        bounded = min(0.98, max(0.02, mean))
        return math.log(bounded / (1 - bounded))

    def rank_items(
        self,
        profile: LearnerProfile,
        candidates: list[DiagnosticItem],
        asked_item_ids: set[str],
    ) -> list[tuple[DiagnosticItem, float]]:
        ranked: list[tuple[DiagnosticItem, float]] = []
        recent = profile.diagnostic_observations[-3:]
        last_difficulty = recent[-1].item_difficulty if recent else None
        for item in candidates:
            if item.item_id in asked_item_ids:
                continue
            posteriors = [
                profile.knowledge_mastery.get(point_id, MasteryPosterior())
                for point_id in item.knowledge_point_ids
            ]
            bayesian_gain = sum(expected_information_gain(value) for value in posteriors)
            theta = self.estimate_theta(profile, item.chapter_id)
            fisher = irt_fisher_information(theta, item)
            coverage = sum(value.evidence_count == 0 for value in posteriors) / len(posteriors)
            focus = 1.0 if item.chapter_id in profile.focus_chapter_ids else 0.0
            chapter_evidence = profile.chapter_mastery.get(
                item.chapter_id, MasteryPosterior()
            ).evidence_count
            chapter_diversity = 1 / (1 + chapter_evidence)
            evidence_quality = 1.0 if item.source_pages else 0.0
            time_cost = min(1.0, item.estimated_seconds / 180)
            difficulty_jump = (
                min(2.0, abs(item.difficulty - last_difficulty))
                if last_difficulty is not None
                else 0.0
            )
            same_chapter_streak = sum(
                observation.chapter_id == item.chapter_id for observation in recent[-2:]
            )
            score = (
                4.0 * bayesian_gain
                + 0.55 * fisher
                + 0.35 * coverage
                + 0.2 * focus
                + 0.25 * chapter_diversity
                + 0.1 * evidence_quality
            )
            score -= 0.12 * time_cost
            score -= 0.16 * difficulty_jump
            score -= 0.12 * same_chapter_streak
            ranked.append((item, score))
        return sorted(ranked, key=lambda pair: pair[1], reverse=True)

    def select_next(
        self,
        profile: LearnerProfile,
        candidates: list[DiagnosticItem],
        asked_item_ids: set[str],
    ) -> DiagnosticItem | None:
        ranked = self.rank_items(profile, candidates, asked_item_ids)
        if not ranked:
            return None
        # Start with an accessible item. Within each difficulty band the existing
        # information/coverage ranking still determines which chapter to probe.
        observations = profile.diagnostic_observations
        if not observations:
            easiest = min(item.difficulty for item, _ in ranked)
            eligible = [pair for pair in ranked if pair[0].difficulty <= easiest + 0.15]
        else:
            previous = observations[-1]
            secure = (
                previous.score >= 0.75
                and previous.self_confidence >= 0.65
                and previous.scoring_confidence >= 0.7
                and previous.evidence_weight >= 0.3
            )
            ceiling = previous.item_difficulty + (0.65 if secure else 0.0)
            eligible = [pair for pair in ranked if pair[0].difficulty <= ceiling]
            if secure:
                next_band = [
                    pair for pair in eligible
                    if pair[0].difficulty > previous.item_difficulty
                ]
                eligible = next_band or eligible
            # A sparse book may have no item in the target band. Use the closest
            # remaining difficulty, rather than repeat a question or get stuck.
            if not eligible:
                nearest = min(abs(item.difficulty - ceiling) for item, _ in ranked)
                eligible = [
                    pair for pair in ranked
                    if abs(pair[0].difficulty - ceiling) <= nearest + 1e-9
                ]
        return eligible[0][0]

    def update_profile(
        self,
        profile: LearnerProfile,
        item: DiagnosticItem,
        response: AssessmentResponse,
        evidence: ScoredEvidence,
        *,
        evidence_kind: EvidenceKind = EvidenceKind.DIAGNOSTIC,
    ) -> LearnerProfile:
        reliability = evidence.scoring_confidence
        hint_penalty = 1 / (1 + 0.25 * response.hints_used)
        speed_ratio = response.response_seconds / item.estimated_seconds
        speed_reliability = 0.72 if speed_ratio < 0.18 else 1.0
        revision_penalty = 1 / (1 + 0.08 * response.revisions)
        evidence_weight = (
            item.discrimination * reliability * hint_penalty * speed_reliability * revision_penalty
        )
        now = datetime.now(UTC)
        for point_id in item.knowledge_point_ids:
            posterior = profile.knowledge_mastery.setdefault(point_id, MasteryPosterior())
            update_knowledge_trace(
                posterior,
                score=evidence.score,
                evidence_weight=evidence_weight,
            )
            posterior.alpha += evidence.score * evidence_weight
            posterior.beta += (1 - evidence.score) * evidence_weight
            posterior.evidence_count += 1
            posterior.last_updated_at = now
        chapter = profile.chapter_mastery.setdefault(item.chapter_id, MasteryPosterior())
        update_knowledge_trace(
            chapter,
            score=evidence.score,
            evidence_weight=evidence_weight,
            learning_rate=0.08,
        )
        chapter.alpha += evidence.score * evidence_weight
        chapter.beta += (1 - evidence.score) * evidence_weight
        chapter.evidence_count += 1
        chapter.last_updated_at = now
        outcome = 1.0 if evidence.score >= 0.65 else 0.0
        profile.confidence_brier_sum += (response.confidence - outcome) ** 2
        profile.confidence_observations += 1
        profile.misconception_candidates = list(
            dict.fromkeys([*profile.misconception_candidates, *evidence.misconception_candidates])
        )[:100]
        observation = DiagnosticObservation(
            item_id=item.item_id,
            chapter_id=item.chapter_id,
            knowledge_point_ids=item.knowledge_point_ids,
            score=evidence.score,
            evidence_weight=evidence_weight,
            scoring_confidence=evidence.scoring_confidence,
            self_confidence=response.confidence,
            response_seconds=response.response_seconds,
            hints_used=response.hints_used,
            item_difficulty=item.difficulty,
            observed_at=now,
            question_snapshot=item.prompt,
            answer_snapshot=response.answer,
            source_pages=item.source_pages,
            knowledge_labels=item.knowledge_point_labels,
            activity_kind=("闪卡复习" if item.response_type == ResponseType.SELF_REPORT
                           else "选择题诊断" if item.response_type in {
                               ResponseType.SINGLE_CHOICE, ResponseType.MULTIPLE_CHOICE}
                           else "学习练习"),
        )
        profile.diagnostic_observations.append(observation)
        profile.evidence.append(
            ProfileEvidence(
                evidence_id=f"evidence_{uuid.uuid4().hex}",
                kind=evidence_kind,
                field="mastery",
                value=f"score={evidence.score:.3f};weight={evidence_weight:.3f}",
                confidence=evidence.scoring_confidence,
                item_id=item.item_id,
                observed_at=now,
            )
        )
        variances = [value.variance for value in profile.knowledge_mastery.values()]
        coverage = sum(value.evidence_count > 0 for value in profile.knowledge_mastery.values())
        coverage_ratio = coverage / max(1, len(profile.knowledge_mastery))
        uncertainty = sum(variances) / max(1, len(variances))
        profile.profile_confidence = min(
            1.0,
            max(0.0, coverage_ratio * (1 - min(1.0, uncertainty / 0.0833))),
        )
        return profile

    def should_stop(self, profile: LearnerProfile, answered_count: int) -> bool:
        if answered_count >= self.max_items:
            return True
        if answered_count < self.min_items:
            return False
        values = list(profile.knowledge_mastery.values())
        if not values:
            return False
        coverage_ratio = sum(value.evidence_count > 0 for value in values) / len(values)
        mean_uncertainty = sum(value.variance for value in values) / len(values)
        return coverage_ratio >= 0.65 and mean_uncertainty <= self.stop_uncertainty


def score_choice(item: DiagnosticItem, response: AssessmentResponse) -> ScoredEvidence:
    expected = set(item.correct_option_ids)
    selected = set(response.selected_option_ids)
    if not expected:
        return ScoredEvidence(score=0, scoring_confidence=0, needs_follow_up=True)
    exact = selected == expected
    overlap = len(selected & expected) / len(expected)
    false_positive = len(selected - expected) / max(1, len(selected))
    score = 1.0 if exact else max(0.0, overlap - 0.5 * false_positive)
    return ScoredEvidence(score=score, scoring_confidence=0.99)
