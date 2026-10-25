"""Real H v13 literal failures: repair exact fields, never relax source semantics."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_diagnostic_chapter_generation_cache import MemoryApprovals

from adaptive_learning.assessment import item_generation as generation
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft, SourceQuote


@pytest.fixture
def cases():
    values = json.loads((Path(__file__).parent / 'fixtures/diagnostic_v13_literal_field_failures.json').read_text())['cases']
    return {row['chapter_id']: row for row in values}


SUTTON, TRANSLATION, PLOIDY = 'chapter_5f29761fe19bff97', 'chapter_c9c510c2d238fec7', 'chapter_d58164c4b1599fca'


def source(case):
    return {'application_source_refs': {case['point_id']: [{'ref': case['source_ref'], 'evidence': case['evidence']}]}}


@pytest.mark.parametrize('chapter,paths', [
    (SUTTON, {'/application_task/required/0'}),
    (TRANSLATION, {'/application_task/required/0', '/application_task/required/1'}),
    (PLOIDY, {'/application_task/quantity'}),
])
def test_actual_last_payload_names_every_bad_field_with_its_value_and_exact_selected_source(cases, chapter, paths):
    case = cases[chapter]
    entry = deepcopy(case['application_items'][-1])
    with pytest.raises(generation.DiagnosticGenerationError) as error:
        generation.DiagnosticItemGenerator._render_application(entry, source(case))
    detail = error.value.feedback
    assert detail['source_ref'] == case['source_ref']
    assert detail['basis_quote'] == case['evidence']['quote']
    assert {row['pointer'] for row in detail['fields']} == paths
    assert all(row['value'] and row['reasons'] for row in detail['fields'])
    assert json.loads(json.dumps(detail, ensure_ascii=False)) == detail
    assert entry == case['application_items'][-1]  # Never silently rewrite the provider.


def test_real_long_continuous_phrase_is_allowed_without_paraphrasing_or_truncation(cases):
    case = cases[SUTTON]
    entry = deepcopy(case['application_items'][0])
    original = entry['application_task']['required'][0]
    assert 16 < len(original) <= 32
    # Keep the literal size regression separate from semantic quality. A real
    # returned review, not this mechanical renderer, must assess requirements.
    entry['application_task']['required'] = [original, '观察']
    entry['application_task']['records'] = [{'available': [0]}, {'available': [1]}]
    rendered, task = generation.DiagnosticItemGenerator._render_application(entry, source(case))
    assert task['required'] == [original, '观察']
    assert all(len(row['conditions']) <= 48 for row in task['derivation']['records'])
    assert len(generation.ItemDraft.model_validate(rendered).prompt) <= 140


def test_all_required_errors_include_source_and_length_reasons_in_one_response(cases):
    case = cases[SUTTON]
    entry = deepcopy(case['application_items'][0])
    entry['application_task']['required'] = [case['evidence']['quote'][:33], '并非来自所选原文的短语']
    with pytest.raises(generation.DiagnosticGenerationError) as error:
        generation.DiagnosticItemGenerator._render_application(entry, source(case))
    fields = error.value.feedback['fields']
    assert [row['pointer'] for row in fields] == ['/application_task/required/0', '/application_task/required/1']
    assert any('exceeds 32 characters' in reason for reason in fields[0]['reasons'])
    assert any('not a continuous literal source phrase' in reason for reason in fields[1]['reasons'])


def test_all_numeric_literal_fields_are_reported_together_not_as_a_generic_shared_condition(cases):
    case = cases[PLOIDY]
    entry = deepcopy(case['application_items'][-1])
    task = entry['application_task']
    task['shared_conditions'] = ['未出现在该原文中的前提']
    task['rules'][0]['category'] = '未出现在原文中的类别'
    task['rules'][1]['condition_quote'] = '未出现在原文中的数量条件'
    with pytest.raises(generation.DiagnosticGenerationError) as error:
        generation.DiagnosticItemGenerator._render_application(entry, source(case))
    assert {row['pointer'] for row in error.value.feedback['fields']} == {
        '/application_task/quantity', '/application_task/shared_conditions/0',
        '/application_task/rules/0/category', '/application_task/rules/1/condition_quote',
    }


def book(case):
    labels = ['离线受控基础前提甲', '离线受控关系前提乙', case['label']]
    quote = SourceQuote.model_validate(case['evidence'])
    chapter = ChapterDraft(chapter_id=case['chapter_id'], order=1, title=case['chapter_title'],
        start_page=quote.page_number, end_page=quote.page_number, summary='Controlled offline contract test',
        knowledge_points=labels, source_block_ids=[], evidence=[quote],
        knowledge_point_evidence={label: [quote] for label in labels})
    return BookStructure(title='Controlled literal repair', summary='No semantic certification',
        source_page_count=125, chapters=[chapter])


@pytest.mark.parametrize('accepted', [True, False])
def test_actual_numeric_field_feedback_reaches_writer_then_independent_review_can_veto(cases, accepted):
    case, writes, reviews, cache = cases[PLOIDY], [], [], MemoryApprovals()

    class Writer:
        def structured(self, *, user, **kwargs):
            request = json.loads(user)
            writes.append(request)
            rows = [{'knowledge_point_id': point['knowledge_point_id'], 'type': 'choice',
                'prompt': '哪项符合受控离线回归前提？', 'options': [point['label'], '受控干扰甲', '受控干扰乙', '受控干扰丙'],
                'correct_index': 0, 'expected_answer': point['label'], 'rubric': [], 'difficulty': 0,
                'estimated_seconds': 45} for point in request['knowledge_points']]
            rows[2] = deepcopy(case['application_items'][-1])
            if len(writes) > 1:
                items = request['repair_feedback']['items']
                assert len(items) == 1
                detail = items[0]['details']
                assert [row['pointer'] for row in detail['fields']] == ['/application_task/quantity']
                assert detail['fields'][0]['value'] == '体细胞中的染色体组数'
                assert detail['basis_quote'] == case['evidence']['quote']
                rows[2]['application_task']['quantity'] = '染色体组'
                expected = deepcopy(case['application_items'][-1])
                expected['application_task']['quantity'] = '染色体组'
                assert rows[2] == expected  # Only the named field changes.
            return {'items': rows}

    class Reviewer:
        def structured(self, *, user, **kwargs):
            request = json.loads(user)
            reviews.append(request)
            return {'reviews': [{'knowledge_point_id': item['knowledge_point_id'],
                'accepted': accepted or item['cognitive_target']['id'] != 'source_application',
                'issues': [] if accepted or item['cognitive_target']['id'] != 'source_application'
                    else ['Controlled independent source/uniqueness/distractor veto']}
                for item in request['items']]}

    subject = generation.DiagnosticItemGenerator(Writer(), review_client=Reviewer(),
        allow_grounded_answer_rewrite=True, validation_retries=1)
    if accepted:
        assert len(subject.generate(book(case), chapter_cache=cache, cache_scope='offline-H')) == 3
    else:
        with pytest.raises(generation.DiagnosticGenerationError, match='independent source'):
            subject.generate(book(case), chapter_cache=cache, cache_scope='offline-H')
        assert not cache.records and cache.outcomes == ['rejected']
    assert len(writes) == 2 and len(reviews) == 1  # Existing bounded repair only.


def test_changed_validation_protocol_cannot_reuse_v13_identity(cases, monkeypatch):
    value = book(cases[PLOIDY])
    current = generation.diagnostic_bank_fingerprint(value)
    assert generation.DIAGNOSTIC_BANK_VERSION.startswith('grounded-independent-review-v15+')
    monkeypatch.setattr(generation, 'DIAGNOSTIC_BANK_VERSION', 'grounded-independent-review-v13+recorded')
    assert generation.diagnostic_bank_fingerprint(value) != current
