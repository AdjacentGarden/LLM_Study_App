"""Isolated explicit discovery publication; never touches live user books or models."""

from fastapi.testclient import TestClient
from native_harness import api, structure, textbook_pdf
from PIL import Image
from test_native_api import native as native


def uploaded(client, name="owned.pdf", parsed=True, payload=None):
    response = client.post("/api/native/uploads", files={"file": (name, payload or textbook_pdf(), "application/pdf")})
    assert response.status_code == 200, response.text
    book_id = response.json()["book_id"]
    if parsed:
        api.job_repository.save_structure(book_id, structure())
    return book_id


def publish(client, book_id, description="My explicit PDF share"):
    return client.post("/api/community/share", json={
        "kind": "book", "book_id": book_id, "description": description, "rights_confirmed": True,
    })


def test_candidates_are_only_current_uploaders_parsed_real_pdfs_and_gets_have_no_side_effects(native):
    a, b, root = native
    ready = uploaded(a)
    pending = uploaded(a, "pending.pdf", parsed=False)
    other = uploaded(b, "other.pdf")
    missing = uploaded(a, "missing.pdf")
    api.job_repository.get_book(missing).file_path.unlink()
    repo = api.account_repository.repo
    assert repo.asset(ready) is None
    response = a.get("/api/community/book-share-candidates")
    assert response.status_code == 200, response.text
    candidates = response.json()
    assert [item["book_id"] for item in candidates] == [ready]
    assert candidates[0]["title"] == "测试生物教材"
    assert candidates[0]["page_count"] == 3 and candidates[0]["chapter_count"] == 1
    assert candidates[0]["publication_status"] == "unshared" and candidates[0]["post_id"] is None
    assert candidates[0]["can_withdraw"] is False
    assert [item["book_id"] for item in b.get("/api/community/book-share-candidates").json()] == [other]
    assert a.get(f"/api/community/books/{other}/publication").status_code == 403
    assert b.get(f"/api/community/books/{ready}/publication").status_code == 403
    assert repo.asset(ready) is None and a.get("/api/library").json() == []
    with repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0


def test_native_upload_publishes_without_claim_or_tutorial_and_can_be_imported_then_withdrawn(native):
    a, b, root = native
    a.app.get("/api/books/{book_id}/cover")(api.book_cover)
    book_id = uploaded(a)
    repo = api.account_repository.repo
    owner = api.account_repository.session_owner(a.cookies.get("zhiwo_account"))
    assert repo.asset(book_id) is None
    directory = root / "covers"
    directory.mkdir(exist_ok=True)
    Image.new("RGB", (80, 120), "purple").save(directory / f"{book_id}.jpg")
    with TestClient(a.app) as anonymous:
        assert anonymous.get(f"/api/books/{book_id}/cover").status_code in {403, 404}
        response = publish(a, book_id)
        assert response.status_code == 200, response.text
        post = response.json()["post_id"]
        assert response.json()["status"] == "shared"
        assert repo.owns(owner, book_id) and repo.asset(book_id) is not None
        # No diagnostics, generated chapter bundle, private notes or learner profile are needed/copied.
        with api.assessment_repository._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM course_bundles").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM interview_sessions").fetchone()[0] == 0
        assert publish(a, book_id).json() == {"post_id": post, "status": "already_shared"}
        state = a.get(f"/api/community/books/{book_id}/publication").json()
        assert state == {"book_id": book_id, "post_id": post, "publication_status": "shared", "can_withdraw": True}
        assert a.get("/api/community/book-share-candidates").json()[0]["post_id"] == post
        public_cover = anonymous.get(f"/api/books/{book_id}/cover")
        assert public_cover.status_code == 200 and public_cover.headers["cache-control"] == "private, no-store"
        catalog_id = "community_post_" + post
        catalog = b.get("/api/native/catalog").json()
        entry = next(item for item in catalog if item["id"] == catalog_id)
        assert entry["cover"].startswith(f"/api/books/{book_id}/cover?v=")
        assert entry["chapters"] == [] and entry["flashcardCount"] == 0
        assert b.get(f"/api/books/{book_id}/source").status_code == 403  # Public cover does not expose PDF.
        imported = b.post(f"/api/native/catalog/{catalog_id}/import", json={
            "id": "workspace_actual_shared_pdf", "name": "Shared book",
        })
        assert imported.status_code == 200, imported.text
        assert imported.json()["resources"][0]["book_id"] == book_id
        original = b.get(f"/api/books/{book_id}/source")
        assert original.status_code == 200 and original.content == api.job_repository.get_book(book_id).file_path.read_bytes()
        assert b.post(f"/api/community/{post}/withdraw").status_code == 403
        assert a.post(f"/api/community/{post}/withdraw").status_code == 200
        assert a.get(f"/api/community/books/{book_id}/publication").json()["publication_status"] == "unshared"
        assert anonymous.get(f"/api/books/{book_id}/cover").status_code in {403, 404}
        assert all(item["id"] != catalog_id for item in b.get("/api/native/catalog").json())
        denied = b.post(f"/api/native/catalog/{catalog_id}/import", json={
            "id": "workspace_after_withdrawal", "name": "Withdrawn",
        })
        assert denied.status_code == 404
        assert b.get(f"/api/books/{book_id}/cover").status_code == 200  # Existing acquired ownership remains private.
        assert b.get(f"/api/books/{book_id}/source").status_code == 200
        republished = publish(a, book_id, "Updated description")
        assert republished.json() == {"post_id": post, "status": "shared"}
        entry = next(item for item in b.get("/api/native/catalog").json() if item["id"] == catalog_id)
        assert entry["description"] == "Updated description"


def test_duplicate_uploaded_pdf_reports_existing_publication_without_transferring_withdrawal(native):
    a, b, _ = native
    payload = textbook_pdf()
    first, duplicate = uploaded(a, payload=payload), uploaded(b, payload=payload)
    post = publish(a, first).json()["post_id"]
    candidate = b.get("/api/community/book-share-candidates").json()[0]
    assert candidate["book_id"] == duplicate and candidate["post_id"] == post
    assert candidate["publication_status"] == "shared_by_other" and candidate["can_withdraw"] is False
    assert publish(b, duplicate).json() == {"post_id": post, "status": "already_shared"}
    state = b.get(f"/api/community/books/{duplicate}/publication").json()
    assert state["publication_status"] == "shared_by_other" and state["can_withdraw"] is False
    assert b.post(f"/api/community/{post}/withdraw").status_code == 403
    with api.account_repository.repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1


def test_publish_does_not_claim_unparsed_other_users_or_missing_pdf_and_requires_rights(native):
    a, b, _ = native
    pending = uploaded(a, parsed=False)
    response = publish(a, pending)
    assert response.status_code == 409
    assert api.account_repository.repo.asset(pending) is None
    ready = uploaded(a)
    assert publish(b, ready).status_code == 403
    missing_rights = a.post("/api/community/share", json={"kind": "book", "book_id": ready, "rights_confirmed": False})
    assert missing_rights.status_code == 422 and api.account_repository.repo.asset(ready) is None
    api.job_repository.get_book(ready).file_path.unlink()
    assert publish(a, ready).status_code == 409
    with api.account_repository.repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0
