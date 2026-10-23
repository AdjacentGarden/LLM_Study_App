"""Merged native layouts must never expose a pre-review compiler draft."""
from copy import deepcopy

import pytest
from test_teaching_content import Client, course, point_batches

from adaptive_learning.assessment.models import LearnerProfile
from adaptive_learning.learning_preferences import LearningDesignConfig
from adaptive_learning.personalization.generator import ChapterCourseCompiler
from adaptive_learning.personalization.models import PublicChapterLearningBundle
from adaptive_learning.personalization.policy import PersonalizationPolicy
from adaptive_learning.personalization.teaching_content import TeachingContentGenerator


def reviewed_course(styles):
    _, chapter, evidence = course(2)
    profile = LearnerProfile(user_id="u", book_id="b")
    profile.constraints.minutes_per_day = 120
    profile.learning_design = LearningDesignConfig(
        minutes_per_day=120, guidance_styles=list(styles), learning_barriers=["memory"]
    )
    bundle = ChapterCourseCompiler().compile(
        chapter=chapter, profile=profile,
        decision=PersonalizationPolicy().decide(profile, chapter.chapter_id),
    )
    client = Client()
    before = bundle.model_dump_json()
    output = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    assert bundle.model_dump_json() == before
    return bundle, output, client


@pytest.mark.parametrize("styles", [("principles", "stepByStep"), ("examples", "keyPoints")])
def test_full_generation_rebuilds_native_layout_from_reviewed_components(styles):
    original, output, client = reviewed_course(styles)
    points = {point.point_id: point for point in output.knowledge_points}
    cards = {card.point_id: card for card in output.flashcards}
    practices = {item.point_id: item for item in output.practice_items}
    assert [unit.unit_id for unit in output.units] == [unit.unit_id for unit in original.units]
    assert output.design_config == original.design_config
    assert output.design_fingerprint == original.design_fingerprint
    assert output.coverage_point_ids == original.coverage_point_ids
    for before, unit in zip(original.units, output.units, strict=True):
        assert unit.knowledge_point_ids == before.knowledge_point_ids
        assert [(section.kind, section.point_id) for section in unit.sections] == [
            (section.kind, section.point_id) for section in before.sections
        ]
        assert unit.objectives == [points[key].explanation for key in unit.knowledge_point_ids]
        assert unit.objectives != before.objectives
        for section in unit.sections:
            if section.kind == "checkpoint":
                expected = practices[section.point_id]
                assert section.content == expected.prompt
            elif section.kind == "recall":
                expected = cards[section.point_id]
                assert section.content == expected.front
            else:
                expected = points[section.point_id]
                assert section.content == expected.explanation
            assert section.citations == expected.citations
    assert client.review_calls == client.rubric_calls == 1
    assert len(point_batches(client)) == 1
    calls_before_projection = len(client.systems)
    public = PublicChapterLearningBundle.from_private(output)
    assert public.units == output.units
    assert len(client.systems) == calls_before_projection
    assert "expected_answer" not in public.model_dump_json()


def test_public_projection_repairs_stale_persisted_units_without_mutating_private_course():
    _, output, client = reviewed_course(("principles", "stepByStep"))
    stale = deepcopy(output)
    for unit in stale.units:
        unit.objectives = ["OLD_UNREVIEWED_DRAFT"]
        unit.sections[0].content = "OLD_UNREVIEWED_DRAFT"
    before = stale.model_dump_json()
    call_count = len(client.systems)
    public = PublicChapterLearningBundle.from_private(stale)
    assert "OLD_UNREVIEWED_DRAFT" not in public.model_dump_json()
    assert public.units == output.units
    assert stale.model_dump_json() == before
    assert len(client.systems) == call_count


@pytest.mark.parametrize("fault", ["foreign_point", "duplicate_coverage", "unsupported_kind"])
def test_invalid_native_layout_cannot_publish_a_reviewed_course(fault):
    _, output, _ = reviewed_course(("principles", "stepByStep"))
    if fault == "foreign_point":
        output.units[0].sections[0].point_id = "foreign"
    elif fault == "duplicate_coverage":
        output.units.append(deepcopy(output.units[0]))
    else:
        output.units[0].sections[0].kind = "unreviewed_draft"
    with pytest.raises(ValueError):
        PublicChapterLearningBundle.from_private(output)
