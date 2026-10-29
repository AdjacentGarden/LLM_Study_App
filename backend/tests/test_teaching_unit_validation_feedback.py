"""Repair context is private guidance, never a replacement for content approval."""
import json
from copy import deepcopy

import pytest
from test_teaching_unit_engine_rubric_only import alignment, reject_rubric
from test_teaching_unit_engine_rubric_only import unit_case as unit_case

from adaptive_learning.personalization import teaching_repairs as repairs
from adaptive_learning.personalization.teaching_review_reuse import fingerprint
from adaptive_learning.personalization.teaching_unit_engine import _UnitRejected


def text_unit(case, kind):
    spec = next(row for row in case.engine._specs() if row['kind'] == kind)
    case.run.initialize_units([spec])
    unit = next(row for row in case.run.units() if row['unit_id'] == spec['unit_id'])
    unit['candidate'] = {'content': '这是尚未完成独立审核的旧稿。', 'source_ids': ['s1']}
    unit['work'].update(mode='repair', phase='write', attempt_nonce='old-attempt',
                        semantic_feedback={'issues': ['本组件仍须核实已有事实的来源。']})
    case.run.write_unit(unit, status='pending', candidate=unit['candidate'])
    return unit


def stored(case, unit):
    reopened = repairs.TeachingRepairStore(case.run.store.path).open(
        case.run.scope, case.run.chapter_id,
        {key: value for key, value in case.run.identity.items() if key != 'epoch'})
    return next(row for row in reopened.units() if row['unit_id'] == unit['unit_id'])


@pytest.mark.parametrize(('kind', 'maximum'), [('opening', 300), ('summary', 680)])
def test_invalid_actual_draft_and_exact_budget_survive_restart_until_next_writer(unit_case, kind, maximum):
    case = unit_case
    unit = text_unit(case, kind)
    previous = deepcopy(unit['candidate'])
    invalid = {'content': '文' * (maximum + 19), 'source_ids': ['s1']}
    case.generator._structured = lambda **kwargs: {'component': deepcopy(invalid)}
    case.engine._advance_one(unit)
    persisted = stored(case, unit)
    assert persisted['status'] == 'pending' and persisted['candidate'] == previous
    assert persisted['candidate_hash'] == fingerprint(previous)
    context = persisted['work']['latest_invalid_draft']
    assert context['candidate'] == invalid and context['candidate_sha256'] == fingerprint(invalid)
    assert context['approved'] is False and context['attempt_nonce']
    assert context['validation'] == {
        'issue': '导入/总结长度不符合组件要求', 'field': '/component/content',
        'actual_chars': maximum + 19, 'min_chars': 1, 'max_chars': maximum,
    }
    assert persisted['work']['feedback']['current_failure'] == context['validation']
    assert persisted['work']['feedback']['unresolved_review']['issues']
    assert 'approval' not in persisted['work'] and case.seen == []

    requests = []
    corrected = {'content': '按照本组件引用原文修正后的完整内容。', 'source_ids': ['s1']}

    def response(**kwargs):
        payload = json.loads(kwargs['user'])
        if kwargs.get('_review'):
            case.seen.append('teacher')
            return {'accepted': True, 'redesign_ok': True, 'issues': []}
        requests.append(payload)
        return {'component': deepcopy(corrected)}

    case.generator._structured = response
    case.engine._advance_one(persisted)
    assert requests[0]['previous_candidate'] == previous
    assert requests[0]['latest_invalid_draft'] == context
    assert requests[0]['repair_task']['failure']['current_failure']['actual_chars'] == maximum + 19
    final = stored(case, unit)
    assert case.seen == ['fact', 'teacher'] and final['status'] == 'approved'
    assert final['candidate'] == corrected
    assert final['work']['approval']['candidate_hash'] == fingerprint(corrected)
    assert 'latest_invalid_draft' not in final['work']


