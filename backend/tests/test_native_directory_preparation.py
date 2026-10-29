"""Native directory availability and durable preparation are separate contracts.

All model adapters here are explicit test boundaries; no production approval or
supplier output is substituted, and no remote services are called.
"""
from types import SimpleNamespace

from native_harness import FixtureBank, api
from test_native_api import native as native
from test_native_api import uploaded, workspace

from adaptive_learning.assessment.diagnostic_cache import DiagnosticRepairPending
from adaptive_learning.assessment.item_generation import diagnostic_bank_key
from adaptive_learning.ingestion.preparation import PreparationQueue
from adaptive_learning.llm.client import LLMError


def setup_resource(native, monkeypatch):
    client, _, root = native
    book = uploaded(client)
    resource = workspace(client, book, "workspace_directory_pending")
    queue = PreparationQueue(root / "isolated-preparation.sqlite3", [
        ("indexing", lambda _: None), ("structuring", lambda _: None),
        ("diagnostics", lambda _: None),
    ])
    monkeypatch.setattr(api, "preparation_queue", queue)
    return client, book, resource, queue


def path(resource):
    return f"/api/workspaces/{resource['workspace_id']}/resources/{resource['id']}/session"


def edit(client, resource, value):
    actor = api.account_repository.session_owner(client.cookies.get("zhiwo_account"))
    api.native_repository.save_structure(actor, resource["workspace_id"], resource["id"],
        resource["revision"], value)


def no_models(*args, **kwargs):
    raise AssertionError("ordinary session must only enqueue the durable preparation worker")


def test_ordinary_directory_is_available_while_one_durable_task_is_pending(native, monkeypatch):
    client, book, resource, queue = setup_resource(native, monkeypatch)
    monkeypatch.setattr(api, "item_generator", SimpleNamespace(generate=no_models))
    for _ in range(2):
        response = client.post(path(resource))
        assert response.status_code == 409
        assert response.headers["X-CloudPath-Error-Code"] == "diagnostic_preparation_pending"
        assert response.headers["Retry-After"] == "5"
    assert queue.get(book)["status"] == "queued"
    with queue.connect() as db:
        assert db.execute("SELECT count(*) FROM preparation").fetchone()[0] == 1
    actual = client.get(path(resource).removesuffix("/session")).json()
    assert actual["structure"]["chapters"] == resource["structure"]["chapters"]
    assert actual["session_id"] is None and actual["courses"] == []
    assert api.assessment_repository.get_bank(diagnostic_bank_key(book)) is None


def test_ordinary_failed_preparation_is_not_silently_restarted(native, monkeypatch):
    client, book, resource, queue = setup_resource(native, monkeypatch)
    queue.enqueue(book)
    queue._update(book, "failed", "diagnostics", 2, "private failure detail")
    before = queue.get(book)
    monkeypatch.setattr(api, "item_generator", SimpleNamespace(generate=no_models))
    response = client.post(path(resource))
    assert response.status_code == 502
    assert response.headers["X-CloudPath-Error-Code"] == "diagnostic_generation_rejected"
    assert "private failure" not in response.text
    assert queue.get(book) == before
    assert client.post(path(resource) + "?retry=true").status_code == 422
    retried = client.post(path(resource) + "?retry=true&retry_request_id=explicit_directory_retry")
    assert retried.status_code == 409
    assert queue.get(book)["status"] == "queued" and queue.get(book)["next_step"] == 2


def test_ordinary_current_bank_creates_session_without_model_call(native, monkeypatch):
    client, book, resource, queue = setup_resource(native, monkeypatch)
    source = api.job_repository.get_structure(book)
    bank = FixtureBank().generate(source)
    api.assessment_repository.save_bank(book_id=diagnostic_bank_key(book),
        structure_fingerprint=api.diagnostic_fingerprint(source), items=bank)
    monkeypatch.setattr(api, "item_generator", SimpleNamespace(generate=no_models))
    response = client.post(path(resource))
    assert response.status_code == 200
    assert queue.get(book) is None
    attached = client.get(path(resource).removesuffix("/session")).json()
    assert attached["session_id"] == response.json()["session_id"]
    restored = client.post(path(resource) + "?resource_revision=0")
    assert restored.status_code == 200 and restored.json()["session_id"] == attached["session_id"]


