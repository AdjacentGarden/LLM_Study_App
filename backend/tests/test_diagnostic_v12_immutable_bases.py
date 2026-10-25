"""Replay real v11 wire defects against the current production boundary.

Scripted reviewers verify routing, vetoes and exact preservation only. They do
not certify the biological correctness or educational quality of these tasks.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_diagnostic_chapter_generation_cache import MemoryApprovals
from test_diagnostic_v6_repair import structure_for

from adaptive_learning.assessment import item_generation as generation
from adaptive_learning.llm.client import LLMNotSubmittedError, LLMTimeoutError


@pytest.fixture
def recorded():
    cases = json.loads((Path(__file__).parent / 'fixtures' /
        'diagnostic_v11_immutable_basis_regression.json').read_text())['cases']
    return {case['source']['chapter_id']: case for case in cases}


CONDITIONS = 'chapter_4d06459dc04a5d5d'
LEAKING_RELATION = 'chapter_d58164c4b1599fca'
OBJECT_TRANSFER = 'chapter_7f991ceb7fc96c21'
ISOLATION = 'chapter_445b90708e561e06'


class Writer:
    def __init__(self, case, transform=None):
        self.case, self.transform, self.calls = case, transform, []

    def structured(self, *, system, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        assert request['application_plan_contract'] in system
        rows = [{'knowledge_point_id': point['knowledge_point_id'], 'type': 'choice',
            'prompt': '以下哪项符合本受控回归中的原文知识点？',
            'options': [point['label'], '受控第一种干扰陈述', '受控第二种干扰陈述', '受控第三种干扰陈述'],
            'correct_index': 0, 'expected_answer': point['label'], 'rubric': [],
            'difficulty': 0, 'estimated_seconds': 40} for point in request['knowledge_points']]
        rows[2] = deepcopy(self.case['attempts'][0]['application_item'])
        response = {'items': rows}
        return self.transform(request, response, len(self.calls)) if self.transform else response


class Reviewer:
    def __init__(self, accepted=True, issue='Controlled independent semantic veto', verdict=None):
        self.accepted, self.issue, self.verdict, self.calls = accepted, issue, verdict, []

    def structured(self, *, system, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        assert request['application_plan_contract'] in system
        return {'reviews': [self.verdict(row, len(self.calls)) if self.verdict else {
            'knowledge_point_id': row['knowledge_point_id'],
            'accepted': self.accepted or row['cognitive_target']['id'] != 'source_application',
            'issues': [] if self.accepted or row['cognitive_target']['id'] != 'source_application' else [self.issue]}
            for row in request['items']]}


def subject(writer, reviewer, **kwargs):
    return generation.DiagnosticItemGenerator(writer, review_client=reviewer,
        allow_grounded_answer_rewrite=True, validation_retries=0, **kwargs)


@pytest.mark.parametrize('chapter_id', [CONDITIONS, LEAKING_RELATION, OBJECT_TRANSFER])
@pytest.mark.parametrize('accepted', [True, False])
def test_actual_g_quote_copy_errors_resolve_only_by_exact_source_id_and_still_require_review(recorded, chapter_id, accepted):
    case, events, cache = recorded[chapter_id], [], MemoryApprovals()
    writer, reviewer = Writer(case), Reviewer(accepted)
    instance = subject(writer, reviewer, audit_callback=events.append)
    value = structure_for(case['source'])
    if accepted:
        bank = instance.generate(value, chapter_cache=cache, cache_scope='offline-v12')
        assert len(bank) == 3
        assert instance.generate(value, chapter_cache=cache, cache_scope='offline-v12') == bank
        assert next(iter(cache.records.values()))['status'] == 'approved'
    else:
        with pytest.raises(generation.DiagnosticGenerationError, match='semantic veto'):
            instance.generate(value, chapter_cache=cache, cache_scope='offline-v12')
        assert not cache.records and cache.outcomes == ['rejected']
    assert len(writer.calls) == len(reviewer.calls) == 1
    raw = next(row['response'] for row in events if row['event'] == 'generation_response')['items'][2]
    canonical = next(row['items'] for row in events if row['event'] == 'candidate')[2]['application_task']
    assert raw == case['attempts'][0]['application_item']
    directory = writer.calls[0]['application_source_refs'][case['application_point_id']]
    original = next(row['evidence']['quote'] for row in directory if row['ref'] == canonical['basis']['source_ref'])
    assert canonical['basis']['quote'] == original
    assert reviewer.calls[0]['items'][2]['application_task'] == canonical
    assert canonical['derivation']['source_refs'] == [canonical['basis']['source_ref']]
    if chapter_id != CONDITIONS:
        assert raw['application_task']['basis']['quote'] not in original
        assert '\n' in original


@pytest.mark.parametrize('echo', [None, '完全伪造且不能作为事实的模型回声', '拼接并改写的原文回声'])
def test_provider_quote_is_not_an_evidence_channel_and_raw_echo_remains_auditable(recorded, echo):
    def transform(request, response, call):
        basis = response['items'][2]['application_task']['basis']
        if echo is None:
            basis.pop('quote')
        else:
            basis['quote'] = echo
        return response
    events, case = [], recorded[OBJECT_TRANSFER]
    writer, reviewer = Writer(case, transform), Reviewer(False, '无源机制或对象关系仍须独立否决')
    with pytest.raises(generation.DiagnosticGenerationError, match='无源机制'):
        subject(writer, reviewer, audit_callback=events.append).generate(structure_for(case['source']))
    raw = next(row['response'] for row in events if row['event'] == 'generation_response')['items'][2]
    assert raw['application_task']['basis'].get('quote') == echo
    task = reviewer.calls[0]['items'][2]['application_task']
    directory = writer.calls[0]['application_source_refs'][case['application_point_id']]
    assert task['basis']['quote'] == next(row['evidence']['quote'] for row in directory
        if row['ref'] == task['basis']['source_ref'])
    assert echo is None or echo not in task['basis']['quote']


@pytest.mark.parametrize('defect', ['unknown', 'sibling', 'near_ref', 'changed_source'])
def test_exact_ref_never_falls_back_to_nearby_text_or_another_point(recorded, defect):
    case = deepcopy(recorded[OBJECT_TRANSFER])
    def transform(request, response, call):
        basis = response['items'][2]['application_task']['basis']
        basis['source_ref'] = ('missing' if defect == 'unknown' else
            request['application_source_refs'][request['knowledge_points'][0]['knowledge_point_id']][0]['ref']
            if defect == 'sibling' else basis['source_ref'] + ' ' if defect == 'near_ref' else basis['source_ref'])
        return response
    if defect == 'changed_source':
        for quote in case['source']['knowledge_points'][2]['evidence']:
            quote['quote'] += '新增限定改变精确来源身份。'
    writer, reviewer = Writer(case, transform), Reviewer()
    with pytest.raises(generation.DiagnosticGenerationError, match='exact immutable source_ref'):
        subject(writer, reviewer).generate(structure_for(case['source']))
    assert len(writer.calls) == 1 and reviewer.calls == []


@pytest.mark.parametrize('chapter_id', [CONDITIONS, OBJECT_TRANSFER])
@pytest.mark.parametrize('explicit_wrong_id', [False, True])
def test_record_ids_are_program_owned_and_explicit_wrong_order_is_rejected(recorded, chapter_id, explicit_wrong_id):
    def transform(request, response, call):
        task = response['items'][2]['application_task']
        rows = task['bindings' if task['mode'] == 'role_transfer' else 'records']
        for row in rows:
            row.pop('id', None)
        if explicit_wrong_id:
            rows[0]['id'] = 'r2'
        return response
    writer, reviewer = Writer(recorded[chapter_id], transform), Reviewer()
    instance = subject(writer, reviewer)
    if explicit_wrong_id:
        with pytest.raises(generation.DiagnosticGenerationError, match='ordered r1/r2'):
            instance.generate(structure_for(recorded[chapter_id]['source']))
        assert reviewer.calls == []
    else:
        bank = instance.generate(structure_for(recorded[chapter_id]['source']))
        task = reviewer.calls[0]['items'][2]['application_task']
        rows = task['bindings' if task['mode'] == 'role_transfer' else 'records']
        assert [row['id'] for row in rows] == ['r1', 'r2']
        assert [row['id'] for row in task['derivation']['records']] == ['r1', 'r2']
        if chapter_id == CONDITIONS:
            assert task['basis']['input_role'] in bank[2].prompt
            assert '一定正常' not in bank[2].prompt + ''.join(bank[2].options)


@pytest.mark.parametrize('chapter_id', ['chapter_5f29761fe19bff97', 'chapter_c9c510c2d238fec7'])
def test_actual_g_identical_premise_aa_mode_is_not_a_production_plan(recorded, chapter_id):
    writer, reviewer, cache = Writer(recorded[chapter_id]), Reviewer(), MemoryApprovals()
    assert writer.case['attempts'][0]['application_item']['application_task']['mode'] == 'forward_instances'
    with pytest.raises(generation.DiagnosticGenerationError, match='union_tag_invalid'):
        subject(writer, reviewer).generate(structure_for(writer.case['source']), chapter_cache=cache, cache_scope='offline')
    assert reviewer.calls == [] and not cache.records and cache.outcomes == ['rejected']


def test_actual_g_source_does_not_justify_faking_two_independent_requirements(recorded):
    case = recorded[ISOLATION]
    writer, reviewer = Writer(case), Reviewer()
    with pytest.raises(generation.DiagnosticGenerationError, match='too_short'):
        subject(writer, reviewer).generate(structure_for(case['source']))
    assert reviewer.calls == []
    def hierarchy(request, response, call):
        response['items'][2] = deepcopy(case['attempts'][1]['application_item'])
        return response
    writer, reviewer, cache = Writer(case, hierarchy), Reviewer(False, '上位概念和子类型不是两个独立必需项'), MemoryApprovals()
    with pytest.raises(generation.DiagnosticGenerationError, match='不是两个独立必需项'):
        subject(writer, reviewer).generate(structure_for(case['source']), chapter_cache=cache, cache_scope='offline')
    assert len(reviewer.calls) == 1 and not cache.records


def test_answer_leaking_actual_g_role_template_still_requires_a_returned_independent_veto(recorded):
    case = recorded[LEAKING_RELATION]
    writer, reviewer = Writer(case), Reviewer(False, '题干已经明示所问的自由组合结论，尚未形成应用')
    with pytest.raises(generation.DiagnosticGenerationError, match='已经明示'):
        subject(writer, reviewer).generate(structure_for(case['source']))
    assert len(writer.calls) == len(reviewer.calls) == 1
    assert '也自由组合' in reviewer.calls[0]['items'][2]['prompt']


@pytest.mark.parametrize('defect', ['derivation', 'template', 'free_outcomes'])
def test_source_resolution_does_not_open_an_application_override_channel(recorded, defect):
    def transform(request, response, call):
        task = response['items'][2]['application_task']
        if defect == 'derivation':
            task['derivation'] = {'mapping': ['a', 'a'], 'accepted': True}
        elif defect == 'template':
            task['record_template'] = '{input.__class__}转入{output}'
        else:
            task['outcomes'] = ['指定正确结论', '另一个结论']
        return response
    writer, reviewer = Writer(recorded[OBJECT_TRANSFER], transform), Reviewer(accepted=defect != 'derivation')
    failure = 'semantic veto' if defect == 'derivation' else 'batch validation failed'
    with pytest.raises(generation.DiagnosticGenerationError, match=failure):
        subject(writer, reviewer).generate(structure_for(writer.case['source']))
    if defect == 'derivation':
        assert len(reviewer.calls) == 1
        assert reviewer.calls[0]['items'][2]['application_task']['derivation']['mapping'] == ['b', 'a']
    else:
        assert reviewer.calls == []


def test_canonical_plan_replays_exactly_and_every_private_component_binds_approval(recorded):
    def transform(request, response, call):
        if call > 1:
            response['items'][2] = deepcopy(next(row for row in request['preserve_accepted_items'] if 'application_task' in row))
        return response
    def verdict(row, call):
        accepted = call > 1 or row['cognitive_target']['id'] != 'relation_understanding'
        return {'knowledge_point_id': row['knowledge_point_id'], 'accepted': accepted,
                'issues': [] if accepted else ['Controlled relationship repair']}
    writer, reviewer, events = Writer(recorded[OBJECT_TRANSFER], transform), Reviewer(verdict=verdict), []
    instance = subject(writer, reviewer, audit_callback=events.append)
    instance.validation_retries = 1
    value = structure_for(writer.case['source'])
    instance.generate(value)
    assert len(writer.calls) == len(reviewer.calls) == 2
    assert [row['cognitive_target']['id'] for row in reviewer.calls[1]['items']] == ['relation_understanding']
    candidates = [row['items'][2] for row in events if row['event'] == 'candidate']
    assert candidates[0] == candidates[1]
    candidate = candidates[0]
    chapter = value.chapters[0]
    item = instance._validate_item(generation.ItemDraft.model_validate(candidate), chapter,
        {point[0]: point for point in instance._select_points(chapter)}, allow_rewrite=True, structured_application=True)
    original = instance._approval_key(writer.calls[0], item, candidate['application_task'])
    for field in ('basis', 'bindings', 'record_template', 'result_role', 'derivation'):
        task = deepcopy(candidate['application_task'])
        task[field] = 'different private plan component'
        assert instance._approval_key(writer.calls[0], item, task) != original
    different_source = deepcopy(writer.calls[0])
    different_source['application_source_refs'][candidate['knowledge_point_id']][0]['evidence']['quote'] += '新限定'
    assert instance._approval_key(different_source, item, candidate['application_task']) != original


def test_v12_uses_a_new_protocol_claim_without_deleting_v11_approval_history(recorded, monkeypatch):
    writer, reviewer, cache = Writer(recorded[OBJECT_TRANSFER]), Reviewer(), MemoryApprovals()
    instance, value = subject(writer, reviewer), structure_for(writer.case['source'])
    current = generation.DIAGNOSTIC_BANK_VERSION
    monkeypatch.setattr(generation, 'DIAGNOSTIC_BANK_VERSION', 'grounded-independent-review-v11+historical')
    instance.generate(value, chapter_cache=cache, cache_scope='same-book')
    previous = deepcopy(cache.records)
    monkeypatch.setattr(generation, 'DIAGNOSTIC_BANK_VERSION', current)
    instance.generate(value, chapter_cache=cache, cache_scope='same-book')
    assert current.startswith('grounded-independent-review-v15+')
    assert len(writer.calls) == len(reviewer.calls) == len(cache.records) == 2
    assert all(cache.records[key] == record for key, record in previous.items())


@pytest.mark.parametrize('error,outcome', [(LLMNotSubmittedError('confirmed not sent'), 'not_admitted'),
                                         (LLMTimeoutError('read outcome unknown'), 'unknown')])
def test_current_plan_keeps_confirmed_not_submitted_distinct_from_unknown(recorded, error, outcome):
    writer, reviewer, cache = Writer(recorded[OBJECT_TRANSFER]), Reviewer(), MemoryApprovals()
    calls = []
    def fail(**kwargs):
        calls.append(kwargs)
        raise error
    reviewer.structured = fail
    instance = subject(writer, reviewer)
    instance.validation_retries = 2
    with pytest.raises(type(error)):
        instance.generate(structure_for(writer.case['source']), chapter_cache=cache, cache_scope='offline')
    assert len(writer.calls) == len(calls) == 1 and cache.outcomes == [outcome]
    assert not cache.records if outcome == 'not_admitted' else next(iter(cache.records.values()))['status'] == 'unknown'
