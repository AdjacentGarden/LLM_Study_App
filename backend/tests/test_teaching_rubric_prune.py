"""Program-owned rubric deletion is a draft, never a review or approval."""
from copy import deepcopy

import pytest
from test_teaching_content import positive_rubric_alignment_response
from test_teaching_unit_engine_rubric_only import (
    alignment,
    reject_rubric,
)
from test_teaching_unit_engine_rubric_only import (
    unit_case as _source_unit_case,
)

from adaptive_learning.personalization.rubric_alignment import (
    make_rubric_alignment_batch,
    parse_rubric_alignment_response,
)
from adaptive_learning.personalization.teaching_unit_engine import _UnitRejected

unit_case = _source_unit_case


def positive_review(case):
    def rubric(batch):
        case.seen.append('rubric-again')
        return parse_rubric_alignment_response(positive_rubric_alignment_response(batch.to_payload()), batch)
    case.generator._request_rubric_review = rubric


def test_typed_prune_skips_writer_preserves_all_other_fields_and_rechecks(unit_case):
    case = unit_case
    reject_rubric(case)
    old = deepcopy(case.unit['candidate'])
    case.unit['work']['repair_failures'] = 8
    case.unit['work']['mode'] = 'redesign'  # historical threshold must not destroy a good task
    case.engine._write_one = lambda unit: pytest.fail('Deletion draft must not pay for a writer')
    positive_review(case)
    case.seen.clear()
    case.engine._advance_one(case.unit)
    assert case.seen == ['fact', 'teacher', 'rubric-again']
    assert case.unit['candidate']['rubric'] == old['rubric'][1:]
    assert {key: value for key, value in case.unit['candidate'].items() if key != 'rubric'} == {
        key: value for key, value in old.items() if key != 'rubric'}
    assert case.unit['status'] == 'approved'
    assert case.unit['work']['approval']['candidate_hash'] == case.run.units()[0]['candidate_hash']


def test_qualified_rubric_deletion_does_not_promote_to_redesign(unit_case):
    unit_case.unit['work']['repair_failures'] = 2
    reject_rubric(unit_case)
    assert unit_case.unit['work']['mode'] == 'repair'
    assert unit_case.unit['status'] != 'approved'
    assert unit_case.engine._rubric_prune_draft(unit_case.unit) is not None


@pytest.mark.parametrize('fault', ['task_revision', 'all_false', 'missing', 'wrong_text'])
def test_no_pruning_without_complete_necessary_typed_criteria(unit_case, fault):
    case = unit_case
    batch = make_rubric_alignment_batch([
        {key: case.unit['candidate'][key] for key in ('item_id', 'prompt', 'rubric')}])
    verdict = alignment(case.unit, 'task_revision' if fault == 'task_revision' else 'rubric_only')
    feedback = verdict.to_dict()
    if fault == 'all_false':
        checks = feedback['items'][0]['rubric_checks']
        other = deepcopy(checks[0])
        other.update(rubric_index=1, rubric_text=batch.items[0].rubric[1],
                     rubric_quote=batch.items[0].rubric[1])
        checks[1] = other
    elif fault == 'missing':
        feedback['items'][0]['rubric_checks'].pop()
    elif fault == 'wrong_text':
        feedback['items'][0]['rubric_checks'][0]['rubric_text'] += 'tampered'
    case.engine._lock_rubric(case.unit)
    case.unit['work'].update(failure_stage='rubric', semantic_feedback=feedback)
    assert case.engine._rubric_prune_draft(case.unit) is None


@pytest.mark.parametrize('fault', ['candidate', 'evidence', 'real_fact_veto'])
def test_prune_cannot_cross_the_actual_task_source_or_fact_boundary(unit_case, fault):
    case = unit_case
    reject_rubric(case)
    if fault == 'candidate':
        case.unit['candidate']['expected_answer'] += 'changed'
    elif fault == 'evidence':
        case.unit['work']['rubric_only_lock']['evidence_fingerprint'] = 'other-source'
    else:
        case.engine._failed(case.unit, _UnitRejected({'issue': '事实需要修正。'}, 'fact'), 'repair')
    assert case.engine._rubric_prune_draft(case.unit) is None


def test_unknown_fence_runs_before_pruning_and_does_not_submit(unit_case):
    case = unit_case
    reject_rubric(case)
    before = deepcopy(case.unit['candidate'])
    case.unit['work']['phase'] = 'write'
    case.run.submission_blocked = lambda unit_id: True
    case.engine._rubric_prune_draft = lambda unit: pytest.fail('Unknown writer cannot reach pruning')
    case.engine._advance_one(case.unit)
    assert case.unit['candidate'] == before
    assert case.unit['status'] == 'waiting'


@pytest.mark.parametrize('gate', ['fact', 'teacher'])
def test_pruned_candidate_still_fails_a_new_independent_veto(unit_case, gate):
    case = unit_case
    reject_rubric(case)
    case.engine._write_one = lambda unit: pytest.fail('Deletion does not require a writer')
    def veto(unit):
        raise _UnitRejected({'issue': '新的独审明确否决当前事实。'}, 'fact')
    if gate == 'fact':
        case.engine._fact_one = veto
    else:
        case.generator._structured = lambda **kwargs: {
            'accepted': False, 'redesign_ok': True, 'issues': ['当前题设仍有事实错误。']}
    case.engine._advance_one(case.unit)
    assert case.unit['status'] != 'approved'
    assert case.unit['work']['failure_stage'] == gate
    assert 'rubric_only_lock' not in case.unit['work']
