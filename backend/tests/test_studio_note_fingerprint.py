import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from adaptive_learning.studio import Studio


def spec(kind="improve", request_id="request_note_111"):
    return {"book_id": "book-1", "note_id": "note-1", "revision": 2,
            "action": kind, "transcript": "保留通常和个人操作记录。", "consent": True,
            "chapter_title": "第一章", "request_id": request_id}


def old_fingerprint(data):
    return hashlib.sha256(json.dumps({k: v for k, v in data.items() if k != "request_id"}, sort_keys=True).encode()).hexdigest()


def row(studio, key):
    return dict(studio.job("owner", key))


@pytest.mark.parametrize("kind", ["improve", "complete"])
def test_old_unversioned_success_same_request_is_unchanged_but_fresh_id_uses_new_note_review(tmp_path, kind):
    studio = Studio(tmp_path)
    data = spec(kind)
    key = studio.submit("owner", data, kind)["id"]
    with studio.repo.connect() as db:
        db.execute("UPDATE studio_jobs SET fingerprint=?,status='succeeded' WHERE id=?", (old_fingerprint(data), key))
    before = row(studio, key)
    assert studio.submit("owner", data, kind)["id"] == key
    assert row(studio, key) == before
    assert set(before) == {
        "id", "owner", "book_id", "request_id", "fingerprint", "kind", "status", "data",
        "result", "provider_id", "error", "reserved", "created", "updated", "next_poll",
        "lease_owner", "lease_expires", "attempts",
    }
    fresh = {**data, "request_id": "new_quality_request_111"}
    new_key = studio.submit("owner", fresh, kind)["id"]
    assert new_key != key and row(studio, new_key)["fingerprint"] != before["fingerprint"]
    assert json.loads(row(studio, new_key)["data"]) == fresh
    assert row(studio, key) == before  # Never relabel or rerun the old success.


@pytest.mark.parametrize("kind", ["improve", "complete"])
def test_request_alias_survives_note_review_revision_and_only_fresh_id_gets_new_job(tmp_path, monkeypatch, kind):
    studio = Studio(tmp_path)
    data = spec(kind)
    monkeypatch.setattr("adaptive_learning.studio.NOTE_REVIEW_REVISION", "previous-quality")
    key = studio.submit("owner", data, kind)["id"]
    studio.update(key, status="succeeded")
    before = row(studio, key)
    monkeypatch.setattr("adaptive_learning.studio.NOTE_REVIEW_REVISION", "next-quality")
    assert studio.submit("owner", dict(reversed(list(data.items()))), kind)["id"] == key
    assert row(studio, key) == before
    fresh = {**data, "request_id": "next_quality_request_111"}
    new_key = studio.submit("owner", fresh, kind)["id"]
    assert new_key != key
    # Normal same-content deduplication within the new quality version remains.
    assert studio.submit("owner", {**fresh, "request_id": "same_new_quality_222"}, kind)["id"] == new_key
    with studio.repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM studio_jobs").fetchone()[0] == 2


@pytest.mark.parametrize("change", [
    {"consent": 1}, {"revision": 2.0}, {"transcript": "保留通常和个人操作记录。 "},
    {"chapter_title": "第二章"}, {"extra": None},
])
def test_same_request_body_comparison_is_type_and_value_strict_and_never_reopens_work(tmp_path, monkeypatch, change):
    studio = Studio(tmp_path)
    data = spec()
    key = studio.submit("owner", data, "improve")["id"]
    before = row(studio, key)
    monkeypatch.setattr("adaptive_learning.studio.NOTE_REVIEW_REVISION", "new-quality")
    with pytest.raises(HTTPException) as rejected:
        studio.submit("owner", {**data, **change}, "improve")
    assert rejected.value.status_code == 409 and row(studio, key) == before
    with studio.repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM studio_jobs").fetchone()[0] == 1


def test_same_request_matches_parsed_original_json_not_storage_format_and_checks_kind(tmp_path):
    studio = Studio(tmp_path)
    data = spec()
    key = studio.submit("owner", data, "improve")["id"]
    with studio.repo.connect() as db:
        db.execute("UPDATE studio_jobs SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=True, indent=2), key))
    before = row(studio, key)
    assert studio.submit("owner", dict(reversed(list(data.items()))), "improve")["id"] == key
    assert row(studio, key) == before
    with pytest.raises(HTTPException) as rejected:
        studio.submit("owner", data, "complete")
    assert rejected.value.status_code == 409 and row(studio, key) == before


@pytest.mark.parametrize("kind", ["recognize", "image", "video", "diagram"])
def test_media_policy_and_recognition_fingerprints_are_independent_of_note_revision(tmp_path, monkeypatch, kind):
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_IMAGE_MODEL", "image-01")
    monkeypatch.setenv("MINIMAX_VIDEO_MODEL", "MiniMax-H3-Lite")
    monkeypatch.setenv("MINIMAX_VIDEO_RESOLUTION", "480P")
    studio = Studio(tmp_path)
    data = spec(kind)
    key = studio.submit("owner", data, kind)["id"]
    before = row(studio, key)
    expected = old_fingerprint(data)
    if kind in {"image", "video"}:
        from adaptive_learning.studio import media_review_mode
        media_identity = {
            "body": {k: v for k, v in data.items() if k != "request_id"},
            "media_review_mode": media_review_mode(), "media_review_revision": 1,
        }
        if kind == "image":
            media_identity["image_generator"] = studio.image_generation_config()
        else:
            media_identity["video_generator"] = studio.video_generation_config()
        expected = hashlib.sha256(json.dumps(media_identity, sort_keys=True).encode()).hexdigest()
    assert before["fingerprint"] == expected
    monkeypatch.setattr("adaptive_learning.studio.NOTE_REVIEW_REVISION", "unrelated-note-revision")
    assert studio.submit("owner", {**data, "request_id": "unchanged_media_222"}, kind)["id"] == key
    assert row(studio, key) == before
    with studio.repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM studio_note_request_aliases").fetchone()[0] == (
            2 if kind in {"image", "video"} else 0
        )


