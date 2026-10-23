"""Offline mechanical tests; fixture labels never enter model contracts.

Real v14 draft examples remain unpublished. Passing these tests establishes
coverage/provenance mechanics, never correct model semantic classification.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from adaptive_learning.personalization import lesson_review_contract as contract


@pytest.fixture(scope="module")
def real():
    return json.loads(
        (
            Path(__file__).resolve().parent / "fixtures/teaching_v14_lesson_diagnostic.json"
        ).read_text()
    )


def decisions(payload):
    return [
        {
            "assertion_id": row["assertion_id"],
            "is_factual": True,
            "source_entailment": True,
            "support_span_ids": payload["allowed_support_span_ids"],
            "findings": [],
        }
        for row in payload["assertions"]
    ]


def test_assertions_are_stable_exact_raw_coverage_for_every_field():
    fields = {
        "title": "标题",
        "step_1": " α\n第一句。" + "甲" * 600 + "\r\n末尾。 ",
        "answer": "AUG—RNA",
    }
    spans = contract.split_assertions("example_2", fields)
    assert spans == contract.split_assertions("example_2", fields)
    assert len({row["assertion_id"] for row in spans}) == len(spans)
    for field, raw in fields.items():
        selected = [row for row in spans if row["field"] == field]
        assert "".join(row["text"] for row in selected) == raw
        assert selected[0]["start"] == 0 and selected[-1]["end"] == len(raw)
        assert all(
            raw[row["start"] : row["end"]] == row["text"] and len(row["text"]) <= 240
            for row in selected
        )
        assert all(a["end"] == b["start"] for a, b in zip(selected, selected[1:], strict=False))


@pytest.mark.parametrize(
    "damage", ["missing", "duplicate", "unknown", "string_bool", "no_proof", "foreign_proof"]
)
def test_bad_assertion_envelopes_cannot_omit_or_borrow_review(real, damage):
    own = real["lesson"]["reading_source_ids"]
    payload = contract.build_assertion_contract(
        "opening", {"content": real["lesson"]["opening"]}, own, real["spans"], real["sources"]
    )
    rows = decisions(payload)
    if damage == "missing":
        rows = rows[:-1]
    if damage == "duplicate":
        rows.append(deepcopy(rows[0]))
    if damage == "unknown":
        rows[0]["assertion_id"] = "unknown"
    if damage == "string_bool":
        rows[0]["is_factual"] = "true"
    if damage == "no_proof":
        rows[0]["support_span_ids"] = []
    if damage == "foreign_proof":
        rows[0]["support_span_ids"] = [
            next(key for key, row in real["spans"].items() if row["source_id"] not in own)
        ]
    with pytest.raises(contract.LessonContractError):
        contract.validate_assertion_review(payload, rows, real["spans"], real["sources"])


def test_real_bad_opening_located_rejection_and_corrected_citation_contract(real):
    fields = {"content": real["lesson"]["opening"]}
    own = real["lesson"]["reading_source_ids"]
    missing = "src_p57_0_444_6b143d08d3ab"
    assert missing not in own
    payload = contract.build_assertion_contract(
        "opening", fields, own, real["spans"], real["sources"]
    )
    rows = decisions(payload)
    rows[0].update(
        source_entailment=False,
        support_span_ids=[],
        findings=[
            {
                "kind": "unsupported_claim",
                "quote": "基因是有遗传效应的DNA片段",
                "issue": "当前自身引用缺少基因定义的实际来源。",
                "evidence_source_ids": [missing],
            }
        ],
    )
    rejected = contract.validate_assertion_review(payload, rows, real["spans"], real["sources"])
    assert not rejected["source_entailment"]
    assert rejected["findings"][0]["candidate_field"] == "content"
    repaired = contract.build_assertion_contract(
        "opening", fields, own + [missing], real["spans"], real["sources"]
    )
    assert contract.validate_assertion_review(
        repaired, decisions(repaired), real["spans"], real["sources"]
    )["source_entailment"]
    assert not any(
        key in json.dumps(repaired) for key in ["expectations", "expected_verdict", "fixture_label"]
    )


def test_real_example2_negative_finding_is_confined_to_its_exact_step(real):
    example = real["lesson"]["worked_examples"][1]
    fields = {
        "title": example["title"],
        "problem": example["problem"],
        "answer": example["answer"],
        **{f"step_{i}": text for i, text in enumerate(example["steps"], 1)},
    }
    missing = "src_p61_0_350_60adad10c52e"
    assert missing not in example["source_ids"]
    payload = contract.build_assertion_contract(
        "example_2", fields, example["source_ids"], real["spans"], real["sources"]
    )
    rows = decisions(payload)
    target = next(
        i for i, row in enumerate(payload["assertions"]) if "tRNA上的反密码子" in row["text"]
    )
    rows[target].update(
        source_entailment=False,
        support_span_ids=[],
        findings=[
            {
                "kind": "unsupported_claim",
                "quote": "tRNA上的反密码子与密码子互补配对",
                "issue": "这一步自身引用未包含反密码子名称和配对的依据。",
                "evidence_source_ids": [missing],
            }
        ],
    )
    result = contract.validate_assertion_review(payload, rows, real["spans"], real["sources"])
    assert result["findings"][0]["candidate_field"] == "step_3"
    rows[target]["findings"][0]["quote"] = fields["answer"]
    with pytest.raises(contract.LessonContractError, match="own assertion"):
        contract.validate_assertion_review(payload, rows, real["spans"], real["sources"])
    fixed = contract.build_assertion_contract(
        "example_2", fields, example["source_ids"] + [missing], real["spans"], real["sources"]
    )
    assert contract.validate_assertion_review(
        fixed, decisions(fixed), real["spans"], real["sources"]
    )["source_entailment"]


def test_nonfactual_self_classification_is_explicit_but_never_semantically_verified(real):
    payload = contract.build_assertion_contract(
        "opening",
        {"content": real["lesson"]["opening"]},
        real["lesson"]["reading_source_ids"],
        real["spans"],
        real["sources"],
    )
    rows = decisions(payload)
    for row in rows:
        row.update(is_factual=False, support_span_ids=[])
    result = contract.validate_assertion_review(payload, rows, real["spans"], real["sources"])
    assert result["semantic_classification_unverified"] is True
    # A malicious/incorrect model could mislabel this real factual opening.
    # This demonstrates the need for heldout model controls, not a quality pass.
    rows[0]["source_entailment"] = False
    with pytest.raises(contract.LessonContractError, match="nonfactual"):
        contract.validate_assertion_review(payload, rows, real["spans"], real["sources"])


def test_summary_all_chapter_boundaries_and_forced_unbounded_process_claim(real):
    fields = {"content": real["lesson"]["summary"]}
    links = contract.associate_lesson_boundaries(
        "summary", fields, real["lesson"]["reading_source_ids"], real["points"], real["sources"]
    )
    all_ids = {row["boundary_id"] for point in real["points"] for row in point["scope_boundaries"]}
    assert {row["boundary_id"] for row in links["boundaries"]} == all_ids
    assert links["sources"] == real["sources"] and not links["association_is_semantic_approval"]
    assertions = contract.split_assertions("summary", fields)
    target = next(row for row in links["boundaries"] if "线粒体" in row["condition"])
    claim = next(row for row in assertions if "转录在细胞核内" in row["text"])
    rows = [
        {
            "boundary_id": row["boundary_id"],
            "flags": [False, False, False, False],
            "claim_assertion_id": claim["assertion_id"],
            "limitation_assertion_id": None,
        }
        for row in links["boundaries"]
    ]
    next(row for row in rows if row["boundary_id"] == target["boundary_id"])["flags"] = [
        True,
        True,
        True,
        False,
    ]
    result = contract.materialize_scope_checks(assertions, links["boundaries"], rows)
    assert not result["scope_exceptions"]
    finding = result["findings"][0]
    assert finding["candidate_quote"] in fields[finding["candidate_field"]]
    check = next(
        row for row in result["scope_checks"] if row["boundary_id"] == target["boundary_id"]
    )
    assert check["status"] == "unbounded" and check["is_class_definition"]
    # The first flag includes a process/general-property claim, not only a
    # formal definition. Semantic truth of all other flags remains untested.
    fixed = (
        fields["content"].replace("转录在细胞核内", "对细胞核基因而言，转录在细胞核内")
        + "线粒体和叶绿体中的DNA也能进行转录。"
    )
    repaired = contract.split_assertions("summary", {"content": fixed})
    limited = next(row for row in reversed(repaired) if "线粒体和叶绿体" in row["text"])
    repaired_claim = next(row for row in repaired if "转录在细胞核内" in row["text"])
    repaired_rows = [
        {
            "boundary_id": target["boundary_id"],
            "flags": [True, True, True, True],
            "claim_assertion_id": repaired_claim["assertion_id"],
            "limitation_assertion_id": limited["assertion_id"],
        }
    ]
    fixed_result = contract.materialize_scope_checks(repaired, [target], repaired_rows)
    assert (
        fixed_result["scope_exceptions"] and fixed_result["scope_checks"][0]["status"] == "bounded"
    )
    assert len(fixed_result["scope_checks"][0]["limitation_quote"]) <= 240


def test_boundary_links_use_real_ids_raw_dedup_and_no_match_falls_back(real):
    example = real["lesson"]["worked_examples"][1]
    linked = contract.associate_lesson_boundaries(
        "example_2",
        {"content": "\n".join(example["steps"])},
        example["source_ids"],
        real["points"],
        real["sources"],
    )
    assert linked["boundaries"] and not linked["chapter_has_no_scope_boundaries"]
    assert all(row["point_ids"] for row in linked["boundaries"])
    for boundary in linked["boundaries"]:
        assert boundary["actual_source_text"] == [
            {"source_id": key, "quote": real["sources"][key]["quote"]}
            for key in boundary["source_ids"]
        ]
    fallback = contract.associate_lesson_boundaries(
        "checkpoint_2", {"question": "◇"}, [], real["points"], real["sources"]
    )
    assert fallback["association_mode"] == "whole_chapter_fallback" and fallback["boundaries"]
    all_ids = {row["boundary_id"] for point in real["points"] for row in point["scope_boundaries"]}
    assert {row["boundary_id"] for row in linked["boundaries"]} == all_ids


def test_generic_theme_matching_and_boundary_dedup_has_no_subject_terms():
    sources = {"a": {"quote": "全文甲"}, "b": {"quote": "全文乙"}}
    shared = {
        "boundary_id": "b1",
        "boundary_type": "precondition",
        "condition": "温度不变",
        "source_ids": ["a"],
    }
    points = [
        {
            "point_id": "real_p1",
            "label": "ZephyrLab 稀有连通结构",
            "point_source_ids": ["a"],
            "scope_boundaries": [shared],
        },
        {
            "point_id": "real_p2",
            "label": "另一主题",
            "point_source_ids": ["b"],
            "scope_boundaries": [shared],
        },
    ]
    linked = contract.associate_lesson_boundaries(
        "example_1", {"problem": "ZephyrLab结构怎样处理？"}, [], points, sources
    )
    assert linked["association_mode"] == "whole_chapter_ranked_navigation"
    assert linked["boundaries"][0]["point_ids"] == ["real_p1", "real_p2"]
    assert linked["point_associations"][0]["navigation_priority"]
    assert not linked["point_associations"][1]["navigation_priority"]
    whole = contract.associate_lesson_boundaries(
        "opening", {"content": "课程安排"}, [], points, sources
    )
    assert len(whole["boundaries"]) == 1 and whole["boundaries"][0]["point_ids"] == [
        "real_p1",
        "real_p2",
    ]
    over = deepcopy(points[:1])
    over[0]["scope_boundaries"] = [{**shared, "boundary_id": f"b{i}"} for i in range(129)]
    with pytest.raises(contract.LessonContractError, match="truncate"):
        contract.associate_lesson_boundaries("summary", {"content": "安排"}, [], over, sources)


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "duplicate",
        "unknown",
        "string_bool",
        "unknown_claim",
        "fake_limitation",
        "extra_status",
    ],
)
def test_compact_scope_contract_rejects_bad_coverage_or_fake_limits(damage):
    spans = contract.split_assertions("summary", {"content": "通用过程产生结果。存在例外时终止。"})
    boundaries = [{"boundary_id": "b1", "condition": "特殊情形终止", "source_ids": ["s1"]}]
    rows = [
        {
            "boundary_id": "b1",
            "flags": [True, True, True, False],
            "claim_assertion_id": spans[0]["assertion_id"],
            "limitation_assertion_id": None,
        }
    ]
    if damage == "missing":
        rows = []
    if damage == "duplicate":
        rows *= 2
    if damage == "unknown":
        rows[0]["boundary_id"] = "not_real"
    if damage == "string_bool":
        rows[0]["flags"][0] = "true"
    if damage == "unknown_claim":
        rows[0]["claim_assertion_id"] = "not_real"
    if damage == "fake_limitation":
        rows[0]["limitation_assertion_id"] = spans[0]["assertion_id"]
    if damage == "extra_status":
        rows[0]["status"] = "not_applicable"
    with pytest.raises(contract.LessonContractError):
        contract.materialize_scope_checks(spans, boundaries, rows)


def test_hardware_mixed_theme_keeps_unmatched_boundary_in_all_components():
    sources = {
        "gpu": {"quote": "显存不足时须分批处理。"},
        "disk": {"quote": "断电时未落盘的数据可能丢失。"},
    }
    points = [
        {
            "point_id": "actual_gpu",
            "label": "GPU显存分批处理",
            "point_source_ids": ["gpu"],
            "scope_boundaries": [
                {
                    "boundary_id": "gpu_limit",
                    "boundary_type": "precondition",
                    "condition": "显存不足须分批",
                    "source_ids": ["gpu"],
                }
            ],
        },
        {
            "point_id": "actual_disk",
            "label": "持久存储与断电",
            "point_source_ids": ["disk"],
            "scope_boundaries": [
                {
                    "boundary_id": "disk_limit",
                    "boundary_type": "exception",
                    "condition": "断电可能丢失未落盘数据",
                    "source_ids": ["disk"],
                }
            ],
        },
    ]
    for component in ["opening", "summary", "example_2", "checkpoint_1"]:
        links = contract.associate_lesson_boundaries(
            component,
            {"content": "GPU的显存应分批管理；其余输入也要保存。"},
            ["gpu"],
            points,
            sources,
        )
        assert {row["boundary_id"] for row in links["boundaries"]} == {"gpu_limit", "disk_limit"}
        disk = next(row for row in links["boundaries"] if row["boundary_id"] == "disk_limit")
        assert disk["point_ids"] == ["actual_disk"]
        assert disk["actual_source_text"] == [
            {"source_id": "disk", "quote": sources["disk"]["quote"]}
        ]


def matrix_fixture():
    fields = {"content": "温度不变时可以沿用判断。任何温度下都可以直接测量。"}
    assertions = contract.split_assertions("summary", fields)
    assert len(assertions) == 2
    boundaries = [
        {
            "boundary_id": "actual_temperature_boundary",
            "boundary_type": "precondition",
            "condition": "温度改变后不能沿用判断，必须重新校准。",
            "source_ids": ["s1"],
            "actual_source_text": [
                {"source_id": "s1", "quote": "温度不变才可沿用；温度改变须重新校准。"}
            ],
            "point_ids": ["actual_measurement"],
        }
    ]
    wire = contract.build_scope_matrix_contract(assertions, boundaries)
    rows = [
        {"assertion_id": assertions[0]["assertion_id"], "checks": [[True, True, True, True, 0]]},
        {
            "assertion_id": assertions[1]["assertion_id"],
            "checks": [[True, True, True, False, None]],
        },
    ]
    return fields, wire, rows


def test_matrix_cannot_hide_unconditional_second_sentence_behind_bounded_first():
    fields, wire, rows = matrix_fixture()
    result = contract.materialize_scope_matrix(wire, rows)
    assert result["matrix_cells"] == 2 and not result["scope_exceptions"]
    assert [row["status"] for row in result["scope_checks"]] == ["bounded", "unbounded"]
    assert result["findings"][0]["candidate_quote"] == "任何温度下都可以直接测量。"
    assert result["findings"][0]["candidate_quote"] in fields["content"]
    assert len({row["boundary_id"] for row in result["expanded_boundaries"]}) == 2
    assert {row["parent_boundary_id"] for row in result["expanded_boundaries"]} == set(
        wire["boundary_ids"]
    )
    assert all(row["source_ids"] == ["s1"] for row in result["expanded_boundaries"])
    assert result == contract.materialize_scope_matrix(wire, list(reversed(rows)))
    assert "expected_verdict" not in json.dumps(wire)


@pytest.mark.parametrize(
    "damage",
    [
        "missing_assertion",
        "duplicate_assertion",
        "unknown_assertion",
        "missing_cell",
        "extra_cell",
        "short_cell",
        "string_bool",
        "bool_index",
        "outside_index",
        "missing_index",
        "false_with_index",
        "extra_status",
    ],
)
def test_matrix_requires_exact_pair_coverage_and_strict_cell_types(damage):
    _, wire, rows = matrix_fixture()
    if damage == "missing_assertion":
        rows = rows[:1]
    if damage == "duplicate_assertion":
        rows.append(deepcopy(rows[0]))
    if damage == "unknown_assertion":
        rows[0]["assertion_id"] = "foreign_assertion"
    if damage == "missing_cell":
        rows[0]["checks"] = []
    if damage == "extra_cell":
        rows[0]["checks"] *= 2
    if damage == "short_cell":
        rows[0]["checks"][0] = rows[0]["checks"][0][:4]
    if damage == "string_bool":
        rows[0]["checks"][0][0] = "true"
    if damage == "bool_index":
        rows[0]["checks"][0][4] = True
    if damage == "outside_index":
        rows[0]["checks"][0][4] = 2
    if damage == "missing_index":
        rows[0]["checks"][0][4] = None
    if damage == "false_with_index":
        rows[1]["checks"][0][4] = 0
    if damage == "extra_status":
        rows[0]["status"] = "not_applicable"
    with pytest.raises(contract.LessonContractError):
        contract.materialize_scope_matrix(wire, rows)


def test_matrix_preserves_fixed_boundary_order_and_rejects_oversize_without_truncation():
    _, wire, rows = matrix_fixture()
    extra = {**deepcopy(wire["boundaries"][0]), "boundary_id": "actual_second_boundary"}
    full = contract.build_scope_matrix_contract(wire["assertions"], wire["boundaries"] + [extra])
    for row in rows:
        row["checks"].append([False, False, False, False, None])
    result = contract.materialize_scope_matrix(full, rows)
    assert result["parent_boundary_ids"] == [
        "actual_temperature_boundary",
        "actual_second_boundary",
    ]
    assert result["matrix_cells"] == 4
    full["boundary_ids"].reverse()
    with pytest.raises(contract.LessonContractError, match="fixed boundary order"):
        contract.materialize_scope_matrix(full, rows)
    too_many = [{**extra, "boundary_id": f"boundary_{i}"} for i in range(129)]
    with pytest.raises(contract.LessonContractError, match="128"):
        contract.build_scope_matrix_contract(wire["assertions"], too_many)
    many_assertions = contract.split_assertions("summary", {"content": "这是独立断言。" * 33})
    with pytest.raises(contract.LessonContractError, match="4096"):
        contract.build_scope_matrix_contract(many_assertions, too_many[:128])


def test_legacy_short_nonempty_rationale_is_accepted_without_arbitrary_padding():
    _, wire, _ = matrix_fixture()
    result = contract.materialize_scope_checks(
        wire["assertions"],
        wire["boundaries"],
        [
            {
                "boundary_id": wire["boundary_ids"][0],
                "flags": [True, True, True, False],
                "claim_assertion_id": wire["assertions"][1]["assertion_id"],
                "limitation_assertion_id": None,
                "rationale": "未限缩。",
            }
        ],
    )
    assert not result["scope_exceptions"] and result["scope_checks"][0]["rationale"] == "未限缩。"


def source_supported_scope_fixture(kind="scope_overreach"):
    sources = {
        "own": {"quote": "可以沿用原先的判断。另一项测量结果暂不作推广。"},
        "boundary": {"quote": "只有温度不变才可沿用，温度改变须重新校准。"},
        "unrelated": {"quote": "另一实验还应注意颜色改变。"},
    }
    spans = {
        "own_span": {"source_id": "own", "quote": sources["own"]["quote"]},
        "foreign_span": {"source_id": "boundary", "quote": sources["boundary"]["quote"]},
    }
    payload = contract.build_assertion_contract(
        "summary", {"content": sources["own"]["quote"]}, ["own"], spans, sources
    )
    rows = decisions(payload)
    rows[0]["findings"] = [
        {
            "kind": kind,
            "quote": "可以沿用原先的判断",
            "issue": "该判断没有保留温度不变的必要条件。",
            "evidence_source_ids": ["boundary"],
            **({"missing_text": "只有温度不变"} if kind == "missing_condition" else {}),
        }
    ]
    matrix = contract.build_scope_matrix_contract(
        payload["assertions"],
        [
            {
                "boundary_id": "temperature_boundary",
                "boundary_type": "precondition",
                "condition": sources["boundary"]["quote"],
                "source_ids": ["boundary"],
            }
        ],
    )
    matrix_rows = [
        {"assertion_id": row["assertion_id"], "checks": [[False, False, False, False, None]]}
        for row in payload["assertions"]
    ]
    matrix_rows[0]["checks"][0] = [True, True, True, False, None]
    return payload, rows, spans, sources, matrix, matrix_rows


@pytest.mark.parametrize("kind", ["scope_overreach", "missing_condition"])
def test_source_supported_assertion_can_retain_its_independent_scope_rejection(kind):
    payload, rows, spans, sources, matrix, matrix_rows = source_supported_scope_fixture(kind)
    original = deepcopy(rows)
    scope = contract.materialize_scope_matrix(matrix, matrix_rows)
    result = contract.validate_assertion_review(payload, rows, spans, sources, scope_result=scope)
    assert result["source_entailment"] is True
    assert scope["scope_exceptions"] is False
    assert result["findings"][0]["kind"] == kind
    assert result["findings"][0]["candidate_field"] == "content"
    assert result["findings"][0]["candidate_quote"] == rows[0]["findings"][0]["quote"]
    assert rows == original


@pytest.mark.parametrize(
    "damage",
    [
        "no_matrix",
        "no_negative_cell",
        "other_assertion",
        "unrelated_evidence",
        "no_own_proof",
        "foreign_proof",
        "unsupported_claim",
        "invalid_inference",
        "contradiction",
        "nonfactual",
        "false_counterexample",
        "unbounded_status_only",
        "wrong_claim",
        "wrong_pair",
        "numeric_flag",
    ],
)
def test_source_positive_findings_cannot_borrow_or_invent_scope_evidence(damage):
    payload, rows, spans, sources, matrix, matrix_rows = source_supported_scope_fixture()
    if damage == "no_negative_cell":
        matrix_rows[0]["checks"][0] = [False, False, False, False, None]
    elif damage == "other_assertion":
        matrix_rows[0]["checks"][0] = [False, False, False, False, None]
        matrix_rows[1]["checks"][0] = [True, True, True, False, None]
    elif damage == "unrelated_evidence":
        rows[0]["findings"][0]["evidence_source_ids"] = ["unrelated"]
    elif damage == "no_own_proof":
        rows[0]["support_span_ids"] = []
    elif damage == "foreign_proof":
        rows[0]["support_span_ids"] = ["foreign_span"]
    elif damage in {"unsupported_claim", "invalid_inference", "contradiction"}:
        rows[0]["findings"][0]["kind"] = damage
    elif damage == "nonfactual":
        rows[0]["is_factual"] = False
    scope = contract.materialize_scope_matrix(matrix, matrix_rows)
    if damage == "no_matrix":
        scope = None
    elif damage == "false_counterexample":
        scope["scope_checks"][0]["has_same_class_counterexample"] = False
    elif damage == "unbounded_status_only":
        scope["scope_checks"][0]["is_class_definition"] = False
    elif damage == "wrong_claim":
        scope["scope_checks"][0]["claim_quote"] = payload["assertions"][1]["text"]
    elif damage == "wrong_pair":
        scope["expanded_boundaries"][0]["parent_boundary_id"] = "another_parent"
    elif damage == "numeric_flag":
        scope["scope_checks"][0]["is_class_definition"] = 1
    with pytest.raises(contract.LessonContractError):
        contract.validate_assertion_review(payload, rows, spans, sources, scope_result=scope)


def test_each_source_positive_finding_needs_its_own_scope_evidence():
    payload, rows, spans, sources, matrix, matrix_rows = source_supported_scope_fixture()
    rows[0]["findings"].append(
        {**deepcopy(rows[0]["findings"][0]), "evidence_source_ids": ["unrelated"]}
    )
    scope = contract.materialize_scope_matrix(matrix, matrix_rows)
    with pytest.raises(contract.LessonContractError, match="own unbounded"):
        contract.validate_assertion_review(payload, rows, spans, sources, scope_result=scope)
