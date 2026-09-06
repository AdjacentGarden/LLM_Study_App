from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

from app.rag.cache import RagAnswerCache


def test_rag_answer_cache_coalesces_concurrent_identical_requests() -> None:
    cache: RagAnswerCache[str, str] = RagAnswerCache(capacity=4, ttl_seconds=30)
    builder_entered = threading.Event()
    release_builder = threading.Event()
    calls = 0

    def builder() -> str:
        nonlocal calls
        calls += 1
        builder_entered.set()
        assert release_builder.wait(timeout=2)
        return "answer"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get_or_compute, "same", builder)
        assert builder_entered.wait(timeout=1)
        second = pool.submit(cache.get_or_compute, "same", builder)
        deadline = time.monotonic() + 1
        while cache.snapshot()["coalesced"] != 1 and time.monotonic() < deadline:
            threading.Event().wait(0.005)
        release_builder.set()
        assert first.result(timeout=1) == ("answer", False)
        assert second.result(timeout=1) == ("answer", True)

    assert calls == 1
    assert cache.snapshot() == {
        "size": 1,
        "capacity": 4,
        "ttl_seconds": 30,
        "inflight": 0,
        "hits": 1,
        "misses": 1,
        "coalesced": 1,
        "evictions": 0,
    }
