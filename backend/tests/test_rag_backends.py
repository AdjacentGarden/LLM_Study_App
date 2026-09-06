from __future__ import annotations

from typing import Any

import pytest

from adaptive_learning.rag.backends import _require_free_gpu_memory
from adaptive_learning.rag.index import RAGIndexError


class FakeCuda:
    def __init__(self, free_mib: int) -> None:
        self.free_mib = free_mib
        self.calls = 0

    def mem_get_info(self, _: object) -> tuple[int, int]:
        self.calls += 1
        mib = 1024 * 1024
        return self.free_mib * mib, 24_000 * mib


class FakeTorch:
    def __init__(self, free_mib: int) -> None:
        self.cuda = FakeCuda(free_mib)

    @staticmethod
    def device(value: str) -> str:
        return value


def test_gpu_memory_guard_rejects_unsafe_model_load() -> None:
    torch: Any = FakeTorch(1000)

    with pytest.raises(RAGIndexError, match="insufficient"):
        _require_free_gpu_memory(torch, "cuda", 2048)


def test_gpu_memory_guard_accepts_sufficient_capacity() -> None:
    torch: Any = FakeTorch(4096)

    _require_free_gpu_memory(torch, "cuda:0", 2048)

    assert torch.cuda.calls == 1


def test_gpu_memory_guard_does_not_probe_cpu() -> None:
    torch: Any = FakeTorch(0)

    _require_free_gpu_memory(torch, "cpu", 2048)

    assert torch.cuda.calls == 0
