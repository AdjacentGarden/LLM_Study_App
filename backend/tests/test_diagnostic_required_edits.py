"""Mechanical repair contracts; scripted verdicts never certify pedagogical quality."""
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_diagnostic_generation import structure, valid_payload

from adaptive_learning.assessment import item_generation as generation


def verdict(key, *, accepted=True, prompt=False, indices=()):
    return {'knowledge_point_id': key, 'accepted': accepted,
            'issues': [] if accepted else ['指定题干或选项存在实际缺陷'],
            'required_edits': None if accepted else {'prompt': prompt, 'option_indices': list(indices)}}


class Writer:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def structured(self, *, user, **kwargs):
        self.calls.append(json.loads(user))
        return deepcopy(self.responses[min(len(self.calls)-1, len(self.responses)-1)])


class Reviewer:
    def __init__(self, reject):
        self.reject, self.calls = reject, []

    def structured(self, *, user, **kwargs):
        assert kwargs['max_tokens'] == 1400
        request = json.loads(user)
        self.calls.append(request)
        return {'reviews': [self.reject(row, len(self.calls)) for row in request['items']]}


def run(writer, reviewer, retries=2, events=None):
    return generation.DiagnosticItemGenerator(writer, review_client=reviewer,
        allow_grounded_answer_rewrite=True, validation_retries=retries,
        audit_callback=events.append if events is not None else None).generate(structure())


@pytest.mark.parametrize('bad', [None, {}, {'prompt': False, 'option_indices': []},
    {'prompt': True, 'option_indices': [0, 0]}, {'prompt': False, 'option_indices': [-1]},
    {'prompt': False, 'option_indices': [4]}, {'prompt': False, 'option_indices': [True]},
    {'prompt': False, 'option_indices': [1.0]}, {'prompt': False, 'option_indices': ['1']},
    {'prompt': 'true', 'option_indices': []}, {'prompt': True, 'option_indices': [], 'unknown': 1}])
def test_rejections_require_strict_concrete_targets(bad):
    row = verdict('kp', accepted=False, prompt=True)
    row['required_edits'] = bad
    with pytest.raises(ValidationError):
        generation.ItemReview.model_validate(row)


@pytest.mark.parametrize('change', ['missing', 'issues', 'edits', 'unknown', 'empty_edits'])
def test_acceptance_cannot_hide_issues_edits_missing_contract_or_unknown_fields(change):
    row = verdict('kp')
    if change == 'missing':
        row.pop('required_edits')
    elif change == 'issues':
        row['issues'] = ['仍有问题']
    elif change == 'edits':
        row['required_edits'] = {'prompt': True, 'option_indices': []}
    elif change == 'empty_edits':
        row['required_edits'] = {'prompt': False, 'option_indices': []}
    else:
        row['other'] = True
    with pytest.raises(ValidationError):
        generation.ItemReview.model_validate(row)


def test_shuffled_review_indices_map_to_exact_strings_and_never_reach_writer_as_indices():
    first = valid_payload()
    second = deepcopy(first)
    target = first['items'][0]['knowledge_point_id']
    bad = first['items'][0]['options'][2]
    second['items'][0]['options'][2] = '已重新设计的不同干扰判断'
    observed = []
    def judge(row, call):
        if row['knowledge_point_id'] != target or call > 1:
            return verdict(row['knowledge_point_id'])
        index = row['options'].index(bad)
        observed.append(index)
        return verdict(target, accepted=False, indices=[index])
    writer, reviewer = Writer([first, second]), Reviewer(judge)
    assert len(run(writer, reviewer)) == 2
    repair = writer.calls[1]['required_repairs'][0]
    assert repair == {'knowledge_point_id': target, 'prompt': None, 'option_texts': [bad]}
    assert 'option_indices' not in json.dumps(writer.calls[1]['repair_feedback'])
    assert len(reviewer.calls) == 2 and len(reviewer.calls[1]['items']) == 1
    assert first['items'][0]['options'].index(bad) != observed[0]  # Real permutation, not an identity-only test.