def test_corrupt_stored_request_data_fails_closed_without_new_submission(tmp_path):
    studio = Studio(tmp_path)
    data = spec()
    key = studio.submit("owner", data, "improve")["id"]
    with studio.repo.connect() as db:
        db.execute("UPDATE studio_jobs SET data='not-json' WHERE id=?", (key,))
    before = row(studio, key)
    with pytest.raises(HTTPException) as rejected:
        studio.submit("owner", data, "improve")
    assert rejected.value.status_code == 409 and row(studio, key) == before


@pytest.mark.parametrize("kind", ["improve", "complete"])
def test_coalesced_note_request_alias_survives_restart_and_quality_revision_without_new_work(tmp_path, monkeypatch, kind):
    studio = Studio(tmp_path)
    first = spec(kind)
    key = studio.submit("owner", first, kind)["id"]
    alias_body = {**first, "request_id": "coalesced_request_222"}
    assert studio.submit("owner", alias_body, kind)["id"] == key
    before = row(studio, key)
    monkeypatch.setattr("adaptive_learning.studio.NOTE_REVIEW_REVISION", "next-quality-version")
    restarted = Studio(tmp_path)
    assert restarted.submit("owner", alias_body, kind)["id"] == key
    assert row(restarted, key) == before
    with restarted.repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM studio_jobs").fetchone()[0] == 1
        alias = db.execute("SELECT * FROM studio_note_request_aliases WHERE owner=? AND request_id=?", ("owner", alias_body["request_id"])).fetchone()
        assert json.loads(alias["body_json"]) == alias_body and alias["job_id"] == key
    fresh = restarted.submit("owner", {**first, "request_id": "fresh_new_revision_333"}, kind)
    assert fresh["id"] != key and row(restarted, key) == before


def test_concurrent_note_coalesces_atomically_remember_both_request_ids(tmp_path, monkeypatch):
    studio = Studio(tmp_path)
    bodies = [spec(request_id="concurrent_note_111"), spec(request_id="concurrent_note_222")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda body: studio.submit("owner", body, "improve")["id"], bodies))
    assert ids[0] == ids[1]
    before = row(studio, ids[0])
    with studio.repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM studio_jobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM studio_note_request_aliases").fetchone()[0] == 2
    monkeypatch.setattr("adaptive_learning.studio.NOTE_REVIEW_REVISION", "next-quality")
    assert all(studio.submit("owner", body, "improve")["id"] == ids[0] for body in bodies)
    assert row(studio, ids[0]) == before


@pytest.mark.parametrize("change,kind", [
    ({"consent": 1}, "improve"), ({"revision": 2.0}, "improve"),
    ({"transcript": "保留通常和个人操作记录。 "}, "improve"),
    ({}, "complete"), ({}, "recognize"), ({}, "image"),
])
def test_coalesced_alias_replay_requires_original_strict_body_and_kind(tmp_path, monkeypatch, change, kind):
    studio = Studio(tmp_path)
    first = spec()
    key = studio.submit("owner", first, "improve")["id"]
    alias_body = {**first, "request_id": "coalesced_conflict_222"}
    assert studio.submit("owner", alias_body, "improve")["id"] == key
    before = row(studio, key)
    monkeypatch.setattr("adaptive_learning.studio.NOTE_REVIEW_REVISION", "another-quality")
    with pytest.raises(HTTPException) as rejected:
        studio.submit("owner", {**alias_body, **change}, kind)
    assert rejected.value.status_code == 409 and row(studio, key) == before
    with studio.repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM studio_jobs").fetchone()[0] == 1


def test_note_request_aliases_are_owner_scoped(tmp_path):
    studio = Studio(tmp_path)
    data = spec()
    first = studio.submit("owner", data, "improve")["id"]
    alias_body = {**data, "request_id": "shared_request_id_222"}
    assert studio.submit("owner", alias_body, "improve")["id"] == first
    other = studio.submit("other-owner", alias_body, "improve")["id"]
    assert other != first
    assert studio.submit("owner", alias_body, "improve")["id"] == first
    assert studio.submit("other-owner", alias_body, "improve")["id"] == other


@pytest.mark.parametrize("foreign_target", [False, True])
def test_alias_to_missing_or_foreign_job_fails_closed_without_new_submission(tmp_path, foreign_target):
    studio = Studio(tmp_path)
    foreign = studio.submit("other-owner", spec(), "improve")["id"]
    before = dict(studio.job("other-owner", foreign))
    body = spec(request_id="dangling_alias_222")
    with studio.repo.connect() as db:
        db.execute("INSERT INTO studio_note_request_aliases VALUES (?,?,?,?,?,?)", (
            "owner", body["request_id"], "improve", json.dumps(body, sort_keys=True, separators=(",", ":")),
            foreign if foreign_target else "f" * 32, 0))
    with pytest.raises(HTTPException) as rejected:
        studio.submit("owner", body, "improve")
    assert rejected.value.status_code == 409 and dict(studio.job("other-owner", foreign)) == before
    with studio.repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM studio_jobs").fetchone()[0] == 1
