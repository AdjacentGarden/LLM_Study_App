"""Contract tests only: no fake response counts as measured LLM quality."""

import copy
import json
import threading
from copy import deepcopy
from pathlib import Path

import pytest

from adaptive_learning import studio, studio_note_coverage
from adaptive_learning.studio_note_coverage import (
    CoverageError,
    continuous_parts,
    parse_review,
    registry,
)

BASE = Path(__file__).resolve().parent / "fixtures/studio_note_coverage"


@pytest.fixture
def packet():
    return json.loads(
        (
            BASE / "actual_v5_second_review_packet.json"
        ).read_text()
    )


def envelope(directory, failed=(), negative_global=()):
    # Synthetic contract response; it is never called a genuine semantic approval.
    failures = set(failed) | {"g_" + x for x in negative_global}
    return {
        "candidate_id": directory["candidate_id"],
        "passed": not failures,
        "reason": "offline contract fixture only",
        "global_checks": {k: k not in negative_global for k in directory["global_ids"]},
        "clause_checks": [[c["id"], c["id"] not in failed] for c in directory["clauses"]],
        "findings": [[x, "A" if x == "s1c0" else "U"] for x in sorted(failures)],
        "failure_scope": "none"
        if not failures
        else "other_or_uncertain"
        if negative_global
        else "optional_suggestions_only",
    }


def test_actual_prefix_is_required_and_all_characters_are_restored(packet):
    directory = registry(packet)
    assert len(directory["clauses"]) == 7
    assert (
        next(c for c in directory["clauses"] if c["id"] == "s1c0")["text"]
        == "可补充萨顿的平行关系依据："
    )
    for index, item in enumerate(packet["suggestions"]):
        rows = [c for c in directory["clauses"] if c["suggestion_index"] == index]
        assert "".join(c["text"] for c in rows) == item["suggestion"]
        assert all(item["suggestion"][c["start"] : c["end"]] == c["text"] for c in rows)


def test_actual_legacy_sparse_review_cannot_be_upgraded(packet):
    raw = json.loads(
        (BASE / "actual_v5_sparse_review_response.json").read_text()
    )
    before = deepcopy(raw)
    with pytest.raises(CoverageError):
        parse_review(raw, registry(packet))
    assert raw == before and raw["passed"] is False


@pytest.mark.parametrize(
    "fault",
    [
        "omit_prefix",
        "duplicate",
        "foreign",
        "stale",
        "string_bool",
        "int_bool",
        "missing_global",
        "global_string",
        "true_over_false",
        "false_over_true",
        "missing_finding",
        "wrong_finding",
        "duplicate_finding",
        "unknown_key",
    ],
)
def test_fail_closed_exact_coverage(packet, fault):
    directory = registry(packet)
    raw = envelope(directory, failed=["s1c0"])
    if fault == "omit_prefix":
        raw["clause_checks"] = [r for r in raw["clause_checks"] if r[0] != "s1c0"]
    elif fault == "duplicate":
        raw["clause_checks"][-1] = deepcopy(raw["clause_checks"][0])
    elif fault == "foreign":
        raw["clause_checks"][0][0] = "other_claim"
    elif fault == "stale":
        raw["candidate_id"] = "0" * 64
    elif fault == "string_bool":
        raw["clause_checks"][0][1] = "true"
    elif fault == "int_bool":
        raw["clause_checks"][0][1] = 1
    elif fault == "missing_global":
        raw["global_checks"].pop("fidelity")
    elif fault == "global_string":
        raw["global_checks"]["summary"] = "true"
    elif fault == "true_over_false":
        raw["passed"] = True
    elif fault == "false_over_true":
        raw = envelope(directory)
        raw["passed"] = False
    elif fault == "missing_finding":
        raw["findings"] = []
    elif fault == "wrong_finding":
        raw["findings"][0][0] = "s2c0"
    elif fault == "duplicate_finding":
        raw["findings"] *= 2
    else:
        raw["extra"] = True
    with pytest.raises(CoverageError):
        parse_review(raw, directory)


def test_id_restores_literal_no_retyped_claim_and_does_not_modify_content(packet):
    before = deepcopy(packet)
    directory = registry(packet)
    raw = envelope(directory, failed=["s1c0"])
    snapshot = deepcopy(raw)
    result = parse_review(raw, directory)
    assert (
        result["passed"] is False and result["findings"][0]["claim"] == "可补充萨顿的平行关系依据："
    )
    assert packet == before and raw == snapshot


