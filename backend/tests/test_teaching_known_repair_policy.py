"""Known corrections keep valid questions; retired lost requests cannot replay."""
import json
import time
from copy import deepcopy

import pytest
from test_teaching_content import positive_rubric_alignment_response
from test_teaching_unit_engine_rubric_only import unit_case as unit_case

from adaptive_learning.llm.client import LLMTimeoutError
from adaptive_learning.personalization import teaching_repairs as repairs
from adaptive_learning.personalization.rubric_alignment import parse_rubric_alignment_response
from adaptive_learning.personalization.teaching_unit_engine import _UnitRejected


def providers(case, revised, veto=None):
    calls = []

    def provider(**kwargs):
        payload = json.loads(kwargs['user'])
        calls.append(payload)
        if 'mode' in payload:
            return {'component': deepcopy(revised)}
        return {'accepted': veto != 'teacher', 'redesign_ok': True,
                'issues': ['实际题设仍有错误。'] if veto == 'teacher' else []}

    def fact(unit):
        case.seen.append('fact')
        if veto == 'fact':
            raise _UnitRejected({'issue': '实际结论仍无原文支持。'}, 'fact')
        return {'source_entailment': True, 'scope_exceptions': True, 'findings': []}

    def rubric(batch):
        case.seen.append('rubric')
        return parse_rubric_alignment_response(positive_rubric_alignment_response(batch.to_payload()), batch)

    case.generator._structured = provider
    case.engine._fact_one = fact
    case.generator._request_rubric_review = rubric
    return calls


@pytest.mark.parametrize('mode', ['repair', 'redesign'])
def test_known_same_question_answer_correction_gets_all_independent_reviews(unit_case, mode):
    case = unit_case
    old = deepcopy(case.unit['candidate'])
    case.unit['work'].update(mode=mode, phase='write', redesign_pending_review=True,
                             rejected_design=deepcopy(old), repair_failures=4)
    revised = {**old, 'expected_answer': old['expected_answer'] + '必须先核对适用条件。'}
    calls = providers(case, revised)
    case.engine._advance_one(case.unit)
    assert case.unit['status'] == 'approved'
    assert case.unit['candidate']['prompt'] == old['prompt']
    assert calls[0]['repair_task']['must_redesign_question'] is False
    assert calls[1]['must_check_redesign'] is False
    assert case.seen == ['fact', 'rubric']
    assert case.unit['work']['approval']['candidate_hash'] == case.run.units()[0]['candidate_hash']


@pytest.mark.parametrize('veto', ['fact', 'teacher'])
def test_keeping_a_question_does_not_remove_real_content_veto(unit_case, veto):
    case = unit_case
    case.unit['work'].update(mode='redesign', phase='write')
    revised = deepcopy(case.unit['candidate'])
    revised['expected_answer'] += '仍有未经证实的结论。'
    providers(case, revised, veto)
    case.engine._advance_one(case.unit)
    assert case.unit['status'] != 'approved'
    assert case.unit['work']['failure_stage'] == veto
    assert 'approval' not in case.unit['work']


def test_exact_unchanged_known_candidate_still_rejected_before_fact(unit_case):
    case = unit_case
    case.unit['work'].update(mode='redesign', phase='write')
    providers(case, deepcopy(case.unit['candidate']))
    case.engine._advance_one(case.unit)
    assert case.unit['work']['failure_stage'] == 'unchanged'
    assert case.seen == []


def lost_request(case):
    token = repairs.current_unit.set(case.unit['unit_id'])
    try:
        with pytest.raises(LLMTimeoutError):
            case.run.structured(lambda: (_ for _ in ()).throw(LLMTimeoutError('controlled lost review')),
                                {'review': 'exact-old-request'})
    finally:
        repairs.current_unit.reset(token)
        repairs.last_submission.set(None)
    with repairs.connect(case.run.store.path) as db:
        db.execute('UPDATE teaching_course_submissions SET updated=?', (time.time() - 120,))


def test_native_lost_review_retirement_still_forces_different_question(unit_case):
    case = unit_case
    original = deepcopy(case.unit['candidate'])
    lost_request(case)
    calls = providers(case, {**original, 'expected_answer': original['expected_answer'] + '仅更正答案。'})
    case.engine._advance_one(case.unit)
    assert case.unit['status'] == 'waiting' and calls == []
    case.engine._advance_one(case.unit)
    assert case.run.has_retired_review(case.unit['unit_id'])
    assert not case.run.submission_blocked(case.unit['unit_id'])
    assert case.unit['work']['lost_review_requires_new_question'] is True
    assert case.unit['work']['failure_stage'] == 'redesign_unchanged'
    assert calls[0]['repair_task']['must_redesign_question'] is True
    assert case.seen == []
    # A later ordinary failed draft must not replace the frozen lost input.
    case.unit['candidate'] = {**original, 'prompt': '当前另一个未批准的问项'}
    case.unit['work']['mode'] = 'redesign'
    case.engine._advance_one(case.unit)
    assert case.unit['work']['rejected_design'] == original
    assert case.unit['work']['failure_stage'] == 'redesign_unchanged'
    token = repairs.current_unit.set(case.unit['unit_id'])
    try:
        with pytest.raises(repairs.TeachingRepairPending):
            case.run.structured(lambda: pytest.fail('Retired old request cannot be replayed'),
                                {'review': 'exact-old-request'})
    finally:
        repairs.current_unit.reset(token)


def test_legacy_retired_pending_flag_is_migrated_but_completed_recovery_not_reopened(unit_case):
    case = unit_case
    lost_request(case)
    assert case.run.retire_lost_review(case.unit['unit_id'])
    case.unit['work'].update(redesign_pending_review=True,
                             rejected_design=deepcopy(case.unit['candidate']))
    revised = deepcopy(case.unit['candidate'])
    revised['prompt'] = '如何判断另一组测量结果在改变条件后是否仍可使用？'
    case.unit['candidate'] = revised
    calls = providers(case, revised)
    case.engine._advance_one(case.unit)
    assert case.unit['status'] == 'approved'
    assert calls[0]['must_check_redesign'] is True
    assert case.unit['work']['lost_review_requires_new_question'] is False
    # The durable False records completion even though the retired ledger stays.
    case.unit['work'].update(mode='repair', phase='write', redesign_pending_review=True)
    providers(case, {**revised, 'expected_answer': revised['expected_answer'] + '补全有效条件。'})
    case.engine._advance_one(case.unit)
    assert case.unit['status'] == 'approved'
    assert case.run.has_retired_review(case.unit['unit_id'])
    assert case.unit['work']['lost_review_requires_new_question'] is False
