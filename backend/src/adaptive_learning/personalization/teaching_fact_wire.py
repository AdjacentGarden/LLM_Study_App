"""Lossless factual-request evidence references; raw sources remain readable."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

FORMAT = "teaching-fact-source-refs-v1"
QUOTE_REF = "$source_quote_ref"


def compact_fact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if {"wire_format", "wire_metadata"} & payload.keys():
        raise ValueError("payload collides with fact wire envelope")
    sources = payload["sources"]
    if not all(isinstance(value.get("quote"), str) for value in sources.values()):
        raise ValueError("full immutable source text must remain readable")

    def reference(source_id: str, text: str) -> dict[str, str]:
        if source_id not in sources or text != sources[source_id]["quote"]:
            raise ValueError("source context differs from its immutable parent")
        return {QUOTE_REF: source_id}

    def walk(value: Any, path: tuple[str, ...] = ()) -> Any:
        if isinstance(value, list):
            return [walk(child, (*path, str(index))) for index, child in enumerate(value)]
        if not isinstance(value, dict):
            return value
        if QUOTE_REF in value:
            raise ValueError("input collides with source reference marker")
        result = {}
        for key, child in value.items():
            if key == "scope_boundaries":
                # Deliberate local repetition: every candidate boundary keeps
                # its adjacent immutable raw evidence for counterexample review.
                result[key] = deepcopy(child)
            elif (
                key == "quote"
                and "source_id" in value
                and any(
                    name in path
                    for name in ("actual_source_text", "scope_contexts", "candidate_context_sources")
                )
            ):
                result[key] = reference(value["source_id"], child)
            elif key == "scope_context_sources":
                result[key] = {
                    source_id: reference(source_id, text) for source_id, text in child.items()
                }
            else:
                result[key] = walk(child, (*path, key))
        return result

    result = walk(payload)
    conditions = {
        row["point_id"]: row["scope_conditions"]
        for row in payload.get("knowledge_points", [])
        if "scope_conditions" in row
    }
    removed = "scope_conditions" in payload and payload["scope_conditions"] == conditions
    if removed:
        result.pop("scope_conditions")
    result.update(
        wire_format=FORMAT,
        wire_metadata={
            "scope_conditions_from_knowledge_points": removed,
            "top_key_order": list(payload),
        },
    )
    # This check is intentionally before inference, not a claim of equivalent
    # model behavior. A reference bug must never silently remove evidence.
    restored = restore_fact_payload(result)
    if restored != payload or json.dumps(restored, ensure_ascii=False) != json.dumps(
        payload, ensure_ascii=False
    ):
        raise ValueError("fact request compaction is not lossless")
    return result


def restore_fact_payload(wire: dict[str, Any]) -> dict[str, Any]:
    if wire.get("wire_format") != FORMAT or set(wire.get("wire_metadata", {})) != {
        "scope_conditions_from_knowledge_points",
        "top_key_order",
    }:
        raise ValueError("unknown/incomplete fact wire contract")
    if type(wire["wire_metadata"]["scope_conditions_from_knowledge_points"]) is not bool:
        raise ValueError("scope condition reconstruction flag must be boolean")
    sources = wire["sources"]

    def walk(value: Any) -> Any:
        if isinstance(value, list):
            return [walk(child) for child in value]
        if not isinstance(value, dict):
            return value
        if QUOTE_REF in value:
            if set(value) != {QUOTE_REF} or value[QUOTE_REF] not in sources:
                raise ValueError("invalid/unknown source quote reference")
            text = sources[value[QUOTE_REF]]["quote"]
            if not isinstance(text, str):
                raise ValueError("source quote reference is not a raw string")
            return text
        return {key: walk(child) for key, child in value.items()}

    result = walk(
        {key: value for key, value in wire.items() if key not in {"wire_format", "wire_metadata"}}
    )
    if wire["wire_metadata"]["scope_conditions_from_knowledge_points"]:
        result["scope_conditions"] = {
            row["point_id"]: deepcopy(row["scope_conditions"])
            for row in result["knowledge_points"]
            if "scope_conditions" in row
        }
    order = wire["wire_metadata"]["top_key_order"]
    if (
        not isinstance(order, list)
        or not all(isinstance(key, str) for key in order)
        or len(set(order)) != len(order)
        or set(order) != set(result)
    ):
        raise ValueError("fact wire omitted or duplicated an original top-level field")
    return {key: result[key] for key in order}
