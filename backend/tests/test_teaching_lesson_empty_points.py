"""The real F3f lesson-only wire may omit an empty envelope, never content."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_teaching_v15_integration import CandidateClient, Planner

from adaptive_learning.personalization import teaching_content as teaching

OBSERVED = json.loads((Path(__file__).parent / "fixtures" /
                       "teaching_lesson_empty_points_3f.json").read_text())


def setup():
    audit = []
    generator = teaching.TeachingContentGenerator(
        CandidateClient(), scope_planner=Planner(), audit_callback=audit.append
    )
    lesson = teaching._Lesson.model_validate(OBSERVED["baseline_lesson"])
    contract = generator._repair_contract(set(), {}, {}, lesson, {"checkpoint_3"}, True)
    return generator, contract, lesson, audit


def test_exact_real_lesson_wire_adds_only_empty_points_and_preserves_raw_and_slot_content():
    generator, contract, baseline, audit = setup()
    raw = deepcopy(OBSERVED["response"])
    original = deepcopy(raw)
    assert set(raw) == {"lesson"}
    assert contract["output_shape"]["points"] == []
    assert set(contract["output_shape"]["lesson"]) == {"checkpoint_3"}
    result = generator._materialize_generation_patch(raw, contract, {}, baseline)
    assert raw == original
    assert result.points == []
    assert result.lesson.checkpoints[2].model_dump() == raw["lesson"]["checkpoint_3"]
    unchanged = result.lesson.model_dump()
    unchanged["checkpoints"][2] = baseline.checkpoints[2].model_dump()
    assert unchanged == baseline.model_dump()
    generator._validate_candidate_repair(
        [], result.lesson, {"points": [], "lesson": baseline.model_dump()},
        teaching._RepairRequirements(exact_lesson={"checkpoint_3"}),
    )
    assert [row["event"] for row in audit] == [
        "generation_patch_envelope_normalized", "generation_patch_materialized"
    ]
    normalized, merged = audit
    assert normalized["added_empty_fields"] == {"points": []}
    assert normalized["original_sha256"] == hashlib.sha256(
        json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    assert normalized["approved"] is merged["approved"] is False
    assert normalized["independent_review_required"] is True
    assert merged["patch"] == raw


@pytest.mark.parametrize("fault", ["extra", "transport_type_without_points", "points_null",
                                   "missing_slot", "foreign_slot", "bad_length", "null_lesson"])
def test_empty_envelope_normalization_never_admits_other_shapes_or_invalid_slot_content(fault):
    generator, contract, baseline, audit = setup()
    raw = deepcopy(OBSERVED["response"])
    if fault == "extra":
        raw["approved"] = True
    elif fault == "transport_type_without_points":
        raw["type"] = "json_object"
    elif fault == "points_null":
        raw["points"] = None
    elif fault == "missing_slot":
        raw["lesson"] = {}
    elif fault == "foreign_slot":
        raw["lesson"]["checkpoint_2"] = raw["lesson"]["checkpoint_3"]
    elif fault == "bad_length":
        raw["lesson"]["checkpoint_3"]["question"] = "短"
    else:
        raw["lesson"] = None
    with pytest.raises((ValueError, teaching.TeachingContentError)):
        generator._materialize_generation_patch(raw, contract, {}, baseline)
    assert audit == []  # No successful normalization/materialization of invalid data.


@pytest.mark.parametrize("fault", ["point_repair", "lesson_not_required", "unknown_mode"])
def test_missing_points_is_not_normalized_outside_the_known_empty_lesson_contract(fault):
    generator, contract, baseline, audit = setup()
    if fault == "point_repair":
        contract["point_components"] = {"kp_0123456789abcdef": ["practice"]}
    elif fault == "lesson_not_required":
        contract["lesson_required"] = False
    else:
        contract["mode"] = "other-protocol"
    with pytest.raises(teaching.TeachingContentError, match="精确points/lesson"):
        generator._materialize_generation_patch(OBSERVED["response"], contract, {}, baseline)
    assert audit == []


def test_adding_empty_points_cannot_disguise_an_unchanged_rejected_lesson_slot():
    generator, contract, baseline, _ = setup()
    result = generator._materialize_generation_patch(
        {"lesson": {"checkpoint_3": baseline.checkpoints[2].model_dump()}},
        contract, {}, baseline,
    )
    with pytest.raises(teaching.TeachingContentError, match="未修改"):
        generator._validate_candidate_repair(
            [], result.lesson, {"points": [], "lesson": baseline.model_dump()},
            teaching._RepairRequirements(exact_lesson={"checkpoint_3"}),
        )


def test_an_explicit_complete_envelope_does_not_emit_a_normalization_event():
    generator, contract, baseline, audit = setup()
    generator._materialize_generation_patch(
        {"points": [], **OBSERVED["response"]}, contract, {}, baseline
    )
    assert [row["event"] for row in audit] == ["generation_patch_materialized"]
