from native_harness import api, structure
from test_native_api import native as native
from test_native_api import uploaded


def expose(client):
    client.app.dependency_overrides[api.require_chapter_reconstructor] = lambda: None
    client.app.add_api_route(
        "/api/books/{book_id}/process/reprocess", api.reprocess_book, methods=["POST"]
    )
    client.app.add_api_route(
        "/api/books/{book_id}/structure", api.generate_book_structure, methods=["POST"]
    )


def test_ready_reprocess_is_explicit_owner_only_and_cannot_replace_active_lease(native):
    client, other, _ = native
    expose(client)
    book = uploaded(client)
    path = f"/api/books/{book}/process/reprocess"
    assert other.post(path).status_code == 403
    # The ordinary retry contract does not change.
    assert client.post(f"/api/books/{book}/process/retry").status_code == 409
    response = client.post(path)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"
    assert client.post(path).status_code == 409
    active = api.job_repository.claim_next(owner="reprocessing-test", lease_seconds=60)
    assert active is not None and active.book_id == book
    assert client.post(path).status_code == 409
    current = api.job_repository.get_job(book)
    assert current.lease_owner == "reprocessing-test" and current.status == "running"


def test_acquiring_a_book_does_not_grant_source_refresh_or_reprocess_permission(native):
    client, other, _ = native
    expose(client)
    book = uploaded(client)
    owner = api.account_repository.session_owner(other.cookies.get("zhiwo_account"))
    canonical = api.account_repository.repo.register_asset(
        {"book_id": book, "title": "Shared source"}, "shared-source", None
    )
    api.account_repository.repo.add_book(owner, canonical)
    assert other.post(f"/api/books/{book}/process/reprocess").status_code == 403
    assert other.post(f"/api/books/{book}/structure?refresh=true").status_code == 403


def test_structure_refresh_requires_original_owner_and_preserves_other_sources(native):
    client, other, _ = native
    expose(client)
    book = uploaded(client)
    original = api.job_repository.get_structure(book)
    updated = structure().model_copy(update={"title": "Rebuilt verified structure"})

    class Reconstructor:
        calls = 0

        def reconstruct_book(self, pages, title):
            self.calls += 1
            assert len(pages) == original.source_page_count
            return updated

    reconstructor = Reconstructor()
    client.app.dependency_overrides[api.require_chapter_reconstructor] = lambda: reconstructor
    try:
        assert client.post(f"/api/books/{book}/structure").json()["title"] == original.title
        assert reconstructor.calls == 0
        assert other.post(f"/api/books/{book}/structure?refresh=true").status_code == 403
        rebuilt = client.post(f"/api/books/{book}/structure?refresh=true")
        assert rebuilt.status_code == 200, rebuilt.text
        assert rebuilt.json()["title"] == updated.title
        assert reconstructor.calls == 1
        assert api.job_repository.get_structure(book).title == updated.title
    finally:
        client.app.dependency_overrides.clear()
