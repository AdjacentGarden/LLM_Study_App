"""Concise source-backed lessons pass style checks; content gates still decide."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_teaching_content import course
from test_teaching_unit_engine_persistence import rig as persistence_rig

from adaptive_learning.llm.client import LLMTimeoutError
from adaptive_learning.personalization.teaching_content import (
    TeachingContentError,
    TeachingContentGenerator,
    _Card,
    _Checkpoint,
    _Example,
    _ExplanationPatch,
    _Lesson,
    _Practice,
    _TeachingReviewFormatError,
)
from adaptive_learning.personalization.teaching_repairs import TeachingRepairPending, current_repair
from adaptive_learning.personalization.teaching_scope import (
    MAX_CONDITIONS_PER_POINT,
    _ComparedPoint,
    _Comparison,
    _Condition,
)
from adaptive_learning.personalization.teaching_unit_engine import UnitEngine, _UnitRejected

rig = persistence_rig


@pytest.mark.parametrize('model,value', [
    (_ExplanationPatch, {'title': '偶数', 'explanation': '偶数是能被2整除的整数；0也是偶数，例如4=2×2。',
                         'source_ids': ['s1']}),
    (_Card, {'card_id': 'card', 'front': '偶数？', 'back': '能被2整除的整数。', 'source_ids': ['s1']}),
    (_Practice, {'item_id': 'item', 'prompt': '4是偶数吗？', 'expected_answer': '是，因为4=2×2。',
                 'rubric': ['用整除2的定义作出判断'], 'source_ids': ['s1']}),
    (_Example, {'title': '判断', 'problem': '4是偶数吗？', 'steps': ['4=2×2，能被2整除。'],
                'answer': '是。', 'source_ids': ['s1']}),
    (_Checkpoint, {'question': '何谓偶数？', 'expected_answer': '能被2整除的整数。', 'source_ids': ['s1']}),
])
def test_concise_complete_fields_are_eligible_for_independent_review(model, value):
    assert model.model_validate(value).model_dump() == value
    field = next(key for key in ('explanation', 'front', 'prompt', 'problem', 'question') if key in value)
    with pytest.raises(ValidationError):
        model.model_validate({**value, field: '   '})
    with pytest.raises(ValidationError):
        model.model_validate({**value, 'source_ids': []})


def test_empty_steps_and_empty_rubric_are_still_invalid():
    with pytest.raises(ValidationError):
        _Example.model_validate({'title': '判断', 'problem': '4是偶数吗？', 'steps': [],
                                 'answer': '是。', 'source_ids': ['s1']})
    with pytest.raises(ValidationError):
        _Practice.model_validate({'item_id': 'item', 'prompt': '4是偶数吗？',
                                  'expected_answer': '是。', 'rubric': [], 'source_ids': ['s1']})


def test_short_scope_navigation_remains_nonempty_typed_and_source_identified():
    assert _Comparison.model_validate({'source_id': 's1', 'changes_scope': False,
                                      'reason': '  仅为发现史  '}).reason == '仅为发现史'
    condition = {'boundary_type': 'precondition', 'condition': '对象限定',
                 'scope_effect': '限定对象', 'source_ids': ['s1']}
    assert _Condition.model_validate(condition).scope_effect == '限定对象'
    for bad in ('', '   '):
        with pytest.raises(ValidationError):
            _Comparison.model_validate({'source_id': 's1', 'changes_scope': False, 'reason': bad})
    with pytest.raises(ValidationError):
        _Comparison.model_validate({'source_id': 's1', 'changes_scope': 'false', 'reason': '发现史'})
    conditions = [{**condition, 'condition': f'第{i}项不同的真实限制'} for i in range(5)]
    assert len(_ComparedPoint.model_validate({'point_id': 'p', 'conditions': conditions,
                                             'comparisons': []}).conditions) == 5
    assert MAX_CONDITIONS_PER_POINT == 32


@pytest.mark.parametrize('point_count,question_count,total', [(1, 1, 7), (2, 2, 12), (12, 3, 44)])
def test_lesson_slot_quantity_tracks_real_points(point_count, question_count, total):
    bundle, chapter, evidence = course(point_count)
    engine = object.__new__(UnitEngine)
    engine.inputs = TeachingContentGenerator._inputs(bundle, evidence)
    engine.chapter = chapter
    specs = engine._specs()
    assert len(specs) == total
    for prefix in ('example_', 'checkpoint_'):
        assert [row['kind'] for row in specs if row['kind'].startswith(prefix)] == [
            prefix + str(i) for i in range(1, question_count + 1)]
    assert len([row for row in specs if row['kind'] == 'practice']) == point_count


def open_engine(rig):
    _, run = rig.open_run()
    token = current_repair.set(run)
    try:
        engine = rig.engine(run)
    finally:
        current_repair.reset(token)
    return run, engine


def test_single_point_materializes_one_real_example_and_checkpoint_through_final_gate(rig):
    rig.advance()
    run, engine = open_engine(rig)
    seen = []

    def review(points, lesson, *args, **kwargs):
        seen.append((len(points), len(lesson.worked_examples), len(lesson.checkpoints)))
        return (SimpleNamespace(model_dump=lambda: {'accepted': True}),
                SimpleNamespace(model_dump=lambda: {'accepted': True}, _rubric_alignment={}))

    engine.g._review_candidate = review
    engine.g._fact_feedback = lambda *args: []
    engine.g._review_feedback = lambda *args: []
    result = UnitEngine._assemble(engine, run.units(), run.load()['plans'])
    assert seen == [(1, 1, 1)]
    assert len(result.worked_examples) == len(result.checkpoint_questions) == 1
    assert result.practice_items[0].item_id == rig.bundle.practice_items[0].item_id
    assert run.load()['published_candidate_hash']


@pytest.mark.parametrize('error', [
    _TeachingReviewFormatError('review JSON omits a matrix row'),
    TeachingContentError('unlocated validator failure'),
])
def test_final_format_failure_cannot_trigger_global_redesign_or_publication(rig, error):
    rig.advance()
    run, engine = open_engine(rig)
    before = deepcopy(run.units())
    engine.g._review_candidate = lambda *args, **kwargs: (_ for _ in ()).throw(error)
    engine.g._apply = lambda *args: pytest.fail('unreviewed course must not publish')
    for _ in range(3):
        with pytest.raises(TeachingRepairPending):
            UnitEngine._assemble(engine, run.units(), run.load()['plans'])
    assert run.units() == before
    assert 'published_candidate_hash' not in run.load()


@pytest.mark.parametrize('error', [
    _UnitRejected({'issue': 'missing required review JSON key'}, 'fact_format'),
    _TeachingReviewFormatError('malformed review'),
    LLMTimeoutError('unknown review submission; outcome fenced separately'),
])
def test_protocol_and_service_errors_do_not_consume_semantic_redesign_counter(rig, error):
    rig.advance()
    run, engine = open_engine(rig)
    unit = next(row for row in run.units() if row['work']['kind'] == 'practice')
    unit['work'].update(mode='repair', phase='review')
    baseline = deepcopy(unit['candidate'])
    for _ in range(3):
        engine._failed(unit, error, 'repair')
        assert unit['work']['mode'] == 'repair'
        assert unit['work']['repair_failures'] == 0
        assert unit['work']['phase'] == 'review'
        assert unit['candidate'] == baseline
    for count in (1, 2):
        engine._failed(unit, _UnitRejected({'issue': 'actual missing source condition'}, 'teacher'), 'repair')
        assert unit['work']['repair_failures'] == count
        assert unit['status'] != 'approved'
    assert unit['work']['mode'] == 'redesign'


def test_content_duplicate_is_located_without_rewriting_other_lesson_slots():
    from test_teaching_content import lesson_candidate, point_candidate
    bundle, _, evidence = course(1)
    inputs = TeachingContentGenerator._inputs(bundle, evidence)
    from adaptive_learning.personalization.teaching_content import _Point
    points = [_Point.model_validate(point_candidate(inputs[0]))]
    raw = lesson_candidate()
    raw['checkpoints'][1] = deepcopy(raw['checkpoints'][0])
    with pytest.raises(TeachingContentError) as failure:
        TeachingContentGenerator._validate_lesson(_Lesson.model_validate(raw), points, evidence)
    assert failure.value.review_feedback['lesson']['structural_validation']['lesson_components'] == [
        'checkpoint_1', 'checkpoint_2']


def test_review_format_failure_after_new_design_resumes_same_candidate(rig):
    rig.advance()
    run, engine = open_engine(rig)
    unit = next(row for row in run.units() if row['work']['kind'] == 'practice')
    unit['work'].update(mode='redesign', phase='review', redesign_pending_review=True)
    before = deepcopy(unit['candidate'])
    engine._failed(unit, _UnitRejected({'issue': 'missing review key'}, 'fact_format'), 'redesign')
    assert unit['work']['mode'] == 'repair' and unit['work']['phase'] == 'review'
    assert unit['work']['redesign_pending_review'] is True
    assert unit['candidate'] == before


@pytest.mark.parametrize('has_content_veto', [True, False])
def test_identical_response_only_counts_as_unresolved_repair_after_real_content_veto(rig, has_content_veto):
    rig.advance()
    run, engine = open_engine(rig)
    unit = next(row for row in run.units() if row['work']['kind'] == 'practice')
    unit['work'].update(mode='repair', phase='write',
                        semantic_feedback={'issue': '原文条件仍未落实'} if has_content_veto else {})
    for _ in range(2):
        engine._failed(unit, _UnitRejected({'issue': '原样返回'}, 'unchanged'), 'repair')
    assert unit['work']['repair_failures'] == (2 if has_content_veto else 0)
    assert unit['work']['mode'] == ('redesign' if has_content_veto else 'repair')
    assert not has_content_veto or unit['work']['semantic_feedback']['issue'] == '原文条件仍未落实'


def test_outer_content_whitespace_is_normalized_before_final_assembly_without_touching_sources(rig):
    rig.advance()
    run, engine = open_engine(rig)
    rows = run.units()
    before_sources = {key: source.model_dump() for key, source in engine.evidence.sources.items()}
    for unit in rows:
        original = deepcopy(unit['candidate'])
        for key, value in unit['candidate'].items():
            if key in {'source_ids', 'card_id', 'item_id'}:
                continue
            unit['candidate'][key] = (' \n' + value + '\t ' if isinstance(value, str)
                                     else [' \n' + item + '\t ' for item in value])
        engine._validate_one(unit, unit['candidate'])
        assert unit['candidate'] == original
    by_id = {row['unit_id']: row for row in rows}
    owner = engine.inputs[0]['point_id']
    from adaptive_learning.personalization.teaching_content import _Point
    point = _Point.model_validate({'point_id': owner, **by_id[owner + '.explanation']['candidate'],
                                   'flashcard': by_id[owner + '.flashcard']['candidate'],
                                   'practice': by_id[owner + '.practice']['candidate']})
    lesson = _Lesson.model_validate({'opening': by_id['lesson.opening']['candidate']['content'],
        'summary': by_id['lesson.summary']['candidate']['content'], 'reading_source_ids': ['s1'],
        'worked_examples': [by_id['lesson.example_1']['candidate']],
        'checkpoints': [by_id['lesson.checkpoint_1']['candidate']]})
    engine.g._validate_lesson(lesson, [point], engine.evidence)
    assert {key: source.model_dump() for key, source in engine.evidence.sources.items()} == before_sources
    for invalid in ('   ', '有效文字\x00恶意控制符', '\x1c不应被strip隐藏'):
        candidate = {**deepcopy(by_id['lesson.opening']['candidate']), 'content': invalid}
        with pytest.raises(ValueError):
            engine._validate_one(by_id['lesson.opening'], candidate)


def test_teacher_cannot_request_nonexistent_third_question_in_a_single_point_lesson():
    from test_teaching_content import lesson_candidate
    lesson = lesson_candidate()
    lesson['worked_examples'] = lesson['worked_examples'][:1]
    lesson['checkpoints'] = lesson['checkpoints'][:1]
    raw = {'accepted': True, 'issues': [], 'points': [{'point_id': 'p',
        'explanation_ok': True, 'flashcard_ok': True, 'practice_ok': True,
        'evidence_ok': True, 'issues': []}],
        'lesson': {'reading_ok': True, 'examples_ok': True, 'checkpoints_ok': True,
                   'evidence_ok': True, 'issues': [], 'required_question_edits': []}}
    calls = []

    def respond(**kwargs):
        calls.append(kwargs)
        result = deepcopy(raw)
        if len(calls) == 1:
            result['accepted'] = False
            result['lesson'].update(checkpoints_ok=False,
                issues=['lesson.checkpoint_3：必须修改这个并不存在的题设。'],
                required_question_edits=['checkpoint_3'])
        return result

    generator = TeachingContentGenerator(object(), review_client=object())
    generator._structured = respond
    review = generator._request_teacher_review({'candidate': {'points': [], 'lesson': lesson}}, {'p'})
    assert review.accepted and len(calls) == 2
