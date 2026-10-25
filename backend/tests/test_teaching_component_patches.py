"""Narrow writer patches are unapproved drafts, including retained siblings.

The F812 fixture is a real rejected, unpublished response. Review doubles below
test orchestration of that frozen veto, not biological truth or model behavior.
"""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_teaching_content import course, lesson_candidate, point_candidate
from test_teaching_v15_integration import FIXED, CandidateClient, Planner

from adaptive_learning.personalization import teaching_content as teaching

OBSERVED = json.loads((Path(__file__).parent / "fixtures" /
                       "teaching_component_patch_f812.json").read_text())


def setup_patch(allowed=None):
    bundle, chapter, evidence = course(2)
    inputs = teaching.TeachingContentGenerator._inputs(bundle, evidence)
    points = {item["point_id"]: teaching._Point.model_validate(point_candidate(item))
              for item in inputs}
    first = inputs[0]["point_id"]
    allowed = allowed or {first: {"practice"}}
    generator = teaching.TeachingContentGenerator(CandidateClient(), scope_planner=Planner())
    contract = generator._repair_contract(set(allowed), points, allowed, None, set(), False)
    return generator, contract, points, inputs, evidence, chapter


def test_patch_only_returns_rejected_practice_and_preserves_frozen_components_exactly():
    generator, contract, baseline, inputs, evidence, _ = setup_patch()
    point_id = inputs[0]["point_id"]
    practice = baseline[point_id].practice.model_dump()
    practice["expected_answer"] += FIXED
    audit = []
    generator.audit_callback = audit.append
    patch = {"points": [{"point_id": point_id, "practice": practice}], "lesson": None}
    result = generator._materialize_generation_patch(patch, contract, baseline, None)
    generator._validate_points(result.points, inputs[:1], evidence)
    before, after = baseline[point_id].model_dump(), result.points[0].model_dump()
    assert after == {**before, "practice": practice}
    assert len(json.dumps(patch)) < len(json.dumps({"points": [before], "lesson": None}))
    assert audit[-1]["event"] == "generation_patch_materialized"
    assert audit[-1]["patch"] == patch
    assert audit[-1]["approved"] is False
    assert audit[-1]["independent_review_required"] is True


