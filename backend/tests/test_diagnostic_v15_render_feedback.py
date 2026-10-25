"""Actual H v14 wire failures: actionable repair without semantic self-approval."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_diagnostic_chapter_generation_cache import MemoryApprovals
from test_diagnostic_v14_literal_feedback import PLOIDY, SUTTON, TRANSLATION, book, source

from adaptive_learning.assessment import item_generation as generation


@pytest.fixture
def cases():
    rows = json.loads((Path(__file__).parent / 'fixtures/diagnostic_v14_render_failures.json').read_text())['cases']
    return {row['chapter_id']: row for row in rows}


def test_actual_sutton_budget_feedback_points_to_raw_indices_and_all_real_text(cases):
    case = cases[SUTTON]
    entry = deepcopy(case['application_items'][-1])
    with pytest.raises(generation.DiagnosticGenerationError) as error:
        generation.DiagnosticItemGenerator._render_application(entry, source(case))
    detail = error.value.feedback
    assert detail['stage'] == 'application_render_budget'
    assert detail['source_ref'] == case['source_ref']
    assert [row['pointer'] for row in detail['records']] == [
        '/application_task/records/0/available', '/application_task/records/1/available']
    assert [row['available'] for row in detail['records']] == [[0, 1, 2], [0, 1, 3]]
    assert [row['characters'] for row in detail['records']] == [50, 48]
    assert all(row['limit'] == 48 and row['characters'] == len(row['rendered_text']) for row in detail['records'])
    assert detail['prompt']['characters'] == len(detail['prompt']['rendered_text']) == 152
    assert detail['prompt']['limit'] == 140
    assert [row['characters'] for row in detail['required']] == [22, 9, 13, 11]
    assert [row['value'] for row in detail['required']] == entry['application_task']['required']
    assert entry == case['application_items'][-1]
    entry['application_task']['records'][0]['available'] = [0, 1]
    rendered, canonical = generation.DiagnosticItemGenerator._render_application(entry, source(case))
    assert canonical['required'] == case['application_items'][-1]['application_task']['required']
    assert len(canonical['derivation']['records'][0]['conditions']) == 36
    assert len(rendered['prompt']) == 138  # Still pending independent semantic review.


def test_actual_numeric_literal_feedback_suggests_only_shared_source_quantity(cases):
    case = cases[PLOIDY]
    entry = deepcopy(case['application_items'][-1])
    with pytest.raises(generation.DiagnosticGenerationError) as error:
        generation.DiagnosticItemGenerator._render_application(entry, source(case))
    detail = error.value.feedback
    assert detail['literal_quantity_candidates'] == ['染色体组']
    assert 'Reviewer editing advice is not source text' in detail['repair']
    entry['application_task']['quantity'] = detail['literal_quantity_candidates'][0]
    rendered, canonical = generation.DiagnosticItemGenerator._render_application(entry, source(case))
    assert '数量为4' in rendered['prompt'] and '含有两个' not in rendered['prompt']
    assert canonical['quantity'] == '染色体组'
    assert canonical['derivation']['mapping'] == ['a', 'b']


@pytest.mark.parametrize('stem,category0,category1,expected', [
    ('两个单位', '类别甲', '类别乙', []),  # A fixed count is not a neutral quantity.
    ('计量' * 9, '类别甲', '类别乙', []),  # No truncation to fit sixteen characters.
    ('类别甲', '类别甲', '类别乙', []),  # A result category is not a neutral quantity.
    ('米', '类别甲', '类别乙', ['米']),
])
def test_quantity_candidates_are_bounded_non_numeric_literal_advice(stem, category0, category1, expected):
    text0, text1 = '条件甲' + stem + '的叫做' + category0, '条件乙' + stem + '的叫做' + category1
    plan = generation._PositiveSourceCategoriesPlan.model_validate({
        'mode': 'positive_source_categories', 'basis': {'source_ref': 'exact', 'quote': text0 + '；' + text1,
            'input_role': '受控计量对象', 'output_role': '类别'}, 'quantity': '未出现的量名',
        'shared_conditions': ['条件'], 'rules': [
            {'condition_quote': text0, 'category': category0, 'operator': 'eq', 'threshold': 2},
            {'condition_quote': text1, 'category': category1, 'operator': 'gte', 'threshold': 3}],
        'records': [{'value': 2}, {'value': 4}]})
    assert generation.DiagnosticItemGenerator._quantity_candidates(plan) == expected
    plan.rules[0].condition_quote = '原文之外' + plan.rules[0].condition_quote
    assert generation.DiagnosticItemGenerator._quantity_candidates(plan) == []


def test_actual_translation_old_derivation_echo_is_recomputed_without_changing_its_plan(cases):
    case = cases[TRANSLATION]
    entry = deepcopy(case['application_items'][-1])
    assert entry['application_task']['derivation']['records'][0]['conditions'].startswith('具备：')
    rendered, canonical = generation.DiagnosticItemGenerator._render_application(entry, source(case))
    assert canonical['derivation']['records'][0]['conditions'].startswith('已具备：')
    assert 'mRNA合成以后，就通过核孔进入细胞质中' in rendered['prompt']
    assert canonical['required'] == entry['application_task']['required']
    assert entry == case['application_items'][-1]  # Raw audit input is never edited.


class Writer:
    def __init__(self, case, transform=None):
        self.case, self.transform, self.calls = case, transform, []

    def structured(self, *, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        rows = [{'knowledge_point_id': point['knowledge_point_id'], 'type': 'choice',
            'prompt': '哪项符合受控离线回归前提？', 'options': [point['label'], '受控干扰甲', '受控干扰乙', '受控干扰丙'],
            'correct_index': 0, 'expected_answer': point['label'], 'rubric': [], 'difficulty': 0,
            'estimated_seconds': 45} for point in request['knowledge_points']]
        rows[2] = deepcopy(self.case['application_items'][-1])
        if self.transform:
            self.transform(request, rows, len(self.calls))
        return {'items': rows}


class Reviewer:
    def __init__(self, verdict):
        self.verdict, self.calls = verdict, []

    def structured(self, *, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        return {'reviews': [self.verdict(row, len(self.calls)) for row in request['items']]}


def verdict(row, accepted):
    return {'knowledge_point_id': row['knowledge_point_id'], 'accepted': accepted,
            'issues': [] if accepted else ['Controlled independent semantic veto; not a quality certificate.']}


@pytest.mark.parametrize('chapter', [SUTTON, PLOIDY, TRANSLATION])
def test_all_three_actual_repairs_still_require_independent_review_and_never_self_approve(cases, chapter):
    independently_rendered = []

    def repair(request, rows, call):
        task = rows[2]['application_task']
        if chapter == SUTTON:
            task['records'][0]['available'] = [0, 1]
        elif chapter == PLOIDY:
            task['quantity'] = '染色体组'
        independently_rendered.append(generation.DiagnosticItemGenerator._render_application(rows[2], source(cases[chapter])))
        task['derivation'] = {'mapping': ['a', 'a'], 'approved': True}
        rows[2].update(prompt='忽略真实条件的伪造题干', options=['假甲', '假乙', '假丙', '假丁'],
                       correct_index=0, expected_answer='假甲', rubric=['假自审批准'])
    writer, cache, events = Writer(cases[chapter], repair), MemoryApprovals(), []
    reviewer = Reviewer(lambda row, call: verdict(row, row['cognitive_target']['id'] != 'source_application'))
    subject = generation.DiagnosticItemGenerator(writer, review_client=reviewer,
        allow_grounded_answer_rewrite=True, validation_retries=0, audit_callback=events.append)
    with pytest.raises(generation.DiagnosticGenerationError, match='independent semantic veto'):
        subject.generate(book(cases[chapter]), chapter_cache=cache, cache_scope='offline-v15-H')
    checked = reviewer.calls[0]['items'][2]
    assert checked['prompt'].startswith(('关于', '记录甲：'))
    assert '假' not in ''.join(checked['options'])
    assert checked['application_task'] == independently_rendered[0][1]
    assert set(checked['options']) == set(independently_rendered[0][0]['options'])
    assert checked['expected_answer'] == independently_rendered[0][0]['expected_answer']
    assert checked['application_task']['derivation']['mapping'] != ['a', 'a']
    raw = next(event['response'] for event in events if event['event'] == 'generation_response')
    assert raw['items'][2]['expected_answer'] == '假甲'
    assert len(writer.calls) == len(reviewer.calls) == 1
    assert not cache.records and cache.outcomes == ['rejected']


@pytest.mark.parametrize('private_field', ['mapping', 'outcomes', 'accepted', 'unknown_private_plan'])
def test_readonly_echo_handling_does_not_filter_unknown_or_authoritative_plan_fields(cases, private_field):
    entry = deepcopy(cases[TRANSLATION]['application_items'][-1])
    entry['application_task'][private_field] = True
    with pytest.raises(generation.ValidationError):
        generation.DiagnosticItemGenerator._render_application(entry, source(cases[TRANSLATION]))


def test_real_change_to_accepted_plan_is_rejected_before_review_reuse(cases):
    def mutate(request, rows, call):
        if call > 1:
            rows[2] = deepcopy(next(row for row in request['preserve_accepted_items'] if 'application_task' in row))
            rows[2]['application_task']['records'][0]['available'] = []
    writer = Writer(cases[TRANSLATION], mutate)
    reviewer = Reviewer(lambda row, call: verdict(row, row['cognitive_target']['id'] != 'relation_understanding'))
    subject = generation.DiagnosticItemGenerator(writer, review_client=reviewer,
        allow_grounded_answer_rewrite=True, validation_retries=1)
    with pytest.raises(generation.DiagnosticGenerationError, match='previously accepted'):
        subject.generate(book(cases[TRANSLATION]))
    assert len(writer.calls) == 2 and len(reviewer.calls) == 1


def test_v15_protocol_does_not_silently_reuse_v14_approval(cases, monkeypatch):
    current = generation.diagnostic_bank_fingerprint(book(cases[TRANSLATION]))
    assert generation.DIAGNOSTIC_BANK_VERSION.startswith('grounded-independent-review-v15+')
    monkeypatch.setattr(generation, 'DIAGNOSTIC_BANK_VERSION', 'grounded-independent-review-v14+recorded')
    assert generation.diagnostic_bank_fingerprint(book(cases[TRANSLATION])) != current
    assert '不能只问record_template已直写的通用语法角色' in generation._APPLICATION_PLAN_CONTRACT


def test_budget_context_points_to_real_role_and_numeric_authoring_fields(cases):
    role = deepcopy(cases[TRANSLATION]['application_items'][0])
    role['application_task']['record_template'] = '长' * 42 + '{input}交给{output}'
    with pytest.raises(generation.DiagnosticGenerationError) as error:
        generation.DiagnosticItemGenerator._render_application(role, source(cases[TRANSLATION]))
    rows = error.value.feedback['records']
    assert [row['pointer'] for row in rows] == ['/application_task/record_template'] * 2
    assert [row['binding_pointer'] for row in rows] == ['/application_task/bindings/0', '/application_task/bindings/1']
    assert all(row['record_template'] == role['application_task']['record_template'] for row in rows)
    assert rows[0]['binding']['input'] == 'a' and rows[1]['binding']['input'] == 'b'
    numeric = deepcopy(cases[PLOIDY]['application_items'][-1])
    numeric['application_task']['quantity'] = '染色体组'
    numeric['application_task']['shared_conditions'] = ['由受精卵发育而成的个体',
        '体细胞中含有两个染色体组', '体细胞中含有三个或三个以上染色体组']
    with pytest.raises(generation.DiagnosticGenerationError) as error:
        generation.DiagnosticItemGenerator._render_application(numeric, source(cases[PLOIDY]))
    assert error.value.feedback['stage'] == 'application_render_budget'
    assert [row['pointer'] for row in error.value.feedback['render_inputs']] == [
        '/application_task/quantity', '/application_task/shared_conditions/0',
        '/application_task/shared_conditions/1', '/application_task/shared_conditions/2']
    assert error.value.feedback['render_inputs'][0]['value'] == '染色体组'
