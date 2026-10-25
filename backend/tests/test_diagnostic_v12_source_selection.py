"""Source-pattern priorities select candidates, never establish semantic approval."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_diagnostic_v6_repair import structure_for

from adaptive_learning.assessment import item_generation as generation
from adaptive_learning.ingestion.models import ChapterDraft, SourceQuote


def selector():
    # Selection alone must not construct or call a provider.
    instance = generation.DiagnosticItemGenerator.__new__(generation.DiagnosticItemGenerator)
    instance.points_per_chapter = 3
    return instance


def chapter(labels_and_quotes):
    return ChapterDraft(chapter_id='controlled-selection', order=1, title='源模式候选回归',
        start_page=1, end_page=1, summary='Explicit offline selection inputs',
        knowledge_points=[label for label, _ in labels_and_quotes], source_block_ids=[], evidence=[],
        knowledge_point_evidence={label: [SourceQuote(page_number=1, quote=quote)]
                                  for label, quote in labels_and_quotes})


@pytest.mark.parametrize('text,score', [
    ('该过程需要条件甲、条件乙和条件丙。', 3),
    ('能够在约定范围内执行任务并且产生可验证结果的对象称为合格单元。', 3),
    ('将发送方的记录转入接收方的数据库。', 2),
    ('生活在一定区域的同类设备的全部节点叫做一个设备群。', 1),
    ('隔离是形成新类别的必要条件。', 0),
    ('互执行并且产生结果的对象称为合格单元。', 0),
    ('需要条件甲。', 0),
    ('这里没有表示关系的普通陈述。', 0),
])
def test_generic_source_patterns_only_supply_a_candidate_priority(text, score):
    assert selector()._application_signal([SourceQuote(page_number=1, quote=text)]) == score


def test_actual_g7_replaces_the_single_definition_application_with_a_complete_multiconstraint_candidate():
    value = json.loads((Path(__file__).parent / 'fixtures' /
        'diagnostic_v11_immutable_basis_regression.json').read_text())['selection_regression']
    book = structure_for(value['source'])
    source = book.chapters[0]
    before = source.model_dump(mode='json')
    instance = selector()
    selected = instance._select_points(source)
    assert [point[0] for point in selected] == [
        'kp_dcd5fd6931d6b2a0', 'kp_38b4a51f61a55fd6', value['candidate_application_point_id']]
    assert len({point[0] for point in selected}) == 3
    assert value['previous_application_point_id'] not in {point[0] for point in selected}
    candidate = selected[2]
    assert any(quote.page_number == 109 and '一定区域的同种生物的全部个体叫做种群' in quote.quote
               for quote in candidate[2])
    assert candidate[2] == source.knowledge_point_evidence[candidate[1]]
    assert instance._application_signal(candidate[2]) == 2  # Includes source-explicit gene transfer; still only a hint.
    for _ in range(5):
        assert instance._select_points(source) == selected
    assert source.model_dump(mode='json') == before
    # The truncated double-column species quote is deliberately not repaired,
    # completed from its label, or promoted as a full joint definition.
    truncated = next(row for row in value['source']['knowledge_points'] if row['knowledge_point_id'] == 'kp_5fb7076bfe0ef75b')
    assert instance._application_signal([SourceQuote(**quote) for quote in truncated['evidence']]) == 0


def test_application_role_uses_source_signal_not_label_and_ordinary_points_keep_value_and_order():
    value = chapter([
        ('普通低价值点', '该过程需要条件甲、条件乙。'),
        ('重要概念的定义及作用和形成关系', '这是普通的一条原文陈述。'),
        ('另一个重要概念的条件机制和形成过程', '这是另一个有源的普通陈述。'),
        ('有源但非优先点', '这里仅描述一个对象。'),
    ])
    selected = selector()._select_points(value)
    assert [point[1] for point in selected] == [value.knowledge_points[1], value.knowledge_points[2], value.knowledge_points[0]]
    assert len({point[0] for point in selected}) == 3
    assert all(quotes == value.knowledge_point_evidence[label] for _, label, quotes in selected)
    revised = value.model_copy(deep=True)
    revised.knowledge_point_evidence[value.knowledge_points[0]][0].quote = '此处并无条件清单。'
    assert selector()._select_points(revised)[2][1] != value.knowledge_points[0]


def test_equal_signals_keep_the_existing_application_slot_and_empty_sources_never_enter_selection():
    value = chapter([(f'受控概念{number}的定义', '过程需要条件甲、条件乙。') for number in range(3)])
    value.knowledge_points.append('无来源但标签声称需要条件甲、条件乙的定义')
    assert [point[1] for point in selector()._select_points(value)] == value.knowledge_points[:3]


def test_selection_version_is_bound_to_bank_and_private_chapter_protocol(monkeypatch):
    value = structure_for(json.loads((Path(__file__).parent / 'fixtures' /
        'diagnostic_v11_immutable_basis_regression.json').read_text())['selection_regression']['source'])
    instance = selector()
    instance.client = instance.review_client = None
    instance.review_identity = {}
    instance.allow_grounded_answer_rewrite = instance.verify_items = True
    points = instance._select_points(value.chapters[0])
    bank_hash = generation.diagnostic_bank_fingerprint(value)
    identity = instance._chapter_identity(value, value.chapters[0], points, 'controlled-book')
    original = deepcopy(identity)
    monkeypatch.setattr(generation, 'DIAGNOSTIC_POINT_SELECTION_VERSION', 'different-selection-contract')
    assert generation.diagnostic_bank_fingerprint(value) != bank_hash
    revised = instance._chapter_identity(value, value.chapters[0], points, 'controlled-book')
    assert revised['protocol_fingerprint'] != identity['protocol_fingerprint']
    assert revised['source_fingerprint'] == identity['source_fingerprint']
    assert identity == original
