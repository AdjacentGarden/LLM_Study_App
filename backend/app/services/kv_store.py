from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from app.services.persistence import JsonStateStore


class _PersistedKVStore:
    """A dict-like helper backed by JsonStateStore with lazy load."""

    def __init__(self, name: str) -> None:
        self._store = JsonStateStore(name)
        self._loaded = False
        self._lock = Lock()
        self._data: dict[str, Any] = {}

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._store.load()
            self._data = dict(self._store.all())
            self._loaded = True

    def reload(self) -> None:
        with self._lock:
            self._loaded = False
            self._data.clear()

    @property
    def load_failed(self) -> bool:
        self._ensure_loaded()
        return self._store.load_failed

    def keys(self) -> list[str]:
        self._ensure_loaded()
        with self._lock:
            return list(self._data.keys())

    def values(self) -> list[Any]:
        self._ensure_loaded()
        with self._lock:
            return list(self._data.values())

    def get(self, key: str) -> Any | None:
        self._ensure_loaded()
        with self._lock:
            return self._data.get(key)

    def upsert(self, key: str, value: Any) -> Any:
        self._ensure_loaded()
        with self._lock:
            self._data[key] = value
            self._store.set(key, value)
            return value

    def update_in_place(self, key: str, fn: Callable[[Any | None], Any]) -> Any:
        self._ensure_loaded()
        with self._lock:
            current = self._data.get(key)
            updated = fn(current)
            self._data[key] = updated
            self._store.set(key, updated)
            return updated

    def with_value_lock(self, key: str, fn: Callable[[Any | None], Any]) -> Any:
        """Run a read/side-effect callback while this store's mutation lock is held.

        This is used for compare-and-publish boundaries: a generation can be
        validated and its filesystem publication completed before another
        local caller can supersede the same persisted record.
        """

        self._ensure_loaded()
        with self._lock:
            return fn(self._data.get(key))

    def remove(self, key: str) -> None:
        self._ensure_loaded()
        with self._lock:
            if key in self._data:
                del self._data[key]
                self._store.remove(key)

    def clear_cache(self) -> None:
        self._loaded = False
        with self._lock:
            self._data.clear()
