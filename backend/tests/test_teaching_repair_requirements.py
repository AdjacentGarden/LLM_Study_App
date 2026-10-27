"""Offline integration regressions for allowed scope versus required changes.

The fake provider still traverses production writer, fact, teacher and rubric
validation. Its positive proof fixtures are mechanical, not teaching approval.
Dispatch uses roles and candidate content, never concurrent call order.
"""

from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from test_teaching_content import course, lesson_candidate, point_candidate
from test_teaching_v15_integration import FIXED, CandidateClient, Planner

from adaptive_learning.llm import teaching_parallel
from adaptive_learning.personalization import teaching_content as teaching


@pytest.fixture(autouse=True)
def isolated_leaf_pool(monkeypatch):
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="repair-requirements") as pool:
        monkeypatch.setattr(teaching_parallel, "_pool", (pool, 4))
        yield


class RequirementsClient(CandidateClient):
    def __init__(
        self,
        target,
        *,
        rejection="global",
        edits=None,
        edit_example=False,
        factual_failure=False,
        repeat_rejection=False,
        malformed_once=False,
        malformed_always=False,
    ):
        super().__init__(fact_failures={(target, "explanation")} if factual_failure else ())
        self.target = target
        self.rejection = rejection
        self.edits = dict(edits or {})
        self.edit_example = edit_example
        self.repeat_rejection = repeat_rejection
        self.malformed_once = malformed_once
        self.malformed_always = malformed_always
        self.sent_malformed = False
        self.teacher_requests = []

    def structured(self, *, system, user, **kwargs):
        if system != teaching.GENERATION_PROMPT:
            return super().structured(system=system, user=user, **kwargs)
        data = json.loads(user)
        with self.lock:
            self.generations.append(deepcopy(data))
        previous = data["previous_candidate"] or {}
        old_points = {point["point_id"]: point for point in previous.get("points", [])}
        points = [
            deepcopy(old_points[item["point_id"]])
            if item["point_id"] in old_points
            else point_candidate(item)
            for item in data["batch_points"]
        ]
        lesson = None
        if data["include_lesson"]:
            lesson = deepcopy(previous.get("lesson") or lesson_candidate())
        if data["previous_candidate"] is not None:
            for point in points:
                for component in self.edits.get(point["point_id"], ()):
                    if component == "explanation":
                        field, owner = "explanation", point
                    else:
                        field = "back" if component == "flashcard" else "expected_answer"
                        owner = point[component]
                    if FIXED not in owner[field]:
                        owner[field] += FIXED
            if lesson is not None and self.edit_example:
                if FIXED not in lesson["worked_examples"][0]["steps"][0]:
                    lesson["worked_examples"][0]["steps"][0] += FIXED
        return {"points": points, "lesson": lesson}

    def teacher(self, data):
        with self.lock:
            self.teacher_requests.append(deepcopy(data))
        changed = FIXED in json.dumps(data["candidate"], ensure_ascii=False)
        if changed and (
            self.malformed_always or (self.malformed_once and not self.sent_malformed)
        ):
            self.sent_malformed = True
            return {"malformed": True}
        result = super().teacher(data)
        if changed and not self.repeat_rejection:
            return result
        result["accepted"] = False
        if self.rejection == "owner":
            row = next(row for row in result["points"] if row["point_id"] == self.target)
            row["evidence_ok"] = False
            row["issues"] = ["该知识点仍有未定位到具体字段的教学问题。"]
        elif self.rejection == "category":
            result["lesson"]["examples_ok"] = False
            result["lesson"]["issues"] = ["这一组例题仍需要完善推导。"]
        else:
            result["issues"] = ["全章仍有未定位的教学问题，需要修改。"]
            if self.rejection == "exact_and_global":
                row = next(row for row in result["points"] if row["point_id"] == self.target)
                item_id = next(
                    point["practice"]["item_id"]
                    for point in data["candidate"]["points"]
                    if point["point_id"] == self.target
                )
                row["practice_ok"] = False
                row["issues"] = [f"practice_{item_id}：这道练习的答案必须修改。"]
        return result


def case(*, count=2, target_index=0, edit_component="explanation", **options):
    bundle, chapter, evidence = course(count)
    target = bundle.knowledge_points[target_index].point_id
    edits = {} if edit_component is None else {target: {edit_component}}
    client = RequirementsClient(target, edits=edits, **options)
    events = []
    generator = teaching.TeachingContentGenerator(
        client, scope_planner=Planner(), max_attempts=3, audit_callback=events.append
    )
    return generator, client, (bundle, chapter, evidence), events


def candidates(events):
    return [event["candidate"] for event in events if event["event"] == "candidate"]


def changed_point_components(before, after):
    changed = set()
    for old, new in zip(before["points"], after["points"], strict=True):
        assert old["point_id"] == new["point_id"]
        if any(old[field] != new[field] for field in ("title", "explanation", "source_ids")):
            changed.add((old["point_id"], "explanation"))
        for field in ("flashcard", "practice"):
            if old[field] != new[field]:
                changed.add((old["point_id"], field))
    return changed


