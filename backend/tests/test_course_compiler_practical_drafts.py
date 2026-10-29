"""Long source goals survive private scaffolding without becoming published facts."""
from types import SimpleNamespace

import pytest
from test_teaching_content import course
from test_teaching_publication import publication_environment

from adaptive_learning.assessment.item_generation import stable_knowledge_point_id
from adaptive_learning.assessment.models import LearnerProfile
from adaptive_learning.ingestion.models import SourceQuote
from adaptive_learning.personalization.generator import ChapterCourseCompiler
from adaptive_learning.personalization.models import SourceCitation
from adaptive_learning.personalization.policy import PersonalizationPolicy
from adaptive_learning.personalization.teaching_content import TeachingContentGenerator
from adaptive_learning.personalization.teaching_evidence import TeachingEvidence
from adaptive_learning.personalization.teaching_repairs import TeachingRepairStore, current_repair
from adaptive_learning.personalization.teaching_unit_engine import ENGINE_VERSION, UnitEngine

SOURCE = '只有观测条件甲保持不变时，才能讨论结论乙是否成立。' * 20
assert len(SOURCE) == 500


@pytest.mark.parametrize('summary', [SOURCE * 2, '甲' * 1000], ids=['complete-sentences', 'unbroken-legacy-text'])
def test_long_source_is_kept_whole_for_unit_engine_even_when_navigation_is_short(tmp_path, monkeypatch, summary):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('DATABASE_HOST', raising=False)
    _, chapter, _ = course(1)
    citation = SourceQuote(page_number=1, quote=SOURCE)
    chapter = chapter.model_copy(update={'summary': summary, 'knowledge_points': [SOURCE],
        'evidence': [citation], 'knowledge_point_evidence': {SOURCE: [citation]}})
    profile = LearnerProfile(user_id='u', book_id='b')
    bundle = ChapterCourseCompiler().compile(chapter=chapter, profile=profile,
        decision=PersonalizationPolicy().decide(profile, chapter.chapter_id))
    point = bundle.knowledge_points[0]
    assert point.point_id == stable_knowledge_point_id(chapter.chapter_id, SOURCE)
    assert point.explanation == SOURCE and point.citations[0].quote == SOURCE
    assert point.title == '原文要点 1'
    assert len(bundle.flashcards[0].front) <= 120 and len(bundle.flashcards[0].back) <= 360
    assert len(bundle.practice_items[0].prompt) <= 300
    assert len(bundle.summary) <= 680 and chapter.summary == summary
    assert not bundle.teaching_quality_version and not bundle.teaching_fact_review and not bundle.teaching_review
    evidence = TeachingEvidence(sources={'s1': SourceCitation(page_number=1, quote=SOURCE)},
        point_sources={point.point_id: ['s1']}, warnings=[], fingerprint='whole-500-character-source')
    run = TeachingRepairStore(tmp_path / 'course.sqlite3').open('user-book', chapter.chapter_id,
        {'engine': ENGINE_VERSION, 'evidence_fingerprint': evidence.fingerprint})
    token = current_repair.set(run)
    try:
        engine = UnitEngine(TeachingContentGenerator(object(), review_client=object()), bundle, chapter, evidence)
    finally:
        current_repair.reset(token)
    assert engine.inputs[0]['label'] == SOURCE
    assert engine.chapter.summary == summary
    assert engine.evidence.sources['s1'].quote == SOURCE


def test_current_public_read_gate_rejects_long_source_unreviewed_scaffold(tmp_path, monkeypatch):
    env = publication_environment(tmp_path, monkeypatch)
    env.chapter.knowledge_points = [SOURCE]
    env.chapter.evidence = [SourceQuote(page_number=1, quote=SOURCE)]
    env.chapter.knowledge_point_evidence = {SOURCE: env.chapter.evidence}
    env.chapter.summary = SOURCE * 2
    env.pages[0].raw_text = SOURCE
    env.module.job_repository.get_structure = lambda _: SimpleNamespace(chapters=[env.chapter])
    _, _, evidence, _, source_hash = env.module._course_context('session', env.chapter.chapter_id)
    draft = ChapterCourseCompiler().compile(chapter=env.chapter, profile=env.session.profile,
        decision=env.module.personalization_policy.decide(env.session.profile, env.chapter.chapter_id),
        source_fingerprint=source_hash, instance_key='session')
    # Even a matching version/source identity is not an approval proof.
    draft.teaching_quality_version = env.module.TEACHING_CONTENT_VERSION
    draft.teaching_evidence_fingerprint = evidence.fingerprint
    env.repo.save_course('session', draft)
    result = env.client.get('/api/interviews/session/courses/morgan')
    assert result.status_code == 409
    assert env.generator.calls == 0
    assert '原文要点 1' not in result.text
