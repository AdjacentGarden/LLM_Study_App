"""Shared capacity checks; canned wire replies do not establish semantic quality."""
from copy import deepcopy

import pytest

from adaptive_learning.personalization.models import RUBRIC_MAX_ITEMS
from adaptive_learning.personalization.rubric_alignment import (
    RubricAlignmentError,
    make_rubric_alignment_batch,
    parse_rubric_alignment_response,
)


def batch_and_response(count):
    criteria = [f'第{index + 1}项明确要求' for index in range(count)]
    batch = make_rubric_alignment_batch([
        {'item_id': 'capacity', 'prompt': '逐项回答题目要求。', 'rubric': criteria}])
    raw = {'items': [{
        'item_id': 'capacity', 'minimal_sufficient_answer': '机械验证用的完整答复。',
        'rubric_checks': [{
            'rubric_index': index, 'rubric_text': text, 'required': True,
            'reason': '仅验证完整定位与容量合同，不替代独立语义审核。',
            'prompt_quote': '回答题目要求', 'rubric_quote': text,
            'counterexample_answer': None, 'repair': None,
        } for index, text in enumerate(criteria)],
    }]}
    return batch, raw


@pytest.mark.parametrize('count', [1, 6, 7, 12])
def test_writer_and_independent_reviewer_accept_same_supported_capacity(count):
    batch, raw = batch_and_response(count)
    result = parse_rubric_alignment_response(raw, batch)
    assert result.accepted
    assert [row.rubric_index for row in result.items[0].rubric_checks] == list(range(count))
    assert batch.to_payload()['output_contract']['rubric_indices_by_item']['capacity'] == list(range(count))


def test_shared_max_controls_provider_schema_and_rejects_thirteenth_input():
    batch, _ = batch_and_response(RUBRIC_MAX_ITEMS)
    schema = batch.response_schema()['$defs']
    assert schema['_Criterion']['properties']['rubric_index']['maximum'] == RUBRIC_MAX_ITEMS - 1
    assert schema['_ItemVerdict']['properties']['rubric_checks']['maxItems'] == RUBRIC_MAX_ITEMS
    with pytest.raises(RubricAlignmentError):
        batch_and_response(RUBRIC_MAX_ITEMS + 1)


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'out_of_range', 'thirteenth'])
def test_expanded_capacity_still_requires_every_exact_index_once(fault):
    batch, raw = batch_and_response(RUBRIC_MAX_ITEMS)
    checks = raw['items'][0]['rubric_checks']
    if fault == 'missing':
        checks.pop()
    elif fault == 'duplicate':
        checks[-1] = deepcopy(checks[0])
    elif fault == 'out_of_range':
        checks[-1]['rubric_index'] = RUBRIC_MAX_ITEMS
    else:
        checks.append(deepcopy(checks[-1]))
    with pytest.raises(RubricAlignmentError):
        parse_rubric_alignment_response(raw, batch)


def test_twelfth_negative_verdict_is_preserved_and_not_approved():
    batch, raw = batch_and_response(RUBRIC_MAX_ITEMS)
    last = raw['items'][0]['rubric_checks'][-1]
    last.update(required=False, counterexample_answer='完整回答问项但不作额外比较。',
                reason='当前问项没有要求额外比较，该评分点会排斥正确答复。',
                repair='删除未问比较要求，保留实际问项对应的评分。', repair_scope='rubric_only')
    result = parse_rubric_alignment_response(raw, batch)
    assert not result.accepted
    rejected = result.items[0].rubric_checks[-1]
    assert rejected.rubric_index == RUBRIC_MAX_ITEMS - 1
    assert rejected.required is False
    assert rejected.repair_scope == 'rubric_only'
    assert result.writer_feedback[0]['rubric_index'] == RUBRIC_MAX_ITEMS - 1
