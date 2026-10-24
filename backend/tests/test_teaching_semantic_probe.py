"""Registered controls distinguish scope, citation coverage and false rejection."""
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_teaching_content import selected_proof

from adaptive_learning.personalization.teaching_content import (
    FACT_REVIEW_PROMPT,
    TeachingContentGenerator,
)
from adaptive_learning.personalization.teaching_scope import SCOPE_PLANNING_PROMPT

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "teaching_semantic_probe.py"
spec = importlib.util.spec_from_file_location("release_teaching_semantic_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def verdict(*, failed=(), scope_fail=(), source_fail=()):
    components = SimpleNamespace(**{name: SimpleNamespace(
        source_entailment=name not in source_fail, scope_exceptions=name not in scope_fail,
        findings=[{"issue": "具体且已定位的问题"}] if name in failed else []) for name in probe.COMPONENTS})
    row = SimpleNamespace(point_id="point_measurement", accepted=not failed,
        source_entailment=not source_fail, scope_exceptions=not scope_fail,
        failed_components=list(failed), issues=["局部问题"] if failed else [], components=components)
    return SimpleNamespace(accepted=not failed, issues=[], points=[row])


def test_controls_change_one_component_or_its_citations_and_keep_navigation_identical():
    cases = probe.build_cases()
    good, bad, missing = cases.values()
    for raw in cases.values():
        chapter, evidence, points, inputs = probe.prepare(raw)
        assert len(points) == 1 and len(inputs) == 1
        assert evidence.sources == probe.prepare(good)[1].sources
        assert raw["chapter"] == good["chapter"] and raw["inputs"] == good["inputs"]
    changed = deepcopy(bad)
    changed["points"][0]["explanation"] = good["points"][0]["explanation"]
    changed["expectations"] = good["expectations"]
    assert changed == good
    changed = deepcopy(missing)
    changed["points"][0]["practice"]["source_ids"] = ["source_a", "source_b"]
    changed["expectations"] = good["expectations"]
    assert changed == good


def test_scope_negative_can_fail_entailment_too_but_cannot_reject_correct_siblings():
    expected = probe.build_cases()["control_02"]["expectations"]
    review = verdict(failed={"explanation"}, scope_fail={"explanation"}, source_fail={"explanation"})
    assert probe.score(review, expected)["passed"]
    assert not probe.score(verdict(failed=probe.COMPONENTS, scope_fail=probe.COMPONENTS), expected)["passed"]
    assert not probe.score(verdict(failed={"explanation"}, source_fail={"explanation"}), expected)["passed"]
    assert not probe.score(verdict(), expected)["passed"]


def test_correct_but_missing_citation_requires_source_rejection_without_false_scope_error():
    cases = probe.build_cases()
    expected = cases["control_03"]["expectations"]
    assert probe.score(verdict(failed={"practice"}, source_fail={"practice"}), expected)["passed"]
    assert not probe.score(verdict(failed={"practice"}, source_fail={"practice"}, scope_fail={"practice"}), expected)["passed"]
    assert probe.score(verdict(), cases["control_01"]["expectations"])["passed"]


def test_registration_refuses_overwrite_and_hashes_inputs_before_any_model_call(tmp_path):
    directory = tmp_path / "registry"
    probe.register(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    assert len(manifest["cases"]) == 3
    assert manifest["planner_expectations"]["irrelevant_history_source"] == "source_c"
    with pytest.raises(FileExistsError):
        probe.register(directory)


def test_model_requests_never_contain_case_identity_expected_labels_or_prior_verdicts():
    raw = probe.build_cases()["control_01"]
    raw["expectations"]["secret"] = "SECRET_EXPECTATION"
    raw["inputs"][0]["prior_review"] = "SECRET_PRIOR_VERDICT"
    raw["regression_metadata"] = {"case": "SECRET_CASE_ID"}
    chapter, evidence, points, inputs = probe.prepare(raw)

    class Client:
        payloads = []

        def structured(self, *, system, user, **kwargs):
            self.payloads.append(user)
            data = json.loads(user)
            if system == SCOPE_PLANNING_PROMPT:
                return {"points": [{"point_id": inputs[0]["point_id"], "conditions": [],
                    "comparisons": [{"source_id": key, "changes_scope": False,
                        "reason": "该候选未改变这个测试主题在当前范围中的成立条件。"}
                        for key in data["knowledge_points"][0]["scope_context_source_ids"]]}]}
            assert system == FACT_REVIEW_PROMPT
            point = data["candidate"]["points"][0]
            return {"accepted": True, "issues": [], "points": [{
                "point_id": point["point_id"], "accepted": True, "source_entailment": True,
                "scope_exceptions": True, "failed_components": [], "issues": [],
                "support_summary": "source_a与source_b共同支持本装置的适用条件与重新校准步骤。",
                "components": {name: selected_proof(point["source_ids"] if name == "explanation"
                    else point[name]["source_ids"], data) for name in probe.COMPONENTS}}]}

    client = Client()
    generator = TeachingContentGenerator(client)
    plans = generator.scope_planner.plan(inputs, evidence)
    inputs[0]["scope_conditions"] = plans[inputs[0]["point_id"]]
    generator._fact_review(points, None, inputs, chapter, evidence, [])
    assert len(client.payloads) == 2
    for payload in client.payloads:
        assert not any(value in payload for value in ["SECRET_", "control_01", "expectations", "regression_metadata", "prior_review"])


def test_registered_actual_pair_preserves_bad_draft_and_keeps_annotations_out_of_inputs():
    import hashlib
    directory = Path(__file__).resolve().parent / "fixtures" / "teaching-real-controls-v13-registered"
    manifest = json.loads((directory / "manifest.json").read_text())
    raws = {}
    for name, entry in manifest["cases"].items():
        path = directory / f"{name}.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        raws[name] = json.loads(path.read_text())
        _, evidence, points, inputs = probe.prepare(raws[name])
        assert "expectations" not in json.dumps(inputs, ensure_ascii=False)
        assert all(source_id in evidence.sources for point in points for source_id in point.source_ids)
    bad, good = raws["actual_bad"], raws["actual_good"]
    assert bad["chapter"] == good["chapter"] and bad["evidence"] == good["evidence"]
    assert bad["inputs"] == good["inputs"]
    canonical = json.dumps(bad["points"][0], ensure_ascii=False, sort_keys=True)
    assert hashlib.sha256(canonical.encode()).hexdigest() == manifest["provenance"]["bad_candidate_sha256"]
    assert probe.score(verdict(failed=probe.COMPONENTS, scope_fail=probe.COMPONENTS), bad["expectations"])["passed"]
    assert not probe.score(verdict(), bad["expectations"])["passed"]
    assert probe.score(verdict(), good["expectations"])["passed"]
    point = good["points"][0]
    assert all("终止" in text for text in [point["explanation"], point["flashcard"]["back"], point["practice"]["expected_answer"]])
