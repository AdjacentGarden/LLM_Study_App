from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from adaptive_learning.api import qa_dependency


def test_concurrent_cold_start_builds_one_qa_service(monkeypatch: Any) -> None:
    sentinel = object()
    calls = 0

    def create() -> object:
        nonlocal calls
        calls += 1
        time.sleep(0.02)
        return sentinel

    monkeypatch.setattr(qa_dependency, "_service", None)
    monkeypatch.setattr(qa_dependency, "_create_qa_service", create)

    with ThreadPoolExecutor(max_workers=20) as executor:
        services = list(executor.map(lambda _: qa_dependency.build_qa_service(), range(40)))

    assert calls == 1
    assert all(service is sentinel for service in services)
