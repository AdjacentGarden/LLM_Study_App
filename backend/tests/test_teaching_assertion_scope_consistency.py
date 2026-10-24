"""Review-format consistency, not a substitute for semantic model review.

The corrected-question case retains the actual F-course checkpoint wording.
Its reviewer marked the quotation nonfactual but supplied unbounded cells for
the same assertion. Neither side of that contradictory response may be silently
rewritten into approval.
"""

from copy import deepcopy

import pytest

from adaptive_learning.personalization import lesson_review_contract as contract


def corrected_checkpoint():
    fields = {
        "question": "某同学说：“一个精原细胞和一个卵原细胞经过减数分裂，"
        "都能形成四个成熟生殖细胞。”请判断这一说法，"
        "并分别说明精子形成和卵细胞形成中细胞数目与后续变化。",
        "expected_answer": "该说法不正确。一个初级精母细胞经减数第一次分裂和"
        "减数第二次分裂形成四个精细胞，精细胞还要经过复杂的变形才成为精子，"
        "因此减数分裂直接产生的是精细胞而非成熟精子。"
        "一个初级卵母细胞经减数分裂形成一个卵细胞和三个极体，"
        "三个极体不久都退化消失，结果只形成一个卵细胞。"
        "所以不能把“形成四个成熟生殖细胞”同时套用到精子和卵细胞形成上。",
    }
    # A small immutable source pool suffices to test the response contract.
    # These tests do not establish the truth of any model-supplied classification.
    quote = "一个初级卵母细胞经过减数分裂，就形成一个卵细胞和三个极体。"
    sources = {"ovum": {"quote": quote}}
    spans = {"ovum_span": {"source_id": "ovum", "quote": quote}}
    payload = contract.build_assertion_contract(
        "checkpoint_1", fields, ["ovum"], spans, sources
    )
    rows = [
        {
            "assertion_id": assertion["assertion_id"],
            "is_factual": assertion["field"] != "question",
            "source_entailment": True,
            "support_span_ids": ["ovum_span"] if assertion["field"] != "question" else [],
            "findings": [],
        }
        for assertion in payload["assertions"]
    ]
    matrix = contract.build_scope_matrix_contract(
        payload["assertions"],
        [
            {
                "boundary_id": "ovum_products",
                "boundary_type": "precondition",
                "condition": quote,
                "source_ids": ["ovum"],
            }
        ],
    )
    matrix_rows = [
        {"assertion_id": row["assertion_id"], "checks": [[False, False, False, False, None]]}
        for row in rows
    ]
    return payload, rows, spans, sources, matrix, matrix_rows


def test_f_corrected_question_nonfactual_and_unbounded_is_format_error_without_mutation():
    payload, rows, spans, sources, matrix, matrix_rows = corrected_checkpoint()
    matrix_rows[0]["checks"][0] = [True, True, True, False, None]
    scope = contract.materialize_scope_matrix(matrix, matrix_rows)
    raw = deepcopy((rows, matrix_rows, scope))

    with pytest.raises(contract.LessonContractError, match="nonfactual assertion.*unbounded") as error:
        contract.validate_assertion_review(payload, rows, spans, sources, scope_result=scope)

    assert rows[0]["assertion_id"] in str(error.value)
    assert "field=question" in str(error.value)
    assert (rows, matrix_rows, scope) == raw
    assert scope["scope_exceptions"] is False and scope["findings"]


def test_corrected_question_consistent_nonfactual_not_applicable_can_be_validated():
    payload, rows, spans, sources, matrix, matrix_rows = corrected_checkpoint()
    scope = contract.materialize_scope_matrix(matrix, matrix_rows)
    result = contract.validate_assertion_review(payload, rows, spans, sources, scope_result=scope)

    assert result["source_entailment"] is True and result["findings"] == []
    assert scope["scope_exceptions"] is True and scope["findings"] == []
    assert result["assertions"][0]["is_factual"] is False
    assert result["semantic_classification_unverified"] is True


def test_reviewer_can_correct_role_to_factual_while_retaining_genuine_negative():
    payload, rows, spans, sources, matrix, matrix_rows = corrected_checkpoint()
    rows[0].update(
        is_factual=True,
        source_entailment=False,
        findings=[
            {
                "kind": "scope_overreach",
                "quote": "都能形成四个成熟生殖细胞",
                "issue": "若此命题仍作为教师认可的事实，则未区分卵细胞与极体。",
                "evidence_source_ids": ["ovum"],
            }
        ],
    )
    matrix_rows[0]["checks"][0] = [True, True, True, False, None]
    scope = contract.materialize_scope_matrix(matrix, matrix_rows)
    raw = deepcopy((rows, scope))
    result = contract.validate_assertion_review(payload, rows, spans, sources, scope_result=scope)

    assert result["source_entailment"] is False
    assert result["assertions"][0]["is_factual"] is True
    assert result["findings"][0]["candidate_field"] == "question"
    assert result["findings"][0]["kind"] == "scope_overreach"
    assert scope["scope_exceptions"] is False and scope["findings"]
    assert (rows, scope) == raw


@pytest.mark.parametrize("kind", ["scope_overreach", "missing_condition"])
def test_nonfactual_negative_finding_is_not_erased_to_reach_consistency(kind):
    payload, rows, spans, sources, matrix, matrix_rows = corrected_checkpoint()
    rows[0]["findings"] = [
        {
            "kind": kind,
            "quote": "都能形成四个成熟生殖细胞",
            "issue": "本条保留独立负判，不能因为非事实标记而自动清除。",
            "evidence_source_ids": ["ovum"],
            **({"missing_text": "一个卵细胞和三个极体"} if kind == "missing_condition" else {}),
        }
    ]
    scope = contract.materialize_scope_matrix(matrix, matrix_rows)
    raw = deepcopy(rows)

    with pytest.raises(contract.LessonContractError, match="nonfactual"):
        contract.validate_assertion_review(payload, rows, spans, sources, scope_result=scope)

    assert rows == raw and rows[0]["findings"][0]["kind"] == kind


def test_other_assertion_scope_rejection_does_not_invalidate_nonfactual_question_role():
    payload, rows, spans, sources, matrix, matrix_rows = corrected_checkpoint()
    target = next(i for i, row in enumerate(payload["assertions"]) if row["field"] == "expected_answer")
    matrix_rows[target]["checks"][0] = [True, True, True, False, None]
    scope = contract.materialize_scope_matrix(matrix, matrix_rows)
    result = contract.validate_assertion_review(payload, rows, spans, sources, scope_result=scope)

    assert result["assertions"][0]["is_factual"] is False
    assert scope["scope_exceptions"] is False
    assert all(finding["candidate_field"] == "expected_answer" for finding in scope["findings"])
