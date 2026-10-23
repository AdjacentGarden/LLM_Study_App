from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from adaptive_learning.rag.service import QABusyError, TextbookQAService
from adaptive_learning.rag.grounded_qa import GroundedAnswerGenerator
from test_qa_service import FakeClient, FakeIndex


def service(**kwargs):
    client = FakeClient()
    return TextbookQAService(
        book_id="test-book", index=FakeIndex(3), generator=GroundedAnswerGenerator(client), **kwargs
    ), client


def test_cache_is_bounded_and_returns_independent_objects():
    qa, client = service(cache_size=1)
    original = qa.answer("Q1")
    original.claims.clear()
    cached = qa.answer(" Q1 ")
    assert cached.cache_hit and len(cached.claims) == 1
    assert cached.generation_duration_ms == 0
    qa.answer("Q2")
    qa.answer("Q1")
    assert client.calls == 3


def test_cache_expires_and_does_not_cache_refusals():
    qa, client = service(cache_seconds=0.01)
    qa.answer("Q1")
    time.sleep(0.02)
    qa.answer("Q1")
    assert client.calls == 2
    qa.index.score = -1
    qa.answer("unrelated")
    qa.index.score = 3
    assert qa.answer("unrelated").status == "supported"


def test_simultaneous_identical_questions_generate_once():
    qa, client = service()
    entered, release = threading.Event(), threading.Event()
    original = qa._answer

    def slow(question):
        entered.set()
        assert release.wait(2)
        return original(question)

    qa._answer = slow
    with ThreadPoolExecutor(max_workers=6) as pool:
        first = pool.submit(qa.answer, "same")
        assert entered.wait(1)
        others = [pool.submit(qa.answer, "same") for _ in range(5)]
        release.set()
        results = [first.result()] + [future.result() for future in others]
    assert client.calls == 1
    assert len({id(item) for item in results}) == 6


def test_admission_control_and_failure_release():
    qa, _ = service(max_concurrent=1, wait_seconds=0.01)
    entered, release = threading.Event(), threading.Event()
    original = qa._answer

    def failing(question):
        entered.set()
        assert release.wait(2)
        raise RuntimeError("provider unavailable")

    qa._answer = failing
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(qa.answer, "same")
        assert entered.wait(1)
        with pytest.raises(QABusyError):
            qa.answer("different")
        with pytest.raises(QABusyError):
            qa.answer("same")
        release.set()
        with pytest.raises(RuntimeError):
            first.result()
    qa._answer = original
    assert qa.answer("same").status == "supported"
    assert qa._inflight == {}


def test_book_services_never_share_answers():
    first, a = service()
    second, b = service()
    first.answer("same")
    second.answer("same")
    assert a.calls == b.calls == 1


@pytest.mark.parametrize("question", ["", "   ", "x" * 2001])
def test_service_validates_inputs(question):
    qa, client = service()
    with pytest.raises(ValueError):
        qa.answer(question)
    assert client.calls == 0
