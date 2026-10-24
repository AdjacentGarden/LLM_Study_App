"""Replay the observed v8 dead-end and exercise safe, explicit application representations."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_diagnostic_chapter_generation_cache import MemoryApprovals
from test_diagnostic_v6_repair import structure_for
from test_diagnostic_v8_quality_patterns import case_input

from adaptive_learning.assessment import item_generation as generation
from adaptive_learning.ingestion.models import SourceQuote


@pytest.fixture
def recorded():
    return json.loads((Path(__file__).parent / 'fixtures' /
        'diagnostic_v8_application_repair.json').read_text())


def assert_construction_contract(system, contract):
    assert contract in system
    assert '记录甲乙、编号、颜色或方案等中性表示' in contract
    assert '不要求原文出现相同编号或颜色' in contract
    assert '不得把无源的学科机制、数量关系或因果事实包装成“假设/给定”' in contract
    assert '修复建议也要说明可用的原文关系' in contract


def test_actual_three_rounds_keep_complete_feedback_but_never_approve_fake_application(recorded):
    target = recorded['target_knowledge_point_id']
    writes, reviews = [], []
    class Writer:
        def structured(self, *, system, user, **kwargs):
            request = json.loads(user)
            writes.append(request)
            assert_construction_contract(system, request['question_contract'])
            if len(writes) > 1:
                previous = recorded['attempts'][len(writes) - 2]
                assert request['repair_feedback']['reviews'] == previous['review_verdict']['reviews']
                assert next(row for row in request['previous_candidate']['items']
                            if row['knowledge_point_id'] == target)['prompt'] == next(
                    row for row in previous['generation_response']['items']
                    if row['knowledge_point_id'] == target)['prompt']
                assert len(request['preserve_accepted_items']) == 2
            return deepcopy(recorded['attempts'][len(writes) - 1]['generation_response'])
    class Reviewer:
        def structured(self, *, system, user, **kwargs):
            request = json.loads(user)
            reviews.append(request)
            assert_construction_contract(system, request['question_contract'])
            assert request['question_contract'] == writes[-1]['question_contract']
            actual = next(row for row in request['source']['knowledge_points']
                          if row['knowledge_point_id'] == target)
            source = next(row for row in recorded['source']['knowledge_points']
                          if row['knowledge_point_id'] == target)
            assert actual['evidence'] == source['evidence']
            assert actual['cognitive_target']['id'] == 'source_application'
            assert [row['knowledge_point_id'] for row in request['items']] == (
                [row['knowledge_point_id'] for row in recorded['attempts'][0]['generation_response']['items']]
                if len(reviews) == 1 else [target])
            return deepcopy(recorded['attempts'][len(reviews) - 1]['review_verdict'])
    cache = MemoryApprovals()
    subject = generation.DiagnosticItemGenerator(Writer(), review_client=Reviewer(),
        allow_grounded_answer_rewrite=True, validation_retries=2)
    with pytest.raises(generation.DiagnosticGenerationError, match='review rejected'):
        subject.generate(structure_for(recorded['source']), chapter_cache=cache, cache_scope='offline_book')
    assert len(writes) == len(reviews) == 3
    assert cache.outcomes == ['rejected']
    assert not any(row.get('status') == 'approved' for row in cache.records.values())


@pytest.mark.parametrize('given_prefix', ['', '题设给定：'])
def test_unsupported_mechanisms_cannot_be_laundered_as_given_conditions(recorded, given_prefix):
    response = deepcopy(recorded['attempts'][2]['generation_response'])
    target = recorded['target_knowledge_point_id']
    item = next(row for row in response['items'] if row['knowledge_point_id'] == target)
    if given_prefix:
        item['prompt'] = given_prefix + '解旋酶提供游离原料。该细胞复制精确性主要依靠什么保证？'
    item['given_conditions'] = ['解旋酶提供游离原料']
    item['source_supported'] = True
    item['application_plan'] = {'accepted': True}
    seen = []
    class Writer:
        def structured(self, **kwargs):
            return deepcopy(response)
    class Reviewer:
        def structured(self, *, system, user, **kwargs):
            request = json.loads(user)
            seen.append(request)
            assert_construction_contract(system, request['question_contract'])
            row = next(row for row in request['items'] if row['knowledge_point_id'] == target)
            assert 'given_conditions' not in row and 'source_supported' not in row and 'application_plan' not in row
            assert row['prompt'] == item['prompt']
            assert set(row['options']) == set(item['options'])
            return {'reviews': [{'knowledge_point_id': row['knowledge_point_id'],
                'accepted': row['knowledge_point_id'] != target,
                'issues': [] if row['knowledge_point_id'] != target else [
                    '给定表示不能把无源的酶机制或数量结论变成教材事实；原假应用仍不合格。']}
                for row in request['items']]}
    with pytest.raises(generation.DiagnosticGenerationError, match='无源'):
        generation.DiagnosticItemGenerator(Writer(), review_client=Reviewer(),
            allow_grounded_answer_rewrite=True, validation_retries=0).generate(structure_for(recorded['source']))
    assert len(seen) == 1


def controlled_representation_case():
    chapter = 'controlled_representation'
    label = '过程乙的适用关系'
    point = generation.stable_knowledge_point_id(chapter, label)
    quote = SourceQuote(page_number=1, quote='条件甲成立时过程乙适用；条件甲不成立时过程乙不适用。')
    case = {'chapter_id': chapter, 'chapter_title': 'Explicit controlled source',
        'point_label': label, 'target_role': 'source_application', 'evidence': [quote.model_dump(mode='json')],
        'candidate': {'knowledge_point_id': point, 'type': 'choice',
            'prompt': '记录1用蓝色表示条件甲成立；记录2用灰色表示条件甲不成立。哪种处理方案与原文适用条件一致？',
            'options': ['仅将过程乙用于记录1', '仅将过程乙用于记录2', '将过程乙用于两条记录', '两条记录均不用过程乙'],
            'correct_index': 0, 'expected_answer': '仅将过程乙用于记录1', 'rubric': [],
            'difficulty': 0, 'estimated_seconds': 45}}
    return case, case_input(case)


@pytest.mark.parametrize('accept', [True, False])
def test_explicit_record_and_color_mapping_still_requires_independent_approval(accept):
    case, (structure, response) = controlled_representation_case()
    target = case['candidate']['knowledge_point_id']
    requests = []
    class Writer:
        def structured(self, **kwargs):
            return deepcopy(response)
    class Reviewer:
        def structured(self, *, system, user, **kwargs):
            request = json.loads(user)
            requests.append(request)
            assert_construction_contract(system, request['question_contract'])
            source = next(row for row in request['source']['knowledge_points']
                          if row['knowledge_point_id'] == target)
            assert '蓝色' not in source['evidence'][0]['quote']
            assert '灰色' not in source['evidence'][0]['quote']
            actual = next(row for row in request['items'] if row['knowledge_point_id'] == target)
            assert actual['prompt'] == case['candidate']['prompt']
            assert actual['options'][actual['correct_index']] == case['candidate']['expected_answer']
            return {'reviews': [{'knowledge_point_id': row['knowledge_point_id'],
                'accepted': accept, 'issues': [] if accept else ['Controlled independent relation/uniqueness veto']}
                for row in request['items']]}
    cache = MemoryApprovals()
    subject = generation.DiagnosticItemGenerator(Writer(), review_client=Reviewer(),
        allow_grounded_answer_rewrite=True, validation_retries=0)
    if accept:
        assert len(subject.generate(structure, chapter_cache=cache, cache_scope='offline_book')) == 3
        assert sum(row['status'] == 'approved' for row in cache.records.values()) == 1
    else:
        with pytest.raises(generation.DiagnosticGenerationError, match='uniqueness veto'):
            subject.generate(structure, chapter_cache=cache, cache_scope='offline_book')
        assert cache.outcomes == ['rejected']
    assert len(requests) == 1


def test_new_construction_protocol_rechecks_previous_v8_approval(monkeypatch):
    case, (structure, response) = controlled_representation_case()
    calls = []
    class Writer:
        def structured(self, **kwargs):
            calls.append('writer')
            return deepcopy(response)
    class Reviewer:
        def structured(self, *, user, **kwargs):
            calls.append('reviewer')
            return {'reviews': [{'knowledge_point_id': row['knowledge_point_id'], 'accepted': True, 'issues': []}
                for row in json.loads(user)['items']]}
    subject = generation.DiagnosticItemGenerator(Writer(), review_client=Reviewer(),
        allow_grounded_answer_rewrite=True, validation_retries=0)
    cache = MemoryApprovals()
    current = generation.DIAGNOSTIC_BANK_VERSION
    prior = 'grounded-independent-review-v8+chapter-approvals-v1+balanced-options-v1'
    assert current != prior
    monkeypatch.setattr(generation, 'DIAGNOSTIC_BANK_VERSION', prior)
    subject.generate(structure, chapter_cache=cache, cache_scope='offline_book')
    old = deepcopy(cache.claims[-1])
    monkeypatch.setattr(generation, 'DIAGNOSTIC_BANK_VERSION', current)
    subject.generate(structure, chapter_cache=cache, cache_scope='offline_book')
    new = cache.claims[-1]
    assert old['source_fingerprint'] == new['source_fingerprint']
    assert old['protocol_fingerprint'] != new['protocol_fingerprint']
    assert old['review_fingerprint'] != new['review_fingerprint']
    assert calls == ['writer', 'reviewer', 'writer', 'reviewer']
