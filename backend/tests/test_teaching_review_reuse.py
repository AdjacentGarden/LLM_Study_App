from copy import deepcopy

import pytest

from adaptive_learning.personalization.teaching_review_reuse import VerifiedComponentReuse


def setup():
    sources = {
        "s1": "密闭容器中温度不变时，可直接沿用原判断。",
        "s2": "温度改变后，应重新校准再测量。",
    }
    context = {
        "chapter_id": "chapter",
        "evidence_fingerprint": "immutable-evidence",
        "review_identity": {"provider": "independent", "model": "reviewer"},
        "prompt_sha256": "review-prompt",
        "version": "quality-revision",
    }
    component = {
        "owner_id": "point",
        "component_id": "explanation",
        "fields": {"explanation": "可直接沿用原判断，但温度改变后须重新校准。"},
        "source_ids": ["s1", "s2"],
        "boundaries": [
            {"boundary_id": "b1", "condition": "温度改变后须重新校准", "source_ids": ["s2"]}
        ],
    }
    proof = {
        "source_entailment": True,
        "scope_exceptions": True,
        "source_ids": ["s1", "s2"],
        "rationale": "两段原文共同支持沿用条件和温度改变后的处理。",
        "support_quotes": [{"source_id": key, "quote": value} for key, value in sources.items()],
        "findings": [],
        "scope_checks": [
            {
                "boundary_id": "b1",
                "is_class_definition": True,
                "assigns_class_property": True,
                "has_same_class_counterexample": True,
                "candidate_explicitly_limited": True,
                "status": "bounded",
                "claim_field": "explanation",
                "claim_quote": "可直接沿用原判断",
                "limitation_field": "explanation",
                "limitation_quote": "温度改变后须重新校准",
                "rationale": "此组件自己明确限定温度改变后的处理。",
            }
        ],
    }
    return sources, context, component, proof


def test_only_exact_unchanged_verified_component_is_reused_without_mutating_saved_proof():
    sources, context, component, proof = setup()
    cache = VerifiedComponentReuse(source_texts=sources, context=context)
    assert cache.remember(component, proof)
    first = cache.get(component)
    proof["scope_checks"][0]["claim_quote"] = "changed outside"
    first[1]["support_quotes"].clear()
    assert cache.get(component)[1]["support_quotes"]
    assert cache.get(component)[1]["scope_checks"][0]["claim_quote"] == "可直接沿用原判断"


@pytest.mark.parametrize("change", ["answer", "citation", "boundary", "identity", "point"])
def test_answer_citation_or_boundary_changes_cannot_inherit_previous_approval(change):
    sources, context, component, proof = setup()
    cache = VerifiedComponentReuse(source_texts=sources, context=context)
    cache.remember(component, proof)
    changed = deepcopy(component)
    if change == "answer":
        changed["fields"]["explanation"] += "所有情况均无需校准。"
    elif change == "citation":
        changed["source_ids"] = ["s1"]
    elif change == "boundary":
        changed["boundaries"][0]["condition"] += "每次"
    elif change == "identity":
        changed["component_id"] = "practice"
    else:
        changed["owner_id"] = "another_point"
    assert cache.get(changed) is None


@pytest.mark.parametrize(
    "change", ["model", "provider", "prompt", "version", "new_exception", "fingerprint"]
)
def test_cache_identity_changes_with_full_sources_model_prompt_or_revision(change):
    sources, context, component, proof = setup()
    a = VerifiedComponentReuse(source_texts=sources, context=context)
    a.remember(component, proof)
    changed = deepcopy(context)
    if change == "model":
        changed["review_identity"]["model"] = "new-reviewer"
    elif change == "provider":
        changed["review_identity"]["provider"] = "another-provider"
    elif change == "prompt":
        changed["prompt_sha256"] = "different-instructions"
    elif change == "version":
        changed["version"] = "new-quality-rules"
    elif change == "fingerprint":
        changed["evidence_fingerprint"] = "changed"
    else:
        sources["new_source"] = "但开放容器不能沿用此结论。"
    b = VerifiedComponentReuse(source_texts=sources, context=changed)
    assert b.key(component) != a.key(component)
    assert b.get(component) is None


@pytest.mark.parametrize(
    "fault",
    [
        "rejected",
        "string_boolean",
        "foreign_quote",
        "invented_quote",
        "missing_boundary",
        "other_component_claim",
        "unbounded",
    ],
)
def test_unverified_or_unbound_proof_cannot_seed_cache(fault):
    sources, context, component, proof = setup()
    cache = VerifiedComponentReuse(source_texts=sources, context=context)
    if fault == "rejected":
        proof["source_entailment"] = False
    elif fault == "string_boolean":
        proof["scope_exceptions"] = "true"
    elif fault == "foreign_quote":
        proof["support_quotes"][0]["source_id"] = "foreign"
    elif fault == "invented_quote":
        proof["support_quotes"][0]["quote"] += "任何温度都可沿用"
    elif fault == "missing_boundary":
        proof["scope_checks"] = []
    elif fault == "other_component_claim":
        proof["scope_checks"][0]["claim_quote"] = "其他答案里的文字"
    else:
        proof["scope_checks"][0].update(candidate_explicitly_limited=False, status="unbounded")
    if fault in {"rejected", "string_boolean"}:
        assert not cache.remember(component, proof)
    else:
        with pytest.raises(ValueError):
            cache.remember(component, proof)
    assert cache.get(component) is None


def test_cached_proof_reenters_current_span_directory_instead_of_fabricating_span_ids():
    sources, context, component, proof = setup()
    cache = VerifiedComponentReuse(source_texts=sources, context=context)
    cache.remember(component, proof)
    spans = {
        "immutable_" + key: {"source_id": key, "quote": quote} for key, quote in sources.items()
    }
    selection = cache.selection(cache.get(component)[1], spans)
    assert set(selection["support_span_ids"]) == {"immutable_s1", "immutable_s2"}
    del spans["immutable_s2"]
    with pytest.raises(ValueError, match="current immutable span"):
        cache.selection(proof, spans)
