from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

from app.core.config import get_settings
from app.core.limits import heavy_task_limiter


@pytest.fixture(autouse=True)
def _bookcourse_test_env(monkeypatch, tmp_path) -> Iterator[None]:
    # Run legacy tests against the original optional/demo auth mode unless the
    # test itself overrides BOOKCOURSE_AUTH_MODE.
    monkeypatch.setenv("BOOKCOURSE_AUTH_MODE", "optional")
    monkeypatch.setenv("BOOKCOURSE_PERSIST_STATE", "false")
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", os.environ.get("BOOKCOURSE_STORAGE_ROOT", str(tmp_path)))
    monkeypatch.setenv("BOOKCOURSE_USE_WORKER", "false")
    monkeypatch.setenv("BOOKCOURSE_GLOBAL_RATE_PER_MINUTE", "600")
    monkeypatch.setenv("BOOKCOURSE_WRITE_RATE_PER_MINUTE", "600")
    monkeypatch.setenv("BOOKCOURSE_RAG_CACHE_ENABLED", "true")
    get_settings.cache_clear()
    # Clear persisted state caches used by service modules.
    from app.services.kv_store import _PersistedKVStore  # local import to avoid cycles
    from app.rag.cache import clear_rag_cache
    clear_rag_cache()

    yield
    heavy_task_limiter.reset()
    clear_rag_cache()
    get_settings.cache_clear()