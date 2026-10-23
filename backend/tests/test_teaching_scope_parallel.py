"""Scope batches stay independent, bounded, ordered, and audit-context aware."""
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Barrier, Lock

import pytest

from adaptive_learning.llm import teaching_parallel
from adaptive_learning.personalization.models import SourceCitation
from adaptive_learning.personalization.teaching_evidence import TeachingEvidence
from adaptive_learning.personalization.teaching_scope import (
    SCOPE_PLANNING_PROMPT,
    TeachingScopePlanner,
    TeachingScopeServiceError,
)


def chapter(prefix, count):
    inputs = [{"point_id": f"{prefix}{i}", "label": "条件范围",
               "point_source_ids": ["anchor"], "scope_context_source_ids": []}
              for i in range(count)]
    evidence = TeachingEvidence(
        sources={"anchor": SourceCitation(page_number=1, quote="给定条件成立时结论才适用。")},
        point_sources={row["point_id"]: ["anchor"] for row in inputs},
        warnings=[], fingerprint="independent-scope-fixture")
    return inputs, evidence


@pytest.fixture
def bounded_pool(monkeypatch):
    # Isolate this test's capacity from any earlier suite-wide configuration.
    with ThreadPoolExecutor(max_workers=2) as pool:
        monkeypatch.setattr(teaching_parallel, "_pool", (pool, 2))
        yield


def answer(payload):
    return {"points": [{"point_id": row["point_id"], "comparisons": [], "conditions": []}
                       for row in payload["knowledge_points"]]}


def test_two_chapters_share_bound_and_keep_invocation_context_and_input_order(bounded_pool):
    context = ContextVar("scope-test-invocation", default="missing")
    barrier, lock = Barrier(2), Lock()
    active = peak = 0
    seen = []

    class Client:
        def structured(self, **kwargs):
            nonlocal active, peak
            payload = json.loads(kwargs["user"])
            ids = [row["point_id"] for row in payload["knowledge_points"]]
            assert kwargs["system"] == SCOPE_PLANNING_PROMPT
            assert context.get() == ids[0][0]
            with lock:
                active += 1
                peak = max(peak, active)
                seen.append(tuple(ids))
            try:
                # Serial scope calls cannot pass this rendezvous.
                barrier.wait(timeout=5)
                return answer(payload)
            finally:
                with lock:
                    active -= 1

    def run(prefix):
        token = context.set(prefix)
        try:
            inputs, evidence = chapter(prefix, 12)
            result = TeachingScopePlanner(Client()).plan(inputs, evidence)
            assert list(result) == [row["point_id"] for row in inputs]
            return result
        finally:
            context.reset(token)

    with ThreadPoolExecutor(max_workers=2) as callers:
        first, second = callers.submit(run, "A"), callers.submit(run, "B")
        assert len(first.result(timeout=15)) == len(second.result(timeout=15)) == 12
    assert peak == 2 and active == 0
    assert len(seen) == len(set(seen)) == 6
    assert context.get() == "missing"


def test_format_repair_is_local_to_its_batch_and_preserves_valid_sibling(bounded_pool):
    lock, calls = Lock(), []

    class Client:
        def structured(self, **kwargs):
            payload = json.loads(kwargs["user"])
            ids = tuple(row["point_id"] for row in payload["knowledge_points"])
            with lock:
                calls.append((ids, payload["format_feedback"]))
                number = sum(item[0] == ids for item in calls)
            if ids[0] == "A4" and number == 1:
                return {"points": []}
            return answer(payload)

    inputs, evidence = chapter("A", 8)
    assert list(TeachingScopePlanner(Client()).plan(inputs, evidence)) == [row["point_id"] for row in inputs]
    assert Counter(ids for ids, _ in calls) == {tuple(f"A{i}" for i in range(4)): 1,
                                             tuple(f"A{i}" for i in range(4, 8)): 2}
    assert [bool(feedback) for ids, feedback in calls if ids[0] == "A4"] == [False, True]


def test_failed_batch_never_returns_partial_plan_or_retries_valid_batch(bounded_pool):
    barrier, lock, calls = Barrier(2), Lock(), []

    class Client:
        def structured(self, **kwargs):
            payload = json.loads(kwargs["user"])
            ids = tuple(row["point_id"] for row in payload["knowledge_points"])
            with lock:
                calls.append(ids)
            barrier.wait(timeout=5)
            return answer(payload) if ids[0] == "A0" else {"points": []}

    inputs, evidence = chapter("A", 8)
    with pytest.raises(TeachingScopeServiceError, match="有界格式纠错"):
        TeachingScopePlanner(Client(), max_attempts=1).plan(inputs, evidence)
    assert len(calls) == 2
