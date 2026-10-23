"""Explicit offline note-review fixtures; malformed legacy replies stay malformed.

Only process_note mock responses use this adapter. Direct legacy-contract tests
must keep calling _note_review_feedback with their original payloads.
"""
from __future__ import annotations

import copy
import json


def note_review_fixture(prompt, legacy, *, global_failure=None, issue_code="S"):
    raw = copy.deepcopy(legacy)
    if not prompt.startswith("审核笔记整理"):
        return raw
    if type(raw) is not dict or "candidate_id" in raw:
        return raw
    if set(raw) - {"passed", "reason", "findings", "failure_scope"}:
        return raw
    if type(raw.get("passed")) is not bool or not isinstance(raw.get("reason"), str):
        return raw
    if not raw["reason"].strip() or len(raw["reason"]) > 180:
        return raw
    findings = raw.get("findings", [])
    if type(findings) is not list or len(findings) > 12 or (raw["passed"] and findings):
        return raw
    scope = raw.get("failure_scope", "none" if raw["passed"] else "other_or_uncertain")
    if scope not in ("none", "optional_suggestions_only", "other_or_uncertain"):
        return raw
    if (scope == "none") != raw["passed"]:
        return raw
    packet = json.loads(prompt.split("\n待审核数据：", 1)[1])
    coverage = packet["claim_coverage"]
    negative = {}
    for finding in findings:
        if type(finding) is not dict or set(finding) != {"field", "suggestion_index", "claim", "issue"}:
            return raw
        field, index, claim = (finding[key] for key in ("field", "suggestion_index", "claim"))
        if (not isinstance(claim, str) or not claim.strip() or len(claim) > 1000
                or not isinstance(finding["issue"], str) or not finding["issue"].strip()
                or len(finding["issue"]) > 1000):
            return raw
        if field in ("summary", "polished"):
            if index is not None or claim not in packet[field]:
                return raw
            negative["g_" + field] = issue_code
            continue
        if field != "suggestion" or type(index) is not int or not 0 <= index < len(packet["suggestions"]):
            return raw
        text = packet["suggestions"][index]["suggestion"]
        if text.count(claim) != 1:
            return raw  # Never guess an invented or ambiguous finding target.
        start = text.index(claim)
        touched = [row["id"] for row in coverage["clauses"] if row["suggestion_index"] == index
                   and row["start"] < start + len(claim) and start < row["end"]]
        if not touched:
            return raw
        negative.update((key, issue_code) for key in touched)
    if not raw["passed"] and not negative:
        if global_failure not in ("fidelity", "summary", "polished"):
            return raw  # Sparse legacy rejection is not silently made valid.
        negative["g_" + global_failure] = issue_code
    return {
        "candidate_id": coverage["candidate_id"], "passed": raw["passed"],
        "reason": raw["reason"],
        "global_checks": {key: "g_" + key not in negative for key in coverage["global_ids"]},
        "clause_checks": [[row["id"], row["id"] not in negative] for row in coverage["clauses"]],
        "findings": [[key, code] for key, code in negative.items()],
        "failure_scope": scope,
    }
