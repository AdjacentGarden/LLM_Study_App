"""Writer leaves overlap; lesson assembly waits for every ordered point draft."""

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Barrier, Event, Lock

import pytest

from adaptive_learning.llm import teaching_parallel
from adaptive_learning.personalization import teaching_content as teaching
from test_teaching_content import Client, course, lesson_candidate


@pytest.fixture
def writer_pool(monkeypatch):
    with ThreadPoolExecutor(max_workers=3) as pool:
        monkeypatch.setattr(teaching_parallel, "_pool", (pool, 3))
        yield


def test_point_batches_overlap_keep_audit_context_and_lesson_waits_for_all(writer_pool):
    bundle, chapter, evidence = course(13)
    ids = [point.point_id for point in bundle.knowledge_points]
    context = ContextVar("writer-test-invocation", default="missing")
    barrier, final_finished, lock = Barrier(3), Event(), Lock()
    active = peak = 0
    completed, lesson_payloads, audit = [], [], []
    delegate = Client()

    class ParallelClient:
        def structured(self, *, system, user, **kwargs):
            nonlocal active, peak
            assert context.get() == "writer-invocation"
            payload = json.loads(user)
            if system != teaching.GENERATION_PROMPT:
                return delegate.structured(system=system, user=user, **kwargs)
            batch = [row["point_id"] for row in payload["batch_points"]]
            if not batch:
                # Neither an unfinished point nor a completion-order draft may
                # be substituted into this dependent whole-chapter request.
                assert active == 0 and set(completed) == set(ids)
                assert [point["point_id"] for point in payload["existing_point_content"]] == ids
                lesson_payloads.append(payload)
                return delegate.structured(system=system, user=user, **kwargs)
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                barrier.wait(timeout=5)  # A serial implementation cannot pass.
                if batch[0] == ids[0]:
                    assert final_finished.wait(timeout=5)
                result = delegate.structured(system=system, user=user, **kwargs)
                with lock:
                    completed.extend(batch)
                if batch[0] == ids[12]:
                    final_finished.set()
                return result
            finally:
                with lock:
                    active -= 1

    def record(event):
        assert context.get() == "writer-invocation"
        audit.append(event)

    token = context.set("writer-invocation")
    try:
        result = teaching.TeachingContentGenerator(
            ParallelClient(), audit_callback=record
        ).generate(bundle, chapter, evidence)
    finally:
        context.reset(token)
    assert peak == 3 and active == 0
    assert completed.index(ids[12]) < completed.index(ids[0])
    assert len(lesson_payloads) == 1
    candidate, = [event for event in audit if event["event"] == "candidate"]
    assert [point["point_id"] for point in candidate["candidate"]["points"]] == ids
    assert {event["invocation_id"] for event in audit} == {audit[0]["invocation_id"]}
    assert result.teaching_review["accepted"]
    assert audit[-1]["event"] == "accepted"


def test_parallel_failed_batch_retries_only_itself_and_never_publishes_partial(writer_pool):
    bundle, chapter, evidence = course(13)
    ids = [point.point_id for point in bundle.knowledge_points]
    batches = [tuple(ids[:6]), tuple(ids[6:12]), tuple(ids[12:])]
    delegate, barrier, lock = Client(), Barrier(3), Lock()
    calls, audit = Counter(), []

    class PartialFailure:
        def structured(self, *, system, user, **kwargs):
            payload = json.loads(user)
            batch = tuple(row["point_id"] for row in payload.get("batch_points", []))
            if system == teaching.GENERATION_PROMPT and batch:
                with lock:
                    calls[batch] += 1
                    attempt = calls[batch]
                if attempt == 1:
                    barrier.wait(timeout=5)
                result = delegate.structured(system=system, user=user, **kwargs)
                if batch == batches[1] and attempt == 1:
                    result["points"] = []
                return result
            if system == teaching.GENERATION_PROMPT:
                assert calls == {batches[0]: 1, batches[1]: 2, batches[2]: 1}
                assert [point["point_id"] for point in payload["existing_point_content"]] == ids
            return delegate.structured(system=system, user=user, **kwargs)

    result = teaching.TeachingContentGenerator(
        PartialFailure(), audit_callback=audit.append
    ).generate(bundle, chapter, evidence)
    assert calls == {batches[0]: 1, batches[1]: 2, batches[2]: 1}
    assert [event["attempt"] for event in audit if event["event"] == "candidate"] == [2]
    assert sum(event["event"] == "accepted" for event in audit) == 1
    assert result.teaching_review["accepted"]


