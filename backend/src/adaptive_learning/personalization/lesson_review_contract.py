"""Offline lesson assertion/scope contracts; no app, model, or network imports.

These validators establish complete mechanical coverage, literal provenance,
and deterministic scope rules. They cannot establish that an LLM correctly
classified a claim as nonfactual or correctly judged any semantic entailment.
Do not put fixture expectations in build_assertion_contract's model payload.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from copy import deepcopy
from typing import Any


class LessonContractError(ValueError):
    pass


FLAGS_ORDER = (
    "is_class_definition",
    "assigns_class_property",
    "has_same_class_counterexample",
    "candidate_explicitly_limited",
)
GENERAL_CLAIM_RULE = (
    "第一项is_class_definition包括类定义、通用性质、作用、数量和过程规则概括；"
    "不能因只是过程或性质而标false。同类反例代入仍须核验。"
    "is_factual=false只适用于不增加学科事实的教学安排、纯问题、学习建议，"
    "以及同组件教师答复已明确反驳或纠正的被引述错误命题；这需要语义核验，不能按字段名自动豁免。"
    "题目中的真实前提仍是事实；answer/expected_answer/back/step_N/rubric_N中的教师结论仍须原文核验。"
    "若答复认可错误、未明确纠正或真假不明，不得将该命题视为已纠正；必须保留事实或范围否决。"
    "同一断言的非事实角色与真实unbounded范围否决不能并存；须重新核验角色或矩阵，不能删除真实负判来修格式。"
    "同一片段混有安排与事实时，只要包含实际学科事实就必须is_factual=true，不能按主旨豁免。"
)
KINDS = {
    "unsupported_claim",
    "missing_condition",
    "scope_overreach",
    "contradiction",
    "invalid_inference",
}


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise LessonContractError(message)


def _strict_bool(value: Any, name: str) -> bool:
    _check(type(value) is bool, name + " must be a strict boolean")
    return value


def component_role_context(fields: dict[str, str]) -> dict[str, Any]:
    """Describe where to compare a question and its answer, never its truth.

    A misconception quoted for correction is not automatically an endorsed
    textbook claim. Only the semantic reviewer can decide whether the same
    component actually corrects it; the complete fields and assertions remain.
    """
    question_fields = [key for key in fields if key in {"problem", "question", "prompt", "front"}]
    response_fields = [
        key for key in fields
        if key in {"answer", "expected_answer", "back"} or re.fullmatch(r"(?:step|rubric)_\d+", key)
    ]
    return {
        "question_fields": question_fields,
        "teacher_response_fields": response_fields,
        "semantic_classification_verified": False,
        "question_fields_automatically_nonfactual": False,
    }


def _source_quote(sources: dict[str, dict[str, Any]], source_id: str) -> str:
    _check(source_id in sources, "unknown source ID: " + str(source_id))
    text = sources[source_id].get("quote")
    _check(isinstance(text, str), "source quote must be immutable text")
    return text


def split_assertions(
    component_id: str, fields: dict[str, str], *, max_chars: int = 240
) -> list[dict[str, Any]]:
    """Cover every field exactly with stable, contiguous, unnormalized spans.

    Offsets are Python Unicode code points, never UTF-8 byte offsets. Prefer
    sentence/newline ends but keep punctuation, whitespace, and broken OCR.
    A span is an audit unit; it may contain more than one proposition.
    """
    _check(isinstance(component_id, str) and bool(component_id), "missing component ID")
    _check(type(max_chars) is int and 1 <= max_chars <= 240, "max_chars must be 1..240")
    assertions = []
    for field, text in fields.items():
        _check(isinstance(field, str) and isinstance(text, str), "all fields must be text")
        start = 0
        while start < len(text):
            stop = min(start + max_chars, len(text))
            endings = [
                match.end() + start for match in re.finditer(r"[。！？!?；;\n]", text[start:stop])
            ]
            if endings:
                stop = endings[0]
            raw = text[start:stop]
            identity = [component_id, field, start, stop, raw]
            assertions.append(
                {
                    "assertion_id": "assertion_"
                    + hashlib.sha256(_json(identity).encode()).hexdigest()[:24],
                    "component_id": component_id,
                    "field": field,
                    "start": start,
                    "end": stop,
                    "text": raw,
                }
            )
            start = stop
    return assertions


def _assertion_index(assertions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    components = set()
    for row in assertions:
        identity = row.get("assertion_id")
        _check(
            isinstance(identity, str) and identity not in result,
            "duplicate or invalid assertion ID",
        )
        _check(
            isinstance(row.get("text"), str) and 0 < len(row["text"]) <= 240,
            "assertion text must have 1..240 chars",
        )
        _check(
            type(row.get("start")) is int
            and type(row.get("end")) is int
            and row["end"] - row["start"] == len(row["text"]),
            "invalid assertion offsets",
        )
        _check(
            identity
            == "assertion_"
            + hashlib.sha256(
                _json(
                    [row["component_id"], row["field"], row["start"], row["end"], row["text"]]
                ).encode()
            ).hexdigest()[:24],
            "assertion ID/content mismatch",
        )
        result[identity] = row
        components.add(row["component_id"])
    _check(len(components) <= 1, "assertions from different components cannot be mixed")
    field_ends: dict[str, int] = {}
    for row in sorted(result.values(), key=lambda item: (item["field"], item["start"])):
        _check(
            row["start"] == field_ends.get(row["field"], 0), "assertion field has a gap or overlap"
        )
        field_ends[row["field"]] = row["end"]
    return result


def build_assertion_contract(
    component_id: str,
    fields: dict[str, str],
    own_source_ids: list[str],
    support_spans: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return only model-visible source/coverage contract; no expected labels."""
    _check(len(own_source_ids) == len(set(own_source_ids)), "duplicate own source ID")
    for source_id in own_source_ids:
        _source_quote(sources, source_id)
    allowed = []
    for span_id, span in support_spans.items():
        if span["source_id"] in own_source_ids:
            _check(
                isinstance(span.get("quote"), str)
                and bool(span["quote"])
                and span["quote"] in _source_quote(sources, span["source_id"]),
                "support span is not literal raw source",
            )
            allowed.append(span_id)
    return {
        "component_id": component_id,
        "assertions": split_assertions(component_id, fields),
        "field_lengths": {key: len(text) for key, text in fields.items()},
        "own_source_ids": list(own_source_ids),
        "allowed_support_span_ids": allowed,
        "flags_order": list(FLAGS_ORDER),
        "general_claim_rule": GENERAL_CLAIM_RULE,
        "component_roles": component_role_context(fields),
    }


