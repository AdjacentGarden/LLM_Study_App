"""Observed v7 question failures exercise shared prompts and mandatory veto/repair paths.

These scripted clients assert product contracts, not the semantic quality of model output.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from diagnostic_test_inputs import ArchivedFreeformDiagnosticGenerator
from test_diagnostic_chapter_generation_cache import MemoryApprovals

from adaptive_learning.assessment import item_generation as generation
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft, SourceQuote


@pytest.fixture(params=[0, 1, 2], ids=['cause-vs-mechanism', 'weak-distractors', 'fake-application'])
def observed_case(request):
    return json.loads((Path(__file__).parent / 'fixtures' /
        'diagnostic_v7_quality_patterns.json').read_text())['cases'][request.param]


def case_input(case):
    """Keep the actual rejected candidate and its role; siblings are explicit test inputs."""
    chapter_id = case['chapter_id']
    labels = ['受控测试概念甲', '受控测试概念乙']
    target_position = 0 if case['target_role'] == 'basic_recognition' else 2
    labels.insert(target_position, case['point_label'])
    evidence = {}
    rows = []
    for label in labels:
        if label == case['point_label']:
            evidence[label] = [SourceQuote(**row) for row in case['evidence']]
            rows.append(deepcopy(case['candidate']))
        else:
            evidence[label] = [SourceQuote(page_number=case['evidence'][0]['page_number'],
                quote=f'{label}是明确标记的离线测试输入，不代表教材内容。')]
            rows.append({'knowledge_point_id': generation.stable_knowledge_point_id(chapter_id, label),
                'type': 'choice', 'prompt': '哪项对应受控测试概念的输入标识？',
                'options': [label, '受控干扰项一', '受控干扰项二', '受控干扰项三'],
                'correct_index': 0, 'expected_answer': label, 'rubric': [], 'difficulty': 0,
                'estimated_seconds': 40})
    quotes = [quote for values in evidence.values() for quote in values]
    chapter = ChapterDraft(chapter_id=chapter_id, order=1, title=case['chapter_title'],
        start_page=min(quote.page_number for quote in quotes),
        end_page=max(quote.page_number for quote in quotes), summary='Offline observed-pattern replay',
        knowledge_points=labels, source_block_ids=[], evidence=quotes, knowledge_point_evidence=evidence)
    structure = BookStructure(title='Observed pattern with explicit controlled siblings',
        summary='Only the target is a recorded real candidate.', source_page_count=125, chapters=[chapter])
    return structure, {'items': rows}


def assert_shared_contract(system, contract):
    assert contract in system
    assert '上游原因、下游机制、定义中的结构变化或必要条件可能同时成立' in contract
    assert '不能仅凭“只能”“全部”“仅”等绝对词就认定选项错误' in contract
    assert '具体误解的人可能选择的同轴判断' in contract
    assert '场景只是外壳，应重设计而不能当作应用达标' in contract
    assert '一条有据条件推一个尚未给出的结果' in contract


@pytest.mark.parametrize('surface_rewrite', [False, True])
def test_observed_defects_keep_independent_veto_feedback_and_never_enter_approval_cache(observed_case, surface_rewrite):
    structure, response = case_input(observed_case)
    target = observed_case['candidate']['knowledge_point_id']
    writes, reviews = [], []
    class Writer:
        def structured(self, *, system, user, **kwargs):
            payload = json.loads(user)
            writes.append(payload)
            assert_shared_contract(system, payload['question_contract'])
            if len(writes) == 2:
                feedback = next(row for row in payload['repair_feedback']['reviews']
                                if row['knowledge_point_id'] == target)
                assert feedback == {'knowledge_point_id': target, 'accepted': False,
                                    'issues': [observed_case['human_issue']]}
                assert len(payload['preserve_accepted_items']) == 2
                assert next(row for row in payload['previous_candidate']['items']
                    if row['knowledge_point_id'] == target)['options'] == observed_case['candidate']['options']
            value = deepcopy(response)
            if surface_rewrite and len(writes) == 2:
                # A cosmetic writer edit is still submitted to the independent
                # judge; changed text is never a programmatic quality approval.
                item = next(row for row in value['items'] if row['knowledge_point_id'] == target)
                item['prompt'] = item['prompt'].replace('？', '，请据教材判断？')
            return value
    class Reviewer:
        def structured(self, *, system, user, **kwargs):
            payload = json.loads(user)
            reviews.append(payload)
            assert_shared_contract(system, payload['question_contract'])
            assert payload['question_contract'] == writes[-1]['question_contract']
            point = next(row for row in payload['source']['knowledge_points']
                         if row['knowledge_point_id'] == target)
            assert point['cognitive_target']['id'] == observed_case['target_role']
            assert point['evidence'] == [SourceQuote(**row).model_dump(mode='json')
                                        for row in observed_case['evidence']]
            if len(reviews) == 2:
                assert [row['knowledge_point_id'] for row in payload['items']] == [target]
            return {'reviews': [{'knowledge_point_id': row['knowledge_point_id'],
                'accepted': row['knowledge_point_id'] != target,
                'issues': [observed_case['human_issue']] if row['knowledge_point_id'] == target else []}
                for row in payload['items']]}
    cache = MemoryApprovals()
    subject = ArchivedFreeformDiagnosticGenerator(Writer(), review_client=Reviewer(),
        validation_retries=1, allow_grounded_answer_rewrite=True)
    with pytest.raises(generation.DiagnosticGenerationError, match='review rejected'):
        subject.generate(structure, chapter_cache=cache, cache_scope='offline_observed_book')
    assert len(writes) == len(reviews) == 2
    assert cache.outcomes == ['rejected']
    assert not any(row.get('status') == 'approved' for row in cache.records.values())


def test_new_protocol_does_not_reuse_an_old_approval_of_the_observed_defect(observed_case, monkeypatch):
    structure, response = case_input(observed_case)
    target = observed_case['candidate']['knowledge_point_id']
    calls = []
    class Writer:
        def structured(self, **kwargs):
            calls.append('writer')
            return deepcopy(response)
    class Reviewer:
        reject = False
        def structured(self, *, user, **kwargs):
            calls.append('reviewer')
            payload = json.loads(user)
            return {'reviews': [{'knowledge_point_id': row['knowledge_point_id'],
                'accepted': not self.reject or row['knowledge_point_id'] != target,
                'issues': [observed_case['human_issue']] if self.reject and row['knowledge_point_id'] == target else []}
                for row in payload['items']]}
    current = generation.DIAGNOSTIC_BANK_VERSION
    prior = 'grounded-independent-review-v7+chapter-approvals-v1+balanced-options-v1'
    assert current != prior
    cache, reviewer = MemoryApprovals(), Reviewer()
    subject = ArchivedFreeformDiagnosticGenerator(Writer(), review_client=reviewer,
        validation_retries=0, allow_grounded_answer_rewrite=True)
    # This is an explicit historical-approval adapter simulation, not a claim
    # that v7 and v8 prompts are the same or that the real bank was changed.
    monkeypatch.setattr(generation, 'DIAGNOSTIC_BANK_VERSION', prior)
    subject.generate(structure, chapter_cache=cache, cache_scope='offline_book')
    old_identity = deepcopy(cache.claims[-1])
    reviewer.reject = True
    monkeypatch.setattr(generation, 'DIAGNOSTIC_BANK_VERSION', current)
    with pytest.raises(generation.DiagnosticGenerationError, match='review rejected'):
        subject.generate(structure, chapter_cache=cache, cache_scope='offline_book')
    new_identity = cache.claims[-1]
    assert old_identity['source_fingerprint'] == new_identity['source_fingerprint']
    assert old_identity['protocol_fingerprint'] != new_identity['protocol_fingerprint']
    assert old_identity['review_fingerprint'] != new_identity['review_fingerprint']
    assert calls == ['writer', 'reviewer', 'writer', 'reviewer']


@pytest.mark.parametrize('independent_accepts', [True, False])
def test_low_burden_condition_mapping_with_absolute_wording_is_not_a_keyword_veto(independent_accepts):
    quote = SourceQuote(page_number=1,
        quote='只有条件甲成立，过程乙才适用。条件甲不成立时，过程乙不适用。')
    label, chapter_id = '过程乙的适用条件', 'controlled_condition'
    point = generation.stable_knowledge_point_id(chapter_id, label)
    # Put the target at the application position. Its scenario premise is
    # actually used to select applicability; no additional mechanism is invented.
    case = {'chapter_id': chapter_id, 'chapter_title': 'Explicit controlled source',
        'point_label': label, 'target_role': 'source_application',
        'evidence': [quote.model_dump(mode='json')], 'candidate': {
            'knowledge_point_id': point, 'type': 'choice',
            'prompt': '样本一满足条件甲，样本二不满足条件甲。根据原文，过程乙对两个样本的适用关系如何？',
            'options': ['只适用于样本一', '只适用于样本二', '同时适用于两个样本', '对两个样本都不适用'],
            'correct_index': 0, 'expected_answer': '只适用于样本一',
            'rubric': [], 'difficulty': 0, 'estimated_seconds': 45}}
    structure, response = case_input(case)
    calls = []
    class Writer:
        def structured(self, **kwargs):
            return deepcopy(response)
    class Reviewer:
        def structured(self, *, system, user, **kwargs):
            request = json.loads(user)
            calls.append(request)
            assert_shared_contract(system, request['question_contract'])
            actual = next(row for row in request['items'] if row['knowledge_point_id'] == point)
            assert set(actual['options']) == set(case['candidate']['options'])
            assert actual['options'][actual['correct_index']] == '只适用于样本一'
            return {'reviews': [{'knowledge_point_id': row['knowledge_point_id'],
                'accepted': independent_accepts, 'issues': [] if independent_accepts else ['Controlled semantic veto']}
                for row in request['items']]}
    subject = ArchivedFreeformDiagnosticGenerator(Writer(), review_client=Reviewer(),
        allow_grounded_answer_rewrite=True, validation_retries=0)
    if independent_accepts:
        assert len(subject.generate(structure)) == 3
    else:
        with pytest.raises(generation.DiagnosticGenerationError, match='Controlled semantic veto'):
            subject.generate(structure)
    assert len(calls) == 1
