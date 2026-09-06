from __future__ import annotations

from answer_eval import _concept_coverage, _normalized_for_match, _select_evidence_ids


def test_normalized_match_treats_chinese_digits_as_equivalent() -> None:
    assert _normalized_for_match("三个相邻碱基") == _normalized_for_match("3 个相邻碱基")


def test_concept_coverage_accepts_audited_equivalent_phrasing() -> None:
    answer = "mRNA 上 3 个相邻的碱基决定 1 个氨基酸，这样的单位称为密码子。"
    concepts = [
        ["mRNA"],
        ["三个相邻的碱基", "3个相邻的碱基"],
        ["决定一个氨基酸", "决定1个氨基酸"],
        ["密码子"],
    ]
    assert _concept_coverage(answer, concepts) == 1.0


def test_concept_coverage_keeps_real_omissions_visible() -> None:
    answer = "突变和自然选择会改变种群的基因频率。"
    concepts = [["突变"], ["自然选择"], ["迁入迁出", "迁入和迁出"]]
    assert _concept_coverage(answer, concepts) == 2 / 3


def test_evidence_selection_preserves_each_ranked_page_before_siblings() -> None:
    case = {
        "final_pages": [11, 12, 13],
        "final_chunk_ids": ["11a", "11b", "11c", "12a", "12b", "13a"],
    }
    chunks = {
        chunk_id: {"page_number": page}
        for chunk_id, page in {
            "11a": 11,
            "11b": 11,
            "11c": 11,
            "12a": 12,
            "12b": 12,
            "13a": 13,
        }.items()
    }

    assert _select_evidence_ids(case, chunks, max_evidence=4) == [
        "11a",
        "12a",
        "13a",
        "11b",
    ]
