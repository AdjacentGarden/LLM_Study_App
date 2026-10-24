"""Flat review avoids observed missing-brace nesting; canonical proof stays strict."""
import itertools
import json
from collections import Counter

import pytest
from test_teaching_boundary_checks import FIELDS, selection
from test_teaching_content import Client, advertised_fact_response, course

from adaptive_learning.personalization.teaching_content import (
    FACT_REVIEW_PROMPT,
    TeachingContentError,
    TeachingContentGenerator,
)


class FlatClient(Client):
    def structured(self, *, system, user, **kwargs):
        original = super().structured(system=system, user=user, **kwargs)
        if system != FACT_REVIEW_PROMPT:
            return original
        # Keep the advertised assertion-by-assertion lesson proof. Flattening
        # its old aggregate booleans would omit the required assertion matrix.
        return advertised_fact_response(original, json.loads(user))


def test_flat_component_rows_publish_only_after_full_canonical_facts_and_teacher_review():
    bundle, chapter, evidence = course(4)
    client = FlatClient()
    result = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    assert result.teaching_fact_review["accepted"] is True
    assert result.teaching_review["accepted"] is True
    assert {row["point_id"] for row in result.teaching_fact_review["points"]} == {p.point_id for p in bundle.knowledge_points}
    assert set(result.teaching_fact_review["lesson"]["components"]) == {
        "opening", "summary", "example_1", "example_2", "checkpoint_1", "checkpoint_2"}
    assert all(proof["support_quotes"] for row in result.teaching_fact_review["points"] for proof in row["components"].values())
    facts = [payload for system, payload in zip(client.systems, client.payloads, strict=True) if system == FACT_REVIEW_PROMPT]
    assert all("scope_effect" not in json.dumps(payload["scope_boundaries"] if "scope_boundaries" in payload else
        [item.get("scope_boundaries", []) for item in payload["knowledge_points"]]) for payload in facts)
    assert all("scope_effect" not in json.dumps(payload["scope_conditions"]) for payload in facts)


@pytest.mark.parametrize("fault", ["missing", "duplicate", "other_point", "other_component", "lesson_as_point"])
def test_flat_wire_cannot_skip_or_borrow_any_component_pair(fault):
    bundle, chapter, evidence = course(2)
    before = bundle.model_dump_json()
    class BadFlat(FlatClient):
        def structured(self, *, system, user, **kwargs):
            result = super().structured(system=system, user=user, **kwargs)
            if system == FACT_REVIEW_PROMPT and json.loads(user)["candidate"]["points"]:
                rows = result["components"]
                if fault == "missing":
                    rows.pop()
                elif fault == "duplicate":
                    rows.append(rows[0])
                elif fault == "other_point":
                    rows[0]["point_id"] = "wrong_point"
                elif fault == "other_component":
                    rows[0]["component_id"] = "summary"
                else:
                    rows[0]["point_id"] = None
            return result
    client = BadFlat()
    events = []
    with pytest.raises(TeachingContentError):
        TeachingContentGenerator(client, audit_callback=events.append).generate(bundle, chapter, evidence)
    # The independent teacher now runs alongside facts. Its possible approval
    # cannot publish a candidate with an incomplete/foreign factual envelope.
    point_requests = [payload for system, payload in zip(client.systems, client.payloads, strict=True)
        if system == FACT_REVIEW_PROMPT and payload["candidate"]["points"]]
    assert Counter(tuple(payload["output_contract"]["point_ids"])
                   for payload in point_requests) == {
        (point.point_id,): 2 for point in bundle.knowledge_points}
    for point in bundle.knowledge_points:
        own_requests = [payload for payload in point_requests
                        if payload["output_contract"]["point_ids"] == [point.point_id]]
        assert not own_requests[0]["format_feedback"] and own_requests[1]["format_feedback"]
    assert client.review_calls <= 1
    assert len([batch for batch in client.generation_batches if batch]) == 1
    assert bundle.model_dump_json() == before
    assert not any(event["event"] == "accepted" for event in events)
    assert events[-1]["event"] == "failed"


@pytest.mark.parametrize("flags", list(itertools.product([False, True], repeat=4)))
def test_counterexample_truth_table_overrides_not_applicable_only_when_required(flags):
    proof = selection()
    check = proof.scope_checks[0]
    (check.is_class_definition, check.assigns_class_property,
     check.has_same_class_counterexample, check.candidate_explicitly_limited) = flags
    check.status = "not_applicable"
    check.limitation_field = check.limitation_quote = None
    forced = TeachingContentGenerator._enforce_counterexample_scope(proof, [{"boundary_id": "b1", "source_ids": ["s1"]}])
    assert forced is (flags == (True, True, True, False))
    if forced:
        assert proof.scope_exceptions is False and check.status == "unbounded"
        assert proof.findings[0].kind == "scope_overreach"
        assert proof.findings[0].candidate_quote in FIELDS[proof.findings[0].candidate_field]


def test_claiming_an_explicit_limit_requires_its_actual_current_component_quote():
    proof = selection()
    proof.scope_checks[0].status = "not_applicable"
    proof.scope_checks[0].limitation_field = proof.scope_checks[0].limitation_quote = None
    with pytest.raises(TeachingContentError, match="是否已限缩必须"):
        TeachingContentGenerator._validate_boundary_checks(proof, FIELDS, [{"boundary_id": "b1"}])
