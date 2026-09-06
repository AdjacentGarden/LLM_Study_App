from rag_eval import (
    Chunk,
    diversify_chunks,
    exact_term_ranking,
    guard_high_confidence_evidence,
    page_evidence_indexes,
    page_reciprocal_rank_fusion,
    plain_text,
    protect_primary_pages,
    split_long,
    structured_table_texts,
    table_grid,
    technical_terms,
    unique_page_ranking,
)


def test_plain_text_preserves_code_and_inequalities():
    assert plain_text('x < y 且 y > z') == 'x < y 且 y > z'
    assert '<vector>' in plain_text('#include <vector> std::vector<int> values;')
    assert '<int>' in plain_text('#include <vector> std::vector<int> values;')
    assert plain_text('std::vector<B> and Container<i>') == 'std::vector<B> and Container<i>'
    code = '```cpp\nif (a < 8 && b > 2) { return true; }\n```'
    assert plain_text(code) == code
    assert plain_text('<p>内容</p>') == '内容'

CODON_TABLE = """
<table>
  <tr>
    <td rowspan="2">第一个字母</td>
    <td colspan="4">第二个字母</td>
    <td rowspan="2">第三个字母</td>
  </tr>
  <tr><td>U</td><td>C</td><td>A</td><td>G</td></tr>
  <tr>
    <td rowspan="1">A</td><td>甲硫氨酸(起始)</td><td>苏氨酸</td>
    <td>赖氨酸</td><td>精氨酸</td><td>G</td>
  </tr>
</table>
"""


def test_table_grid_expands_rowspan_and_colspan() -> None:
    grid = table_grid(CODON_TABLE)

    assert grid[0] == [
        "第一个字母",
        "第二个字母",
        "第二个字母",
        "第二个字母",
        "第二个字母",
        "第三个字母",
    ]
    assert grid[1] == ["第一个字母", "U", "C", "A", "G", "第三个字母"]


def test_coordinate_table_becomes_atomic_searchable_facts() -> None:
    texts = structured_table_texts(CODON_TABLE)
    structured = texts[-1]

    assert "AUG → 甲硫氨酸(起始)" in structured
    assert "ACG → 苏氨酸" in structured
    assert "AAG → 赖氨酸" in structured
    assert "AGG → 精氨酸" in structured


def test_guard_keeps_reranker_head_and_fused_evidence() -> None:
    reranked = [(7, 8.0), (8, 7.0), (9, 6.0), (10, 5.0), (11, 4.0)]
    fused = [1, 2, 3, 4, 5]

    assert guard_high_confidence_evidence(reranked, fused) == [7, 8, 1, 2, 3]


def test_guard_deduplicates_shared_candidates() -> None:
    reranked = [(2, 8.0), (7, 7.0), (3, 6.0), (4, 5.0), (5, 4.0)]
    fused = [1, 2, 3]

    assert guard_high_confidence_evidence(reranked, fused) == [2, 7, 1, 3, 4]


def _chunk(index: int, page: int, text: str) -> Chunk:
    return Chunk(f"c{index}", page, (index,), ("text",), text)


def test_chunks_fit_model_safe_character_limit() -> None:
    pieces = split_long("甲乙丙丁。" * 200, limit=360, overlap=60)

    assert len(pieces) > 1
    assert all(len(piece) <= 360 for piece in pieces)


def test_exact_identifier_ranking_handles_scientific_tokens() -> None:
    chunks = [
        _chunk(0, 1, "普通遗传学正文"),
        _chunk(1, 2, "AUG → 甲硫氨酸；GAA → 谷氨酸"),
        _chunk(2, 3, "AUG 是起始密码子"),
    ]
    query = "查找 mRNA 中 AUG 和 GAA 对应的氨基酸"

    assert technical_terms(query) == ["mrna", "aug", "gaa"]
    assert exact_term_ranking(query, chunks)[:2] == [1, 2]


def test_page_ranking_removes_duplicate_pages_and_packs_evidence() -> None:
    chunks = [
        _chunk(0, 10, "第一页证据 A"),
        _chunk(1, 10, "第一页证据 B"),
        _chunk(2, 11, "第二页证据"),
        _chunk(3, 12, "第三页证据"),
    ]
    ranking = [0, 1, 2, 3]
    pages = unique_page_ranking(ranking, chunks, limit=3)

    assert pages == [0, 2, 3]
    assert page_evidence_indexes(ranking, pages, chunks, per_page=2) == [0, 1, 2, 3]


def test_chunk_diversity_limits_large_pages_without_losing_context() -> None:
    chunks = [
        _chunk(0, 10, "A"),
        _chunk(1, 10, "B"),
        _chunk(2, 10, "C"),
        _chunk(3, 11, "D"),
        _chunk(4, 12, "E"),
    ]

    assert diversify_chunks([0, 1, 2, 3, 4], chunks, per_page=2) == [0, 1, 3, 4]


def test_page_rrf_counts_each_page_once_per_retrieval_route() -> None:
    chunks = [
        _chunk(0, 10, "A"),
        _chunk(1, 10, "B"),
        _chunk(2, 11, "C"),
        _chunk(3, 12, "D"),
    ]
    ranking_a = [0, 1, 2, 3]
    ranking_b = [2, 3, 0, 1]

    fused = page_reciprocal_rank_fusion((ranking_a, ranking_b), chunks)

    assert [chunks[index].page_number for index in fused] == [11, 10, 12]


def test_primary_page_order_is_kept_while_lexical_pages_fill_tail() -> None:
    chunks = [
        _chunk(0, 10, "A"),
        _chunk(1, 11, "B"),
        _chunk(2, 12, "C"),
        _chunk(3, 13, "D"),
        _chunk(4, 14, "E"),
        _chunk(5, 15, "F"),
    ]

    result = protect_primary_pages([0, 1, 2, 3, 4], [1, 5, 4], chunks)

    assert [chunks[index].page_number for index in result] == [10, 11, 12, 15, 14]
