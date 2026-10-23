"""Exact positional coverage for note review, not a semantic fact checker.

Clauses are continuous positional slices, never claimed to be atomic facts.
The full suggestion, confirmed input and own quote remain mandatory context.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from itertools import pairwise

MAX_CLAUSES = 32  # Explicit admission cap; not proof that every response fits 1000 tokens.
GLOBALS = ("fidelity", "summary", "polished")
SCOPES = {"none", "optional_suggestions_only", "other_or_uncertain"}
ISSUES = {
    "S": "该片段有新增内容未被本条自身引文完整支持；不能借旁项或全局资料补证。",
    "A": "该片段的人物、主体或归属不能由本条自身连续引文确定；核对真实先行词或删无据归属。",
    "Q": "该片段的数量、适用对象、条件或范围强于自身引文；恢复原限定，不补造条件。",
    "C": "该片段与本条自身引文或确认原文相矛盾；按实际命题方向核对。",
    "F": "该目标有原意或限定丢失风险；保留确认原文，不因引文缺失否定用户原话。",
    "U": "该目标尚无法完成核验；保持不通过，不能把未确定当作有据。",
}


class CoverageError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise CoverageError(message)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def continuous_parts(text):
    """Preserve every character; do not silently merge substantive name prefixes."""
    require(isinstance(text, str) and bool(text.strip()), "empty suggestion")
    parts, start = [], 0
    for match in re.finditer(r"[。！？!?；;：:]", text):
        end = match.end()
        segment = text[start:end]
        # Punctuation/space alone is attached, never dropped or treated as a new fact.
        if not re.search(r"[^\s。！？!?；;：:]", segment):
            if parts:
                parts[-1] = (parts[-1][0], end)
                start = end
            continue
        parts.append((start, end))
        start = end
    if start < len(text):
        if text[start:].strip() or not parts:
            parts.append((start, len(text)))
        else:
            parts[-1] = (parts[-1][0], len(text))
    if not parts:
        parts = [(0, len(text))]
    require(parts[0][0] == 0 and parts[-1][1] == len(text), "noncomplete slices")
    require(all(a[1] == b[0] for a, b in pairwise(parts)), "gapped slices")
    require("".join(text[a:b] for a, b in parts) == text, "changed suggestion")
    return parts


def registry(packet):
    require(
        type(packet) is dict
        and set(packet) == {"confirmed_transcript", "summary", "polished", "suggestions"},
        "packet shape",
    )
    require(
        all(isinstance(packet[k], str) for k in ("confirmed_transcript", "summary", "polished")),
        "text fields",
    )
    items = packet["suggestions"]
    require(type(items) is list and len(items) <= 4, "suggestion count")
    clauses = []
    for index, item in enumerate(items):
        require(
            type(item) is dict
            and set(item)
            == {"suggestion_index", "original", "kind", "suggestion", "evidence", "page"},
            "item shape",
        )
        require(
            type(item["suggestion_index"]) is int and item["suggestion_index"] == index,
            "current item identity",
        )
        require(type(item["page"]) is int and item["page"] >= 1, "page")
        require(
            isinstance(item["suggestion"], str) and 0 < len(item["suggestion"]) <= 500,
            "suggestion length",
        )
        for number, (start, end) in enumerate(continuous_parts(item["suggestion"])):
            clauses.append(
                {
                    "id": f"s{index}c{number}",
                    "suggestion_index": index,
                    "start": start,
                    "end": end,
                    "text": item["suggestion"][start:end],
                }
            )
    require(
        len(clauses) <= MAX_CLAUSES,
        "review admission cap exceeded; never drop claims or auto-add calls",
    )
    return {
        "candidate_id": digest(packet),
        "clauses": clauses,
        "global_ids": list(GLOBALS),
        "context": deepcopy(packet),
    }


def parse_review(raw, expected):
    """Strict mandatory production-v6 proposal; legacy shapes are not upgraded."""
    raw = _review_content(raw)
    require(
        type(raw) is dict
        and set(raw)
        == {
            "candidate_id",
            "passed",
            "reason",
            "global_checks",
            "clause_checks",
            "findings",
            "failure_scope",
        },
        "new mandatory review shape",
    )
    require(raw["candidate_id"] == expected["candidate_id"], "stale candidate")
    require(type(raw["passed"]) is bool, "passed must be bool")
    require(
        isinstance(raw["reason"], str) and 0 < len(raw["reason"].strip()) <= 180, "bounded reason"
    )
    globals_ = raw["global_checks"]
    require(
        type(globals_) is dict
        and set(globals_) == set(GLOBALS)
        and all(type(v) is bool for v in globals_.values()),
        "complete strict globals",
    )
    rows, known = raw["clause_checks"], {row["id"]: row for row in expected["clauses"]}
    require(type(rows) is list and len(rows) == len(known), "missing or extra clause row")
    ids, failed = [], set()
    for row in rows:
        require(type(row) is list and len(row) == 2, "compact [id,bool] clause pair")
        key, ok = row
        require(isinstance(key, str) and key in known and type(ok) is bool, "foreign ID or nonbool")
        ids.append(key)
        if not ok:
            failed.add(key)
    require(len(ids) == len(set(ids)) and set(ids) == set(known), "duplicate or missing clause")
    negative_globals = {"g_" + key for key, ok in globals_.items() if not ok}
    required_findings = failed | negative_globals
    findings = raw["findings"]
    require(
        type(findings) is list and len(findings) == len(required_findings),
        "unlocated or missing negative",
    )
    found, restored = [], []
    for finding in findings:
        require(
            type(finding) is list and len(finding) == 2,
            "finding uses [id,issue_code], never retyped quotation",
        )
        key, code = finding
        require(isinstance(key, str) and key in required_findings, "finding not a negative target")
        require(type(code) is str and code in ISSUES, "unknown issue code")
        issue = ISSUES[code]
        found.append(key)
        if key in known:
            clause = known[key]
            restored.append(
                {
                    "field": "suggestion",
                    "suggestion_index": clause["suggestion_index"],
                    "claim": clause["text"],
                    "issue": issue,
                }
            )
    require(
        len(found) == len(set(found)) and set(found) == required_findings,
        "duplicate or uncovered negative",
    )
    aggregate = not required_findings
    require(raw["passed"] == aggregate, "contradictory verdict; never flip false to true")
    scope = raw["failure_scope"]
    require(
        type(scope) is str and scope in SCOPES and (scope == "none") == raw["passed"],
        "scope/verdict conflict",
    )
    require(
        not negative_globals or scope == "other_or_uncertain",
        "global failure cannot authorize optional deletion",
    )
    # Existing reviewer compatibility adapter: raw remains immutable in audit.
    # Global omissions need not pretend there is a corresponding literal in polished.
    reason = raw["reason"] + "".join(
        f" [{key}] {ISSUES[code]}" for key, code in findings if key.startswith("g_")
    )
    require(len(reason) <= 1000, "existing reason ceiling")
    return {"passed": raw["passed"], "reason": reason, "findings": restored, "failure_scope": scope}


def _review_content(raw):
    """Copy away only the observed format marker; preserve every semantic field.

    No other extra key/value is accepted here. The original mandatory validator
    checks the complete remaining object, and the caller retains raw trace data.
    """
    if type(raw) is dict and "type" in raw:
        require(
            type(raw["type"]) is str and raw["type"] == "json_object",
            "unsupported format metadata",
        )
        return {key: value for key, value in raw.items() if key != "type"}
    return raw


def repair_locks(raw, expected):
    """Keep readable current negatives; ambiguous identities/negatives fail closed."""
    require(type(raw) is dict, "only returned parsed JSON objects can be reasked")
    if "candidate_id" in raw:
        require(type(raw["candidate_id"]) is str and raw["candidate_id"] == expected["candidate_id"],
                "explicit wrong/stale candidate cannot be relabeled")
    if "passed" in raw:
        require(type(raw["passed"]) is bool, "ambiguous verdict cannot be repaired")
    if "failure_scope" in raw:
        require(type(raw["failure_scope"]) is str and raw["failure_scope"] in SCOPES,
                "ambiguous failure scope")
    known = {row["id"] for row in expected["clauses"]}
    false_globals, false_clauses, pairs = set(), set(), {}
    globals_ = raw.get("global_checks", {})
    require(type(globals_) is dict, "ambiguous global checks")
    for key, value in globals_.items():
        require(type(value) is bool, "ambiguous global verdict")
        if value is False:
            require(key in GLOBALS, "unlocated foreign global negative")
            false_globals.add(key)
    rows = raw.get("clause_checks", [])
    require(type(rows) is list, "ambiguous clause checks")
    for row in rows:
        require(type(row) is list and len(row) == 2 and type(row[0]) is str
                and type(row[1]) is bool, "ambiguous clause verdict")
        if row[1] is False:
            require(row[0] in known, "foreign clause negative cannot be relabeled")
            false_clauses.add(row[0])
    findings = raw.get("findings", [])
    require(type(findings) is list, "ambiguous findings")
    for pair in findings:
        require(type(pair) is list and len(pair) == 2 and type(pair[0]) is str
                and type(pair[1]) is str and pair[1] in ISSUES, "ambiguous negative finding")
        key, code = pair
        require(key in known or key in {"g_" + name for name in GLOBALS}, "foreign finding cannot be relabeled")
        require(key not in pairs or pairs[key] == code, "conflicting issue codes cannot be silently replaced")
        pairs[key] = code
        if key in known:
            false_clauses.add(key)
        else:
            false_globals.add(key[2:])
    negative = (raw.get("passed") is False or bool(false_globals or false_clauses)
                or raw.get("failure_scope") in {"optional_suggestions_only", "other_or_uncertain"})
    return {"candidate_id": expected["candidate_id"], "has_negative": negative,
            "passed_false": raw.get("passed") is False,
            "global_false": sorted(false_globals), "clause_false": sorted(false_clauses),
            "findings": [[key, value] for key, value in pairs.items()],
            "other_scope": bool(false_globals) or (negative and raw.get("failure_scope") != "optional_suggestions_only")}


def validate_repair(raw, expected, locks):
    """A new full review must satisfy the original contract and every old veto."""
    result = parse_review(raw, expected)
    require(locks["candidate_id"] == expected["candidate_id"], "repair locks belong to another candidate")
    content = _review_content(raw)
    require(not locks["has_negative"] or content["passed"] is False, "format repair cannot upgrade a negative verdict")
    require(all(content["global_checks"][name] is False for name in locks["global_false"]),
            "format repair cannot erase a global veto")
    rows = dict(content["clause_checks"])
    require(all(rows[name] is False for name in locks["clause_false"]), "format repair cannot erase a current clause veto")
    require(all(pair in content["findings"] for pair in locks["findings"]),
            "format repair cannot erase or change an existing finding target/code")
    require(not locks["other_scope"] or content["failure_scope"] == "other_or_uncertain",
            "format repair cannot turn a conservative/global rejection into optional deletion")
    return result