def test_custom_directory_pending_has_explicit_backoff_without_borrowing_original_bank(native, monkeypatch):
    client, book, resource, queue = setup_resource(native, monkeypatch)
    original = api.job_repository.get_structure(book)
    api.assessment_repository.save_bank(book_id=diagnostic_bank_key(book),
        structure_fingerprint=api.diagnostic_fingerprint(original), items=FixtureBank().generate(original))
    custom = original.model_copy(update={"title": "用户确认后的独立目录"})
    edit(client, resource, custom)
    calls = []
    def pending(value, *, chapter_cache, cache_scope):
        calls.append((value.title, cache_scope))
        raise DiagnosticRepairPending(completed=1, total=3, retry_after=30)
    monkeypatch.setattr(api, "item_generator", SimpleNamespace(generate=pending))
    response = client.post(path(resource) + "?resource_revision=1")
    assert response.status_code == 409
    assert response.headers["X-CloudPath-Error-Code"] == "diagnostic_resource_preparation_pending"
    assert response.headers["Retry-After"] == "30" and calls == [(custom.title, book)]
    assert queue.get(book) is None
    assert client.get(path(resource).removesuffix("/session")).json()["session_id"] is None


def test_custom_known_pending_can_finish_but_unknown_never_becomes_pending(native, monkeypatch):
    client, book, resource, queue = setup_resource(native, monkeypatch)
    source = api.job_repository.get_structure(book).model_copy(update={"title": "已确认目录"})
    edit(client, resource, source)
    calls = []
    def generate(value, **kwargs):
        calls.append(value.title)
        if len(calls) == 1:
            raise DiagnosticRepairPending(completed=2, total=3, retry_after=5)
        return FixtureBank().generate(value)
    monkeypatch.setattr(api, "item_generator", SimpleNamespace(generate=generate))
    url = path(resource) + "?resource_revision=1"
    assert client.post(url).headers["X-CloudPath-Error-Code"] == "diagnostic_resource_preparation_pending"
    complete = client.post(url)
    assert complete.status_code == 200 and len(calls) == 2
    key = diagnostic_bank_key(f"native:{resource['id']}:1")
    assert api.assessment_repository.get_bank(key, expected_fingerprint=api.diagnostic_fingerprint(source))
    assert api.assessment_repository.get_bank(diagnostic_bank_key(book)) is None
    assert queue.get(book) is None


def test_custom_unknown_response_is_explicitly_uncertain(native, monkeypatch):
    client, book, resource, queue = setup_resource(native, monkeypatch)
    edit(client, resource, api.job_repository.get_structure(book).model_copy(update={"title": "自定目录"}))
    def unknown(*args, **kwargs):
        raise LLMError("private unknown supplier response")
    monkeypatch.setattr(api, "item_generator", SimpleNamespace(generate=unknown))
    response = client.post(path(resource) + "?resource_revision=1")
    assert response.status_code == 409
    assert response.headers["X-CloudPath-Error-Code"] == "diagnostic_submission_uncertain"
    assert "Retry-After" not in response.headers and "private" not in response.text
    assert queue.get(book) is None


def test_revision_change_stops_continuation_before_any_submission(native, monkeypatch):
    client, book, resource, queue = setup_resource(native, monkeypatch)
    edit(client, resource, api.job_repository.get_structure(book).model_copy(update={"title": "新目录"}))
    monkeypatch.setattr(api, "item_generator", SimpleNamespace(generate=no_models))
    response = client.post(path(resource) + "?resource_revision=0")
    assert response.status_code == 409
    assert response.headers["X-CloudPath-Error-Code"] == "resource_revision_changed"
    assert queue.get(book) is None
