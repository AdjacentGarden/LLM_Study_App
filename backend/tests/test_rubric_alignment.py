"""Frozen v17 draft controls + strict wire tests; no model/semantic pass claim.

The six paired controls are for registration before a future paid probe. Canned
responses below exercise validation and preservation of independently reviewed
judgments. They do not prove a real reviewer will classify these cases correctly.
"""

import json
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from adaptive_learning.personalization.rubric_alignment import (
    RubricAlignmentBatch,
    RubricAlignmentError,
    RubricAlignmentItem,
    make_rubric_alignment_batch,
    parse_rubric_alignment_response,
)

FIXTURE_SOURCE = {
    "candidate": "course-chapter4-v17-01-audits/unpublished-candidate-attempt1.json",
    "candidate_sha256": "6a0c628d68f29234f295d197384a9b748d373709f4692f0615cd478babae4d21",
    "independent_review": "course-chapter4-v17-01-independent-attempt1-review.json",
    "scope": "Three exact bad questions and their good pairs deleting only the unasked rubric; no paid review yet",
}

# Exact input text is frozen here rather than read from a changing model output.
# Keeping the expanded reference answer unchanged in the good pair is deliberate.
REAL_CASES = [
    {
        "case_id": "actual_point1_sugar_bases_unasked",
        "bad_index": 1,
        "item": {
            "item_id": "practice_130aa992b385f90da5af_2",
            "prompt": "某同学比较DNA与RNA时写道：“RNA都是单链，都比DNA短，所以都能通过核孔进入细胞质。”请指出其中不准确的表述，并依据RNA与DNA的结构差异加以改正。",
            "expected_answer": "不准确之处在于把“RNA一般是单链、比DNA短”说成“都是”“都”。RNA的五碳糖是核糖而非脱氧核糖，碱基中无T而含U；RNA一般是单链，而且比DNA短，因此能够通过核孔从细胞核转移到细胞质中。这里的“一般”表明并非所有RNA都必然为单链或都比DNA短，不能把一般特征扩大为全部。",
            "rubric": [
                "指出“都是单链、都比DNA短、都能通过核孔”把一般特征扩大为全部",
                "说明RNA五碳糖为核糖、碱基无T而含U",
                "保留“一般”限定，说明RNA一般单链且比DNA短，因此能通过核孔转移",
            ],
        },
        "minimum": "错误是把一般特征扩大成全部。应改为RNA一般是单链且比DNA短，因此能通过核孔；不能把这些一般特征说成所有RNA必然如此。",
        "prompt_quote": "请指出其中不准确的表述，并依据RNA与DNA的结构差异加以改正",
        "rubric_quote": "五碳糖为核糖、碱基无T而含U",
        "reason": "题干要修正单链、长度与核孔结论的一般性，完整修正这些量词已回答所问；糖和碱基虽真实，却不是修正该错误的必要推导。",
        "repair": "删除这条额外必得项；若要考糖和碱基差异，应在题干明确另问。",
    },
    {
        "case_id": "actual_point4_efficiency_benefit_unasked",
        "bad_index": 2,
        "item": {
            "item_id": "practice_130aa992b385f90da5af_5",
            "prompt": "某同学说：“因为一个mRNA上结合多个核糖体，所以所有mRNA都能同时合成多条肽链，而且细菌合成肽链的速度就是每秒15个氨基酸。”请指出其中的不准确之处并改正。",
            "expected_answer": "不准确之处有两处。其一，原文用“通常”限定：一个mRNA分子上可以相继结合多个核糖体，同时进行多条肽链的合成，不能扩大为所有mRNA都必然如此。其二，速度有条件和约数限定：在37℃时，细菌细胞内合成肽链的速度约为每秒连接15个氨基酸，不能当作普遍精确值。多核糖体同时翻译的意义是少量mRNA即可迅速合成大量蛋白质。",
            "rubric": [
                "指出“所有mRNA都同时合成多条肽链”扩大了“通常”的量词范围",
                "指出速度需保留37℃、细菌细胞和“约”的限定",
                "说明多核糖体同时翻译使少量mRNA迅速合成大量蛋白质",
            ],
        },
        "minimum": "不能把通常扩大为所有mRNA必然同时合成多条肽链；速度应改为37℃时细菌细胞内约每秒连接15个氨基酸，不能视为任何情况的精确值。",
        "prompt_quote": "请指出其中的不准确之处并改正",
        "rubric_quote": "少量mRNA迅速合成大量蛋白质",
        "reason": "恢复通常、37℃、细菌细胞和约数已完整改正两个错误，题干没有询问多核糖体的效率意义，省略该意义并不使答案不完整。",
        "repair": "删除效率意义必得项；如需考意义，题干再明确询问。",
    },
    {
        "case_id": "actual_point11_polymerase_unasked",
        "bad_index": 1,
        "item": {
            "item_id": "practice_130aa992b385f90da5af_12",
            "prompt": "某同学归纳转录时写道：“转录是在细胞核内以DNA双链中的一条链为模板合成mRNA的过程，所以转录只发生在细胞核内。”请指出这一归纳中正确的部分和需要修正的部分，并说明理由。",
            "expected_answer": "正确部分是：转录是在细胞核中以DNA双链中的一条链为模板合成mRNA的过程，合成时DNA双链解开，游离核糖核苷酸与模板链碱基互补配对，在RNA聚合酶作用下连接成mRNA。需要修正的是“只发生在细胞核内”：线粒体和叶绿体中的DNA也能进行转录和翻译，控制某些蛋白质的合成，因此转录并非只发生在细胞核内。",
            "rubric": [
                "正确表述转录以DNA一条链为模板合成mRNA",
                "说明转录中碱基互补配对和RNA聚合酶的作用",
                "指出线粒体和叶绿体中的DNA也能进行转录和翻译",
                "据此修正“只发生在细胞核内”的结论",
            ],
        },
        "minimum": "以DNA一条链为模板合成mRNA是正确的；只发生在细胞核内需要修正，因为线粒体和叶绿体DNA也能转录和翻译。",
        "prompt_quote": "请指出这一归纳中正确的部分和需要修正的部分，并说明理由",
        "rubric_quote": "碱基互补配对和RNA聚合酶的作用",
        "reason": "单链模板和产物判断、细胞质基因反例已完整回应归纳正误与理由；互补配对及RNA聚合酶是额外机制展开，不是该场所判断的必要推导。",
        "repair": "删除该额外机制评分点，或明确增加一道转录机制问项。",
    },
]


