"""Production startup must not install a phantom PDF or prepared interview."""
import importlib

from fastapi.testclient import TestClient

from adaptive_learning.api.store import MemoryStore


def test_startup_does_not_install_demo_book(monkeypatch):
    api = importlib.import_module("adaptive_learning.api.app")
    clean_store = MemoryStore()
    monkeypatch.setattr(api, "store", clean_store)
    with TestClient(api.app) as client:
        assert client.get("/api/health").status_code == 200
        assert clean_store.books == {}
        assert clean_store.interviews == {}