def test_all_clause_true_does_not_bypass_global_fidelity_or_summary(packet):
    directory = registry(packet)
    for name in directory["global_ids"]:
        raw = envelope(directory, negative_global=[name])
        result = parse_review(raw, directory)
        assert result["passed"] is False and result["failure_scope"] == "other_or_uncertain"
        raw["failure_scope"] = "optional_suggestions_only"
        with pytest.raises(CoverageError):
            parse_review(raw, directory)


def test_changing_quote_or_text_invalidates_existing_response(packet):
    directory = registry(packet)
    raw = envelope(directory)
    for field in ["suggestion", "evidence"]:
        changed = deepcopy(packet)
        changed["suggestions"][0][field] += "变化"
        with pytest.raises(CoverageError):
            parse_review(raw, registry(changed))


@pytest.mark.parametrize(
    "text", ["  ：甲：乙；丙。  ", "甲：；乙？", "：：：", "甲 没有分隔符", "甲：乙：丙："]
)
def test_continuous_coverage_handles_punctuation_and_whitespace(text):
    parts = continuous_parts(text)
    assert "".join(text[a:b] for a, b in parts) == text
    assert parts[0][0] == 0 and parts[-1][1] == len(text)


def test_legal_dense_four_suggestions_are_rejected_before_review_never_truncated(packet):
    item = deepcopy(packet["suggestions"][0])
    packet["suggestions"] = [
        {**item, "suggestion_index": i, "suggestion": "甲：" * 250} for i in range(4)
    ]
    snapshot = deepcopy(packet)
    with pytest.raises(CoverageError, match="admission cap"):
        registry(packet)
    assert packet == snapshot


def test_four_suggestions_exactly32_clauses_and_allnegative_codes_are_preserved(packet):
    base = deepcopy(packet["suggestions"][0])
    text = "甲：乙；丙：丁；戊：己；庚：辛。"
    packet["suggestions"] = [{**base, "suggestion_index": i, "suggestion": text} for i in range(4)]
    directory = registry(packet)
    assert len(directory["clauses"]) == 32
    raw = envelope(directory, failed=[x["id"] for x in directory["clauses"]])
    result = parse_review(raw, directory)
    assert result["passed"] is False and len(result["findings"]) == 32
    assert all(
        x["claim"] in packet["suggestions"][x["suggestion_index"]]["suggestion"]
        for x in result["findings"]
    )


def test_unknown_issue_code_or_invented_literal_finding_are_rejected(packet):
    directory = registry(packet)
    raw = envelope(directory, failed=["s1c0"])
    raw["findings"][0][1] = "AUTOPASS"
    with pytest.raises(CoverageError):
        parse_review(raw, directory)
    raw = envelope(directory, failed=["s1c0"])
    raw["findings"] = [{"id": "s1c0", "claim": "invented", "issue": "unsupported"}]
    with pytest.raises(CoverageError):
        parse_review(raw, directory)


@pytest.fixture
def production():
    return studio, studio_note_coverage


def negative(directory, global_failure=False):
    ids = [row["id"] for row in directory["clauses"]]
    return {
        "candidate_id": directory["candidate_id"],
        "passed": False,
        "reason": "offline explicitly negative contract response, no semantic approval",
        "global_checks": {
            name: (not global_failure or name != "fidelity") for name in directory["global_ids"]
        },
        "clause_checks": [[name, False] for name in ids],
        "findings": [[name, "S"] for name in ids]
        + ([["g_fidelity", "F"]] if global_failure else []),
        "failure_scope": "other_or_uncertain" if global_failure else "optional_suggestions_only",
    }