def validate_assertion_review(
    contract: dict[str, Any],
    rows: list[dict[str, Any]],
    support_spans: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    *,
    scope_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Require every assertion's own proof and independently located findings.

    A source-supported claim can still overstate its scope. Only a result from
    the same complete scope matrix may corroborate that separate rejection;
    another assertion's counterexample cannot justify a positive-source finding.
    """
    index = _assertion_index(contract["assertions"])
    actual = [row.get("assertion_id") for row in rows]
    _check(
        all(isinstance(key, str) for key in actual)
        and len(actual) == len(index)
        and len(set(actual)) == len(actual)
        and set(actual) == set(index),
        "assertion review must exactly cover every ID once",
    )
    own = set(contract["own_source_ids"])
    allowed = set(contract["allowed_support_span_ids"])
    fields: dict[str, str] = {}
    for assertion in sorted(index.values(), key=lambda row: (row["field"], row["start"])):
        field = assertion["field"]
        _check(
            assertion["start"] == len(fields.get(field, "")), "assertion field has a gap or overlap"
        )
        fields[field] = fields.get(field, "") + assertion["text"]
    _check(
        set(fields) <= set(contract["field_lengths"])
        and all(
            len(fields.get(field, "")) == length
            for field, length in contract["field_lengths"].items()
        ),
        "assertions do not cover complete original fields",
    )
    unbounded_sources: dict[str, set[str]] = {}
    if scope_result is not None:
        _check(isinstance(scope_result, dict), "scope_result must be a materialized matrix")
        boundaries = scope_result.get("expanded_boundaries")
        scope_checks = scope_result.get("scope_checks")
        _check(
            isinstance(boundaries, list) and isinstance(scope_checks, list),
            "scope_result must contain expanded boundaries and checks",
        )
        boundary_index = {boundary["boundary_id"]: boundary for boundary in boundaries}
        _check(len(boundary_index) == len(boundaries), "duplicate materialized scope boundary")
        for check in scope_checks:
            boundary = boundary_index.get(check.get("boundary_id"))
            if boundary is None:
                continue
            assertion = index.get(boundary.get("assertion_id"))
            if assertion is None:
                continue
            pair_id = (
                "boundary_"
                + hashlib.sha256(
                    _json([boundary.get("parent_boundary_id"), assertion["assertion_id"]]).encode()
                ).hexdigest()[:24]
            )
            if (
                boundary["boundary_id"] == pair_id
                and check.get("claim_field") == assertion["field"]
                and check.get("claim_quote") == assertion["text"]
                and check.get("status") == "unbounded"
                and all(check.get(flag) is True for flag in FLAGS_ORDER[:3])
                and check.get(FLAGS_ORDER[3]) is False
            ):
                unbounded_sources.setdefault(assertion["assertion_id"], set()).update(
                    boundary["source_ids"]
                )
    results, all_findings, all_proofs = [], [], []
    for row in rows:
        _check(
            set(row)
            == {"assertion_id", "is_factual", "source_entailment", "support_span_ids", "findings"},
            "assertion decision has unknown or missing fields",
        )
        assertion = index[row["assertion_id"]]
        factual = _strict_bool(row["is_factual"], "is_factual")
        entailed = _strict_bool(row["source_entailment"], "source_entailment")
        proofs = row["support_span_ids"]
        _check(
            isinstance(proofs, list)
            and all(isinstance(key, str) for key in proofs)
            and len(proofs) == len(set(proofs)),
            "duplicate or invalid proof IDs",
        )
        for proof_id in proofs:
            _check(proof_id in allowed and proof_id in support_spans, "unknown or non-own proof ID")
            proof = support_spans[proof_id]
            _check(proof["source_id"] in own, "proof belongs to another component")
            _check(
                isinstance(proof.get("quote"), str)
                and bool(proof["quote"])
                and proof["quote"] in _source_quote(sources, proof["source_id"]),
                "proof text is not immutable raw",
            )
        findings = row["findings"]
        _check(isinstance(findings, list), "findings must be a list")
        if not factual:
            _check(
                not unbounded_sources.get(row["assertion_id"]),
                "nonfactual assertion conflicts with its own unbounded scope matrix; "
                + f"assertion_id={row['assertion_id']}, field={assertion['field']}; "
                "recheck this exact assertion's semantic role and matrix together. "
                "If it still asserts an uncorrected factual claim, set is_factual=true "
                "and retain the real scope veto; never erase findings or normalize flags to approve it.",
            )
            _check(
                entailed and not findings,
                "nonfactual teaching arrangement must be source=true with no findings",
            )
        elif entailed:
            _check(
                bool(proofs),
                "positive factual assertion needs own proof",
            )
        else:
            _check(bool(findings), "negative factual assertion needs exact located findings")
        canonical = []
        for finding in findings:
            _check(
                set(finding) <= {"kind", "quote", "issue", "evidence_source_ids", "missing_text"}
                and {"kind", "quote", "issue", "evidence_source_ids"} <= set(finding),
                "invalid finding envelope",
            )
            _check(finding["kind"] in KINDS, "unknown finding kind")
            quote = finding["quote"]
            _check(
                isinstance(quote, str) and bool(quote.strip()) and quote in assertion["text"],
                "finding quote must be exact within its own assertion",
            )
            issue = finding["issue"]
            _check(
                isinstance(issue, str) and 8 <= len(issue) <= 700,
                "finding issue must have 8..700 chars",
            )
            source_ids = finding["evidence_source_ids"]
            _check(
                isinstance(source_ids, list)
                and 1 <= len(source_ids) <= 8
                and len(source_ids) == len(set(source_ids)),
                "invalid finding evidence IDs",
            )
            for source_id in source_ids:
                _source_quote(sources, source_id)
            if entailed:
                location = (
                    f"assertion_id={row['assertion_id']}, field={assertion['field']}, "
                    f"finding_kind={finding['kind']}, evidence_source_ids={source_ids}"
                )
                _check(
                    finding["kind"] in {"scope_overreach", "missing_condition"},
                    "source-positive finding needs its own unbounded scope matrix evidence; "
                    + location + "; source_entailment=true conflicts with this factual/inference veto. "
                    "Recheck this exact assertion; retain a real negative finding with source_entailment=false, never erase it to fix format.",
                )
                _check(
                    bool(set(source_ids) & unbounded_sources.get(row["assertion_id"], set())),
                    "source-positive finding needs its own unbounded scope matrix evidence; "
                    + location + "; recheck this same assertion's scope_matrix against the actual cited boundary. "
                    "Do not borrow another assertion's cell, invent unbounded flags or erase a real veto.",
                )
            missing = finding.get("missing_text")
            if finding["kind"] == "missing_condition":
                _check(
                    isinstance(missing, str)
                    and 1 <= len(missing) <= 120
                    and any(
                        missing in _source_quote(sources, source_id) for source_id in source_ids
                    )
                    and missing not in "\n".join(fields.values()),
                    "missing_text is not actually absent raw evidence",
                )
            else:
                _check(missing is None, "only missing_condition permits missing_text")
            canonical.append(
                {
                    "kind": finding["kind"],
                    "candidate_field": assertion["field"],
                    "candidate_quote": quote,
                    "issue": issue,
                    "evidence_source_ids": list(source_ids),
                    **({"missing_text": missing} if missing is not None else {}),
                }
            )
        results.append(
            {
                "assertion_id": row["assertion_id"],
                "is_factual": factual,
                "source_entailment": entailed,
                "support_span_ids": list(proofs),
                "findings": canonical,
            }
        )
        all_findings.extend(canonical)
        all_proofs.extend(proofs)
    all_proofs = list(dict.fromkeys(all_proofs))
    return {
        "assertions": results,
        "source_entailment": all(row["source_entailment"] for row in results),
        "support_span_ids": all_proofs,
        "source_ids": list(dict.fromkeys(support_spans[key]["source_id"] for key in all_proofs)),
        "findings": all_findings,
        "semantic_classification_unverified": True,
    }


def _themes(text: str) -> tuple[set[str], set[str]]:
    latin = {
        value.casefold()
        for value in re.findall(r"[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*", text)
        if len(value) >= 2
    }
    grams = {
        run[start : start + size]
        for run in re.findall(r"[\u3400-\u9fff]+", text)
        for size in (3, 4)
        for start in range(max(0, len(run) - size + 1))
    }
    return grams, latin


def associate_lesson_boundaries(
    component_id: str,
    fields: dict[str, str],
    own_source_ids: list[str],
    points: list[dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    *,
    max_boundaries: int = 128,
) -> dict[str, Any]:
    """Retain ALL chapter boundaries; heuristics only rank navigation.

    points use real point_id,label,point_source_ids,scope_boundaries. Opening
    and summary see the whole chapter. Other components rank own/anchor ID
    relationships and corpus-rare CJK/Latin themes first, but never discard a
    mixed-theme boundary. Associations are not semantic applicability claims.
    """
    _check(
        type(max_boundaries) is int and 1 <= max_boundaries <= 128, "boundary cap must be 1..128"
    )
    ids = [point["point_id"] for point in points]
    _check(len(set(ids)) == len(ids), "duplicate real point IDs")
    for source_id in own_source_ids:
        _source_quote(sources, source_id)
    profiles = {point["point_id"]: _themes(point.get("label", "")) for point in points}
    gram_df = Counter(gram for grams, _ in profiles.values() for gram in grams)
    latin_df = Counter(token for _, tokens in profiles.values() for token in tokens)
    rare_limit = max(1, math.ceil(len(points) / 4))
    own_grams, own_latin = _themes("\n".join(fields.values()))
    reasons: dict[str, list[str]] = {}
    priorities: dict[str, bool] = {}
    chapter_mode = component_id in {"opening", "summary"}
    for point in points:
        point_id = point["point_id"]
        anchors = point.get("point_source_ids", [])
        for source_id in anchors:
            _source_quote(sources, source_id)
        selected = []
        if chapter_mode:
            selected.append("opening/summary conservatively includes all chapter point boundaries")
        overlap = sorted(set(own_source_ids) & set(anchors))
        if overlap:
            selected.append("own source IDs intersect real point anchor IDs: " + ",".join(overlap))
        grams, latin = profiles[point_id]
        rare_grams = sorted(gram for gram in grams & own_grams if gram_df[gram] <= rare_limit)
        rare_latin = sorted(token for token in latin & own_latin if latin_df[token] <= rare_limit)
        if any(len(gram) >= 4 for gram in rare_grams) or len(rare_grams) >= 2:
            selected.append("shared corpus-rare CJK ngrams: " + ",".join(rare_grams))
        if rare_latin:
            selected.append("shared corpus-rare Latin themes: " + ",".join(rare_latin))
        priorities[point_id] = bool(
            overlap
            or rare_latin
            or any(len(gram) >= 4 for gram in rare_grams)
            or len(rare_grams) >= 2
        )
        reasons[point_id] = selected or [
            "retained whole-chapter boundary despite no heuristic link; mixed themes must not be omitted"
        ]
    mode = (
        "whole_chapter"
        if chapter_mode
        else "whole_chapter_ranked_navigation"
        if any(priorities.values())
        else "whole_chapter_fallback"
    )
    # Stable ranking only. All real points, including unmatched ones, remain.
    ordered_points = sorted(
        enumerate(points),
        key=lambda pair: (False if chapter_mode else not priorities[pair[1]["point_id"]], pair[0]),
    )
    boundaries: dict[str, dict[str, Any]] = {}
    identities = {}
    for _, point in ordered_points:
        point_id = point["point_id"]
        for boundary in point.get("scope_boundaries", []):
            boundary_id = boundary["boundary_id"]
            source_ids = boundary["source_ids"]
            raw = [{"source_id": key, "quote": _source_quote(sources, key)} for key in source_ids]
            if "actual_source_text" in boundary:
                _check(
                    boundary["actual_source_text"] == raw,
                    "boundary raw differs from immutable full source",
                )
            identity = {
                "boundary_type": boundary["boundary_type"],
                "condition": boundary["condition"],
                "source_ids": source_ids,
                "actual_source_text": raw,
            }
            _check(
                boundary_id not in identities or identities[boundary_id] == identity,
                "same boundary ID has different raw/condition identity",
            )
            identities[boundary_id] = deepcopy(identity)
            row = boundaries.setdefault(
                boundary_id,
                {
                    "boundary_id": boundary_id,
                    **deepcopy(identity),
                    "point_ids": [],
                    "point_anchor_source_ids": {},
                    "association_reasons": [],
                },
            )
            if point_id not in row["point_ids"]:
                row["point_ids"].append(point_id)
                row["point_anchor_source_ids"][point_id] = list(point.get("point_source_ids", []))
                row["association_reasons"].append(
                    {"point_id": point_id, "reasons": reasons[point_id]}
                )
    _check(
        len(boundaries) <= max_boundaries,
        "too many lesson boundaries; refusing to truncate unreviewed boundaries",
    )
    return {
        "component_id": component_id,
        "association_mode": mode,
        "boundaries": list(boundaries.values()),
        "sources": deepcopy(sources),
        "point_associations": [
            {
                "point_id": point["point_id"],
                "navigation_priority": priorities[point["point_id"]],
                "reasons": reasons[point["point_id"]],
            }
            for _, point in ordered_points
        ],
        "chapter_has_no_scope_boundaries": not any(
            point.get("scope_boundaries") for point in points
        ),
        "association_is_semantic_approval": False,
    }


def materialize_scope_checks(
    assertions: list[dict[str, Any]], boundaries: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Legacy single-claim adapter; not complete lesson scope coverage.

    Publication integration must use materialize_scope_matrix, which cannot
    select one good sentence while skipping another claim in the component.
    """
    index = _assertion_index(assertions)
    expected = {row["boundary_id"]: row for row in boundaries}
    _check(
        len(expected) == len(boundaries) and len(boundaries) <= 128,
        "duplicate or too many boundaries",
    )
    actual = [row.get("boundary_id") for row in rows]
    _check(
        all(isinstance(key, str) for key in actual)
        and len(actual) == len(expected)
        and len(set(actual)) == len(actual)
        and set(actual) == set(expected),
        "scope checks must exactly cover every associated boundary once",
    )
    checks, findings = [], []
    for row in rows:
        _check(
            set(row)
            <= {
                "boundary_id",
                "flags",
                "claim_assertion_id",
                "limitation_assertion_id",
                "rationale",
            }
            and {"boundary_id", "flags", "claim_assertion_id", "limitation_assertion_id"}
            <= set(row),
            "invalid compact scope envelope",
        )
        flags = row["flags"]
        _check(
            isinstance(flags, list) and len(flags) == 4,
            "scope flags must contain exactly four booleans",
        )
        for name, flag in zip(FLAGS_ORDER, flags, strict=True):
            _strict_bool(flag, name)
        claim_id, limitation_id = row["claim_assertion_id"], row["limitation_assertion_id"]
        _check(
            claim_id in index and bool(index[claim_id]["text"].strip()),
            "claim assertion ID is unknown or empty",
        )
        claim = index[claim_id]
        unbounded = all(flags[:3]) and not flags[3]
        status = "unbounded" if unbounded else "bounded" if flags[3] else "not_applicable"
        if flags[3]:
            _check(
                limitation_id in index and bool(index[limitation_id]["text"].strip()),
                "bounded claim needs its own limitation assertion",
            )
        else:
            _check(
                limitation_id is None, "unbounded/not-applicable checks cannot claim a limitation"
            )
        rationale = row.get(
            "rationale",
            "程序按当前组件连续原句与四项独立布尔判断回填范围状态；其语义判定仍须独立审核。",
        )
        _check(
            isinstance(rationale, str) and bool(rationale.strip()) and len(rationale) <= 350,
            "scope rationale must be nonempty and at most 350 chars",
        )
        result = {
            "boundary_id": row["boundary_id"],
            **dict(zip(FLAGS_ORDER, flags, strict=True)),
            "claim_field": claim["field"],
            "claim_quote": claim["text"],
            "status": status,
            "rationale": rationale,
        }
        if flags[3]:
            limitation = index[limitation_id]
            result.update(limitation_field=limitation["field"], limitation_quote=limitation["text"])
        if unbounded:
            boundary = expected[row["boundary_id"]]
            findings.append(
                {
                    "kind": "scope_overreach",
                    "candidate_field": claim["field"],
                    "candidate_quote": claim["text"],
                    "issue": "当前组件作一般性质或过程概括，已有同类反例而未限缩。需处理原文边界："
                    + boundary["condition"],
                    "evidence_source_ids": list(boundary["source_ids"]),
                }
            )
        checks.append(result)
    return {
        "scope_checks": checks,
        "scope_exceptions": not findings,
        "findings": findings,
        "semantic_flags_unverified": True,
    }


def build_scope_matrix_contract(
    assertions: list[dict[str, Any]], boundaries: list[dict[str, Any]]
) -> dict[str, Any]:
    """Fix the complete assertion × parent-boundary matrix and its ID order.

    Wire rows: {assertion_id, checks:[[bool,bool,bool,bool,index|null], ...]}.
    Each check position refers to boundary_ids at exactly the same position;
    limitation indexes refer to assertions at exactly the original position.
    No expected verdicts or fixture labels enter this contract.
    """
    _assertion_index(assertions)
    boundary_ids = [row.get("boundary_id") for row in boundaries]
    _check(
        all(isinstance(key, str) and bool(key) for key in boundary_ids)
        and len(set(boundary_ids)) == len(boundary_ids),
        "duplicate or invalid parent boundary IDs",
    )
    _check(len(boundaries) <= 128, "more than 128 parent boundaries; refusing to truncate")
    cells = len(assertions) * len(boundaries)
    _check(cells <= 4096, "more than 4096 scope matrix cells; refusing to truncate")
    return {
        "assertions": deepcopy(assertions),
        "boundary_ids": list(boundary_ids),
        "boundaries": deepcopy(boundaries),
        "flags_order": list(FLAGS_ORDER),
        "matrix_cells": cells,
        "general_claim_rule": GENERAL_CLAIM_RULE,
    }


def materialize_scope_matrix(
    matrix_contract: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Expand every matrix cell to a unique canonical boundary/claim pair.

    Coverage is exact on both axes: a caller cannot select one bounded sentence
    and silently omit another sentence governed by the same parent boundary.
    The helper cannot establish that the four semantic flags are truthful.
    """
    assertions = matrix_contract["assertions"]
    boundaries = matrix_contract["boundaries"]
    expected_contract = build_scope_matrix_contract(assertions, boundaries)
    _check(
        matrix_contract["boundary_ids"] == expected_contract["boundary_ids"]
        and matrix_contract["matrix_cells"] == expected_contract["matrix_cells"],
        "scope matrix fixed boundary order/count changed",
    )
    index = _assertion_index(assertions)
    actual = [row.get("assertion_id") for row in rows]
    _check(
        all(isinstance(key, str) for key in actual)
        and len(actual) == len(index)
        and len(set(actual)) == len(actual)
        and set(actual) == set(index),
        "scope matrix rows must exactly cover all assertion IDs once",
    )
    by_id = {row["assertion_id"]: row for row in rows}
    expanded, canonical, findings = [], [], []
    for assertion in assertions:
        row = by_id[assertion["assertion_id"]]
        _check(
            set(row) == {"assertion_id", "checks"}, "scope matrix row has extra or missing fields"
        )
        checks = row["checks"]
        _check(
            isinstance(checks, list) and len(checks) == len(boundaries),
            "each assertion must check every fixed parent boundary; missing or extra cells",
        )
        for boundary, cell in zip(boundaries, checks, strict=True):
            _check(
                isinstance(cell, list) and len(cell) == 5,
                "scope matrix cell needs four flags and one limitation index",
            )
            flags, limitation_index = cell[:4], cell[4]
            for name, flag in zip(FLAGS_ORDER, flags, strict=True):
                _strict_bool(flag, name)
            if flags[3]:
                _check(
                    type(limitation_index) is int and 0 <= limitation_index < len(assertions),
                    "bounded scope needs an in-component integer limitation assertion index, not bool",
                )
            else:
                _check(
                    limitation_index is None,
                    "unbounded/not-applicable scope requires null limitation index",
                )
            pair_id = (
                "boundary_"
                + hashlib.sha256(
                    _json([boundary["boundary_id"], assertion["assertion_id"]]).encode()
                ).hexdigest()[:24]
            )
            expanded.append(
                {
                    **deepcopy(boundary),
                    "boundary_id": pair_id,
                    "parent_boundary_id": boundary["boundary_id"],
                    "assertion_id": assertion["assertion_id"],
                }
            )
            unbounded = all(flags[:3]) and not flags[3]
            status = "unbounded" if unbounded else "bounded" if flags[3] else "not_applicable"
            check = {
                "boundary_id": pair_id,
                **dict(zip(FLAGS_ORDER, flags, strict=True)),
                "claim_field": assertion["field"],
                "claim_quote": assertion["text"],
                "status": status,
                "rationale": "程序根据当前断言与固定父边界的四项布尔判断回填状态；布尔判断的语义真实性仍需独立验证。",
            }
            if flags[3]:
                limitation = assertions[limitation_index]
                check.update(
                    limitation_field=limitation["field"], limitation_quote=limitation["text"]
                )
            if unbounded:
                findings.append(
                    {
                        "kind": "scope_overreach",
                        "candidate_field": assertion["field"],
                        "candidate_quote": assertion["text"],
                        "issue": "当前断言作一般性质或过程概括，已有同类反例而未限缩。需处理原文边界："
                        + boundary["condition"],
                        "evidence_source_ids": list(boundary["source_ids"]),
                    }
                )
            canonical.append(check)
    return {
        "expanded_boundaries": expanded,
        "scope_checks": canonical,
        "scope_exceptions": not findings,
        "findings": findings,
        "parent_boundary_ids": list(matrix_contract["boundary_ids"]),
        "matrix_cells": len(canonical),
        "semantic_flags_unverified": True,
    }