def paired_controls():
    """Six registration inputs/expected targets; no fixture labels go to PU."""
    controls = []
    for case in REAL_CASES:
        bad = deepcopy(case["item"])
        good = deepcopy(bad)
        good["rubric"].pop(case["bad_index"])
        controls.extend(
            [
                {
                    "case_id": case["case_id"] + "_bad",
                    "item": bad,
                    "expected_rejected": True,
                    "expected_rejected_indices": [case["bad_index"]],
                },
                {
                    "case_id": case["case_id"] + "_good",
                    "item": good,
                    "expected_rejected": False,
                    "expected_rejected_indices": [],
                },
            ]
        )
    return controls


def review_fixture(*, good=False):
    controls = paired_controls()
    items = (
        [controls[index]["item"] for index in (1, 3, 5)]
        if good
        else [deepcopy(case["item"]) for case in REAL_CASES]
    )
    batch = make_rubric_alignment_batch(items)
    verdicts = []
    for item, case in zip(items, REAL_CASES, strict=True):
        checks = []
        for index, text in enumerate(item["rubric"]):
            rejected = not good and index == case["bad_index"]
            checks.append(
                {
                    "rubric_index": index,
                    "rubric_text": text,
                    "required": not rejected,
                    "reason": case["reason"]
                    if rejected
                    else "该项对应题干明确要求的纠错、判断或其完整说明所需步骤。",
                    "prompt_quote": case["prompt_quote"],
                    "rubric_quote": case["rubric_quote"] if rejected else text,
                    "counterexample_answer": case["minimum"] if rejected else None,
                    "repair": case["repair"] if rejected else None,
                }
            )
        verdicts.append(
            {
                "item_id": item["item_id"],
                "minimal_sufficient_answer": case["minimum"],
                "rubric_checks": checks,
            }
        )
    return batch, {"items": verdicts}