def test_legacy_capacity12_is_not_globally_relaxed_and_v6_keeps32_negatives(production):
    studio, helper = production
    packet = json.loads((BASE / "actual_v5_second_review_packet.json").read_text())
    item = packet["suggestions"][0]
    packet["suggestions"] = [
        {**item, "suggestion_index": i, "suggestion": "甲：乙；丙：丁；戊：己；庚：辛。"}
        for i in range(4)
    ]
    directory = helper.registry(packet)
    normalized = helper.parse_review(negative(directory), directory)
    value = studio.Improvement.model_validate(
        {k: packet[k] for k in ["summary", "polished"]}
        | {
            "suggestions": [
                {k: v for k, v in item.items() if k != "suggestion_index"}
                for item in packet["suggestions"]
            ]
        }
    )
    assert len(normalized["findings"]) == 32
    with pytest.raises(studio.StudioNoteContractError):
        studio._note_review_feedback(normalized, value)
    review, feedback = studio._note_review_feedback(normalized, value, max_findings=32)
    assert review.passed is False and len(feedback["findings"]) == 32
    with pytest.raises(studio.StudioNoteContractError):
        studio._note_optional_omission_candidate(value, normalized, 4)
    omitted, indices = studio._note_optional_omission_candidate(
        value, normalized, 4, max_findings=32
    )
    assert indices == [0, 1, 2, 3] and omitted.suggestions == []
    assert omitted.summary == value.summary and omitted.polished == value.polished
    with pytest.raises(studio.StudioNoteContractError):
        studio._note_review_feedback(normalized, value, max_findings=True)


def replay(production, legacy=False):
    studio, helper = production
    inputs = {
        "book_id": "synthetic-book",
        "note_id": "synthetic-note",
        "revision": 2,
        "kind": "improve",
        "transcript": "基因有遗传效应。个人记录：我们记录响应时间。",
        "evidence": [{"page": 7, "text": "基因有遗传效应。染色体成对存在。"}],
    }
    originals, sources = studio._note_span_catalogues(inputs)
    draft = {
        "summary": "offline mechanical fixture",
        "polished": inputs["transcript"],
        "suggestions": [
            {
                "original_span_id": originals[0]["id"],
                "kind": "可以补充",
                "suggestion": "可补充：基因有遗传效应。",
                "evidence_span_id": sources[0]["id"],
            }
        ],
    }

    class Fake(studio.Studio):
        def __init__(self):
            self._note_trace = threading.local()
            self.calls = []
            self.traces = []
            self.updates = []

        def note(self, owner, key):
            return {"revision": inputs["revision"]}

        def evidence(self, note, transcript):
            assert transcript == inputs["transcript"]
            return copy.deepcopy(inputs["evidence"])

        def update(self, key, **fields):
            self.updates.append(copy.deepcopy(fields))

        def _save_note_trace(self, stage, **fields):
            self.traces.append({"stage": stage, **copy.deepcopy(fields)})

        def llm(self, prompt, images=None):
            assert images is None
            self.calls.append(prompt)
            assert len(self.calls) <= 5
            if not prompt.startswith("审核笔记整理"):
                return copy.deepcopy(draft)
            if legacy:
                return {
                    "passed": False,
                    "reason": "sparse legacy negative cannot bypass mandatory coverage",
                }
            packet = json.loads(prompt.rsplit("\n待审核数据：", 1)[1])
            directory = packet["claim_coverage"]
            return negative(directory, global_failure=not bool(directory["clauses"]))

    f = Fake()
    with pytest.raises(studio.StudioNoteContractError if legacy else ValueError):
        f.process_note(
            {"owner": "offline", "id": "offline", "kind": "improve"},
            {k: inputs[k] for k in ["book_id", "note_id", "revision", "transcript"]},
        )
    assert not any(x.get("status") == "succeeded" for x in f.updates)
    return f


def test_actual_process_has_max5_calls_last_false_and_no_false_to_true(production):
    f = replay(production)
    assert len(f.calls) == 5
    reviews = [x for x in f.traces if x["stage"] in ["final_review", "omission_final_review"]]
    assert len(reviews) == 3 and all(x["response"]["passed"] is False for x in reviews)
    omitted = next(x for x in f.traces if x["stage"] == "optional_suggestions_omitted")
    assert omitted["feedback"]["candidate"]["summary"] == reviews[1]["review_input"]["summary"]
    assert omitted["feedback"]["candidate"]["polished"] == reviews[1]["review_input"]["polished"]
    assert f.calls[-1].startswith("审核笔记整理")


def test_actual_process_old_shape_cannot_skip_v6_coverage(production):
    f = replay(production, legacy=True)
    assert len(f.calls) == 3  # One bounded full-format reask, then unchanged sparse wire still fails.
    assert next(x for x in f.traces if x["stage"] == "final_review")["response"]["passed"] is False
    assert next(x for x in f.traces if x["stage"] == "review_format_repair")["response"]["passed"] is False
