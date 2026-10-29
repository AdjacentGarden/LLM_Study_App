"""A larger presentation budget never supplies independent content approval."""
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_teaching_content import course, lesson_candidate
from test_teaching_unit_engine_rubric_only import unit_case as unit_case
from test_teaching_unit_validation_feedback import text_unit

from adaptive_learning.personalization import teaching_content as teaching
from adaptive_learning.personalization.generator import _draft_summary
from adaptive_learning.personalization.models import SUMMARY_MAX_CHARS, ChapterLearningBundle
from adaptive_learning.personalization.teaching_unit_engine import _UnitRejected

OBSERVED = json.loads((Path(__file__).parent / 'fixtures/teaching_h_summary_697.json').read_text())
# Captured from the actual durable H run under Windows-created 91dc48fd0d72.
# This fixed expected hash is independent of the new normalization helper.
LEGACY_H_PROTOCOL = '4dc857e1b3e28cdfde6151a5c635e217c59ed0bc68b87a76f65aed898ce39bb8'


def test_observed_697_characters_round_trip_all_summary_schemas_without_truncation(unit_case):
    summary = OBSERVED['content']
    assert len(summary) == OBSERVED['characters'] == 697
    assert hashlib.sha256(summary.encode()).hexdigest() == OBSERVED['sha256']
    assert SUMMARY_MAX_CHARS == 1000
    assert teaching._Lesson.model_validate({**lesson_candidate(), 'summary': summary}).summary == summary
    bundle, _, _ = course(1)
    assert ChapterLearningBundle.model_validate({**bundle.model_dump(), 'summary': summary}).summary == summary
    assert _draft_summary(summary) == summary
    unit = text_unit(unit_case, 'summary')
    candidate = {'content': summary, 'source_ids': ['s1']}
    unit_case.engine._validate_one(unit, candidate)
    assert candidate['content'] == summary and 'approval' not in unit['work']
    assert unit['status'] != 'approved'
    assert 'latest_invalid_draft' not in unit['work']
    assert 'summary须1-1000字' in teaching.GENERATION_PROMPT
    assert teaching.TeachingContentGenerator._writer_shapes()['summary'] == '完整总结（1–1000字）'


def test_1001_characters_still_fail_with_exact_context_and_no_truncation(unit_case):
    content = '文' * 1001
    unit = text_unit(unit_case, 'summary')
    candidate = {'content': content, 'source_ids': ['s1']}
    with pytest.raises(_UnitRejected) as caught:
        unit_case.engine._validate_one(unit, candidate)
    assert caught.value.feedback['field'] == '/component/content'
    assert caught.value.feedback['actual_chars'] == 1001
    assert caught.value.feedback['max_chars'] == 1000
    assert candidate['content'] == unit['work']['latest_invalid_draft']['candidate']['content'] == content
    assert 'approval' not in unit['work'] and unit['status'] != 'approved'
    with pytest.raises(ValidationError):
        teaching._Lesson.model_validate({**lesson_candidate(), 'summary': content})
    bundle, _, _ = course(1)
    with pytest.raises(ValidationError):
        ChapterLearningBundle.model_validate({**bundle.model_dump(), 'summary': content})


@pytest.mark.parametrize('gate', ['fact', 'teacher'])
def test_observed_697_candidate_still_requires_both_independent_reviews(unit_case, gate):
    case = unit_case
    unit = text_unit(case, 'summary')
    candidate = {'content': OBSERVED['content'], 'source_ids': ['s1']}
    case.engine._validate_one(unit, candidate)
    unit['candidate'] = candidate

    def fact(row):
        case.seen.append('fact')
        if gate == 'fact':
            raise _UnitRejected({'issues': ['该断言仍不被引用原文支持。']}, 'fact')
        return {'source_entailment': True, 'scope_exceptions': True, 'findings': []}

    def teacher(**kwargs):
        case.seen.append('teacher')
        return {'accepted': False, 'redesign_ok': True, 'issues': ['当前总结遗漏真实必要条件。']}

    case.engine._fact_one = fact
    case.generator._structured = teacher
    with pytest.raises(_UnitRejected) as caught:
        case.engine._review_one(unit)
    assert caught.value.stage == gate
    assert case.seen == (['fact'] if gate == 'fact' else ['fact', 'teacher'])
    assert 'approval' not in unit['work'] and unit['status'] != 'approved'


def test_only_the_known_presentation_change_retains_the_actual_h_protocol(monkeypatch):
    assert teaching._teaching_protocol_fingerprint() == LEGACY_H_PROTOCOL
    new_prompt = teaching.GENERATION_PROMPT
    monkeypatch.setattr(teaching, 'GENERATION_PROMPT', new_prompt.replace('summary须1-1000字', 'summary须1-680字', 1))
    assert teaching._teaching_protocol_fingerprint() == LEGACY_H_PROTOCOL


@pytest.mark.parametrize(('field', 'change'), [
    ('GENERATION_PROMPT', lambda text: text + '\n额外的事实条件审核要求。'),
    ('GENERATION_PROMPT', lambda text: text.replace('opening须1-300字', 'opening须1-301字')),
    ('GENERATION_PROMPT', lambda text: text + '\n其他组件summary须1-1000字。'),
    ('GENERATION_PROMPT', lambda text: text.replace('summary须1-1000字', 'summary须1-1001字')),
    ('REVIEW_PROMPT', lambda text: text + '\n不同的独立审核规则。'),
    ('FACT_REVIEW_PROMPT', lambda text: text + '\n不同的事实审核规则。'),
    ('RUBRIC_ALIGNMENT_PROMPT', lambda text: text + '\n不同的评分审核规则。'),
])
def test_other_semantic_reviewer_or_numeric_changes_still_invalidate_protocol(monkeypatch, field, change):
    monkeypatch.setattr(teaching, field, change(getattr(teaching, field)))
    assert teaching._teaching_protocol_fingerprint() != LEGACY_H_PROTOCOL
