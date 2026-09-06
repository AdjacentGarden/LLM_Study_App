from pathlib import Path
from typing import cast

import pytest
from test_chaptering import page
from test_grounded_qa import FakeClient, SequenceFakeClient

from adaptive_learning.ingestion.chaptering import (
    ChapterBoundaryDetector,
    ChapterReconstructor,
    load_normalized_pages,
)
from adaptive_learning.llm.client import OpenAICompatibleClient
from adaptive_learning.rag.grounded_qa import EvidenceChunk, GroundedAnswerGenerator
from adaptive_learning.rag.index import IndexedChunk, PersistentRAGIndex, _definition_ranking


def test_unit_and_split_title_are_one_boundary():
    pages = [page(1, ('text', 'UNIT 1', 1), ('text', 'DIVERSE CULTURES', 1)),
             page(2, ('footer', 'UNIT 1 DIVERSE CULTURES', None))]
    chapters, fallback = ChapterBoundaryDetector().detect(pages)
    assert not fallback and len(chapters) == 1
    assert chapters[0].title == 'UNIT 1 DIVERSE CULTURES'


def test_evidence_character_budget_is_global_for_very_long_chapter():
    pages = [page(n, ('text', '原文内容。' * 200, None)) for n in range(1, 501)]
    payload, sources = ChapterReconstructor(max_chapter_characters=6000)._evidence_units(pages)
    assert sum(len(item['text']) for item in payload) <= 6000
    assert {1, 500} <= {item['page_number'] for item in payload}
    assert len(sources) == len(payload)


@pytest.mark.parametrize('book_id,count,start,end', [
    ('996d1581e1f6', 29, 25, 724),
    ('50eade62e574', 5, 8, 56),
    ('d6609d24a611', 20, 19, 514),
    ('daf7dc274fe9', 4, 5, 101),
    ('dd5a0fb41a5a', 14, 20, 336),
])
def test_real_cross_book_boundaries(book_id, count, start, end):
    path = Path(__file__).parents[2]/'artifacts/examples-20260905'/book_id/'normalized/pages.jsonl'
    if not path.exists():
        pytest.skip('Optional full-book evaluation fixture not installed')
    chapters, fallback = ChapterBoundaryDetector().detect(load_normalized_pages(path))
    assert not fallback and len(chapters) == count
    assert chapters[0].start_page == start
    assert chapters[-1].start_page == end
    assert all(a.end_page + 1 == b.start_page for a, b in zip(chapters, chapters[1:], strict=False))


def test_parent_context_keeps_following_equation():
    chunks = [IndexedChunk('p1-c01', 1, '根据公式可以得到', 'parent'),
              IndexedChunk('p1-c01-s01', 1, '根据公式', 'child'),
              IndexedChunk('p1-c02', 1, 'z1*z2=r1*r2*(cos(a+b)+i*sin(a+b))', 'parent')]
    index = object.__new__(PersistentRAGIndex)
    index.chunks = chunks
    index._by_id = {c.chunk_id: c for c in chunks}
    index._page_parents = {1: [chunks[0], chunks[2]]}
    evidence = index._expanded_evidence([1, 0, 2])
    assert len(evidence) == 1
    assert chunks[2].text in evidence[0].text
    assert evidence[0].page_number == 1


def test_definition_route_prefers_explicit_meanings_not_code_frequency():
    chunks = [IndexedChunk('a', 1, 'const double v; constexpr f(); '*10, 'parent'),
              IndexedChunk('b', 2, 'const: value does not change; constexpr: evaluated at compile time', 'parent')]
    assert _definition_ranking('const 和 constexpr 各表示什么？', chunks) == [1]
    assert _definition_ranking('const 初始化临时对象时何时销毁？', chunks) == []


def test_parent_windows_deduplicate_source_overlap():
    common = '同一原文重叠片段不能重复影响公式的语义和完整性'
    chunks = [IndexedChunk('p1-c01', 1, '开始'+common, 'parent'),
              IndexedChunk('p1-c02', 1, common+'结束', 'parent')]
    index = object.__new__(PersistentRAGIndex)
    index.chunks = chunks
    index._by_id = {c.chunk_id: c for c in chunks}
    index._page_parents = {1: chunks}
    assert index._expanded_evidence([0])[0].text == '开始'+common+'结束'


def test_low_score_lexical_match_cannot_bypass_semantic_review():
    client = FakeClient({})
    gen = GroundedAnswerGenerator(cast(OpenAICompatibleClient, client))
    result = gen.answer(question='Where is Queens?', evidence=[EvidenceChunk(
        source_id='E1', page_number=1, text='Queens is in New York.')],
        retrieval_score=-1, lexical_support=True)
    assert result.status == 'insufficient' and client.calls == 0


def test_lexical_match_with_semantic_review_still_requires_exact_citations():
    client = SequenceFakeClient([{'status':'supported','claims':[{'text':'Queens is in New York.',
        'citations':[{'source_id':'E1','quote':'Queens is in New York.'}]}], 'confidence':.9},
        {'relevant':True,'reviews':[{'claim_index':0,'supported':True,'reason':'原文明示'}]}])
    gen = GroundedAnswerGenerator(cast(OpenAICompatibleClient, client), use_semantic_review=True)
    result = gen.answer(question='Where is Queens?', evidence=[EvidenceChunk(
        source_id='E1', page_number=1, text='Queens is in New York.')],
        retrieval_score=-1, lexical_support=True)
    assert result.status == 'supported' and result.semantic_checked
    assert client.calls == 2
