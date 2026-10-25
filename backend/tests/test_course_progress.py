"""Phase events describe real work without changing model/quality contracts."""
from contextvars import copy_context

import pytest
from test_teaching_content import Client, course, point_batches

from adaptive_learning.personalization.course_progress import (
    CourseStage,
    observe_course_progress,
    report_course_stage,
)
from adaptive_learning.personalization.teaching_content import TeachingContentGenerator


@pytest.mark.parametrize('mode', ['ok', 'partial_retry', 'lesson_retry'])
def test_actual_generator_reports_coarse_phases_without_extra_generation(mode):
    bundle, chapter, evidence = course(3)
    client, phases = Client(mode), []
    with observe_course_progress(phases.append):
        result = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    assert phases[0] is CourseStage.PLANNING
    assert CourseStage.WRITING in phases and CourseStage.REVIEWING in phases
    assert phases[-1] is CourseStage.REVIEWING
    assert (CourseStage.REFINING in phases) == (mode != 'ok')
    assert [len(batch) for batch in point_batches(client)] == (
        [3, 1] if mode == 'partial_retry' else [3]
    )
    assert client.review_calls == (1 if mode == 'ok' else 2)
    assert result.teaching_review['accepted'] and result.teaching_fact_review['accepted']


def test_observer_failure_cannot_enter_writer_or_review_repairs(caplog):
    def unavailable(_):
        raise RuntimeError('PRIVATE_PROVIDER_BODY_MUST_NOT_ENTER_PUBLIC_PROGRESS')

    bundle, chapter, evidence = course(1)
    client = Client()
    with observe_course_progress(unavailable):
        output = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    assert len(point_batches(client)) == 1 and client.review_calls == 1
    assert output.teaching_review['accepted'] is True
    assert 'PRIVATE' not in caplog.text


def test_context_resets_and_arbitrary_private_text_is_not_a_phase():
    first, second = [], []
    with observe_course_progress(first.append):
        report_course_stage(CourseStage.PLANNING)
        first_context = copy_context()
        with observe_course_progress(second.append):
            report_course_stage(CourseStage.WRITING)
            report_course_stage('PRIVATE_PROVIDER_BODY')
        report_course_stage(CourseStage.REVIEWING)
    report_course_stage(CourseStage.REFINING)
    assert first == [CourseStage.PLANNING, CourseStage.REVIEWING]
    assert second == [CourseStage.WRITING]
    # A copied context retains only its observer; the queue separately fences
    # the live owner/run/attempt so a detached leaf cannot update a public job.
    first_context.run(report_course_stage, CourseStage.REFINING)
    assert first[-1] is CourseStage.REFINING and second == [CourseStage.WRITING]