def test_short_final_example_step_feedback_names_exact_field_and_existing_minimum():
    _, _, evidence = course()
    raw = lesson_candidate()
    raw["worked_examples"][1]["steps"][-1] = "所以重新测量。"
    lesson = teaching._Lesson.model_validate(raw)
    with pytest.raises(teaching.TeachingContentError) as caught:
        teaching.TeachingContentGenerator._validate_lesson(lesson, [], evidence)
    message = str(caught.value)
    assert "lesson.worked_examples[1].steps[1]" in message
    assert "至少需要12个字符" in message
    assert "末步" in message
    lesson.worked_examples[1].steps[-1] = "题设中温度发生改变因此需要重新测量体积。"
    teaching.TeachingContentGenerator._validate_lesson(lesson, [], evidence)


@pytest.mark.parametrize("configured,expected", [(None, 6), ("1", 1), ("2", 2), ("6", 6)])
def test_configured_writer_batch_size_keeps_compatible_default(monkeypatch, configured, expected):
    if configured is None:
        monkeypatch.delenv("TEACHING_POINT_BATCH_SIZE", raising=False)
    else:
        monkeypatch.setenv("TEACHING_POINT_BATCH_SIZE", configured)
    assert teaching._configured_point_batch_size() == expected


@pytest.mark.parametrize("configured", ["0", "7", "2.5", "maximum"])
def test_invalid_writer_batch_size_fails_before_any_model_call(monkeypatch, configured):
    monkeypatch.setenv("TEACHING_POINT_BATCH_SIZE", configured)
    bundle, chapter, evidence = course()
    client = Client()
    with pytest.raises(ValueError, match="TEACHING_POINT_BATCH_SIZE"):
        teaching.TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    assert client.payloads == []


def test_seventeen_points_in_two_point_batches_fill_all_eight_leaf_slots(monkeypatch):
    monkeypatch.setenv("TEACHING_POINT_BATCH_SIZE", "2")
    bundle, chapter, evidence = course(17)
    ids = [point.point_id for point in bundle.knowledge_points]
    delegate, barrier, lock = Client(), Barrier(8), Lock()
    active = peak = started = finished = 0
    lesson_payloads, batch_ids = [], []

    class EightWayWriter:
        def structured(self, *, system, user, **kwargs):
            nonlocal active, peak, started, finished
            payload = json.loads(user)
            if system != teaching.GENERATION_PROMPT:
                return delegate.structured(system=system, user=user, **kwargs)
            batch = [row["point_id"] for row in payload["batch_points"]]
            if not batch:
                assert active == 0 and finished == 9
                assert [row["point_id"] for row in payload["existing_point_content"]] == ids
                lesson_payloads.append(payload)
                return delegate.structured(system=system, user=user, **kwargs)
            with lock:
                active += 1
                started += 1
                call_number = started
                peak = max(peak, active)
                batch_ids.append(tuple(batch))
            try:
                if call_number <= 8:
                    # All eight pool slots must enter the writer simultaneously.
                    barrier.wait(timeout=5)
                return delegate.structured(system=system, user=user, **kwargs)
            finally:
                with lock:
                    active -= 1
                    finished += 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        monkeypatch.setattr(teaching_parallel, "_pool", (pool, 8))
        result = teaching.TeachingContentGenerator(EightWayWriter()).generate(
            bundle, chapter, evidence
        )
    assert peak == 8 and active == 0 and started == finished == 9
    assert Counter(batch_ids) == {
        tuple(ids[offset:offset + 2]): 1 for offset in range(0, len(ids), 2)
    }
    assert len(lesson_payloads) == 1
    assert result.teaching_review["accepted"]
