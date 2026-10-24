"""Invocation-local reuse of an unchanged, already validated factual proof.

This draft never accepts a provider response or decides semantic correctness.
The caller records a proof only after the complete ordinary factual validator
has accepted it. Exact source, scope, model and prompt identity are part of the
cache key, and literal/provenance checks are repeated before every reuse.
"""

from __future__ import annotations

import hashlib
import json
import threading
from copy import deepcopy
from typing import Any


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


class VerifiedComponentReuse:
    """One generation invocation; no disk cache and no cross-course reuse."""

    def __init__(self, *, source_texts: dict[str, str], context: dict[str, Any]):
        if not source_texts or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in source_texts.items()
        ):
            raise ValueError("reuse requires immutable source texts")
        required = {
            "chapter_id",
            "evidence_fingerprint",
            "review_identity",
            "prompt_sha256",
            "version",
        }
        if not required <= context.keys() or not all(context.get(key) for key in required):
            raise ValueError("reuse context is incomplete")
        self._sources = deepcopy(source_texts)
        self._context = deepcopy(context)
        # Include actual full chapter text as well as its supplied fingerprint:
        # a newly introduced exception invalidates every prior component proof.
        self._context_hash = fingerprint({"sources": self._sources, "context": self._context})
        self._proofs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def matches_context(self, *, source_texts: dict[str, str], context: dict[str, Any]) -> bool:
        return fingerprint({"sources": source_texts, "context": context}) == self._context_hash

    def key(self, component: dict[str, Any]) -> str:
        if set(component) != {"owner_id", "component_id", "fields", "source_ids", "boundaries"}:
            raise ValueError("reuse component envelope is incomplete")
        return fingerprint({"context": self._context_hash, "component": component})

    def remember(self, component: dict[str, Any], proof: dict[str, Any]) -> bool:
        if not (
            proof.get("source_entailment") is True
            and proof.get("scope_exceptions") is True
            and proof.get("findings") == []
        ):
            return False
        key = self.key(component)
        self._validate_literal_proof(component, proof)
        with self._lock:
            self._proofs[key] = deepcopy(proof)
        return True

    def get(self, component: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        key = self.key(component)
        with self._lock:
            proof = deepcopy(self._proofs.get(key))
        if proof is None:
            return None
        self._validate_literal_proof(component, proof)
        return key, proof

    def _validate_literal_proof(self, component: dict[str, Any], proof: dict[str, Any]) -> None:
        own = component["source_ids"]
        if (
            not isinstance(own, list)
            or not own
            or len(own) != len(set(own))
            or not set(own) <= self._sources.keys()
        ):
            raise ValueError("reuse component has unknown/duplicate own source IDs")
        ids, quotes = proof.get("source_ids"), proof.get("support_quotes")
        if (
            not isinstance(ids, list)
            or not ids
            or len(ids) != len(set(ids))
            or not set(ids) <= set(own)
            or not isinstance(quotes, list)
            or not quotes
            or {item.get("source_id") for item in quotes} != set(ids)
        ):
            raise ValueError("reuse proof does not bind its own raw sources")
        for item in quotes:
            text = item.get("quote")
            if (
                not isinstance(text, str)
                or not 6 <= len(text) <= 220
                or text not in self._sources[item["source_id"]]
            ):
                raise ValueError("reuse proof is not an immutable continuous raw span")
        boundaries = {item["boundary_id"]: item for item in component["boundaries"]}
        checks = proof.get("scope_checks")
        if (
            not isinstance(checks, list)
            or len(checks) != len(boundaries)
            or {item.get("boundary_id") for item in checks} != set(boundaries)
        ):
            raise ValueError("reuse proof does not cover its exact boundaries")
        fields = component["fields"]
        for item in checks:
            flags = [
                item.get(key)
                for key in (
                    "is_class_definition",
                    "assigns_class_property",
                    "has_same_class_counterexample",
                    "candidate_explicitly_limited",
                )
            ]
            if (
                not all(type(flag) is bool for flag in flags)
                or (all(flags[:3]) and not flags[3])
                or item.get("status") not in {"bounded", "not_applicable"}
                or flags[3] != (item["status"] == "bounded")
            ):
                raise ValueError("reuse proof contains an unbounded/invalid range verdict")
            claim = item.get("claim_quote")
            if (
                not isinstance(claim, str)
                or not claim
                or claim not in fields.get(item.get("claim_field"), "")
            ):
                raise ValueError("reuse claim is not in its current component")
            if item["status"] == "bounded":
                limit = item.get("limitation_quote")
                if (
                    not isinstance(limit, str)
                    or not limit
                    or limit not in fields.get(item.get("limitation_field"), "")
                ):
                    raise ValueError("reuse limitation is not in its current component")

    @staticmethod
    def selection(proof: dict[str, Any], spans: dict[str, dict[str, str]]) -> dict[str, Any]:
        """Re-enter the ordinary span/materialization validator on cache reuse."""
        reverse = {(item["source_id"], item["quote"]): key for key, item in spans.items()}
        try:
            ids = [reverse[(item["source_id"], item["quote"])] for item in proof["support_quotes"]]
        except KeyError as error:
            raise ValueError(
                "cached evidence is not in the current immutable span directory"
            ) from error
        result = {
            "source_entailment": proof["source_entailment"],
            "scope_exceptions": proof["scope_exceptions"],
            "rationale": proof["rationale"],
            "support_span_ids": list(dict.fromkeys(ids)),
            "findings": deepcopy(proof["findings"]),
            "scope_checks": deepcopy(proof["scope_checks"]),
        }
        for directory in ("assertions", "scope_matrix"):
            if directory in proof:
                result[directory] = deepcopy(proof[directory])
        if "assertion_checks" in proof:
            # The ordinary decoder/validator must see and recheck the complete
            # sentence proof directory, not just the parent's union of spans.
            result["assertion_checks"] = deepcopy(proof["assertion_checks"])
        return result