def test_unknown_global_allows_only_actual_bad_component_to_change_then_full_reaudit():
    generator, client, args, events = case(factual_failure=True)
    result = generator.generate(*args)
    before, after = candidates(events)
    assert changed_point_components(before, after) == {(client.target, "explanation")}
    assert before["lesson"] == after["lesson"]
    assert len(client.teacher_requests) == 2
    assert client.teacher_requests[1]["candidate"] == after
    assert len(client.teacher_requests[1]["candidate"]["points"]) == 2
    assert result.teaching_review["accepted"] is True
    assert result.teaching_fact_review["accepted"] is True
    assert len([event for event in events if event["event"] == "accepted"]) == 1


def test_unknown_owner_requires_one_change_in_owner_without_rewriting_good_siblings():
    generator, client, args, events = case(rejection="owner", edit_component="flashcard")
    generator.generate(*args)
    before, after = candidates(events)
    assert changed_point_components(before, after) == {(client.target, "flashcard")}
    assert before["lesson"] == after["lesson"]
    repairs = [row for row in client.generations if row["previous_candidate"] is not None]
    assert len(repairs) == 1
    assert repairs[0]["output_contract"]["point_ids"] == [client.target]
    assert set(repairs[0]["allowed_repairs"][client.target]) == {
        "explanation", "flashcard", "practice"
    }
    assert len(client.teacher_requests) == 2


def test_failed_example_category_can_change_only_first_example_then_full_reaudit():
    generator, client, args, events = case(
        rejection="category", edit_component=None, edit_example=True
    )
    generator.generate(*args)
    before, after = candidates(events)
    assert before["points"] == after["points"]
    for field in ("opening", "summary", "reading_source_ids", "checkpoints"):
        assert before["lesson"][field] == after["lesson"][field]
    assert before["lesson"]["worked_examples"][1] == after["lesson"]["worked_examples"][1]
    assert before["lesson"]["worked_examples"][0] != after["lesson"]["worked_examples"][0]
    repairs = [row for row in client.generations if row["previous_candidate"] is not None]
    assert len(repairs) == 1 and repairs[0]["batch_points"] == []
    assert len(client.teacher_requests) == 2


@pytest.mark.parametrize("rejection", ["global", "owner", "category"])
def test_unchanged_candidate_or_required_owner_category_is_bounded_rejection(rejection):
    generator, client, args, events = case(rejection=rejection, edit_component=None)
    with pytest.raises(teaching.TeachingContentError):
        generator.generate(*args)
    assert len(candidates(events)) == 1
    assert len(client.teacher_requests) == 1
    assert not any(event["event"] == "accepted" for event in events)
    assert len(client.generations) <= 6  # Three bounded rounds; no hidden regeneration loop.


def test_global_change_does_not_satisfy_an_independent_exact_practice_requirement():
    generator, client, args, events = case(rejection="exact_and_global")
    with pytest.raises(teaching.TeachingContentError):
        generator.generate(*args)
    assert len(candidates(events)) == 1
    assert len(client.teacher_requests) == 1
    assert any(row["previous_candidate"] is not None for row in client.generations)
    assert not any(event["event"] == "accepted" for event in events)


def test_global_guard_waits_for_third_point_batch_instead_of_rejecting_unchanged_prefix():
    generator, client, args, events = case(count=13, target_index=12, factual_failure=True)
    generator.generate(*args)
    before, after = candidates(events)
    assert changed_point_components(before, after) == {(client.target, "explanation")}
    assert before["lesson"] == after["lesson"]
    point_repairs = [
        row for row in client.generations
        if row["previous_candidate"] is not None and row["batch_points"]
    ]
    assert Counter(len(row["batch_points"]) for row in point_repairs) == {6: 2, 1: 1}
    single, = [row for row in point_repairs if len(row["batch_points"]) == 1]
    assert single["output_contract"]["point_ids"] == [client.target]
    assert len(client.teacher_requests) == 2
    assert all(len(row["candidate"]["points"]) == 13 for row in client.teacher_requests)


def test_format_retry_keeps_same_candidate_and_next_rejection_uses_latest_baseline():
    generator, client, args, events = case(malformed_once=True, repeat_rejection=True)
    with pytest.raises(teaching.TeachingContentError):
        generator.generate(*args)
    before, changed = candidates(events)
    assert changed_point_components(before, changed) == {(client.target, "explanation")}
    assert len(client.teacher_requests) == 3
    assert client.teacher_requests[1]["candidate"] == client.teacher_requests[2]["candidate"]
    assert client.teacher_requests[1]["format_feedback"] == []
    assert client.teacher_requests[2]["format_feedback"]
    # Round three repeats the latest rejected content. An old/cleared baseline
    # would incorrectly count round two's change a second time and review it.
    assert len(client.generations) == 6
    assert FIXED in client.generations[-2]["previous_candidate"]["points"][0]["explanation"]
    assert not any(event["event"] == "accepted" for event in events)


def test_exhausted_review_format_error_stops_without_another_writer_round():
    generator, client, args, events = case(malformed_always=True)
    with pytest.raises(teaching.TeachingServiceUnavailableError):
        generator.generate(*args)
    assert len(candidates(events)) == 2
    assert len(client.generations) == 4
    assert len(client.teacher_requests) == 3  # Initial rejection plus two format attempts.
    roles = Counter(job[0] for job, *_ in client.calls)
    assert roles["teacher"] == 3
    assert not any(event["event"] == "accepted" for event in events)
