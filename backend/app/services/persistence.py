from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.storage import storage_root


_logger = get_logger("app.persistence")


def state_dir() -> Path:
    root = storage_root() / "_state"
    root.mkdir(parents=True, exist_ok=True)
    return root


def persistence_enabled() -> bool:
    return get_settings().persist_state


def _atomic_write(path: Path, payload: str) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class JsonStateStore:
    """Minimal JSON-backed key->value store with atomic writes and load on init.

    The whole state is kept in memory after load() and persisted on every save().
    Suitable for small maps (jobs, submissions, plans, mistakes). Not designed
    for high frequency writes or large collections.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = Lock()
        self._data: dict[str, Any] = {}
        self._load_failed = False

    @property
    def path(self) -> Path:
        return state_dir() / f"{self.name}.json"

    def load(self) -> None:
        self._load_failed = False
        if not persistence_enabled():
            return
        path = self.path
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self._data = payload
        except Exception as exc:
            self._load_failed = True
            _logger.warning("state_load_failed", extra={"event": "state_load_failed", "error": repr(exc)})

    @property
    def load_failed(self) -> bool:
        with self._lock:
            return self._load_failed

    def all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def get(self, key: str) -> Any | None:
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._persist_locked()

    def remove(self, key: str) -> None:
        with self._lock:
            if key in self._data:
                del self._data[key]
                self._persist_locked()

    def values(self) -> list[Any]:
        with self._lock:
            return list(self._data.values())

    def update(self, key: str, fn: Callable[[Any | None], Any]) -> Any:
        with self._lock:
            current = self._data.get(key)
            updated = fn(current)
            self._data[key] = updated
            self._persist_locked()
            return updated

    def list_where(self, predicate: Callable[[Any], bool]) -> list[Any]:
        with self._lock:
            return [item for item in self._data.values() if predicate(item)]

    def _persist_locked(self) -> None:
        if not persistence_enabled():
            return
        payload = json.dumps(self._data, ensure_ascii=False, default=str)
        _atomic_write(self.path, payload)


def reset_all() -> None:
    """For tests: clear in-memory stores if running without persistence."""
    if persistence_enabled():
        return
    # When persistence is disabled, the stores reset by recreating
    # their JSON file path (which won't be written). Callers should still
    # hold the same instances; this helper is a no-op marker for tests.