@pytest.mark.parametrize('change', ['move', 'whitespace', 'nfkc', 'casefold', 'format', 'choice_prefix', 'difficulty', 'answer'])
def test_all_flagged_old_text_must_disappear_across_every_position(change):
    first = valid_payload()
    first['items'][0]['options'][1] = 'BAD option'
    second = deepcopy(first)
    row = second['items'][0]
    if change == 'move':
        row['options'][1], row['options'][3] = row['options'][3], row['options'][1]
    elif change == 'whitespace':
        row['options'][1] = ' B A D  option\n'
    elif change == 'nfkc':
        row['options'][1] = 'ＢＡＤ option'
    elif change == 'casefold':
        row['options'][1] = 'bad OPTION'
    elif change == 'format':
        row['options'][1] = 'BAD\u200b option'
    elif change == 'choice_prefix':
        row['options'][1] = '选项Ｂ： BAD option'
    elif change == 'difficulty':
        row['difficulty'] = 1
    else:
        row['options'][0], row['options'][2] = row['options'][2], row['options'][0]
        row['correct_index'] = 2
    key = row['knowledge_point_id']
    def judge(item, call):
        return verdict(item['knowledge_point_id'], accepted=item['knowledge_point_id'] != key,
                       indices=[item['options'].index('BAD option')] if item['knowledge_point_id'] == key else [])
    writer, reviewer = Writer([first, second]), Reviewer(judge)
    with pytest.raises(generation.DiagnosticGenerationError, match='required diagnostic edits'):
        run(writer, reviewer)
    assert len(writer.calls) == 3 and len(reviewer.calls) == 1
    assert writer.calls[2]['required_repairs'] == writer.calls[1]['required_repairs']


def test_prompt_obligation_survives_an_intermediate_other_item_schema_failure():
    first = valid_payload()
    malformed = deepcopy(first)
    malformed['items'][1]['options'] = ['invalid']
    last = deepcopy(first)
    last['items'][0]['prompt'] = ' 下面哪项准确说明了甲？\u200b '
    key = first['items'][0]['knowledge_point_id']
    reviewer = Reviewer(lambda row, call: verdict(row['knowledge_point_id'],
        accepted=row['knowledge_point_id'] != key, prompt=True))
    writer = Writer([first, malformed, last])
    with pytest.raises(generation.DiagnosticGenerationError, match='required diagnostic edits'):
        run(writer, reviewer)
    assert len(writer.calls) == 3 and len(reviewer.calls) == 1
    assert writer.calls[2]['required_repairs'][0]['prompt'] == first['items'][0]['prompt']


def test_changed_fields_are_not_approval_and_each_new_rejection_replaces_only_its_exact_baseline():
    first = valid_payload()
    second, third = deepcopy(first), deepcopy(first)
    key = first['items'][0]['knowledge_point_id']
    second['items'][0]['prompt'] = '按原文限定，下面哪项说明了甲？'
    third['items'][0]['prompt'] = '原文中的甲符合下面哪项判断？'
    reviewer = Reviewer(lambda row, call: verdict(row['knowledge_point_id'],
        accepted=row['knowledge_point_id'] != key, prompt=True))
    writer = Writer([first, second, third])
    with pytest.raises(generation.DiagnosticGenerationError, match='review rejected'):
        run(writer, reviewer)
    assert len(writer.calls) == len(reviewer.calls) == 3
    assert writer.calls[2]['required_repairs'][0]['prompt'] == second['items'][0]['prompt']
    assert [len(row['items']) for row in reviewer.calls] == [2, 1, 1]
    assert all('repair_feedback' not in request and 'previous_candidate' not in request for request in reviewer.calls)


def test_changed_previously_approved_item_still_rejected_before_review():
    first = valid_payload()
    second = deepcopy(first)
    key = first['items'][0]['knowledge_point_id']
    second['items'][0]['prompt'] += '请判断。'
    second['items'][1]['prompt'] += '请判断。'
    reviewer = Reviewer(lambda row, call: verdict(row['knowledge_point_id'],
        accepted=row['knowledge_point_id'] != key, prompt=True))
    with pytest.raises(generation.DiagnosticGenerationError, match='changed previously accepted'):
        run(Writer([first, second]), reviewer, retries=1)
    assert len(reviewer.calls) == 1


def test_shared_contract_closes_target_identity_and_condition_check_collisions_without_approval():
    common = generation._QUESTION_CONTRACT + generation._APPLICATION_CONTRACT
    assert '目标对象' in common and '作用位置' in common
    assert '未预先列全判断标准' in common and '必要条件' in common
    assert '充分保证' in common and '项目最多' in common and '可信同轴干扰' in common
    assert generation.DIAGNOSTIC_BANK_VERSION.startswith('grounded-independent-review-v17+required-edits-v1+')


