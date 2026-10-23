"""Writer-only feedback reduction keeps every recorded rejection offline."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from adaptive_learning.ingestion.models import ChapterDraft
from adaptive_learning.personalization.models import SourceCitation
from adaptive_learning.personalization.teaching_content import TeachingContentGenerator, _Lesson
from adaptive_learning.personalization.teaching_evidence import TeachingEvidence
from adaptive_learning.personalization.teaching_feedback_wire import compact_writer_feedback


def pair_id(parent_id, assertion_id):
    raw = json.dumps(
        [parent_id, assertion_id], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "boundary_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def negative_check(boundary_id, *, quote="范围断言", status="unbounded"):
    return {
        "boundary_id": boundary_id,
        "is_class_definition": True,
        "assigns_class_property": True,
        "has_same_class_counterexample": True,
        "candidate_explicitly_limited": False,
        "claim_field": "content",
        "claim_quote": quote,
        "status": status,
        "rationale": "当前断言自己没有保留必要条件。",
    }


def without_metadata(finding):
    return {key: value for key, value in finding.items() if key != "evidence_pages"}


@pytest.fixture(scope="module")
def real_feedback():
    data = json.loads(
        (Path(__file__).parent / "fixtures/teaching_v15_summary_review_regression.json").read_text()
    )
    common, selected = data["common"], data["cases"]["summary_bad"]

    class OfflineReview:
        calls = 0

        def structured(self, **kwargs):
            self.calls += 1
            assert self.calls == 1
            return deepcopy(selected["responses"][0])

    source_data = common["evidence"]
    evidence = TeachingEvidence(
        sources={
            key: SourceCitation.model_validate(value)
            for key, value in source_data["sources"].items()
        },
        point_sources=source_data["point_sources"],
        warnings=source_data["warnings"],
        fingerprint=source_data["fingerprint"],
    )
    client = OfflineReview()
    generator = TeachingContentGenerator(client)
    job = generator._build_fact_jobs(
        [],
        _Lesson.model_validate(selected["lesson"]),
        common["inputs"],
        ChapterDraft.model_validate(common["chapter"]),
        evidence,
        [],
        lesson_component_ids={"summary"},
    )[0]
    review = generator._run_fact_job(job)
    assert not review.accepted and client.calls == 1
    return (
        [{"factual_lesson": review.lesson.model_dump()}],
        job["payload"]["lesson_components"]["summary"]["scope_boundaries"],
        source_data["sources"],
    )


def test_real_canonical_summary_keeps_all_negative_cells_findings_raw_fields_and_pages(
    real_feedback,
):
    feedback, boundaries, sources = real_feedback
    original = deepcopy((feedback, boundaries, sources))
    compact = compact_writer_feedback(feedback, boundaries=boundaries, sources=sources)
    before = json.dumps(feedback, ensure_ascii=False)
    after = json.dumps(compact, ensure_ascii=False)
    assert len(before) > 80_000
    assert len(after) < 12_000 and len(after) < len(before) / 4
    assert (feedback, boundaries, sources) == original
    full_parent, parent = feedback[0]["factual_lesson"], compact[0]["factual_lesson"]
    assert {key: value for key, value in parent.items() if key != "components"} == {
        key: value for key, value in full_parent.items() if key != "components"
    }
    full = full_parent["components"]["summary"]
    proof = parent["components"]["summary"]
    assert "support_quotes" not in proof and "scope_matrix" not in proof
    negative = [row for row in full["scope_checks"] if row["status"] == "unbounded"]
    assert len(negative) == len(proof["scope_checks"]) == 3
    assert [without_metadata(row) for row in proof["findings"]] == full["findings"]
    assert len(proof["assertion_checks"]) == len(proof["assertions"]) == 2
    assert all(row in full["assertions"] for row in proof["assertions"])
    retained_ids = {row["assertion_id"] for row in proof["assertions"]}
    for row in proof["assertion_checks"]:
        raw = next(
            item for item in full["assertion_checks"] if item["assertion_id"] == row["assertion_id"]
        )
        assert {key: value for key, value in row.items() if key != "findings"} == {
            key: value for key, value in raw.items() if key != "findings"
        }
        assert [without_metadata(finding) for finding in row["findings"]] == raw["findings"]
    by_id = {boundary["boundary_id"]: boundary for boundary in boundaries}
    for old, check in zip(negative, proof["scope_checks"], strict=True):
        assert {key: value for key, value in check.items() if key != "boundary_context"} == old
        context = check["boundary_context"]
        raw = by_id[context["boundary_id"]]
        assert context["condition"] == raw["condition"]
        assert context["boundary_type"] == raw["boundary_type"]
        assert context["evidence_source_ids"] == raw["source_ids"]
        assert "actual_source_text" not in context
        assert any(pair_id(raw["boundary_id"], key) == check["boundary_id"] for key in retained_ids)
        assert context["evidence_pages"] == [
            {"source_id": key, "page_number": sources[key]["page_number"]}
            for key in raw["source_ids"]
        ]
    for finding in proof["findings"]:
        assert finding["evidence_pages"] == [
            {"source_id": key, "page_number": sources[key]["page_number"]}
            for key in finding["evidence_source_ids"]
        ]


def small_fixture():
    sources = {"s1": {"quote": "不能省略的原文。", "page_number": 7}}
    boundaries = [
        {
            "boundary_id": "b1",
            "boundary_type": "exception",
            "condition": "条件改变时不适用。",
            "source_ids": ["s1"],
        }
    ]
    finding = {
        "kind": "missing_condition",
        "candidate_field": "content",
        "candidate_quote": "范围断言",
        "issue": "此处遗漏了原文明确的温度条件。",
        "evidence_source_ids": ["s1"],
        "missing_text": "温度条件",
    }
    rejected = {
        "source_entailment": True,
        "scope_exceptions": False,
        "source_ids": ["s1"],
        "rationale": "记录本组件自己的范围失败。",
        "support_quotes": [{"source_id": "s1", "quote": "不能省略的原文。"}],
        "findings": [deepcopy(finding) for _ in range(6)],
        "scope_checks": [
            negative_check("b1"),
            negative_check("b1", status="not_applicable"),
            {"boundary_id": "positive", "status": "bounded"},
        ],
        "assertions": [],
        "assertion_checks": [],
        "scope_matrix": [["positive matrix"]],
    }
    approved = {
        "source_entailment": True,
        "scope_exceptions": True,
        "findings": [],
        "support_quotes": [{"quote": "passed"}],
        "scope_checks": [],
    }
    parent = {
        "accepted": False,
        "source_entailment": True,
        "scope_exceptions": False,
        "issues": ["整体问题同样保留。"],
        "point_id": "point",
        "failed_components": ["explanation", "practice"],
        "components": {
            "explanation": rejected,
            "practice": deepcopy(rejected),
            "flashcard": approved,
        },
    }
    return [{"factual_point": parent}], boundaries, sources


def test_all_failed_siblings_all_findings_and_tttf_cells_survive_without_input_mutation():
    feedback, boundaries, sources = small_fixture()
    original = deepcopy((feedback, boundaries, sources))
    result = compact_writer_feedback(feedback, boundaries=boundaries, sources=sources)
    parent = result[0]["factual_point"]
    assert set(parent["components"]) == {"explanation", "practice"}
    assert parent["failed_components"] == ["explanation", "practice"]
    for proof in parent["components"].values():
        assert len(proof["findings"]) == 6
        assert len(proof["scope_checks"]) == 2
        assert all(
            row["boundary_context"]["evidence_pages"] == [{"source_id": "s1", "page_number": 7}]
            for row in proof["scope_checks"]
        )
        assert "support_quotes" not in proof and "scope_matrix" not in proof
    assert (feedback, boundaries, sources) == original
    parent["components"]["explanation"]["findings"][0]["issue"] = "external mutation"
    assert (feedback, boundaries, sources) == original


def test_global_teacher_unknown_rows_and_parent_only_rejections_are_unchanged():
    feedback, boundaries, sources = small_fixture()
    approved = deepcopy(feedback[0]["factual_point"]["components"]["flashcard"])
    feedback[0]["factual_point"]["components"] = {"explanation": approved}
    feedback.extend(
        [
            {"factual_overall": {"accepted": False, "issues": ["未定位的全局问题。"]}},
            {"accepted": False, "lesson": {"issues": ["教师要求补充步骤。"]}},
            {"unknown": {"data": [1, 2]}},
            "opaque issue",
        ]
    )
    result = compact_writer_feedback(feedback, boundaries=boundaries, sources=sources)
    assert result[1:] == feedback[1:]
    assert result[0]["factual_point"]["accepted"] is False
    assert result[0]["factual_point"]["issues"] == feedback[0]["factual_point"]["issues"]
    assert result[0]["factual_point"]["components"] == {}
    result[2]["lesson"]["issues"].append("outside")
    assert len(feedback[2]["lesson"]["issues"]) == 1


def test_negative_scope_retains_its_source_positive_assertion_without_assertion_findings():
    feedback, boundaries, sources = small_fixture()
    proof = feedback[0]["factual_point"]["components"]["explanation"]
    assertions = [
        {
            "assertion_id": key,
            "field": "content",
            "start": start,
            "end": start + len(text),
            "text": text,
        }
        for key, start, text in [("first", 0, "范围断言。"), ("second", 5, "正向断言。")]
    ]
    proof.update(
        assertions=assertions,
        assertion_checks=[
            {
                "assertion_id": row["assertion_id"],
                "is_factual": True,
                "source_entailment": True,
                "support_span_ids": ["span"],
                "findings": [],
            }
            for row in assertions
        ],
        scope_checks=[negative_check(pair_id("b1", "first"))],
    )
    result = compact_writer_feedback(
        [{"factual_lesson": {"components": {"summary": proof}}}],
        boundaries=boundaries,
        sources=sources,
    )
    compact = result[0]["factual_lesson"]["components"]["summary"]
    assert compact["assertions"] == assertions[:1]
    assert compact["assertion_checks"] == proof["assertion_checks"][:1]


def test_unknown_negative_boundary_is_retained_explicitly_and_unknown_pages_are_not_invented():
    feedback, boundaries, sources = small_fixture()
    proof = feedback[0]["factual_point"]["components"]["explanation"]
    proof["scope_checks"] = [negative_check("missing")]
    proof["findings"][0]["evidence_source_ids"] = ["unknown"]
    result = compact_writer_feedback(feedback, boundaries=boundaries, sources=sources)
    compact = result[0]["factual_point"]["components"]["explanation"]
    assert compact["scope_checks"][0]["boundary_context"]["unresolved"] is True
    assert compact["scope_checks"][0]["claim_quote"] == "范围断言"
    assert compact["findings"][0]["evidence_pages"] == [
        {"source_id": "unknown", "page_unresolved": True}
    ]


def test_unlocated_negative_lesson_scope_keeps_all_raw_assertions_conservatively():
    feedback, boundaries, sources = small_fixture()
    proof = feedback[0]["factual_point"]["components"]["explanation"]
    proof.update(
        assertions=[
            {"assertion_id": "a", "field": "content", "start": 0, "end": 5, "text": "其他断言。"}
        ],
        assertion_checks=[{"assertion_id": "a", "source_entailment": True, "findings": []}],
        scope_checks=[negative_check("unresolvable")],
    )
    result = compact_writer_feedback(
        [{"factual_lesson": {"components": {"summary": proof}}}],
        boundaries=boundaries,
        sources=sources,
    )
    compact = result[0]["factual_lesson"]["components"]["summary"]
    assert compact["assertion_location_unresolved"] is True
    assert compact["assertions"] == proof["assertions"]
    assert compact["assertion_checks"] == proof["assertion_checks"]
