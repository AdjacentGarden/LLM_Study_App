from adaptive_learning.assessment.item_generation import stable_knowledge_point_id
from adaptive_learning.assessment.models import LearnerProfile, MasteryPosterior
from adaptive_learning.ingestion.models import ChapterDraft, SourceQuote
from adaptive_learning.personalization.generator import ChapterCourseCompiler
from adaptive_learning.personalization.models import PublicChapterLearningBundle, TeachingDepth
from adaptive_learning.personalization.policy import PersonalizationPolicy


def chapter(point_count: int = 10) -> ChapterDraft:
    points = [f"概念{i}是用于验证个性化课程的第{i}个关键关系" for i in range(point_count)]
    evidence = {
        point: [SourceQuote(page_number=index + 1, quote=f"原文证据：{point}。")]
        for index, point in enumerate(points)
    }
    return ChapterDraft(
        chapter_id="chapter_course",
        order=1,
        title="第一章 可核验课程",
        start_page=1,
        end_page=point_count,
        summary="本章说明十个用于课程测试的概念。",
        knowledge_points=points,
        source_block_ids=[f"block_{index}" for index in range(point_count)],
        evidence=[SourceQuote(page_number=1, quote="本章说明多个概念。")],
        knowledge_point_evidence=evidence,
    )


def test_compiler_prioritizes_unknown_and_weak_points_with_exact_citations() -> None:
    value = chapter()
    weak_label = value.knowledge_points[8]
    strong_label = value.knowledge_points[0]
    weak_id = stable_knowledge_point_id(value.chapter_id, weak_label)
    strong_id = stable_knowledge_point_id(value.chapter_id, strong_label)
    profile = LearnerProfile(
        user_id="u",
        book_id="b",
        goal="准备考试",
        knowledge_mastery={
            weak_id: MasteryPosterior(alpha=1, beta=5, evidence_count=2),
            strong_id: MasteryPosterior(alpha=8, beta=1, evidence_count=3),
        },
    )
    profile.constraints.minutes_per_day = 60
    decision = PersonalizationPolicy().decide(profile, value.chapter_id)

    bundle = ChapterCourseCompiler().compile(
        chapter=value, profile=profile, decision=decision
    )

    selected_ids = [point.point_id for point in bundle.knowledge_points]
    assert selected_ids.index(weak_id) < selected_ids.index(strong_id)
    source_quotes = {
        (quote.page_number, quote.quote)
        for quotes in value.knowledge_point_evidence.values()
        for quote in quotes
    }
    for section in bundle.original_reading:
        for citation in section.citations:
            assert (citation.page_number, citation.quote) in source_quotes
    for point in bundle.knowledge_points:
        assert point.citations
        assert point.explanation in value.knowledge_points
    assert bundle.summary == value.summary


def test_course_changes_depth_and_size_for_different_profiles() -> None:
    value = chapter()
    novice = LearnerProfile(
        user_id="novice",
        book_id="b",
        chapter_mastery={value.chapter_id: MasteryPosterior(alpha=1, beta=5, evidence_count=3)},
    )
    novice.constraints.minutes_per_day = 60
    advanced = LearnerProfile(
        user_id="advanced",
        book_id="b",
        chapter_mastery={value.chapter_id: MasteryPosterior(alpha=9, beta=1, evidence_count=4)},
    )
    advanced.constraints.minutes_per_day = 60
    policy = PersonalizationPolicy()
    compiler = ChapterCourseCompiler()

    foundation = compiler.compile(
        chapter=value,
        profile=novice,
        decision=policy.decide(novice, value.chapter_id),
    )
    challenge = compiler.compile(
        chapter=value,
        profile=advanced,
        decision=policy.decide(advanced, value.chapter_id),
    )

    assert foundation.decision.depth == TeachingDepth.FOUNDATION
    assert challenge.decision.depth == TeachingDepth.ADVANCED
    assert len(foundation.knowledge_points) == 10
    assert len(challenge.knowledge_points) == 6
    assert foundation.flashcards[0].front != challenge.flashcards[0].front


def test_public_course_never_contains_practice_answer_keys() -> None:
    value = chapter(3)
    profile = LearnerProfile(user_id="u", book_id="b")
    decision = PersonalizationPolicy().decide(profile, value.chapter_id)
    private = ChapterCourseCompiler().compile(
        chapter=value, profile=profile, decision=decision
    )

    public = PublicChapterLearningBundle.from_private(private)
    serialized = public.model_dump_json()

    assert len(public.practice_items) == 3
    assert "expected_answer" not in serialized
    assert "correct_option_ids" not in serialized
    assert '"rubric"' not in serialized
    assert "profile_fingerprint" not in serialized
    assert "source_fingerprint" not in serialized


def test_same_inputs_are_idempotent_but_new_evidence_changes_course_id() -> None:
    value = chapter(3)
    profile = LearnerProfile(user_id="u", book_id="b")
    compiler = ChapterCourseCompiler()
    decision = PersonalizationPolicy().decide(profile, value.chapter_id)
    first = compiler.compile(chapter=value, profile=profile, decision=decision)
    second = compiler.compile(chapter=value, profile=profile, decision=decision)
    racing_version = compiler.compile(
        chapter=value, profile=profile, decision=decision, version=2
    )
    point_id = stable_knowledge_point_id(value.chapter_id, value.knowledge_points[0])
    profile.knowledge_mastery[point_id] = MasteryPosterior(alpha=2, beta=1, evidence_count=1)
    changed = compiler.compile(
        chapter=value,
        profile=profile,
        decision=PersonalizationPolicy().decide(profile, value.chapter_id),
        version=2,
    )

    assert first.course_id == second.course_id
    assert first.course_id == racing_version.course_id
    assert first.course_id != changed.course_id
    assert first.profile_fingerprint != changed.profile_fingerprint


def test_compiler_rejects_chapter_without_evidence_backed_points() -> None:
    value = chapter(1)
    value.knowledge_point_evidence = {}
    profile = LearnerProfile(user_id="u", book_id="b")

    try:
        ChapterCourseCompiler().compile(
            chapter=value,
            profile=profile,
            decision=PersonalizationPolicy().decide(profile, value.chapter_id),
        )
    except ValueError as error:
        assert "evidence-backed" in str(error)
    else:
        raise AssertionError("course compiler accepted an ungrounded chapter")
