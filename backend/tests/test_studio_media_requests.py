"""Offline receipts: a lost/coalesced media response never needs another POST."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from test_community import community as community
from test_studio import media
from test_studio import setup as setup

from adaptive_learning.studio import Studio


def lookup(client, request_id, book_id="book-0"):
    return client.get("/api/studio/jobs", params={"book_id": book_id, "request_id": request_id})


@pytest.mark.parametrize("kind", ["image", "video"])
def test_coalesced_media_alias_is_exactly_recoverable_after_restart_and_mode_change(setup, monkeypatch, kind):
    a, b, studio = setup
    first = a.post("/api/studio/media", json=media(kind=kind)).json()
    alias = "coalesced_media_receipt_222"
    second = a.post("/api/studio/media", json=media(kind=kind, request_id=alias)).json()
    assert second["id"] == first["id"]
    # The POST may still carry the canonical job's original request ID.
    assert second["request_id"] == first["request_id"]
    monkeypatch.setenv("STUDIO_MEDIA_REVIEW_MODE", "off")
    restarted = Studio(studio.repo.path.parent.parent)
    owner = studio.repo.visitor(a.cookies.get("zhiwo_visitor"), [])[0]
    recovered = restarted.job_by_request(owner, "book-0", alias)
    assert recovered["id"] == first["id"] and recovered["request_id"] == alias
    receipt = lookup(a, alias)
    assert receipt.status_code == 200
    assert [r["id"] for r in receipt.json()["items"]] == [first["id"]]
    assert receipt.json()["items"][0]["request_id"] == alias
    assert "_diagnostic" not in receipt.json()["items"][0]["result"]
    assert lookup(b, alias).json()["items"] == []
    assert lookup(a, alias, "book-1").json()["items"] == []
    with studio.repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM studio_jobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM studio_note_request_aliases").fetchone()[0] == 2


@pytest.mark.parametrize("kind", ["image", "video"])
def test_media_alias_cannot_be_reused_for_changed_content_or_kind(setup, kind):
    a, _, studio = setup
    first = a.post("/api/studio/media", json=media(kind=kind)).json()
    alias = "same_content_alias_222"
    assert a.post("/api/studio/media", json=media(kind=kind, request_id=alias)).json()["id"] == first["id"]
    assert a.post("/api/studio/media", json=media(
        kind=kind, request_id=alias, excerpt="另一段教材的原文内容。"
    )).status_code == 409
    assert a.post("/api/studio/media", json=media(
        kind="video" if kind == "image" else "image", request_id=alias
    )).status_code == 409
    with studio.repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM studio_jobs").fetchone()[0] == 1


def test_media_alias_exact_lookup_is_not_limited_by_hundred_job_history(setup):
    a, _, studio = setup
    canonical = a.post("/api/studio/media", json=media()).json()
    alias = "old_media_receipt_alias"
    a.post("/api/studio/media", json=media(request_id=alias))
    owner = studio.repo.visitor(a.cookies.get("zhiwo_visitor"), [])[0]
    with studio.repo.connect() as db:
        db.execute("UPDATE studio_jobs SET created=1 WHERE id=?", (canonical["id"],))
        for i in range(105):
            db.execute(
                "INSERT INTO studio_jobs(id,owner,book_id,request_id,fingerprint,kind,status,data,created,updated) "
                "VALUES(?,?,?,?,?,'recognize','failed','{}',?,?)",
                (uuid.uuid4().hex, owner, "book-0", f"history_{i}", f"history_{i}", i + 2, i + 2),
            )
    ordinary = a.get("/api/studio/jobs", params={"book_id": "book-0"}).json()["items"]
    assert len(ordinary) == 100 and canonical["id"] not in {r["id"] for r in ordinary}
    exact = lookup(a, alias)
    assert exact.status_code == 200 and exact.json()["items"][0]["id"] == canonical["id"]


def test_concurrent_media_coalescing_remembers_every_request_without_new_execution(tmp_path):
    studio = Studio(tmp_path)
    bodies = [media(request_id=f"concurrent_media_{i}") for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda body: studio.submit("owner", body, "image"), bodies))
    job_id = results[0]["id"]
    assert {r["id"] for r in results} == {job_id}
    for body in bodies:
        receipt = studio.job_by_request("owner", "book-0", body["request_id"])
        assert receipt["id"] == job_id and receipt["request_id"] == body["request_id"]
    with studio.repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM studio_jobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM studio_note_request_aliases").fetchone()[0] == len(bodies)


@pytest.mark.parametrize("fault", ["body", "foreign_owner", "kind"])
def test_corrupt_media_alias_fails_closed_without_exposing_another_job(tmp_path, fault):
    studio = Studio(tmp_path)
    first = studio.submit("owner", media(), "image")
    alias = "damaged_receipt_alias"
    studio.submit("owner", media(request_id=alias), "image")
    other = studio.submit("other", media(request_id="other_owner_media"), "image")
    with studio.repo.connect() as db:
        if fault == "body":
            db.execute("UPDATE studio_note_request_aliases SET body_json=? WHERE owner=? AND request_id=?",
                       (json.dumps(media(request_id=alias, excerpt="与原请求不同的文字")), "owner", alias))
        elif fault == "foreign_owner":
            db.execute("UPDATE studio_note_request_aliases SET job_id=? WHERE owner=? AND request_id=?",
                       (other["id"], "owner", alias))
        else:
            db.execute("UPDATE studio_note_request_aliases SET kind='video' WHERE owner=? AND request_id=?",
                       ("owner", alias))
    with pytest.raises(HTTPException) as failure:
        studio.job_by_request("owner", "book-0", alias)
    assert failure.value.status_code == 409
    assert studio.job_by_request("other", "book-0", alias) is None
    assert studio.job("owner", first["id"])["status"] == "queued"


def test_request_lookup_handles_pre_alias_jobs_and_remains_read_only_without_generation_capability(setup, monkeypatch):
    a, _, studio = setup
    created = a.post("/api/studio/media", json=media()).json()
    with studio.repo.connect() as db:
        db.execute("DELETE FROM studio_note_request_aliases")
    monkeypatch.setattr(Studio, "capabilities", lambda self: {"image": False, "video": False})
    response = lookup(a, media()["request_id"])
    assert response.status_code == 200 and response.json()["items"][0]["id"] == created["id"]
    assert lookup(a, "not_a_recorded_request").json()["items"] == []
    assert lookup(a, "../invalid_request").status_code == 422
    with studio.repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM studio_jobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM studio_note_request_aliases").fetchone()[0] == 0