def test_actual_bad_three_preserve_every_negative_judgment_and_exact_writer_target():
    batch, raw = review_fixture()
    before = deepcopy(raw)
    result = parse_rubric_alignment_response(raw, batch)
    assert not result.accepted
    assert result.rejected_item_ids == tuple(case["item"]["item_id"] for case in REAL_CASES)
    assert len(result.writer_feedback) == 3
    assert [
        (row["item_id"], row["rubric_index"], row["rubric_text"]) for row in result.writer_feedback
    ] == [
        (case["item"]["item_id"], case["bad_index"], case["item"]["rubric"][case["bad_index"]])
        for case in REAL_CASES
    ]
    assert all(row["counterexample_answer"] and row["repair"] for row in result.writer_feedback)
    assert all(row["component"] == "practice" for row in result.writer_feedback)
    assert raw == before  # no correction, deletion or false→true postprocessing
    assert result.to_dict()["rejected_item_ids"] == list(result.rejected_item_ids)


def test_good_pairs_only_delete_unasked_rubric_and_keep_expanded_reference_answers():
    controls = paired_controls()
    for bad, good in zip(controls[::2], controls[1::2], strict=True):
        assert bad["item"]["prompt"] == good["item"]["prompt"]
        assert bad["item"]["expected_answer"] == good["item"]["expected_answer"]
        expected = bad["item"]["rubric"].copy()
        expected.pop(bad["expected_rejected_indices"][0])
        assert good["item"]["rubric"] == expected
    batch, raw = review_fixture(good=True)
    result = parse_rubric_alignment_response(raw, batch)
    assert result.accepted and result.rejected_item_ids == () and result.writer_feedback == []
    payload = batch.to_payload()
    assert all(set(item) == {"item_id", "prompt", "rubric"} for item in payload["items"])
    assert "expected_answer" not in json.dumps(payload, ensure_ascii=False)
    assert not any(
        token in json.dumps(payload)
        for token in ("expected_rejected", "case_id", "candidate_sha256", "sources")
    )


def test_batch_is_frozen_and_output_can_reorder_without_missing_actual_targets():
    source = [deepcopy(case["item"]) for case in REAL_CASES]
    batch = make_rubric_alignment_batch(source)
    original_text = batch.items[0].rubric[0]
    source[0]["rubric"][0] = "Changed after dispatch"
    assert batch.items[0].rubric[0] == original_text
    with pytest.raises(FrozenInstanceError):
        batch.items = ()
    _, raw = review_fixture()
    raw["items"].reverse()
    for item in raw["items"]:
        item["rubric_checks"].reverse()
    parsed = parse_rubric_alignment_response(raw, batch)
    assert [row.item_id for row in parsed.items] == [item.item_id for item in batch.items]
    assert all(
        [row.rubric_index for row in item.rubric_checks] == list(range(len(item.rubric_checks)))
        for item in parsed.items
    )