def test_patch_uses_current_citation_repair_baseline_without_replacing_rejection_identity():
    generator, contract, baseline, inputs, evidence, _ = setup_patch()
    point_id = inputs[0]["point_id"]
    rejected = {"points": [baseline[point_id].model_dump()], "lesson": None}
    baseline[point_id].source_ids.append("citation-added-before-writer")
    evidence.sources["citation-added-before-writer"] = evidence.sources["s1"].model_copy()
    audit = []
    generator.audit_callback = audit.append
    practice = baseline[point_id].practice.model_dump()
    practice["expected_answer"] += FIXED
    generated = generator._materialize_generation_patch(
        {"points": [{"point_id": point_id, "practice": practice}], "lesson": None},
        contract, baseline, None, rejected,
    )
    generator._validate_points(generated.points, inputs[:1], evidence)
    assert generated.points[0].source_ids == ["s1", "citation-added-before-writer"]
    assert rejected["points"][0]["source_ids"] == ["s1"]
    expected = hashlib.sha256(
        json.dumps(rejected, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    assert audit[-1]["rejection_candidate_sha256"] == expected
    assert audit[-1]["merge_baseline_sha256"] != expected


@pytest.mark.parametrize("fault", ["frozen", "missing", "foreign", "duplicate", "top_extra"])
def test_patch_rejects_frozen_component_writes_and_identity_or_envelope_gaps(fault):
    generator, contract, baseline, inputs, _, _ = setup_patch()
    point_id = inputs[0]["point_id"]
    row = {"point_id": point_id, "practice": baseline[point_id].practice.model_dump()}
    patch = {"points": [row], "lesson": None}
    if fault == "frozen":
        row["flashcard"] = baseline[point_id].flashcard.model_dump()
    elif fault == "missing":
        del row["practice"]
    elif fault == "foreign":
        row["point_id"] = inputs[1]["point_id"]
    elif fault == "duplicate":
        patch["points"].append(deepcopy(row))
    else:
        patch["approved"] = True
    with pytest.raises(teaching.TeachingContentError):
        generator._materialize_generation_patch(patch, contract, baseline, None)


@pytest.mark.parametrize("fault", ["source", "item_id"])
def test_materialized_patch_still_checks_source_and_inner_item_identity(fault):
    generator, contract, baseline, inputs, evidence, _ = setup_patch()
    point_id = inputs[0]["point_id"]
    practice = baseline[point_id].practice.model_dump()
    practice["source_ids" if fault == "source" else "item_id"] = (
        ["fabricated-source"] if fault == "source" else "different-item"
    )
    generated = generator._materialize_generation_patch(
        {"points": [{"point_id": point_id, "practice": practice}], "lesson": None},
        contract, baseline, None,
    )
    with pytest.raises(teaching.TeachingContentError):
        generator._validate_points(generated.points, inputs[:1], evidence)


def test_lesson_group_permissions_expand_to_exact_slots_without_rewriting_frozen_reading():
    generator, _, baseline, _, evidence, _ = setup_patch()
    lesson = teaching._Lesson.model_validate(lesson_candidate())
    contract = generator._repair_contract(
        set(), baseline, {}, lesson, {"worked_examples", "checkpoint_2"}, True
    )
    assert contract["lesson_components"] == ["checkpoint_2", "example_1", "example_2"]
    parts = generator._lesson_components(lesson)
    patch = {"example_1": lesson.worked_examples[0].model_dump(),
             "example_2": lesson.worked_examples[1].model_dump(),
             "checkpoint_2": lesson.checkpoints[1].model_dump()}
    patch["example_1"]["steps"][0] += FIXED
    raw = {"points": [], "lesson": patch}
    generated = generator._materialize_generation_patch(raw, contract, baseline, lesson)
    assert generated.lesson.opening == lesson.opening
    assert generated.lesson.summary == lesson.summary
    assert generated.lesson.checkpoints[0] == lesson.checkpoints[0]
    assert generated.lesson.reading_source_ids == lesson.reading_source_ids
    assert generator._lesson_components(generated.lesson)["example_1"] != parts["example_1"]
    bad = deepcopy(raw)
    bad["lesson"]["summary"] = lesson.summary
    with pytest.raises(teaching.TeachingContentError, match="全章组件"):
        generator._materialize_generation_patch(bad, contract, baseline, lesson)
    bad = deepcopy(raw)
    bad["lesson"]["example_1"]["source_ids"] = ["fabricated-source"]
    materialized = generator._materialize_generation_patch(bad, contract, baseline, lesson)
    with pytest.raises(teaching.TeachingContentError):
        generator._validate_lesson(materialized.lesson, list(baseline.values()), evidence)


def test_observed_unchanged_chicken_practice_is_still_rejected_by_the_same_guard():
    original = teaching._Point.model_validate(OBSERVED["chicken_initial"])
    repeated = teaching._Point.model_validate(OBSERVED["chicken_unchanged_repair"])
    assert repeated.practice == original.practice  # Actual F attempt-2 response.
    generator = teaching.TeachingContentGenerator(CandidateClient(), scope_planner=Planner())
    contract = generator._repair_contract(
        {original.point_id}, {original.point_id: original},
        {original.point_id: {"practice"}}, None, set(), False,
    )
    generated = generator._materialize_generation_patch(
        {"points": [{"point_id": original.point_id, "practice": repeated.practice.model_dump()}],
         "lesson": None}, contract, {original.point_id: original}, None,
    )
    with pytest.raises(teaching.TeachingContentError, match="未修改"):
        generator._validate_point_repair(
            generated.points[0], original,
            teaching._RepairRequirements(exact_points={original.point_id: {"practice"}}),
        )


class SiblingClient(CandidateClient):
    def __init__(self, ids, *, reject_retained=False, bad_source=False):
        super().__init__(teacher_failures={(key, "practice") for key in ids})
        self.ids = ids
        self.repair_count = 0
        self.reject_retained = reject_retained
        self.bad_source = bad_source

    def structured(self, *, system, user, **kwargs):
        payload = json.loads(user)
        result = super().structured(system=system, user=user, **kwargs)
        if system == teaching.GENERATION_PROMPT and payload["repair_contract"]:
            self.repair_count += 1
            if self.repair_count == 1:
                old = {row["point_id"]: row for row in payload["previous_candidate"]["points"]}
                for row in result["points"]:
                    if row["point_id"] == self.ids[1]:
                        row["practice"] = deepcopy(old[self.ids[1]]["practice"])
                    elif self.bad_source:
                        row["practice"]["source_ids"] = ["fabricated-source"]
        return result

    def teacher(self, payload):
        result = super().teacher(payload)
        if self.reject_retained:
            for point, row in zip(payload["candidate"]["points"], result["points"], strict=True):
                if point["point_id"] == self.ids[0] and FIXED in point["practice"]["expected_answer"]:
                    row.update(practice_ok=False, issues=["practice：该修订仍有独立教学缺陷。"])
                    result["accepted"] = False
        return result


@pytest.mark.parametrize("reject_retained", [False, True])
def test_valid_sibling_draft_survives_unchanged_peer_but_cannot_bypass_final_review(reject_retained):
    bundle, chapter, evidence = course(2)
    ids = [point.point_id for point in bundle.knowledge_points]
    client = SiblingClient(ids, reject_retained=reject_retained)
    audit = []
    generator = teaching.TeachingContentGenerator(
        client, scope_planner=Planner(), audit_callback=audit.append, max_attempts=3
    )
    if reject_retained:
        with pytest.raises(teaching.TeachingContentError, match="未通过"):
            generator.generate(bundle, chapter, evidence)
    else:
        result = generator.generate(bundle, chapter, evidence)
        assert result.teaching_review["accepted"]
    batches = [row for row in client.generations if row["batch_points"]]
    assert [[row["point_id"] for row in batch["batch_points"]] for batch in batches] == [
        ids, ids, [ids[1]]
    ]
    retained = [row for row in audit if row["event"] == "point_repair_drafts_retained"]
    assert len(retained) == 1 and retained[0]["point_ids"] == [ids[0]]
    assert retained[0]["pending_point_ids"] == [ids[1]]
    assert retained[0]["approved"] is False
    candidates = [row for row in audit if row["event"] == "candidate"]
    assert [row["attempt"] for row in candidates] == [1, 3]
    after = candidates[-1]["candidate"]["points"]
    assert after[0] == retained[0]["drafts"][0]
    assert candidates[0]["candidate_sha256"] != candidates[-1]["candidate_sha256"]
    teachers = [row for row in audit if row["event"] == "teacher_review"]
    assert [row["review"]["accepted"] for row in teachers] == [False, not reject_retained]
    # Changed draft content must be sent for independent fact review, not reuse
    # the first candidate's proof merely because the writer leaf was retained.
    changed_facts = [data for (role, _, _), data, *_ in client.calls
                     if role == "fact" and any(
                         point["point_id"] == ids[0] and
                         FIXED in point["practice"]["expected_answer"]
                         for point in data["candidate"]["points"])]
    assert changed_facts


def test_bad_source_patch_is_not_retained_as_a_successful_sibling():
    bundle, chapter, evidence = course(2)
    ids = [point.point_id for point in bundle.knowledge_points]
    client = SiblingClient(ids, bad_source=True)
    audit = []
    with pytest.raises(teaching.TeachingContentError, match="未通过"):
        teaching.TeachingContentGenerator(
            client, scope_planner=Planner(), audit_callback=audit.append, max_attempts=2
        ).generate(bundle, chapter, evidence)
    assert not any(row["event"] == "point_repair_drafts_retained" for row in audit)
    assert len([row for row in audit if row["event"] == "candidate"]) == 1


def test_retained_draft_new_semantic_rejection_becomes_new_baseline_and_pending_target():
    class RejectRetainedOnce(SiblingClient):
        def structured(self, *, system, user, **kwargs):
            result = super().structured(system=system, user=user, **kwargs)
            if system == teaching.GENERATION_PROMPT and self.repair_count == 3:
                result["points"][0]["practice"]["expected_answer"] += "第二次修订重新核对必要推理。"
            return result

        def teacher(self, payload):
            result = super().teacher(payload)
            for point, row in zip(payload["candidate"]["points"], result["points"], strict=True):
                text = point["practice"]["expected_answer"]
                if point["point_id"] == self.ids[0] and FIXED in text and "第二次修订" not in text:
                    row.update(practice_ok=False, issues=["practice：此修订仍须补足必要推理。"])
                    result["accepted"] = False
            return result

    bundle, chapter, evidence = course(2)
    ids = [point.point_id for point in bundle.knowledge_points]
    client, audit = RejectRetainedOnce(ids), []
    result = teaching.TeachingContentGenerator(
        client, scope_planner=Planner(), audit_callback=audit.append, max_attempts=4
    ).generate(bundle, chapter, evidence)
    assert result.teaching_review["accepted"]
    batches = [row for row in client.generations if row["batch_points"]]
    assert [[row["point_id"] for row in batch["batch_points"]] for batch in batches] == [
        ids, ids, [ids[1]], [ids[0]]
    ]
    candidates = [row for row in audit if row["event"] == "candidate"]
    assert [row["attempt"] for row in candidates] == [1, 3, 4]
    before, after = candidates[-2]["candidate"], candidates[-1]["candidate"]
    assert before["points"][1] == after["points"][1]
    assert before["points"][0] != after["points"][0]
    assert "第二次修订" in after["points"][0]["practice"]["expected_answer"]


class ObservedScopeClient(CandidateClient):
    """Freeze the actual teacher veto instead of inventing a semantic approver."""

    def __init__(self, target):
        super().__init__()
        self.target = target

    def structured(self, *, system, user, **kwargs):
        payload = json.loads(user)
        result = super().structured(system=system, user=user, **kwargs)
        if system == teaching.GENERATION_PROMPT:
            for row in result["points"]:
                if row["point_id"] != self.target:
                    continue
                identity = (row["point_id"], row["flashcard"]["card_id"], row["practice"]["item_id"])
                actual = deepcopy(OBSERVED["sutton_failed_repair" if payload["previous_candidate"]
                                           else "sutton_initial"])
                actual["point_id"], actual["flashcard"]["card_id"], actual["practice"]["item_id"] = identity
                actual["source_ids"] = ["s1"]
                actual["flashcard"]["source_ids"] = ["s1"]
                actual["practice"]["source_ids"] = ["s1"]
                row.update(actual)
        return result

    def teacher(self, payload):
        result = super().teacher(payload)
        for row in result["points"]:
            if row["point_id"] == self.target:
                row.update(explanation_ok=False, flashcard_ok=False, practice_ok=False,
                           issues=[f"{name}：{issue.replace(OBSERVED['sutton_initial']['point_id'], self.target)}"
                                   for name, issue in zip(
                               ("explanation", "flashcard", "practice"),
                               OBSERVED["teacher_issues"][:3], strict=True)])
                result["accepted"] = False
        return result


def test_actual_history_framing_and_citation_edit_does_not_override_independent_scope_veto():
    bundle, chapter, evidence = course(2)
    audit = []
    client = ObservedScopeClient(bundle.knowledge_points[0].point_id)
    with pytest.raises(teaching.TeachingContentError, match="未通过"):
        teaching.TeachingContentGenerator(
            client, scope_planner=Planner(), audit_callback=audit.append, max_attempts=2
        ).generate(bundle, chapter, evidence)
    candidates = [row for row in audit if row["event"] == "candidate"]
    assert len(candidates) == 2  # The real-looking patch still reaches full review.
    final = candidates[-1]["candidate"]["points"][0]
    assert "萨顿还列举" in final["explanation"]
    assert "位于非同源染色体上的非等位基因" not in final["explanation"]
    assert all(not row["review"]["accepted"] for row in audit
               if row["event"] == "teacher_review")
    assert all(row["review"]["accepted"] for row in audit if row["event"] == "fact_review")
    request = [row for row in client.generations if row["repair_contract"]][-1]
    assert set(request["point_revision_tasks"][0]["components"]) == {
        "explanation", "flashcard", "practice"
    }
    assert OBSERVED["condition"]["source_id"] in json.dumps(request["review_feedback"])


class ObservedDuplicateClient(CandidateClient):
    def structured(self, *, system, user, **kwargs):
        payload = json.loads(user)
        result = super().structured(system=system, user=user, **kwargs)
        if system == teaching.GENERATION_PROMPT and payload["include_lesson"]:
            lesson = deepcopy(OBSERVED["lesson"])
            lesson["worked_examples"][1:] = deepcopy(OBSERVED["duplicate_examples"])
            lesson["reading_source_ids"] = ["s1"]
            for item in [*lesson["worked_examples"], *lesson["checkpoints"]]:
                item["source_ids"] = ["s1"]
            if payload["repair_contract"]:
                # A real edit to steps still cannot fix the frozen same-task
                # objection when the actual question/task remains unchanged.
                lesson["worked_examples"][1]["steps"][0] += FIXED
            result["lesson"] = lesson
        return result

    def teacher(self, payload):
        result = super().teacher(payload)
        result["accepted"] = False
        result["lesson"].update(examples_ok=False, issues=[OBSERVED["teacher_issues"][-1]])
        return result


def test_actual_duplicate_example_tasks_remain_vetoed_after_a_well_formed_slot_patch():
    bundle, chapter, evidence = course(2)
    audit = []
    client = ObservedDuplicateClient()
    with pytest.raises(teaching.TeachingContentError, match="未通过"):
        teaching.TeachingContentGenerator(
            client, scope_planner=Planner(), audit_callback=audit.append, max_attempts=2
        ).generate(bundle, chapter, evidence)
    candidates = [row["candidate"] for row in audit if row["event"] == "candidate"]
    assert len(candidates) == 2
    first, second = [row["lesson"]["worked_examples"] for row in candidates]
    assert first[1]["steps"] != second[1]["steps"]
    assert [row["problem"] for row in first] == [row["problem"] for row in second]
    assert all(not row["review"]["accepted"] for row in audit
               if row["event"] == "teacher_review")
    assert any(row["event"] == "generation_patch_materialized" for row in audit)