@pytest.mark.parametrize('gate', ['fact', 'teacher'])
def test_length_repair_still_requires_each_independent_content_gate(unit_case, gate):
    case = unit_case
    unit = text_unit(case, 'summary')
    case.generator._structured = lambda **kwargs: {
        'component': {'content': '文' * 681, 'source_ids': ['s1']}}
    case.engine._advance_one(unit)
    unit = stored(case, unit)

    def response(**kwargs):
        if kwargs.get('_review'):
            return {'accepted': False, 'redesign_ok': True, 'issues': ['当前稿仍缺必要事实前提。']}
        return {'component': {'content': '长度符合要求但尚未获独立批准的新稿。', 'source_ids': ['s1']}}

    if gate == 'fact':
        def fact(row):
            raise _UnitRejected({'issues': ['原文不支持当前断言。']}, 'fact')
        case.engine._fact_one = fact
    case.generator._structured = response
    case.engine._advance_one(unit)
    final = stored(case, unit)
    assert final['status'] != 'approved' and 'approval' not in final['work']
    assert final['work']['failure_stage'] == gate
    assert 'latest_invalid_draft' not in final['work']


def test_empty_content_keeps_existing_text_validator_without_authorizing_a_draft(unit_case):
    unit = text_unit(unit_case, 'summary')
    with pytest.raises(ValueError, match='text must be nonempty'):
        unit_case.engine._validate_one(unit, {'content': ' \n\t ', 'source_ids': ['s1']})
    assert 'approval' not in unit['work'] and 'latest_invalid_draft' not in unit['work']
    assert unit['candidate']['content'] == '这是尚未完成独立审核的旧稿。'


def test_current_typed_task_revision_is_visible_without_history_or_source_promotion(unit_case):
    case = unit_case
    current = alignment(case.unit, 'task_revision').to_dict()
    check = current['items'][0]['rubric_checks'][0]
    check.update(reason='题干未提供建立羽毛特征与性别对应关系所需的前提。',
                 repair='使用原文给出的该杂交后代观察结果修正题设，不自行添加未引用的染色体位点。')
    case.unit['work'].update(semantic_feedback=current, feedback={
        'current_failure': {'issue': '上一轮原样返回。'}, 'unresolved_review': current},
        history=[{'feedback': {'issue': '历史建议，不能作为当前修改依据。'}}])
    before = deepcopy(case.unit['candidate'])
    seen = []

    def writer(**kwargs):
        seen.append(json.loads(kwargs['user']))
        assert '不是新的原文证据' in kwargs['system']
        return {'component': deepcopy(before)}

    case.generator._structured = writer
    result = case.engine._write_one(case.unit)
    assert seen[0]['repair_task']['required_task_edits'] == [{
        'field': '/component/prompt', 'issue': check['reason'], 'repair': check['repair']}]
    assert 'prompt' in seen[0]['repair_task']['editable_fields']
    assert result == before and case.unit['candidate'] == before
    assert 'approval' not in case.unit['work'] and 'rubric_only_lock' not in case.unit['work']


@pytest.mark.parametrize('mutation', ['accepted', 'other_item', 'untyped', 'true', 'old_history_only'])
def test_only_current_exact_typed_task_veto_supplies_edit_targets(unit_case, mutation):
    feedback = alignment(unit_case.unit, 'task_revision').to_dict()
    if mutation == 'accepted':
        feedback['accepted'] = True
    elif mutation == 'other_item':
        feedback['items'][0]['item_id'] = 'some-other-item'
    elif mutation == 'untyped':
        feedback['items'][0]['rubric_checks'][0].pop('repair_scope')
    elif mutation == 'true':
        feedback['items'][0]['rubric_checks'][0]['required'] = True
    else:
        unit_case.unit['work']['history'] = [{'feedback': feedback}]
        feedback = {}
    unit_case.unit['work']['semantic_feedback'] = feedback
    assert unit_case.engine._task_edits(unit_case.unit) == []


def test_rubric_only_lock_cannot_be_broken_by_extra_task_edit_context(unit_case):
    case = unit_case
    reject_rubric(case, 'rubric_only')
    baseline = deepcopy(case.unit['candidate'])
    # Even contradictory retained metadata cannot authorize editing locked fields.
    case.unit['work']['semantic_feedback'] = alignment(case.unit, 'task_revision').to_dict()
    seen = []

    def writer(**kwargs):
        seen.append(json.loads(kwargs['user']))
        return {'component': {'rubric': ['说明原题所问的必要结论。'], 'prompt': '试图偷改题干'}}

    case.generator._structured = writer
    with pytest.raises(_UnitRejected) as failure:
        case.engine._write_one(case.unit)
    assert failure.value.stage == 'repair_lock'
    assert seen[0]['repair_task']['required_task_edits'] == []
    assert seen[0]['repair_task']['editable_fields'] == ['rubric']
    assert case.unit['candidate'] == baseline and case.unit['work']['rubric_only_lock']
