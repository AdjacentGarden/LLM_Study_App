"""Compact writer-only factual feedback without removing negative evidence.

The complete immutable sources remain in the generation payload. This module
only removes approved proof directories; it never changes a review verdict or
serves as an audit, cache, or reviewer representation.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

_FLAGS = (
    "is_class_definition",
    "assigns_class_property",
    "has_same_class_counterexample",
    "candidate_explicitly_limited",
)


def _failed(proof: dict[str, Any]) -> bool:
    # Only an explicit clean approval can disappear. Preserve incomplete or
    # unfamiliar component objects rather than interpreting them as approved.
    return not (
        proof.get("source_entailment") is True
        and proof.get("scope_exceptions") is True
        and proof.get("findings") == []
    )


def _unbounded(check: object) -> bool:
    return isinstance(check, dict) and (
        check.get("status") == "unbounded"
        or (all(check.get(flag) is True for flag in _FLAGS[:3]) and check.get(_FLAGS[3]) is False)
    )


def _pages(ids: object, sources: dict[str, dict]) -> list[dict[str, Any]]:
    if not isinstance(ids, list):
        return []
    result = []
    for source_id in ids:
        item = {"source_id": deepcopy(source_id)}
        source = sources.get(source_id) if isinstance(source_id, str) else None
        if isinstance(source, dict) and "page_number" in source:
            item["page_number"] = deepcopy(source["page_number"])
        else:
            item["page_unresolved"] = True
        result.append(item)
    return result


def _findings(value: object, sources: dict[str, dict]) -> object:
    result = deepcopy(value)
    if isinstance(result, list):
        for finding in result:
            if isinstance(finding, dict):
                finding["evidence_pages"] = _pages(finding.get("evidence_source_ids"), sources)
    return result


def _parent_context(boundary: dict, sources: dict[str, dict]) -> dict[str, Any]:
    ids = deepcopy(boundary.get("source_ids", []))
    return {
        "boundary_id": deepcopy(boundary.get("boundary_id")),
        "boundary_type": deepcopy(boundary.get("boundary_type")),
        "condition": deepcopy(boundary.get("condition")),
        "evidence_source_ids": ids,
        "evidence_pages": _pages(ids, sources),
    }


def _pair_id(parent_id: str, assertion_id: str) -> str:
    raw = json.dumps(
        [parent_id, assertion_id], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "boundary_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _compact_proof(
    proof: dict,
    *,
    lesson: bool,
    boundaries: list[dict],
    sources: dict[str, dict],
) -> dict:
    result = deepcopy(proof)
    result.pop("support_quotes", None)
    result.pop("scope_matrix", None)
    if "findings" in proof:
        result["findings"] = _findings(proof["findings"], sources)

    assertions = proof.get("assertions", [])
    assertion_checks = proof.get("assertion_checks", [])
    if not isinstance(assertions, list) or not isinstance(assertion_checks, list):
        # Unknown directory shapes stay intact. Normal production feedback is
        # canonical, but compaction must never hide an unrecognized rejection.
        result["feedback_directory_unresolved"] = True
        return result
    negatives = [
        row
        for row in assertion_checks
        if not isinstance(row, dict)
        or row.get("source_entailment") is not True
        or bool(row.get("findings"))
    ]
    negative_ids = {
        row["assertion_id"]
        for row in negatives
        if isinstance(row, dict) and isinstance(row.get("assertion_id"), str)
    }
    lookup: dict[str, list[tuple[dict, str | None]]] = {}
    for boundary in boundaries:
        if not isinstance(boundary, dict) or not isinstance(boundary.get("boundary_id"), str):
            continue
        context = _parent_context(boundary, sources)
        if lesson:
            for assertion in assertions:
                if isinstance(assertion, dict) and isinstance(assertion.get("assertion_id"), str):
                    assertion_id = assertion["assertion_id"]
                    key = _pair_id(boundary["boundary_id"], assertion_id)
                    entry = (context, assertion_id)
                    if entry not in lookup.setdefault(key, []):
                        lookup[key].append(entry)
        else:
            entry = (context, None)
            if entry not in lookup.setdefault(boundary["boundary_id"], []):
                lookup[boundary["boundary_id"]].append(entry)

    scope_checks = proof.get("scope_checks", [])
    if not isinstance(scope_checks, list):
        result["feedback_directory_unresolved"] = True
        return result
    kept_checks = []
    unresolved_assertions = False
    for check in scope_checks:
        if not _unbounded(check):
            continue
        kept = deepcopy(check)
        matches = lookup.get(check.get("boundary_id"), [])
        if len(matches) == 1:
            context, assertion_id = matches[0]
            kept["boundary_context"] = deepcopy(context)
            if assertion_id is not None:
                negative_ids.add(assertion_id)
        else:
            kept["boundary_context"] = {
                "boundary_id": deepcopy(check.get("boundary_id")),
                "unresolved": True,
                "reason": "negative boundary has no unique immutable parent association",
            }
            if matches:
                kept["boundary_context"]["possible_parents"] = [
                    deepcopy(context) for context, _ in matches
                ]
        # Also support literal claim locations from older canonical proofs.
        located = []
        quote = check.get("claim_quote")
        if isinstance(quote, str) and quote:
            for assertion in assertions:
                if not isinstance(assertion, dict):
                    continue
                text = assertion.get("text")
                if (
                    assertion.get("field") == check.get("claim_field")
                    and isinstance(text, str)
                    and text
                    and (quote in text or text in quote)
                ):
                    located.append(assertion.get("assertion_id"))
        negative_ids.update(key for key in located if isinstance(key, str))
        if lesson and not located and not (len(matches) == 1 and matches[0][1] is not None):
            unresolved_assertions = True
        kept_checks.append(kept)
    result["scope_checks"] = kept_checks
    if unresolved_assertions:
        # No size target justifies deleting a potentially relevant raw field.
        result["assertion_location_unresolved"] = True
        selected_assertions = assertions
        selected_checks = assertion_checks
    else:
        selected_assertions = [
            row
            for row in assertions
            if not isinstance(row, dict) or row.get("assertion_id") in negative_ids
        ]
        selected_checks = [
            row
            for row in assertion_checks
            if not isinstance(row, dict) or row.get("assertion_id") in negative_ids
        ]
    if selected_assertions or assertions:
        result["assertions"] = deepcopy(selected_assertions)
    else:
        result.pop("assertions", None)
    if selected_checks or assertion_checks:
        result["assertion_checks"] = deepcopy(selected_checks)
        for row in result["assertion_checks"]:
            if isinstance(row, dict) and "findings" in row:
                row["findings"] = _findings(row["findings"], sources)
    else:
        result.pop("assertion_checks", None)
    return result


def compact_writer_feedback(
    feedback: list[object], *, boundaries: list[dict], sources: dict[str, dict]
) -> list[object]:
    """Return a private writer wire, preserving every negative sibling and veto.

    Parent verdicts/issues and unknown/global/teacher rows are deep-copied
    unchanged. There is no truncation or token budget; unresolved negative
    boundary associations remain explicit instead of silently disappearing.
    """
    result = deepcopy(feedback)
    for row in result:
        if not isinstance(row, dict):
            continue
        for key in ("factual_point", "factual_lesson"):
            parent = row.get(key)
            if not isinstance(parent, dict) or not isinstance(parent.get("components"), dict):
                continue
            parent["components"] = {
                name: (
                    _compact_proof(
                        proof,
                        lesson=key == "factual_lesson",
                        boundaries=boundaries,
                        sources=sources,
                    )
                    if isinstance(proof, dict)
                    else deepcopy(proof)
                )
                for name, proof in parent["components"].items()
                if not isinstance(proof, dict) or _failed(proof)
            }
    return result
