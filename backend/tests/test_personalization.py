from adaptive_learning.assessment.models import LearnerProfile, MasteryPosterior
from adaptive_learning.personalization.models import TeachingDepth
from adaptive_learning.personalization.policy import PersonalizationPolicy


def test_policy_changes_depth_for_different_mastery() -> None:
    novice = LearnerProfile(
        user_id="n",
        book_id="b",
        chapter_mastery={"ch": MasteryPosterior(alpha=1, beta=5, evidence_count=3)},
    )
    advanced = LearnerProfile(
        user_id="a",
        book_id="b",
        chapter_mastery={"ch": MasteryPosterior(alpha=9, beta=1, evidence_count=4)},
    )
    policy = PersonalizationPolicy()

    novice_decision = policy.decide(novice, "ch")
    advanced_decision = policy.decide(advanced, "ch")

    assert novice_decision.depth == TeachingDepth.FOUNDATION
    assert advanced_decision.depth == TeachingDepth.ADVANCED
    assert novice_decision.scaffolds != advanced_decision.scaffolds
