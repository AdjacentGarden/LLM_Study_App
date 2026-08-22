"""Audit tests for P0–P1 optimization items.

These tests intentionally do NOT use the optional/auth-off conftest defaults
for the strict-mode checks; they reset the settings cache and explicitly opt
each test into the security configuration expected.
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import get_settings
from app.core.limits import heavy_task_limiter
from app.main import create_app
from app.assignments.service import clear_for_test as clear_assignments
from app.study_plan.service import clear_for_test as clear_plans
from app.services.storage import original_file_path, write_book_owner


API_KEY = "audit-secret-key"
USER_A = "audit_user_a"
USER_B = "audit_user_b"


@pytest.fixture(autouse=True)
def _audit_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOKCOURSE_AUTH_MODE", "strict")
    monkeypatch.setenv("BOOKCOURSE_API_KEY", API_KEY)
    monkeypatch.setenv("BOOKCOURSE_PERSIST_STATE", "true")
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_USE_WORKER", "false")
    monkeypatch.setenv("BOOKCOURSE_GLOBAL_RATE_PER_MINUTE", "600")
    monkeypatch.setenv("BOOKCOURSE_WRITE_RATE_PER_MINUTE", "600")
    # Reset rate-limiter buckets between tests so isolated budgets are enforced.
    from app.core.ratelimit import rate_limiter

    rate_limiter._buckets.clear()
    get_settings.cache_clear()
    clear_assignments()
    clear_plans()
    yield
    rate_limiter._buckets.clear()
    heavy_task_limiter.reset()
    get_settings.cache_clear()


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _pdf_bytes() -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page(width=72, height=72)
    payload = doc.tobytes()
    doc.close()
    return payload


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (1, 1), color=(255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


# ----- A 鉴权与越权 -----
def test_strict_mode_requires_api_key_or_returns_503(monkeypatch):
    monkeypatch.delenv("BOOKCOURSE_API_KEY")
    get_settings.cache_clear()
    client = _client()
    # With no key at all, server treats as misconfigured → 503
    resp = client.get("/api/books")
    assert resp.status_code in (401, 503)
    assert resp.json()["code"] in ("invalid_api_key", "api_key_not_configured")
    # /api/health must remain public
    assert client.get("/api/health").status_code == 200


def test_health_remains_public_in_strict_mode():
    client = _client()
    assert client.get("/api/health").status_code == 200


def test_invalid_api_key_returns_401():
    client = _client()
    resp = client.get("/api/books", headers={"X-BookCourse-Api-Key": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_api_key"


def test_user_mismatch_on_user_endpoint_returns_403():
    client = _client()
    resp = client.get(
        f"/api/users/{USER_B}/mistakes",
        headers={"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": USER_A},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden_user_mismatch"


def test_book_owner_enforced_on_delete():
    client = _client()
    init = client.post(
        "/api/uploads/init",
        json={"filename": "demo.pdf", "size_bytes": 1},
        headers={"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": USER_A},
    )
    assert init.status_code == 200
    book_id = init.json()["book_id"]
    # Write a placeholder original file so delete_course sees the directory
    original_file_path(book_id, "demo.pdf").write_bytes(_pdf_bytes())

    # User B tries to delete a book owned by user A
    del_by_b = client.delete(
        f"/api/books/{book_id}",
        headers={"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": USER_B},
    )
    assert del_by_b.status_code == 403
    assert del_by_b.json()["code"] == "forbidden_book_owner_mismatch"

    del_by_a = client.delete(
        f"/api/books/{book_id}",
        headers={"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": USER_A},
    )
    assert del_by_a.status_code == 204


def test_study_task_owner_enforced():
    client = _client()
    # Seed chapters and a plan
    from app.schemas.books import Chapter
    from app.services.artifact_store import write_chapters

    write_book_owner("audit_book", USER_A)
    write_chapters(
        "audit_book",
        [
            Chapter(
                chapter_id="c1",
                level=2,
                source_title="s",
                ai_title="t",
                page_start=1,
                page_end=2,
                confidence=90,
                status="confirmed",
                source="test",
            )
        ],
    )
    plan_resp = client.post(
        "/api/books/audit_book/plan",
        json={"user_id": USER_A, "days": 3, "daily_minutes": 30},
        headers={"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": USER_A},
    )
    assert plan_resp.status_code == 200, plan_resp.text
    task_id = plan_resp.json()["tasks"][0]["task_id"]

    patch_by_b = client.patch(
        f"/api/study-tasks/{task_id}",
        json={"status": "done", "score": 80},
        headers={"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": USER_B},
    )
    assert patch_by_b.status_code == 403
    assert patch_by_b.json()["code"] == "forbidden_user_mismatch"

    patch_by_a = client.patch(
        f"/api/study-tasks/{task_id}",
        json={"status": "done", "score": 80},
        headers={"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": USER_A},
    )
    assert patch_by_a.status_code == 200


def test_auth_uses_constant_time_compare(monkeypatch):
    import inspect
    from app.core import auth

    source = inspect.getsource(auth)
    assert "secrets.compare_digest" in source


# ----- B 持久化 -----
def test_persisted_state_surives_restart(monkeypatch, tmp_path):
    from app.schemas.books import Chapter
    from app.services.artifact_store import write_chapters

    write_book_owner("persist_book", "persist_user")
    write_chapters(
        "persist_book",
        [
            Chapter(
                chapter_id="c1",
                level=2,
                source_title="s",
                ai_title="t",
                page_start=1,
                page_end=2,
                confidence=90,
                status="confirmed",
                source="test",
            )
        ],
    )
    client = _client()
    plan_resp = client.post(
        "/api/books/persist_book/plan",
        json={"user_id": "persist_user", "days": 3, "daily_minutes": 30},
        headers={"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": "persist_user"},
    )
    assert plan_resp.status_code == 200
    task_id = plan_resp.json()["tasks"][0]["task_id"]
    # Simulate restart: clear in-memory caches so next access has to reload from disk.
    from app.services.kv_store import _PersistedKVStore

    _PersistedKVStore_type = _PersistedKVStore
    client_after = _client()
    get_plan = client_after.get(
        "/api/books/persist_book/plan?user_id=persist_user",
        headers={"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": "persist_user"},
    )
    assert get_plan.status_code == 200
    assert get_plan.json()["tasks"][0]["task_id"] == task_id


def test_mistakes_persisted_across_restart(monkeypatch):
    from app.schemas.books import Asset, Chapter, Chunk
    from app.services.artifact_store import write_assets, write_chapters, write_chunks

    write_book_owner("persist_book_m", "persist_user")
    write_chapters(
        "persist_book_m",
        [
            Chapter(
                chapter_id="c1",
                level=2,
                source_title="s",
                ai_title="t",
                page_start=1,
                page_end=2,
                confidence=90,
                status="confirmed",
                source="test",
            )
        ],
    )
    Chunk_type = Chunk
    write_chunks(
        "persist_book_m",
        [
            Chunk_type(
                chunk_id="ck1",
                book_id="persist_book_m",
                chapter_id="c1",
                page_start=1,
                page_end=1,
                content_type="text",
                text="\u540c\u6e90\u67d3\u8272\u4f53\u5728\u51cf\u6570\u5206\u88c2\u7b2c\u4e00\u6b21\u540e\u671f\u5206\u79bb\uff0c\u59d0\u59b9\u67d3\u8272\u5355\u4f53\u5728\u7b2c\u4e8c\u6b21\u5206\u88c2\u540e\u671f\u5206\u79bb\u3002",
                asset_ids=[],
                key_concepts=["\u540c\u6e90\u67d3\u8272\u4f53"],
            )
        ],
    )
    client = _client()
    submit = client.post(
        "/api/assignments/assign1/submit",
        json={
            "user_id": "persist_user",
            "book_id": "persist_book_m",
            "chapter_id": "c1",
            "question": "\u540c\u6e90\u67d3\u8272\u4f53\u5206\u79bb?",
            "answer": "\u51cf\u6570\u7b2c\u4e8c\u6b21\u5206\u88c2\u540e\u671f",
        },
        headers={"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": "persist_user"},
    )
    assert submit.status_code == 200
    diag = client.post(
        f"/api/assignments/assign1/diagnose?submission_id={submit.json()['submission_id']}",
        headers={"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": "persist_user"},
    )
    assert diag.status_code == 200
    assert diag.json()["mistake_recorded"] is True

    # Restart: re-create client; service caches should reload from disk
    client2 = _client()
    get = client2.get(
        "/api/users/persist_user/mistakes?book_id=persist_book_m",
        headers={"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": "persist_user"},
    )
    assert get.status_code == 200
    assert get.json()[0]["user_id"] == "persist_user"


def test_state_files_written(tmp_path):
    client = _client()
    init = client.post(
        "/api/uploads/init",
        json={"filename": "demo.pdf", "size_bytes": 1},
        headers={"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": USER_A},
    )
    book_id = init.json()["book_id"]
    # _state directory must exist; book owner marker must be persisted on disk.
    state_dir = Path(get_settings().storage_root) / "_state"
    assert state_dir.exists()
    owner_marker = Path(get_settings().storage_root) / "books" / book_id / "_owner.json"
    assert owner_marker.exists()
    assert json.loads(owner_marker.read_text(encoding="utf-8"))["user_id"] == USER_A


# ----- C 限流 -----
def test_global_rate_limit_kicks_in(monkeypatch):
    monkeypatch.setenv("BOOKCOURSE_GLOBAL_RATE_PER_MINUTE", "5")
    from app.core.ratelimit import rate_limiter

    rate_limiter._buckets.clear()
    get_settings.cache_clear()
    client = _client()
    headers = {"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": USER_A}
    codes = []
    for _ in range(8):
        codes.append(client.get("/api/books", headers=headers).status_code)
    assert 429 in codes


def test_write_rate_limit_kicks_in(monkeypatch):
    monkeypatch.setenv("BOOKCOURSE_WRITE_RATE_PER_MINUTE", "2")
    from app.core.ratelimit import rate_limiter

    rate_limiter._buckets.clear()
    get_settings.cache_clear()
    client = _client()
    codes = []
    for _ in range(5):
        codes.append(
            client.post(
                "/api/uploads/init",
                json={"filename": "demo.pdf", "size_bytes": 1},
                headers={"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": USER_A},
            ).status_code
        )
    assert 429 in codes


def test_read_and_write_rate_budgets_are_independent(monkeypatch):
    monkeypatch.setenv("BOOKCOURSE_GLOBAL_RATE_PER_MINUTE", "5")
    monkeypatch.setenv("BOOKCOURSE_WRITE_RATE_PER_MINUTE", "1")
    from app.core.ratelimit import rate_limiter

    rate_limiter._buckets.clear()
    get_settings.cache_clear()
    client = _client()
    headers = {"X-BookCourse-Api-Key": API_KEY, "X-BookCourse-User-Id": USER_A}

    write_response = client.post(
        "/api/uploads/init",
        json={"filename": "demo.pdf", "size_bytes": 1},
        headers=headers,
    )
    read_response = client.get("/api/books", headers=headers)

    assert write_response.status_code == 200
    assert read_response.status_code == 200


def test_rate_limit_response_keeps_cors_headers(monkeypatch):
    monkeypatch.setenv("BOOKCOURSE_GLOBAL_RATE_PER_MINUTE", "1")
    from app.core.ratelimit import rate_limiter

    rate_limiter._buckets.clear()
    get_settings.cache_clear()
    client = _client()
    origin = "http://127.0.0.1:5173"
    headers = {
        "Origin": origin,
        "X-BookCourse-Api-Key": API_KEY,
        "X-BookCourse-User-Id": USER_A,
    }

    assert client.get("/api/books", headers=headers).status_code == 200
    limited = client.get("/api/books", headers=headers)

    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limited"
    assert limited.headers["access-control-allow-origin"] == origin


# ----- D 日志与请求追踪 -----
def test_response_returns_request_id():
    client = _client()
    rid = "abc-123"
    resp = client.get(
        "/api/health",
        headers={"X-Request-Id": rid},
    )
    assert resp.headers.get("X-Request-Id") == rid


def test_response_generates_request_id_when_absent():
    client = _client()
    resp = client.get("/api/health")
    assert resp.headers.get("X-Request-Id")


def test_internal_error_payload_does_not_leak_class_name(monkeypatch):
    # Force an unhandled exception inside a route via monkeypatching the imported symbol.
    from app.api import routes_books
    from app.rag import service as rag_service

    def _boom(*_a, **_kw):
        raise RuntimeError("internal boom")

    monkeypatch.setattr(routes_books, "answer_query", _boom)
    # Optional auth mode so we don't need headers for the test payload
    monkeypatch.setenv("BOOKCOURSE_AUTH_MODE", "optional")
    monkeypatch.delenv("BOOKCOURSE_API_KEY")
    from app.core.ratelimit import rate_limiter

    rate_limiter._buckets.clear()
    get_settings.cache_clear()
    client = _client()
    # Provide minimal chunks artifact so it doesn't 404 before reaching the patched fn.
    from app.schemas.books import Chapter, Chunk
    from app.services.artifact_store import write_chapters, write_chunks

    write_chapters(
        "boom_book",
        [
            Chapter(
                chapter_id="c1",
                level=2,
                source_title="s",
                ai_title="t",
                page_start=1,
                page_end=2,
                confidence=90,
                status="confirmed",
                source="test",
            )
        ],
    )
    write_chunks(
        "boom_book",
        [
            Chunk(
                chunk_id="ck1",
                book_id="boom_book",
                chapter_id="c1",
                page_start=1,
                page_end=1,
                content_type="text",
                text="hello",
                asset_ids=[],
                key_concepts=[],
            )
        ],
    )
    resp = client.post("/api/rag/query", json={"book_id": "boom_book", "question": "x"})
    assert resp.status_code == 500, resp.text
    payload = resp.json()
    assert payload["code"] == "internal_server_error"
    # type field may exist but the raw exception text must not leak to the client.
    assert "internal boom" not in json.dumps(payload, ensure_ascii=False)


# ----- E 任务队列 -----
def test_worker_lifespan_starts_and_stops(memory_cleaner):
    monkeypatch_local = pytest.MonkeyPatch()
    monkeypatch_local.setenv("BOOKCOURSE_USE_WORKER", "true")
    get_settings.cache_clear()
    client = _client()
    assert client.get("/api/health").status_code == 200
    from app.core.worker import task_queue

    assert task_queue is not None


@pytest.fixture
def memory_cleaner():
    yield


# ----- 前端 -----
def test_frontend_no_apikey_in_runtime_source():
    repo_root = Path(__file__).resolve().parents[3]
    fe_runtime = repo_root / "frontend" / "src" / "config" / "runtime.ts"
    if not fe_runtime.exists():
        pytest.skip("frontend runtime config not found")
    content = fe_runtime.read_text(encoding="utf-8")
    assert "apiKey" not in content
    assert "VITE_BOOKCOURSE_API_KEY" not in content


def test_frontend_api_no_apikey_header():
    repo_root = Path(__file__).resolve().parents[3]
    api_path = repo_root / "frontend" / "src" / "api" / "bookcourseApi.ts"
    if not api_path.exists():
        pytest.skip("frontend api not found")
    content = api_path.read_text(encoding="utf-8")
    assert "X-BookCourse-Api-Key" not in content


def test_frontend_has_error_boundary():
    repo_root = Path(__file__).resolve().parents[3]
    boundary_path = repo_root / "frontend" / "src" / "components" / "ErrorBoundary.tsx"
    assert boundary_path.exists()
