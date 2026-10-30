"""Offline integration checks for the isolated parallel teaching draft.

Run with the existing backend venv and PYTHONPATH=../app/backend/src.
Mock dispatch depends on fixed point/component IDs and actual candidate text,
never the interleaving or a global model-call counter.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from test_teaching_content import (
    advertised_fact_response,
    course,
    lesson_candidate,
    point_candidate,
    positive_rubric_alignment_response,
)

from adaptive_learning.llm import teaching_parallel
from adaptive_learning.llm.client import LLMError, _deadline, model_time_budget
from adaptive_learning.personalization import teaching_content as draft
from adaptive_learning.personalization.rubric_alignment import RUBRIC_ALIGNMENT_PROMPT

FIXED = "已经按当前组件的问题补足推理。"


class Planner:
    def plan(self, inputs, evidence):
        return {item["point_id"]: [] for item in inputs}


@pytest.fixture(autouse=True)
def separate_pool(monkeypatch):
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="draft-review") as pool:
        monkeypatch.setattr(teaching_parallel, "_pool", (pool, 4))
        yield


class CandidateClient:
    def __getattribute__(self, name):
        method = object.__getattribute__(self, name)
        if name != "structured":
            return method

        def adapt(*, system, user, **kwargs):
            result = method(system=system, user=user, **kwargs)
            if system == draft.GENERATION_PROMPT:
                from teaching_writer_test_support import advertised_writer_response

                return advertised_writer_response(result, json.loads(user))
            return result

        return adapt

    def __init__(
        self,
        *,
        fact_failures=(),
        teacher_failures=(),
        lesson_fact=(),
        teacher_lesson=(),
        unchanged=(),
        rendezvous=False,
        error_role=None,
        late_teacher=False,
    ):
        self.fact_failures = set(fact_failures)
        self.teacher_failures = set(teacher_failures)
        self.lesson_fact = set(lesson_fact)
        self.teacher_lesson = set(teacher_lesson)
        self.unchanged = set(unchanged)
        self.error_role = error_role
        self.lock = threading.Lock()
        self.calls = []
        self.generations = []
        self.rendezvous = threading.Barrier(2) if rendezvous else None
        self.rendezvous_fact_claimed = False
        self.teacher_started = threading.Event()
        self.release_teacher = threading.Event()
        self.late_teacher = late_teacher
        self.peak = self.active = 0

    def structured(self, *, system, user, **kwargs):
        data = json.loads(user)
        if system == draft.GENERATION_PROMPT:
            with self.lock:
                self.generations.append(deepcopy(data))
            points = [point_candidate(item) for item in data["batch_points"]]
            lesson = lesson_candidate() if data["include_lesson"] else None
            if data["previous_candidate"] is not None:
                for point in points:
                    for name in ("explanation", "flashcard", "practice"):
                        if (point["point_id"], name) in self.unchanged:
                            continue
                        # Deliberately alter all fields: the implementation must
                        # discard changes outside the reviewers' target union.
                        if name == "explanation":
                            point["explanation"] += FIXED
                        else:
                            field = "back" if name == "flashcard" else "expected_answer"
                            point[name][field] += FIXED
                if lesson:
                    lesson["opening"] += FIXED
                    lesson["summary"] += FIXED
                    for index, example in enumerate(lesson["worked_examples"], 1):
                        if (None, f"example_{index}") not in self.unchanged:
                            example["steps"][0] += FIXED
                    for checkpoint in lesson["checkpoints"]:
                        checkpoint["expected_answer"] += FIXED
            return {"points": points, "lesson": lesson}
        roles = {
            draft.FACT_REVIEW_PROMPT: "fact",
            draft.REVIEW_PROMPT: "teacher",
            RUBRIC_ALIGNMENT_PROMPT: "rubric",
        }
        assert system in roles, "unknown model role must not become a teacher review"
        role = roles[system]
        job = (
            role,
            tuple(data.get("output_contract", {}).get(
                "item_ids" if role == "rubric" else "point_ids", [])),
            tuple(data.get("output_contract", {}).get("lesson_component_ids", [])),
        )
        with self.lock:
            self.calls.append(
                (
                    job,
                    deepcopy(data),
                    threading.current_thread().name,
                    deepcopy(draft._AUDIT_CONTEXT.get()),
                    _deadline.get(),
                )
            )
            self.active += 1
            self.peak = max(self.peak, self.active)
            join_rendezvous = self.rendezvous and (
                role == "teacher"
                or (role == "fact" and job[1] and not self.rendezvous_fact_claimed)
            )
            if join_rendezvous and role == "fact":
                self.rendezvous_fact_claimed = True
        try:
            if role == "teacher":
                self.teacher_started.set()
                if self.late_teacher:
                    assert self.release_teacher.wait(4)
            if join_rendezvous:
                self.rendezvous.wait(4)
            if self.error_role == role:
                if role == "fact" and self.late_teacher:
                    assert self.teacher_started.wait(4)
                raise LLMError("provider unavailable")
            if self.error_role == role + "_format":
                return {"malformed": True}
            if role == "rubric":
                return positive_rubric_alignment_response(data)
            return advertised_fact_response(self.fact(data), data) if role == "fact" else self.teacher(data)
        finally:
            with self.lock:
                self.active -= 1

    def fact(self, data):
        result = []
        for point in data["candidate"]["points"]:
            for component in (
                data.get("output_contract", {})
                .get("component_ids_by_point", {})
                .get(point["point_id"], ("explanation", "flashcard", "practice"))
            ):
                fields = point["component_evidence"][component]["component_fields"]
                field = {
                    "explanation": "explanation",
                    "flashcard": "back",
                    "practice": "expected_answer",
                }[component]
                fail = (point["point_id"], component) in self.fact_failures and FIXED not in fields[
                    field
                ]
                proof = self.proof(
                    point["point_id"],
                    component,
                    fields,
                    field,
                    point["component_evidence"][component]["allowed_support_span_ids"],
                    fail,
                )
                proof["scope_checks"] = [
                    {
                        "boundary_id": boundary["boundary_id"],
                        "is_class_definition": False,
                        "assigns_class_property": False,
                        "has_same_class_counterexample": False,
                        "candidate_explicitly_limited": False,
                        "claim_field": field,
                        "claim_quote": fields[field][:150],
                        "status": "not_applicable",
                    }
                    for boundary in point.get("scope_boundaries", [])
                ]
                result.append(proof)
        for component, item in data["lesson_components"].items():
            fields = item["component_fields"]
            field = "step_1" if component.startswith("example_") else next(iter(fields))
            fail = component in self.lesson_fact and FIXED not in fields[field]
            result.append(
                {
                    "point_id": None,
                    "component_id": component,
                    "assertion_checks": [
                        {
                            "assertion_id": row["assertion_id"],
                            "is_factual": True,
                            "source_entailment": not (fail and row["field"] == field),
                            "support_span_ids": item["allowed_support_span_ids"][:1],
                            "findings": [
                                {
                                    "kind": "invalid_inference",
                                    "quote": row["text"][:40],
                                    "issue": "该组件需要明确列出本项推理的必要步骤。",
                                    "evidence_source_ids": ["s1"],
                                }
                            ]
                            if fail and row["field"] == field
                            else [],
                        }
                        for row in item["assertion_contract"]["assertions"]
                    ],
                    "scope_matrix": [
                        {
                            "assertion_id": row["assertion_id"],
                            "checks": [
                                [False, False, False, False, None]
                                for _ in item["scope_matrix_contract"]["boundary_ids"]
                            ],
                        }
                        for row in item["assertion_contract"]["assertions"]
                    ],
                }
            )
        return {"components": result}

    @staticmethod
    def proof(point_id, component, fields, field, spans, fail):
        return {
            "point_id": point_id,
            "component_id": component,
            "source_entailment": not fail,
            "scope_exceptions": True,
            "rationale": "已经独立核对该组件自己的原文、引用和适用条件。",
            "support_span_ids": spans[:1],
            "scope_checks": [],
            "findings": [
                {
                    "kind": "invalid_inference",
                    "candidate_field": field,
                    "candidate_quote": fields[field][:40],
                    "issue": "该组件需要明确列出本项推理的必要步骤。",
                    "evidence_source_ids": ["s1"],
                }
            ]
            if fail
            else [],
        }

    def teacher(self, data):
        reviews = []
        for point in data["candidate"]["points"]:
            row = {
                "point_id": point["point_id"],
                "explanation_ok": True,
                "flashcard_ok": True,
                "practice_ok": True,
                "evidence_ok": True,
                "issues": [],
            }
            for component in ("explanation", "flashcard", "practice"):
                field = (
                    point["explanation"]
                    if component == "explanation"
                    else point[component]["back" if component == "flashcard" else "expected_answer"]
                )
                if (point["point_id"], component) in self.teacher_failures and FIXED not in field:
                    row[component + "_ok"] = False
                    row["issues"].append(component + "：必须补足题干所需的解释。")
            reviews.append(row)
        lesson = data["candidate"]["lesson"]
        checks = {
            "reading_ok": True,
            "examples_ok": True,
            "checkpoints_ok": True,
            "evidence_ok": True,
            "issues": [],
        }
        for family in self.teacher_lesson:
            items = lesson[family]
            field = (
                items[0]["steps"][0] if family == "worked_examples" else items[0]["expected_answer"]
            )
            if FIXED not in field:
                checks["examples_ok" if family == "worked_examples" else "checkpoints_ok"] = False
                checks["issues"].append(f"lesson.{family}：这一组内容必须补足独立可答的教学步骤。")
        return {
            "accepted": not any(row["issues"] for row in reviews) and not checks["issues"],
            "issues": [],
            "points": reviews,
            "lesson": checks,
        }


def generate(client, count=2, *, attempts=3, audit=None):
    bundle, chapter, evidence = course(count)
    generator = draft.TeachingContentGenerator(
        client, scope_planner=Planner(), max_attempts=attempts, audit_callback=audit
    )
    return generator.generate(bundle, chapter, evidence)


def test_teacher_starts_with_fact_first_window_and_same_candidate_is_audited(monkeypatch):
    client = CandidateClient(rendezvous=True)
    events = []
    scheduled = []
    original_map = draft.ordered_review_map

    def trace_map(function, jobs):
        jobs = list(jobs)
        scheduled.extend(job[0] for job in jobs
                         if isinstance(job, tuple) and job and isinstance(job[0], str))
        return original_map(function, jobs)

    monkeypatch.setattr(draft, "ordered_review_map", trace_map)
    with model_time_budget(20):
        output = generate(client, audit=events.append)
    assert output.teaching_review["accepted"] and output.teaching_fact_review["accepted"]
    assert client.peak >= 2
    assert scheduled[0] == "teacher"
    assert len({row[3]["candidate_sha256"] for row in client.calls}) == 1
    assert {event["candidate_sha256"] for event in events if event["event"] == "candidate"} == {
        row[3]["candidate_sha256"] for row in client.calls
    }
    assert all(row[3]["attempt"] == 1 and row[4] is not None for row in client.calls)
    assert all(row[2].startswith("draft-review") for row in client.calls)
    assert len({event["invocation_id"] for event in events}) == 1
    assert Counter(job[0] for job, *_ in client.calls) == {
        "teacher": 1, "fact": 8, "rubric": 1,
    }


def test_point_repair_is_fact_teacher_union_and_other_fields_stay_exact():
    bundle, chapter, evidence = course(2)
    ids = [point.point_id for point in bundle.knowledge_points]
    client = CandidateClient(
        fact_failures={(ids[0], "explanation")},
        teacher_failures={(ids[0], "practice"), (ids[1], "flashcard")},
    )
    events = []
    output = generate(client, audit=events.append)
    repair = next(row for row in client.generations if row["allowed_repairs"])
    assert repair["allowed_repairs"] == {ids[0]: ["explanation", "practice"], ids[1]: ["flashcard"]}
    assert len([row for row in client.generations if row["batch_points"]]) == 2
    assert len([row for row in client.generations if row["include_lesson"]]) == 1
    assert Counter(job[0] for job, *_ in client.calls) == {
        "teacher": 2, "fact": 10, "rubric": 2,
    }
    counts = Counter(job for job, *_ in client.calls)
    assert all(count == (1 if job[2] else 2) for job, count in counts.items())
    review_hashes = {row[3]["candidate_sha256"] for row in client.calls}
    assert len(review_hashes) == 2  # Both reviewers re-run on the changed snapshot.
    by_id = {point.point_id: point for point in output.knowledge_points}
    assert FIXED in by_id[ids[0]].explanation
    assert FIXED not in by_id[ids[1]].explanation
    assert FIXED not in next(card for card in output.flashcards if card.point_id == ids[0]).back
    assert FIXED in next(card for card in output.flashcards if card.point_id == ids[1]).back
    teacher_payloads = [data for (role, *_), data, *_ in client.calls if role == "teacher"]
    assert teacher_payloads[0]["previous_review_feedback"] == []
    assert all(
        "factual_point" not in row for row in teacher_payloads[1]["previous_review_feedback"]
    )
    fact_payloads = [data for (role, *_), data, *_ in client.calls if role == "fact"]
    assert all(
        "explanation_ok" not in json.dumps(data["previous_review_feedback"])
        for data in fact_payloads
    )


def test_lesson_repair_unions_fact_example_with_teacher_checkpoint_family():
    client = CandidateClient(lesson_fact={"example_1"}, teacher_lesson={"checkpoints"})
    events = []
    generate(client, audit=events.append)
    repair = next(row for row in client.generations if row["allowed_lesson_repairs"])
    assert repair["batch_points"] == []
    assert repair["allowed_lesson_repairs"] == ["checkpoints", "example_1"]
    candidates = [event["candidate"] for event in events if event["event"] == "candidate"]
    before, after = candidates
    assert before["points"] == after["points"]
    assert before["lesson"]["opening"] == after["lesson"]["opening"]
    assert before["lesson"]["summary"] == after["lesson"]["summary"]
    assert before["lesson"]["worked_examples"][1] == after["lesson"]["worked_examples"][1]
    assert before["lesson"]["worked_examples"][0] != after["lesson"]["worked_examples"][0]
    assert before["lesson"]["checkpoints"] != after["lesson"]["checkpoints"]


@pytest.mark.parametrize("role", ["teacher", "fact", "teacher_format", "fact_format", "rubric", "rubric_format"])
def test_review_error_never_generates_a_new_candidate_or_publishes(role):
    client = CandidateClient(error_role=role)
    events = []
    with pytest.raises(draft.TeachingServiceUnavailableError):
        generate(client, audit=events.append)
    assert len(client.generations) == 2
    assert all(row["previous_candidate"] is None for row in client.generations)
    assert sum(event["event"] == "candidate" for event in events) == 1
    assert not any(event["event"] == "accepted" for event in events)
    if role in {"rubric", "rubric_format"}:
        assert Counter(job[0] for job, *_ in client.calls)["rubric"] == (
            2 if role == "rubric_format" else 1
        )


def test_mandatory_rubric_batches_cover_exact_practice_ids_without_answer_or_source_payloads():
    bundle, _, _ = course(7)
    client = CandidateClient()
    output = generate(client, count=7)
    assert output.teaching_review["accepted"]
    rubric_calls = [(job, data) for job, data, *_ in client.calls if job[0] == "rubric"]
    assert len(rubric_calls) == 3
    assert sorted(len(data["items"]) for _, data in rubric_calls) == [1, 3, 3]
    assert Counter(key for job, _ in rubric_calls for key in job[1]) == Counter(
        {item.item_id: 1 for item in bundle.practice_items}
    )
    for job, data in rubric_calls:
        assert job[1] == tuple(data["output_contract"]["item_ids"])
        assert data["response_json_schema"] and data["format_feedback"] == []
        assert "sources" not in data
        assert all("expected_answer" not in item for item in data["items"])


def test_late_teacher_cannot_publish_after_other_leaf_has_failed():
    client = CandidateClient(error_role="fact", late_teacher=True)
    events = []
    try:
        with pytest.raises(draft.TeachingServiceUnavailableError):
            generate(client, audit=events.append)
        assert len(client.generations) == 2
        assert all(row["previous_candidate"] is None for row in client.generations)
        assert not any(event["event"] == "accepted" for event in events)
    finally:
        client.release_teacher.set()


def test_explicitly_failed_component_must_change_even_when_other_targets_change():
    bundle, *_ = course(2)
    point_id = bundle.knowledge_points[0].point_id
    client = CandidateClient(
        fact_failures={(point_id, "explanation")},
        teacher_failures={(point_id, "practice")},
        unchanged={(point_id, "explanation")},
    )
    with pytest.raises(draft.TeachingContentError, match="未通过"):
        generate(client, attempts=2)
    assert Counter(job[0] for job, *_ in client.calls)["teacher"] == 1


def test_explicitly_failed_example_must_change_when_teacher_checkpoint_family_changes():
    client = CandidateClient(
        lesson_fact={"example_1"}, teacher_lesson={"checkpoints"}, unchanged={(None, "example_1")}
    )
    with pytest.raises(draft.TeachingContentError, match="未通过"):
        generate(client, attempts=2)
    assert len([row for row in client.generations if row["batch_points"]]) == 1
    assert len([row for row in client.generations if row["include_lesson"]]) == 2
    assert Counter(job[0] for job, *_ in client.calls)["teacher"] == 1


@pytest.mark.parametrize("global_role", ["fact", "teacher"])
def test_global_unlocalized_rejection_is_not_masked_by_other_localized_failure(global_role):
    bundle, chapter, evidence = course(2)
    point_id = bundle.knowledge_points[0].point_id
    client = CandidateClient(
        teacher_failures={(point_id, "practice")} if global_role == "fact" else (),
        fact_failures={(point_id, "explanation")} if global_role == "teacher" else (),
    )
    generator = draft.TeachingContentGenerator(client, scope_planner=Planner())
    inputs = generator._inputs(bundle, evidence)
    for item in inputs:
        item["scope_conditions"] = []
    points = [draft._Point.model_validate(point_candidate(item)) for item in inputs]
    lesson = draft._Lesson.model_validate(lesson_candidate())
    fact, teacher = generator._review_candidate(
        points, lesson, inputs, chapter, evidence, [], [], attempt=1
    )
    if global_role == "fact":
        fact = fact.model_copy(update={"accepted": False, "issues": ["全章还有未定位的问题。"]})
    else:
        teacher = teacher.model_copy(
            update={"accepted": False, "issues": ["全章还有未定位的问题。"]}
        )
    if global_role == 'teacher':
        with pytest.raises(draft._TeachingReviewFormatError, match='未定位'):
            generator._combined_repair_plan(fact, teacher, points, lesson)
        return
    fields, lesson_targets = generator._combined_repair_plan(fact, teacher, points, lesson)
    assert fields == {point.point_id: {"explanation", "flashcard", "practice"} for point in points}
    assert lesson_targets == set(generator._lesson_components(lesson)) | {"reading_source_ids"}


def test_direct_fact_entry_preserves_probe_coverage_without_teacher():
    bundle, chapter, evidence = course(4)
    client = CandidateClient()
    generator = draft.TeachingContentGenerator(client, scope_planner=Planner())
    inputs = generator._inputs(bundle, evidence)
    for item in inputs:
        item["scope_conditions"] = []
    points = [draft._Point.model_validate(point_candidate(item)) for item in inputs]
    fact = generator._fact_review(
        points, draft._Lesson.model_validate(lesson_candidate()), inputs, chapter, evidence, []
    )
    assert fact.accepted and len(fact.points) == 4
    assert len(fact.lesson.components) == 6
    assert {job[0] for job, *_ in client.calls} == {"fact"}


def test_two_chapters_keep_deadline_and_audit_context_separate():
    def chapter(index):
        client = CandidateClient()
        events = []
        with model_time_budget(30 + index):
            deadline = _deadline.get()
            generate(client, audit=events.append)
        return client, events, deadline

    with ThreadPoolExecutor(max_workers=2) as callers:
        results = list(callers.map(chapter, [0, 1]))
    invocations = []
    for client, _events, deadline in results:
        seen = {row[3]["invocation_id"] for row in client.calls}
        assert len(seen) == 1
        invocations.extend(seen)
        assert all(row[4] == deadline for row in client.calls)
    assert len(set(invocations)) == 2


def test_single_worker_finishes_without_nested_executor_wait(monkeypatch):
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="one-review") as pool:
        monkeypatch.setattr(teaching_parallel, "_pool", (pool, 1))
        client = CandidateClient()
        output = generate(client)
        assert output.teaching_review["accepted"] and client.peak == 1


def test_repair_reuses_exact_accepted_components_but_teacher_checks_new_full_candidate():
    bundle, chapter, evidence = course(2)
    ids = [point.point_id for point in bundle.knowledge_points]
    client = CandidateClient(
        fact_failures={(ids[0], "explanation")}, teacher_failures={(ids[0], "practice")}
    )
    events = []
    result = generate(client, audit=events.append)
    facts_second = [row for row in client.calls if row[0][0] == "fact" and row[3]["attempt"] == 2]
    assert len(facts_second) == 1
    contract = facts_second[0][1]["output_contract"]
    assert contract["point_ids"] == [ids[0]]
    assert contract["component_ids_by_point"] == {ids[0]: ["explanation", "practice"]}
    assert contract["lesson_component_ids"] == []
    teachers = [row for row in client.calls if row[0][0] == "teacher"]
    assert len(teachers) == 2
    assert all(len(row[1]["candidate"]["points"]) == 2 for row in teachers)
    reused = [
        item
        for event in events
        if event["event"] == "fact_components_reused"
        for item in event["components"]
    ]
    assert (ids[0], "flashcard") in {(item["point_id"], item["component_id"]) for item in reused}
    assert result.teaching_fact_review["accepted"] is True
    assert len(result.teaching_fact_review["points"]) == 2
    assert set(result.teaching_fact_review["lesson"]["components"]) == {
        "opening",
        "summary",
        "example_1",
        "example_2",
        "checkpoint_1",
        "checkpoint_2",
    }


def test_cached_component_cannot_be_overwritten_by_extra_provider_approval():
    client = CandidateClient()
    bundle, chapter, evidence = course(1)
    generator = draft.TeachingContentGenerator(client, scope_planner=Planner())
    point = draft._Point.model_validate(point_candidate(generator._inputs(bundle, evidence)[0]))
    valid = CandidateClient.proof(
        point.point_id,
        "explanation",
        {"explanation": point.explanation},
        "explanation",
        ["fake_span"],
        False,
    )
    selection = {k: v for k, v in valid.items() if k not in {"point_id", "component_id"}}
    full = {
        "components": [
            dict(valid, component_id=name) for name in ("explanation", "flashcard", "practice")
        ]
    }
    with pytest.raises(draft.TeachingContentError, match="不能遗漏、重复"):
        generator._decode_fact_selection(
            full, [point], set(), {(point.point_id, "explanation"): selection}
        )


def test_single_changed_component_is_the_only_new_fact_selection():
    bundle, _, _ = course(2)
    point_id = bundle.knowledge_points[0].point_id
    client = CandidateClient(fact_failures={(point_id, "explanation")})
    events = []
    output = generate(client, audit=events.append)
    second = [
        data
        for (kind, *_), data, _, context, _ in client.calls
        if kind == "fact" and context["attempt"] == 2
    ]
    assert len(second) == 1
    assert second[0]["output_contract"]["component_ids_by_point"] == {point_id: ["explanation"]}
    assert len(output.teaching_fact_review["points"]) == 2
    assert all(
        set(row["components"]) == {"explanation", "flashcard", "practice"}
        for row in output.teaching_fact_review["points"]
    )
    assert len([row for row in client.calls if row[0][0] == "teacher"]) == 2


@pytest.mark.parametrize("fault", ["missing", "duplicate", "foreign"])
def test_new_component_reply_cannot_be_completed_from_unrelated_cached_approval(fault):
    bundle, chapter, evidence = course(1)
    generator = draft.TeachingContentGenerator(CandidateClient(), scope_planner=Planner())
    point = draft._Point.model_validate(point_candidate(generator._inputs(bundle, evidence)[0]))
    item = CandidateClient.proof(
        point.point_id,
        "explanation",
        {"explanation": point.explanation},
        "explanation",
        ["fake_span"],
        False,
    )
    cached = {
        (point.point_id, "explanation"): {
            k: v for k, v in item.items() if k not in {"point_id", "component_id"}
        }
    }
    fresh = [dict(item, component_id=name) for name in ("flashcard", "practice")]
    if fault == "missing":
        fresh.pop()
    elif fault == "duplicate":
        fresh.append(deepcopy(fresh[0]))
    else:
        fresh[0]["point_id"] = "not_this_point"
    with pytest.raises(draft.TeachingContentError, match="不能遗漏、重复"):
        generator._decode_fact_selection({"components": fresh}, [point], set(), cached)


def test_review_identity_change_invalidates_unchanged_fact_component_approvals():
    bundle, chapter, evidence = course(2)
    point_id = bundle.knowledge_points[0].point_id

    class ChangingReviewer(CandidateClient):
        generator = None

        def teacher(self, data):
            result = super().teacher(data)
            if FIXED not in data["candidate"]["points"][0]["explanation"]:
                self.generator.review_identity = {"provider": "independent", "model": "reviewer-v2"}
            return result

    client = ChangingReviewer(fact_failures={(point_id, "explanation")})
    generator = draft.TeachingContentGenerator(
        client,
        scope_planner=Planner(),
        review_identity={"provider": "independent", "model": "reviewer-v1"},
    )
    client.generator = generator
    generator.generate(bundle, chapter, evidence)
    second = [
        data
        for (kind, *_), data, _, context, _ in client.calls
        if kind == "fact" and context["attempt"] == 2
    ]
    point_second = [data for data in second if data["output_contract"]["point_ids"]]
    assert all(len(data["output_contract"]["point_ids"]) == 1 for data in point_second)
    assert {point_id for data in point_second for point_id in data["output_contract"]["point_ids"]} == {
        point.point_id for point in bundle.knowledge_points
    }
    assert all(
        names == ["explanation", "flashcard", "practice"]
        for data in point_second
        for names in data["output_contract"]["component_ids_by_point"].values()
    )


def test_top_level_legacy_fact_rejection_is_not_lost_in_component_reuse():
    bundle, chapter, evidence = course(1)
    client = CandidateClient()
    generator = draft.TeachingContentGenerator(client, scope_planner=Planner())
    inputs = generator._inputs(bundle, evidence)
    for item in inputs:
        item["scope_conditions"] = []
    point = draft._Point.model_validate(point_candidate(inputs[0]))
    job = generator._build_fact_jobs([point], None, inputs, chapter, evidence, [])[0]
    valid = generator._request_fact_batch(**job)
    rejected = valid.model_copy(
        update={"accepted": False, "issues": ["整批仍存在未定位的事实问题。"]}
    )
    cache = draft.VerifiedComponentReuse(
        source_texts={key: value.quote for key, value in evidence.sources.items()},
        context=generator._reuse_context(chapter, evidence),
    )
    original = generator._request_fact_batch
    generator._request_fact_batch = lambda **kwargs: rejected
    token = draft._FACT_REUSE.set(cache)
    try:
        assert generator._run_fact_job(job).accepted is False
        generator._request_fact_batch = original
        result = generator._run_fact_job(job)
    finally:
        draft._FACT_REUSE.reset(token)
    # A new model review is required; no cached true leaves may turn global
    # false into a later program-derived acceptance with zero review calls.
    assert result.accepted
    assert len(client.calls) == 2
    assert client.calls[-1][1]["output_contract"]["component_ids_by_point"] == {
        point.point_id: ["explanation", "flashcard", "practice"]
    }


def lesson_job(client, *, conditions=True):
    bundle, chapter, evidence = course(1)
    generator = draft.TeachingContentGenerator(client, scope_planner=Planner())
    inputs = generator._inputs(bundle, evidence)
    for item in inputs:
        item["scope_conditions"] = (
            [
                {
                    "boundary_type": "precondition",
                    "condition": "结论限于温度不变。",
                    "source_ids": ["s1"],
                    "scope_effect": "温度变化时不能沿用稳定结论。",
                }
            ]
            if conditions
            else []
        )
    lesson = draft._Lesson.model_validate(lesson_candidate())
    return generator, next(
        job
        for job in generator._build_fact_jobs([], lesson, inputs, chapter, evidence, [])
        if job["lesson_component_ids"] == {"summary"}
    )


@pytest.mark.parametrize(
    "fault",
    [
        "missing_assertion",
        "duplicate_assertion",
        "missing_matrix_row",
        "missing_matrix_cell",
        "foreign_proof",
        "nonboolean_flag",
        "parent_only",
    ],
)
def test_lesson_approval_cannot_skip_assertion_proofs_or_scope_matrix(fault):
    class IncompleteLesson(CandidateClient):
        def fact(self, data):
            result = super().fact(data)
            row = result["components"][0]
            if fault == "missing_assertion":
                row["assertion_checks"].pop()
            elif fault == "duplicate_assertion":
                row["assertion_checks"].append(deepcopy(row["assertion_checks"][0]))
            elif fault == "missing_matrix_row":
                row["scope_matrix"].pop()
            elif fault == "missing_matrix_cell":
                row["scope_matrix"][0]["checks"].pop()
            elif fault == "foreign_proof":
                row["assertion_checks"][0]["support_span_ids"] = ["unknown_span"]
            elif fault == "nonboolean_flag":
                row["scope_matrix"][0]["checks"][0][0] = "true"
            else:
                row.pop("assertion_checks")
                row.update(
                    source_entailment=True, scope_exceptions=True, support_span_ids=[], findings=[]
                )
            return result

    generator, job = lesson_job(IncompleteLesson())
    with pytest.raises(draft.TeachingServiceUnavailableError):
        generator._run_fact_job(job)


def test_lesson_second_unbounded_assertion_cannot_borrow_first_scoped_sentence():
    class SecondBad(CandidateClient):
        def fact(self, data):
            result = super().fact(data)
            matrix = result["components"][0]["scope_matrix"]
            matrix[0]["checks"][0] = [True, True, True, True, 0]
            matrix[1]["checks"][0] = [True, True, True, False, None]
            return result

    generator, job = lesson_job(SecondBad())
    result = generator._run_fact_job(job)
    assert not result.accepted
    proof = result.lesson.components["summary"]
    assert not proof.scope_exceptions
    assert len(proof.scope_checks) == len(proof.assertions)
    assert proof.scope_checks[0].status == "bounded"
    assert proof.scope_checks[1].status == "unbounded"
    assert proof.findings[0].candidate_quote == proof.assertions[1]["text"]


def test_lesson_cache_revalidates_full_assertion_directory_and_scope_matrix():
    generator, job = lesson_job(CandidateClient())
    evidence = job["evidence"]
    cache = draft.VerifiedComponentReuse(
        source_texts={key: value.quote for key, value in evidence.sources.items()},
        context={
            "chapter_id": "c",
            "evidence_fingerprint": "f",
            "review_identity": "r",
            "prompt_sha256": "p",
            "version": "v",
        },
    )
    token = draft._FACT_REUSE.set(cache)
    try:
        first = generator._run_fact_job(job)
        second = generator._run_fact_job(job)
    finally:
        draft._FACT_REUSE.reset(token)
    assert first.model_dump() == second.model_dump()
    assert len(generator.client.calls) == 1
    proof = second.lesson.components["summary"]
    assert len(proof.assertion_checks) == len(proof.assertions) == len(proof.scope_matrix)
    assert all(value.source_entailment for value in second.lesson.components.values())


@pytest.mark.parametrize("changed", ["scope", "source", "none"])
def test_full_scope_or_source_change_invalidates_even_unchanged_component_approvals(changed):
    bundle, chapter, evidence = course(1)
    client = CandidateClient()
    generator = draft.TeachingContentGenerator(client, scope_planner=Planner())
    inputs = generator._inputs(bundle, evidence)
    for item in inputs:
        item["scope_conditions"] = []
    points = [draft._Point.model_validate(point_candidate(item)) for item in inputs]
    lesson = draft._Lesson.model_validate(lesson_candidate())
    cache = draft.VerifiedComponentReuse(
        source_texts={key: value.quote for key, value in evidence.sources.items()},
        context=generator._reuse_context(chapter, evidence, inputs),
    )
    token = draft._FACT_REUSE.set(cache)
    try:
        assert generator._review_candidate(
            points, lesson, inputs, chapter, evidence, [], [], attempt=1
        )[0].accepted
        if changed == "scope":
            inputs[0]["scope_conditions"] = [
                {
                    "boundary_type": "precondition",
                    "condition": "结论需要温度不变。",
                    "source_ids": ["s1"],
                    "scope_effect": "温度变化时重新核对条件。",
                }
            ]
        elif changed == "source":
            evidence.sources["s1"].quote += "还应核对仪器状态是否一致。"
        second = generator._review_candidate(
            points, lesson, inputs, chapter, evidence, [], [], attempt=2
        )
    finally:
        draft._FACT_REUSE.reset(token)
    assert second[0].accepted
    second_calls = [
        (job, data) for job, data, _, context, _ in client.calls if context["attempt"] == 2
    ]
    assert len([job for job, _ in second_calls if job[0] == "teacher"]) == 1
    assert len([job for job, _ in second_calls if job[0] == "fact"]) == (
        0 if changed == "none" else 7
    )


def test_short_positive_rationales_are_not_a_reason_for_another_model_call():
    class ShortRationale(CandidateClient):
        def fact(self, data):
            result = super().fact(data)
            for row in result["components"]:
                if row["point_id"] is not None:
                    row["rationale"] = "保留有些及有的的量词范围。"
            return result

    client = ShortRationale()
    output = generate(client)
    assert output.teaching_fact_review["accepted"]
    assert len([call for call in client.calls if call[0][0] == "fact"]) == 8


def test_known_lesson_span_from_uncited_source_is_a_located_rejection_without_format_retry():
    class ForeignProof(CandidateClient):
        foreign_span = None

        def fact(self, data):
            result = super().fact(data)
            result["components"][0]["assertion_checks"][0]["support_span_ids"] = [self.foreign_span]
            return result

    client = ForeignProof()
    generator, job = lesson_job(client, conditions=False)
    from adaptive_learning.personalization.models import SourceCitation

    job["evidence"].sources["s2"] = SourceCitation(
        page_number=2, quote="另一来源中存在这条假设，引用它必须在当前学习组件显式选择该来源。"
    )
    job["spans"] = generator._support_spans(job["evidence"])
    client.foreign_span = next(key for key, row in job["spans"].items() if row["source_id"] == "s2")
    review = generator._run_fact_job(job)
    assert not review.accepted
    assert len(client.calls) == 1
    proof = review.lesson.components["summary"]
    assert not proof.source_entailment and proof.scope_exceptions
    assert proof.findings and "s2" in proof.findings[0].evidence_source_ids


@pytest.mark.parametrize(
    "extra", ["source_entailment", "scope_exceptions", "accepted", "source_ids"]
)
def test_lesson_provider_extra_parent_flags_cannot_be_erased_into_approval(extra):
    class ParentFalse(CandidateClient):
        def fact(self, data):
            result = super().fact(data)
            result["components"][0][extra] = [] if extra == "source_ids" else False
            return result

    generator, job = lesson_job(ParentFalse(), conditions=False)
    with pytest.raises(draft.TeachingServiceUnavailableError):
        generator._run_fact_job(job)


def test_complete_nonfactual_opening_needs_no_invented_quote_and_is_not_cached():
    class Arrangements(CandidateClient):
        def fact(self, data):
            result = super().fact(data)
            for row in result["components"][0]["assertion_checks"]:
                row.update(
                    is_factual=False, source_entailment=True, support_span_ids=[], findings=[]
                )
            return result

    client = Arrangements()
    generator, summary_job = lesson_job(client)
    job = generator._build_fact_jobs(
        [],
        summary_job["lesson"],
        [{"point_id": "p", "label": "测量的前提", "scope_conditions": []}],
        course(1)[1],
        summary_job["evidence"],
        [],
        lesson_component_ids={"opening"},
    )[0]
    evidence = job["evidence"]
    cache = draft.VerifiedComponentReuse(
        source_texts={key: value.quote for key, value in evidence.sources.items()},
        context={
            "chapter_id": "c",
            "evidence_fingerprint": "f",
            "review_identity": "r",
            "prompt_sha256": "p",
            "version": "v",
        },
    )
    token = draft._FACT_REUSE.set(cache)
    try:
        first = generator._run_fact_job(job)
        second = generator._run_fact_job(job)
    finally:
        draft._FACT_REUSE.reset(token)
    assert first.accepted and second.accepted
    proof = first.lesson.components["opening"]
    assert proof.assertions and all(row["is_factual"] is False for row in proof.assertion_checks)
    assert proof.source_ids == proof.support_quotes == []
    assert len(client.calls) == 2


@pytest.mark.parametrize("bad_scope", [False, True])
def test_raw_whitespace_assertion_requires_nonfactual_and_not_applicable_scope(bad_scope):
    class Whitespace(CandidateClient):
        def fact(self, data):
            result = super().fact(data)
            assertions = data["lesson_components"]["summary"]["assertion_contract"]["assertions"]
            blanks = {item["assertion_id"] for item in assertions if not item["text"].strip()}
            assert blanks
            for row in result["components"][0]["assertion_checks"]:
                if row["assertion_id"] in blanks:
                    row.update(
                        is_factual=False, source_entailment=True, support_span_ids=[], findings=[]
                    )
            if bad_scope:
                for row in result["components"][0]["scope_matrix"]:
                    if row["assertion_id"] in blanks:
                        row["checks"][0][0] = True
            return result

    client = Whitespace()
    generator, job = lesson_job(client)
    job["lesson"].summary += "\n\n"
    # Rebuild immutable assertion IDs from the exact changed body.
    item = {
        "point_id": "p",
        "label": "测量的前提",
        "scope_conditions": [
            {
                "boundary_type": "precondition",
                "condition": "结论限于温度不变。",
                "source_ids": ["s1"],
                "scope_effect": "温度变化时不能沿用稳定结论。",
            }
        ],
    }
    job = generator._build_fact_jobs(
        [],
        job["lesson"],
        [item],
        course(1)[1],
        job["evidence"],
        [],
        lesson_component_ids={"summary"},
    )[0]
    if bad_scope:
        with pytest.raises(draft.TeachingServiceUnavailableError):
            generator._run_fact_job(job)
    else:
        review = generator._run_fact_job(job)
        assert review.accepted and len(client.calls) == 1
        assert any(
            not row["text"].strip() for row in review.lesson.components["summary"].assertions
        )


@pytest.mark.parametrize("role", ["fact", "teacher"])
def test_one_reviewers_extra_global_issue_is_not_hidden_by_its_local_failure(role):
    bundle, chapter, evidence = course(2)
    target = bundle.knowledge_points[0].point_id
    client = CandidateClient(
        fact_failures={(target, "explanation")}, teacher_failures={(target, "practice")}
    )
    generator = draft.TeachingContentGenerator(client, scope_planner=Planner())
    inputs = generator._inputs(bundle, evidence)
    for item in inputs:
        item["scope_conditions"] = []
    points = [draft._Point.model_validate(point_candidate(item)) for item in inputs]
    lesson = draft._Lesson.model_validate(lesson_candidate())
    fact, teacher = generator._review_candidate(
        points, lesson, inputs, chapter, evidence, [], [], attempt=1
    )
    if role == "fact":
        fact = fact.model_copy(update={"issues": ["另有不能归入当前局部问题的全章事实缺陷。"]})
    else:
        teacher = teacher.model_copy(
            update={"issues": ["另有不能归入当前局部问题的全章教学缺陷。"]}
        )
    if role == 'teacher':
        with pytest.raises(draft._TeachingReviewFormatError, match='未定位'):
            generator._combined_repair_plan(fact, teacher, points, lesson)
        return
    fields, lesson_targets = generator._combined_repair_plan(fact, teacher, points, lesson)
    assert fields == {point.point_id: {"explanation", "flashcard", "practice"} for point in points}
    assert lesson_targets == set(generator._lesson_components(lesson)) | {"reading_source_ids"}


def test_global_fact_issue_with_a_local_failure_does_not_seed_unrelated_positive_cache():
    bundle, chapter, evidence = course(1)
    target = bundle.knowledge_points[0].point_id
    client = CandidateClient(fact_failures={(target, "explanation")})
    generator = draft.TeachingContentGenerator(client, scope_planner=Planner())
    inputs = generator._inputs(bundle, evidence)
    points = [draft._Point.model_validate(point_candidate(item)) for item in inputs]
    job = generator._build_fact_jobs(points, None, inputs, chapter, evidence, [])[0]
    rejected = generator._request_fact_batch(**job).model_copy(
        update={"issues": ["此外同批练习还有未定位的事实问题，必须继续审核。"]}
    )
    assert not rejected.accepted and not rejected.points[0].components.explanation.source_entailment
    cache = draft.VerifiedComponentReuse(
        source_texts={key: value.quote for key, value in evidence.sources.items()},
        context=generator._reuse_context(chapter, evidence, inputs),
    )
    original = generator._request_fact_batch
    generator._request_fact_batch = lambda **kwargs: rejected
    token = draft._FACT_REUSE.set(cache)
    try:
        generator._run_fact_job(job)
        generator._request_fact_batch = original
        generator._run_fact_job(job)
    finally:
        draft._FACT_REUSE.reset(token)
    assert client.calls[-1][1]["output_contract"]["component_ids_by_point"] == {
        target: ["explanation", "flashcard", "practice"]
    }


@pytest.mark.parametrize(
    "case,index,accepted",
    [("summary_bad", 0, False), ("summary_bad", 1, False), ("summary_good", 0, True)],
)
def test_actual_summary_json_keeps_own_source_proof_and_scope_rejection_separate(
    case, index, accepted
):
    from pathlib import Path

    from adaptive_learning.ingestion.models import ChapterDraft
    from adaptive_learning.personalization.models import SourceCitation
    from adaptive_learning.personalization.teaching_evidence import TeachingEvidence

    data = json.loads(
        (Path(__file__).parent / "fixtures/teaching_v15_summary_review_regression.json").read_text()
    )
    common, selected = data["common"], data["cases"][case]

    class StoredReview:
        calls = 0

        def structured(self, **kwargs):
            self.calls += 1
            return deepcopy(selected["responses"][index])

    client = StoredReview()
    generator = draft.TeachingContentGenerator(client, scope_planner=Planner())
    sources = common["evidence"]
    evidence = TeachingEvidence(
        sources={
            key: SourceCitation.model_validate(value) for key, value in sources["sources"].items()
        },
        point_sources=sources["point_sources"],
        warnings=sources["warnings"],
        fingerprint=sources["fingerprint"],
    )
    job = generator._build_fact_jobs(
        [],
        draft._Lesson.model_validate(selected["lesson"]),
        common["inputs"],
        ChapterDraft.model_validate(common["chapter"]),
        evidence,
        [],
        lesson_component_ids={"summary"},
    )[0]
    review = generator._run_fact_job(job)
    assert review.accepted is accepted
    assert client.calls == 1
    proof = review.lesson.components["summary"]
    assert proof.source_entailment is True
    assert proof.scope_exceptions is accepted
    assert proof.support_quotes
    assert all(
        item.quote in evidence.sources[item.source_id].quote for item in proof.support_quotes
    )
    if not accepted:
        assert any(
            check.status == "unbounded" and "转录在细胞核内" in check.claim_quote
            for check in proof.scope_checks
        )
        assert any(
            finding.kind == "scope_overreach" and "转录在细胞核内" in finding.candidate_quote
            for finding in proof.findings
        )
    else:
        assert proof.findings == []


def test_scope_finding_cannot_keep_source_true_without_its_own_negative_matrix_cell():
    from pathlib import Path

    from adaptive_learning.ingestion.models import ChapterDraft
    from adaptive_learning.personalization.models import SourceCitation
    from adaptive_learning.personalization.teaching_evidence import TeachingEvidence

    data = json.loads(
        (Path(__file__).parent / "fixtures/teaching_v15_summary_review_regression.json").read_text()
    )
    common, selected = data["common"], data["cases"]["summary_bad"]
    response = deepcopy(selected["responses"][0])
    for row in response["components"][0]["scope_matrix"]:
        row["checks"] = [[False, False, False, False, None] for _ in row["checks"]]

    class InconsistentReview:
        def structured(self, **kwargs):
            return deepcopy(response)

    generator = draft.TeachingContentGenerator(InconsistentReview(), scope_planner=Planner())
    sources = common["evidence"]
    evidence = TeachingEvidence(
        sources={
            key: SourceCitation.model_validate(value) for key, value in sources["sources"].items()
        },
        point_sources=sources["point_sources"],
        warnings=sources["warnings"],
        fingerprint=sources["fingerprint"],
    )
    job = generator._build_fact_jobs(
        [],
        draft._Lesson.model_validate(selected["lesson"]),
        common["inputs"],
        ChapterDraft.model_validate(common["chapter"]),
        evidence,
        [],
        lesson_component_ids={"summary"},
    )[0]
    with pytest.raises(draft.TeachingServiceUnavailableError):
        generator._run_fact_job(job)
