import json
from pathlib import Path

import pytest

from adaptive_learning.personalization.teaching_fact_wire import (
    QUOTE_REF,
    compact_fact_payload,
    restore_fact_payload,
)


def payload():
    raw = "温度不变时才能直接沿用；温度改变后先重新校准。\n保留全部空白与图注。"
    return {
        "sources": {"s1": {"page_number": 1, "quote": raw}},
        "scope_conditions": {"p1": [{"condition": "温度不变", "source_ids": ["s1"]}]},
        "knowledge_points": [
            {
                "point_id": "p1",
                "scope_conditions": [{"condition": "温度不变", "source_ids": ["s1"]}],
            }
        ],
        "candidate": {
            "points": [
                {
                    "point_id": "p1",
                    "scope_boundaries": [
                        {
                            "boundary_id": "b",
                            "actual_source_text": [{"source_id": "s1", "quote": raw}],
                        }
                    ],
                    "component_evidence": {
                        "explanation": {
                            "actual_source_text": [{"source_id": "s1", "quote": raw}],
                            "allowed_support_span_ids": ["span1"],
                        }
                    },
                }
            ]
        },
        "support_spans": {"span1": {"source_id": "s1", "quote": raw[:18]}},
    }


def test_full_text_and_adjacent_boundaries_stay_readable_own_refs_roundtrip_exact_bytes():
    original = payload()
    compact = compact_fact_payload(original)
    restored = restore_fact_payload(compact)
    assert original == restored
    assert json.dumps(original, ensure_ascii=False) == json.dumps(restored, ensure_ascii=False)
    assert compact["sources"] == original["sources"]
    assert compact["support_spans"] == original["support_spans"]
    assert (
        compact["candidate"]["points"][0]["scope_boundaries"]
        == original["candidate"]["points"][0]["scope_boundaries"]
    )
    assert compact["candidate"]["points"][0]["component_evidence"]["explanation"][
        "actual_source_text"
    ][0]["quote"] == {QUOTE_REF: "s1"}
    assert "scope_conditions" not in compact


@pytest.mark.parametrize("fault", ["unknown", "foreign_text", "missing_top", "string_flag"])
def test_malformed_references_stop_before_inference(fault):
    original = payload()
    if fault == "foreign_text":
        original["candidate"]["points"][0]["component_evidence"]["explanation"][
            "actual_source_text"
        ][0]["quote"] += "新造事实"
        with pytest.raises(ValueError):
            compact_fact_payload(original)
        return
    compact = compact_fact_payload(original)
    if fault == "unknown":
        compact["candidate"]["points"][0]["component_evidence"]["explanation"][
            "actual_source_text"
        ][0]["quote"] = {QUOTE_REF: "not_a_source"}
    elif fault == "missing_top":
        compact.pop("support_spans")
    else:
        compact["wire_metadata"]["scope_conditions_from_knowledge_points"] = "true"
    with pytest.raises(ValueError):
        restore_fact_payload(compact)


def test_every_actual_v14_request_is_exactly_reconstructable_without_source_or_proof_loss():
    requests = json.loads(
        (Path(__file__).resolve().parent / "fixtures/teaching_v14_fact_requests.json").read_text()
    )
    assert len(requests) >= 10
    for original in requests:
        wire = compact_fact_payload(original)
        assert json.dumps(restore_fact_payload(wire), ensure_ascii=False) == json.dumps(
            original, ensure_ascii=False
        )
        assert wire["sources"] == original["sources"]
        assert wire.get("support_spans") == original.get("support_spans")