@pytest.mark.parametrize(
    "fault",
    [
        "missing_item",
        "duplicate_item",
        "unknown_item",
        "missing_rubric",
        "duplicate_rubric",
        "unknown_index",
        "swapped_text",
        "rewritten_text",
        "extra_top",
        "extra_item",
        "extra_row",
    ],
)
def test_unknown_omitted_duplicate_or_rewritten_envelopes_fail_closed(fault):
    batch, raw = review_fixture()
    first = raw["items"][0]
    if fault == "missing_item":
        raw["items"].pop()
    elif fault == "duplicate_item":
        raw["items"][1] = deepcopy(first)
    elif fault == "unknown_item":
        first["item_id"] = "unknown_item"
    elif fault == "missing_rubric":
        first["rubric_checks"].pop()
    elif fault == "duplicate_rubric":
        first["rubric_checks"][1] = deepcopy(first["rubric_checks"][0])
    elif fault == "unknown_index":
        first["rubric_checks"][0]["rubric_index"] = 5
    elif fault == "swapped_text":
        first["rubric_checks"][0]["rubric_text"] = first["rubric_checks"][1]["rubric_text"]
    elif fault == "rewritten_text":
        first["rubric_checks"][0]["rubric_text"] += "。"
    elif fault == "extra_top":
        raw["accepted"] = True
    elif fault == "extra_item":
        first["accepted"] = True
    else:
        first["rubric_checks"][0]["source_ids"] = ["not_allowed"]
    with pytest.raises(RubricAlignmentError):
        parse_rubric_alignment_response(raw, batch)


@pytest.mark.parametrize(
    "field,value",
    [
        ("required", "true"),
        ("required", 1),
        ("required", 0),
        ("rubric_index", True),
        ("rubric_index", 1.0),
        ("rubric_index", "0"),
        ("reason", " \n"),
        ("prompt_quote", ""),
        ("prompt_quote", " "),
        ("rubric_quote", " "),
        ("prompt_quote", "This is only in another question"),
        ("rubric_quote", "This is not this criterion"),
    ],
)
def test_strict_types_and_nonempty_literal_locations_are_required(field, value):
    batch, raw = review_fixture()
    raw["items"][0]["rubric_checks"][0][field] = value
    with pytest.raises(RubricAlignmentError):
        parse_rubric_alignment_response(raw, batch)


@pytest.mark.parametrize(
    "field,value",
    [
        ("counterexample_answer", None),
        ("counterexample_answer", " "),
        ("repair", None),
        ("repair", "\n"),
    ],
)
def test_false_cannot_drop_its_counterexample_or_concrete_repair(field, value):
    batch, raw = review_fixture()
    raw["items"][0]["rubric_checks"][1][field] = value
    with pytest.raises(RubricAlignmentError):
        parse_rubric_alignment_response(raw, batch)


def test_required_with_counterexample_is_contradictory_and_not_silently_accepted():
    batch, raw = review_fixture()
    raw["items"][0]["rubric_checks"][1]["required"] = True
    with pytest.raises(RubricAlignmentError):
        parse_rubric_alignment_response(raw, batch)


@pytest.mark.parametrize(
    "fault",
    [
        "empty",
        "too_many",
        "duplicate_id",
        "blank_prompt",
        "blank_rubric",
        "wrong_rubric_type",
        "unknown_input",
    ],
)
def test_input_is_bounded_known_and_nonempty(fault):
    items = [deepcopy(case["item"]) for case in REAL_CASES]
    if fault == "empty":
        items = []
    elif fault == "too_many":
        items.append(deepcopy(items[0]))
    elif fault == "duplicate_id":
        items[1]["item_id"] = items[0]["item_id"]
    elif fault == "blank_prompt":
        items[0]["prompt"] = " "
    elif fault == "blank_rubric":
        items[0]["rubric"][0] = " "
    elif fault == "wrong_rubric_type":
        items[0]["rubric"][0] = True
    else:
        items[0]["sources"] = {"raw": "must not enter question-only reviewer"}
    with pytest.raises(RubricAlignmentError):
        make_rubric_alignment_batch(items)


def test_direct_empty_batch_cannot_produce_approval_from_an_empty_response():
    # Even bypassing the factory cannot turn a missing review into approval.
    with pytest.raises(RubricAlignmentError):
        parse_rubric_alignment_response({"items": []}, RubricAlignmentBatch(()))


def test_direct_mutable_criteria_cannot_bypass_the_frozen_snapshot_contract():
    with pytest.raises(RubricAlignmentError):
        RubricAlignmentBatch((RubricAlignmentItem("known", "完整问题", ["可变评分"]),))
