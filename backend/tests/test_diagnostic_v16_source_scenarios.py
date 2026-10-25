"""Real-source expressibility, not a claim that scripted judges certify quality."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_diagnostic_chapter_generation_cache import MemoryApprovals
from test_diagnostic_v14_literal_feedback import SUTTON, TRANSLATION, book, source

from adaptive_learning.assessment import item_generation as generation

POPULATION = 'chapter_445b90708e561e06'


@pytest.fixture
def cases():
    data = json.loads((Path(__file__).parent / 'fixtures/diagnostic_v15_source_scenarios.json').read_text())
    return {case['chapter_id']: case for case in data['cases']}


class Writer:
    def __init__(self, case, transform=None):
        self.case, self.transform, self.calls = case, transform, []

    def structured(self, *, system, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        assert set(request['application_output_shapes']) == {'source_scenario'}
        assert request['application_contract'] in system and request['application_plan_contract'] in system
        assert all('plan_hints' not in row for rows in request['application_source_refs'].values() for row in rows)
        rows = [{'knowledge_point_id': point['knowledge_point_id'], 'type': 'choice',
            'prompt': '哪项符合受控离线回归中的原文知识点？',
            'options': [point['label'], '受控干扰一', '受控干扰二', '受控干扰三'], 'correct_index': 0,
            'expected_answer': point['label'], 'rubric': [], 'difficulty': 0, 'estimated_seconds': 40}
            for point in request['knowledge_points']]
        rows[2] = deepcopy(self.case['application_item'])
        if self.transform:
            self.transform(request, rows, len(self.calls))
        return {'items': rows}


class Reviewer:
    def __init__(self, verdict=None):
        self.verdict, self.calls = verdict, []

    def structured(self, *, system, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        assert request['application_contract'] in system and request['application_plan_contract'] in system
        return {'reviews': [self.verdict(row, len(self.calls)) if self.verdict else {
            'knowledge_point_id': row['knowledge_point_id'], 'accepted': True, 'issues': []}
            for row in request['items']]}


def generator(writer, reviewer, retries=0, **kwargs):
    return generation.DiagnosticItemGenerator(writer, review_client=reviewer,
        allow_grounded_answer_rewrite=True, validation_retries=retries, **kwargs)


def reject_application(row, call):
    accepted = row['cognitive_target']['id'] != 'source_application'
    return {'knowledge_point_id': row['knowledge_point_id'], 'accepted': accepted,
            'issues': [] if accepted else ['Controlled independent semantic veto; structure is not approval.']}


@pytest.mark.parametrize('chapter', [POPULATION, TRANSLATION, SUTTON])
def test_actual_sources_express_single_scenarios_then_render_review_publish_and_cache(cases, chapter):
    case, writer, reviewer, cache, events = cases[chapter], Writer(cases[chapter]), Reviewer(), MemoryApprovals(), []
    value = book(case)
    subject = generator(writer, reviewer, audit_callback=events.append)
    bank = subject.generate(value, chapter_cache=cache, cache_scope='offline-v16-H')
    actual = bank[2]
    task = case['application_item']['application_task']
    assert actual.prompt == task['situation'] + '\n' + task['decision']
    assert set(actual.options) == set(task['outcomes']) and actual.expected_answer == task['outcomes'][task['correct_index']]
    assert actual.options[int(actual.correct_option_ids[0])] == actual.expected_answer
    assert len(actual.prompt) <= 140 and all(len(option) <= 180 for option in actual.options)
    checked = reviewer.calls[0]['items'][2]
    assert checked['application_task'] == task
    assert checked['expected_answer'] == actual.expected_answer
    directory = reviewer.calls[0]['application_source_refs'][case['point_id']]
    assert next(row['evidence'] for row in directory if row['ref'] == case['source_ref']) == case['evidence']
    assert 'application_task' not in actual.model_dump()
    assert subject.generate(value, chapter_cache=cache, cache_scope='offline-v16-H') == bank
    assert len(writer.calls) == len(reviewer.calls) == 1
    assert len(cache.records) == 1 and not cache.outcomes
    assert next(event for event in events if event['event'] == 'bank_accepted')


@pytest.mark.parametrize('failure', ['missing_condition', 'answer_leak', 'fake_application', 'unsupported_negative'])
def test_semantically_defective_valid_scenarios_still_require_and_obey_independent_veto(cases, failure):
    case = cases[POPULATION if failure == 'missing_condition' else TRANSLATION]
    def mutate(request, rows, call):
        task = rows[2]['application_task']
        if failure == 'missing_condition':
            task['situation'] = '两张表记录该区域的生物，未说明是否同种或是否覆盖全部个体。'
        elif failure == 'answer_leak':
            task['situation'] += '题设已经确认正确连接是乙栏到甲栏。'
        elif failure == 'fake_application':
            task.update(situation='某人在图书馆复习本章内容。', decision='翻译所用的模板是哪一种物质？',
                        outcomes=['mRNA', '蛋白质', 'DNA', '氨基酸'])
        else:
            task.update(situation='题设移除了mRNA，但没有给出其余反应情况。',
                        decision='随后蛋白质合成必然如何变化？', outcomes=[
                            '必然完全停止', '必然完全保持原状', '必然加速', '必然形成新的机制'])
        rows[2]['source_supported'] = True  # Not an approval input.
    writer, reviewer, cache = Writer(case, mutate), Reviewer(reject_application), MemoryApprovals()
    with pytest.raises(generation.DiagnosticGenerationError, match='independent semantic veto'):
        generator(writer, reviewer).generate(book(case), chapter_cache=cache, cache_scope='offline-negative')
    assert len(writer.calls) == len(reviewer.calls) == 1
    assert reviewer.calls[0]['items'][2]['cognitive_target']['id'] == 'source_application'
    assert not cache.records and cache.outcomes == ['rejected']


@pytest.mark.parametrize('defect', ['unknown_ref', 'sibling_ref', 'duplicate_ref', 'empty_refs',
    'five_options', 'duplicate_options', 'bool_index', 'out_of_range', 'unknown_plan_field', 'provider_quote'])
def test_malformed_plans_and_untrusted_source_channels_never_reach_review(cases, defect):
    def mutate(request, rows, call):
        task = rows[2]['application_task']
        if defect == 'unknown_ref':
            task['source_refs'] = ['unknown_exact_ref']
        elif defect == 'sibling_ref':
            other = request['knowledge_points'][0]['knowledge_point_id']
            task['source_refs'] = [request['application_source_refs'][other][0]['ref']]
        elif defect == 'duplicate_ref':
            task['source_refs'] *= 2
        elif defect == 'empty_refs':
            task['source_refs'] = []
        elif defect == 'five_options':
            task['outcomes'].append('第五个非法选项')
        elif defect == 'duplicate_options':
            task['outcomes'][1] = task['outcomes'][0]
        elif defect == 'bool_index':
            task['correct_index'] = True
        elif defect == 'out_of_range':
            task['correct_index'] = 4
        elif defect == 'provider_quote':
            task['quote'] = '伪造的原文与新结论'
        else:
            task['mapping'] = ['a', 'a']
    writer, reviewer, cache = Writer(cases[SUTTON], mutate), Reviewer(), MemoryApprovals()
    with pytest.raises(generation.DiagnosticGenerationError, match='batch validation failed'):
        generator(writer, reviewer).generate(book(cases[SUTTON]), chapter_cache=cache, cache_scope='offline-invalid')
    assert reviewer.calls == [] and not cache.records and cache.outcomes == ['rejected']


@pytest.mark.parametrize('old_mode', ['required_conditions', 'role_transfer', 'positive_source_categories', 'forward_instances'])
def test_current_generator_never_falls_back_to_retired_closed_grammars(cases, old_mode):
    writer, reviewer = Writer(cases[SUTTON], lambda request, rows, call:
        rows[2]['application_task'].update(mode=old_mode)), Reviewer()
    with pytest.raises(generation.DiagnosticGenerationError, match='batch validation failed'):
        generator(writer, reviewer).generate(book(cases[SUTTON]))
    assert reviewer.calls == []


@pytest.mark.parametrize('words', ['不正确', '不属于', '错误的是', '错误的说法', '不符合', '不包括'])
def test_negative_decision_cannot_bypass_positive_stem_rule(cases, words):
    entry = deepcopy(cases[SUTTON]['application_item'])
    entry['application_task']['decision'] = f'以下哪项{words}？'
    with pytest.raises(generation.DiagnosticGenerationError, match='negative scenario decision'):
        generation.DiagnosticItemGenerator._render_application(entry, source(cases[SUTTON]))
    entry['application_task']['decision'] = cases[SUTTON]['application_item']['application_task']['decision']
    entry['application_task']['situation'] = '研究者尚未得到观察和实验结果，仅有两对象行为相似的记录。'
    rendered, task = generation.DiagnosticItemGenerator._render_application(entry, source(cases[SUTTON]))
    assert '尚未得到' in rendered['prompt']  # Negative givens still go to semantic review.


def test_readonly_echoes_are_recomputed_and_raw_audit_is_unchanged(cases):
    case = cases[SUTTON]
    def mutate(request, rows, call):
        rows[2].update(prompt='绕过情境的伪造题干', options=['假甲', '假乙', '假丙', '假丁'],
            expected_answer='假甲', correct_index=3, rubric=['自审批准'])
        rows[2]['application_task']['derivation'] = {'approved': True, 'source_quote': '伪造来源'}
    writer, reviewer, events = Writer(case, mutate), Reviewer(reject_application), []
    with pytest.raises(generation.DiagnosticGenerationError, match='independent semantic veto'):
        generator(writer, reviewer, audit_callback=events.append).generate(book(case))
    checked = reviewer.calls[0]['items'][2]
    assert checked['application_task'] == case['application_item']['application_task']
    assert checked['expected_answer'] == case['application_item']['application_task']['outcomes'][0]
    assert next(event['response'] for event in events if event['event'] == 'generation_response')['items'][2]['expected_answer'] == '假甲'


@pytest.mark.parametrize('change', ['echo_only', 'situation', 'decision', 'outcomes', 'correct_index'])
def test_exact_accepted_plan_is_preserved_and_echo_cannot_override_it(cases, change):
    def mutate(request, rows, call):
        if call > 1:
            rows[2] = deepcopy(next(row for row in request['preserve_accepted_items'] if 'application_task' in row))
            if change == 'echo_only':
                rows[2]['expected_answer'] = '伪造旧回声'
            elif change == 'correct_index':
                rows[2]['application_task']['correct_index'] = 1
            elif change == 'outcomes':
                rows[2]['application_task']['outcomes'][1] += '被改动'
            else:
                rows[2]['application_task'][change] += '新增条件'
    def verdict(row, call):
        accepted = call > 1 or row['cognitive_target']['id'] != 'relation_understanding'
        return {'knowledge_point_id': row['knowledge_point_id'], 'accepted': accepted,
                'issues': [] if accepted else ['Controlled relation repair only']}
    writer, reviewer = Writer(cases[SUTTON], mutate), Reviewer(verdict)
    subject = generator(writer, reviewer, retries=1)
    if change == 'echo_only':
        assert len(subject.generate(book(cases[SUTTON]))) == 3
        assert [row['cognitive_target']['id'] for row in reviewer.calls[1]['items']] == ['relation_understanding']
    else:
        with pytest.raises(generation.DiagnosticGenerationError, match='previously accepted'):
            subject.generate(book(cases[SUTTON]))
        assert len(reviewer.calls) == 1
    assert len(writer.calls) == 2


def test_combined_budget_is_actionable_and_long_natural_options_keep_existing_limit(cases):
    entry = deepcopy(cases[SUTTON]['application_item'])
    entry['application_task'].update(situation='情' * 112, decision='问' * 40)
    with pytest.raises(generation.DiagnosticGenerationError) as error:
        generation.DiagnosticItemGenerator._render_application(entry, source(cases[SUTTON]))
    assert error.value.feedback['prompt']['characters'] == 153
    assert [row['pointer'] for row in error.value.feedback['fields']] == ['/application_task/situation', '/application_task/decision']
    entry['application_task'].update(situation='两对象仅有行为相似的记录', decision='如何对待所得的新推断？')
    entry['application_task']['outcomes'][0] = '受控长选项' * 20
    rendered, task = generation.DiagnosticItemGenerator._render_application(entry, source(cases[SUTTON]))
    assert 90 < len(rendered['options'][0]) <= 180  # Structure alone does not certify quality.


def test_v16_source_protocol_requires_new_exact_approval(cases, monkeypatch):
    value = book(cases[SUTTON])
    current = generation.diagnostic_bank_fingerprint(value)
    assert generation.DIAGNOSTIC_BANK_VERSION.startswith('grounded-independent-review-v16+')
    monkeypatch.setattr(generation, 'DIAGNOSTIC_BANK_VERSION', 'grounded-independent-review-v15+historical')
    assert generation.diagnostic_bank_fingerprint(value) != current