def test_source_qualifier_is_a_real_edit_but_still_requires_independent_review():
    first = valid_payload()
    first['items'][0]['options'][1] = '非等位基因自由组合'
    second = deepcopy(first)
    second['items'][0]['options'][1] = '非同源染色体上的非等位基因自由组合'
    key = first['items'][0]['knowledge_point_id']
    def judge(row, call):
        if call == 1 and row['knowledge_point_id'] == key:
            return verdict(key, accepted=False, indices=[row['options'].index('非等位基因自由组合')])
        return verdict(row['knowledge_point_id'])
    reviewer = Reviewer(judge)
    assert len(run(Writer([first, second]), reviewer, retries=1)) == 2
    assert len(reviewer.calls) == 2  # Not approved by the textual change itself.


@pytest.mark.parametrize('before,after', [('5', '1.5'), ('2', '1:2'),
                                         ('非等位基因自由组合', '非同源染色体上的非等位基因自由组合')])
def test_normalizer_does_not_remove_numeric_facts_or_semantic_qualifiers(before, after):
    assert generation._repair_text(before) != generation._repair_text(after)


def test_all_prior_rejected_option_texts_remain_forbidden_until_independent_approval():
    first = valid_payload()
    key = first['items'][0]['knowledge_point_id']
    old = first['items'][0]['options'][1]
    second = deepcopy(first)
    second['items'][0]['options'][1] = '新的但尚有问题的同轴判断'
    third = deepcopy(second)
    third['items'][0]['prompt'] += '请作判断。'
    third['items'][0]['options'][1] = old
    def judge(row, call):
        if row['knowledge_point_id'] != key:
            return verdict(row['knowledge_point_id'])
        if call == 1:
            return verdict(key, accepted=False, indices=[row['options'].index(old)])
        return verdict(key, accepted=False, prompt=True)
    writer, reviewer = Writer([first, second, third]), Reviewer(judge)
    with pytest.raises(generation.DiagnosticGenerationError, match='required diagnostic edits'):
        run(writer, reviewer)
    assert len(writer.calls) == 3 and len(reviewer.calls) == 2
    assert writer.calls[2]['required_repairs'][0]['option_texts'] == [old]


@pytest.mark.parametrize('case_index', range(4), ids=['gene-control', 'recipient', 'replication', 'disease'])
def test_actual_h_four_partial_or_semantic_repairs_obey_explicit_edit_contract(case_index):
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / 'fixtures' /
                          'diagnostic_v16_required_edit_regression.json').read_text())
    case = fixture['cases'][case_index]
    before, after = case['previous_candidate'], case['next_candidate']
    edits = case['explicit_test_edit_targets']
    key = before['knowledge_point_id']
    target = generation._ItemRepairTarget(edits['prompt'], tuple(edits['option_texts']))
    assert case['source']['knowledge_point']['evidence']
    assert case['recorded_prior_issues'] and case['recorded_next_issues']
    if case['next_candidate_makes_all_designated_edits']:
        generation._check_required_repairs([after], {key: target})
        # A changed field is not quality approval: these actual later candidates
        # were still semantically rejected. The gate must not replace that judge.
        assert verdict(key, accepted=False, prompt=True)['accepted'] is False
    else:
        with pytest.raises(generation.DiagnosticGenerationError, match='required diagnostic edits'):
            generation._check_required_repairs([after], {key: target})
    assert before != after  # The observed bug was partial repair, not identical whole responses.


def test_application_readonly_echo_edits_cannot_satisfy_true_plan_edit_obligation():
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / 'fixtures' /
                          'diagnostic_v16_required_edit_regression.json').read_text())
    old = fixture['cases'][1]['previous_candidate']
    key = old['knowledge_point_id']
    raw = deepcopy(old)
    raw.update(prompt='只改模型回声的假题干', options=['新甲', '新乙', '新丙', '新丁'])
    source = {'application_source_refs': {key: [
        {'ref': ref} for ref in raw['application_task']['source_refs']]}}
    rendered, _ = generation.DiagnosticItemGenerator._render_application(raw, source)
    assert rendered['prompt'] == old['prompt'] and rendered['options'] == old['options']
    with pytest.raises(generation.DiagnosticGenerationError, match='required diagnostic edits'):
        generation._check_required_repairs([rendered], {
            key: generation._ItemRepairTarget(old['prompt'], tuple(old['options']))})


def test_native_production_boundary_never_supplies_missing_review_targets():
    def old_wire(row, call):
        result = verdict(row['knowledge_point_id'])
        result.pop('required_edits')
        return result
    writer, reviewer = Writer([valid_payload()]), Reviewer(old_wire)
    with pytest.raises(generation.DiagnosticGenerationError, match='review schema is invalid'):
        run(writer, reviewer, retries=0)
    assert len(writer.calls) == len(reviewer.calls) == 1
