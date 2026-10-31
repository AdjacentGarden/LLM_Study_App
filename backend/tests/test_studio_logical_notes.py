"""Logical pages do not mutate immutable model inputs or strand legacy owners."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from test_community import community as community
from test_studio import note
from test_studio import setup as setup

from adaptive_learning.studio import Studio
from adaptive_learning.studio_models import NoteInput, NoteLogicalGroup


def input_note(key="input_00001", group=None, **changes):
    return NoteInput.model_validate(note(id=key, logical_note_id=group, **changes))


def seed(studio, count, *, owner="owner", book="book-0", grouped=False):
    with studio.repo.connect() as db:
        db.begin_write()
        for index in range(count):
            data = input_note(f"legacy_{index:05d}", book_id=book).model_dump(exclude={"logical_note_id"})
            data["revision"] = 1
            db.execute("INSERT INTO studio_notes VALUES(?,?,?,?,?,?,?)",
                       (data["id"], owner, book, 1, json.dumps(data), 1.0, 1.0))
            if grouped:
                db.execute("INSERT INTO studio_note_logical_inputs(note_id,owner,book_id,logical_note_id,created) VALUES(?,?,?,?,?)",
                           (data["id"], owner, book, f"logical_{index:05d}", 1.0))


def rows(studio, table):
    assert table in {"studio_notes", "studio_jobs", "studio_note_request_aliases", "studio_note_logical_inputs"}
    with studio.repo.connect() as db:
        return [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()]


def test_many_immutable_inputs_for_one_page_do_not_use_200_page_quota(tmp_path):
    studio = Studio(tmp_path)
    for index in range(201):
        studio.save_note("owner", input_note(f"snapshot_{index:04d}", "logical_page_1"))
    with studio.repo.connect() as db:
        assert studio._logical_note_count(db, "owner") == 1
    assert len(rows(studio, "studio_notes")) == 201
    assert all("logical_note_id" not in json.loads(row["data"]) for row in rows(studio, "studio_notes"))


def test_legacy_200_can_save_new_drawing_without_recovering_a_legacy_id(tmp_path):
    studio = Studio(tmp_path)
    seed(studio, 200)
    before = rows(studio, "studio_notes")
    with pytest.raises(HTTPException, match="200") as rejected:
        studio.save_note("owner", input_note("old_client_new"))
    assert rejected.value.status_code == 409
    # No local UUID can be inferred from old content hashes. Explicit new
    # logical identity, rather than a guessed legacy association, recovers use.
    saved = studio.save_note("owner", input_note("new_drawing_input", "stable_local_note"))
    assert saved["revision"] == 1
    assert rows(studio, "studio_notes")[:200] == before
    assert len(rows(studio, "studio_notes")) == 201


def test_legacy_bind_then_same_page_save_preserves_all_old_inputs(tmp_path):
    studio = Studio(tmp_path)
    seed(studio, 200)
    before = rows(studio, "studio_notes")
    linked = studio.bind_note_group("owner", "legacy_00000", NoteLogicalGroup(logical_note_id="logical_page_1"))
    assert linked["revision"] == 1 and linked["linked_inputs"] == 1
    studio.save_note("owner", input_note("new_snapshot_1", "logical_page_1"))
    assert rows(studio, "studio_notes")[:200] == before


def test_200_groups_remain_bounded_but_same_group_and_other_owners_work(tmp_path):
    studio = Studio(tmp_path)
    seed(studio, 200, grouped=True)
    with pytest.raises(HTTPException) as rejected:
        studio.save_note("owner", input_note("group_201_input", "logical_group_201"))
    assert rejected.value.status_code == 409
    studio.save_note("owner", input_note("same_page_input", "logical_00000"))
    studio.save_note("other", input_note("other_page_input", "logical_group_201"))
    # A book boundary is part of logical identity, even if a client reuses ID.
    with pytest.raises(HTTPException):
        studio.save_note("owner", input_note("other_book_input", "logical_00000", book_id="book-1"))


def test_binding_cannot_create_group_201_or_reassign_existing_group(tmp_path):
    studio = Studio(tmp_path)
    seed(studio, 200, grouped=True)
    studio.save_note("owner", input_note("ungrouped_input"))
    with pytest.raises(HTTPException) as rejected:
        studio.bind_note_group("owner", "ungrouped_input", NoteLogicalGroup(logical_note_id="logical_group_201"))
    assert rejected.value.status_code == 409
    studio.bind_note_group("owner", "ungrouped_input", NoteLogicalGroup(logical_note_id="logical_00000"))
    before = rows(studio, "studio_note_logical_inputs")
    with pytest.raises(HTTPException) as moved:
        studio.bind_note_group("owner", "ungrouped_input", NoteLogicalGroup(logical_note_id="logical_00001"))
    assert moved.value.status_code == 409
    assert rows(studio, "studio_note_logical_inputs") == before


@pytest.mark.parametrize("status", ["queued", "planning", "uncertain"])
def test_metadata_bind_is_idempotent_and_does_not_change_pending_or_unknown_jobs(tmp_path, status):
    studio = Studio(tmp_path)
    saved = studio.save_note("owner", input_note())
    body = {"note_id": saved["id"], "book_id": "book-0", "revision": 1,
            "request_id": "immutable_request", "transcript": "已核对文字", "action": "improve", "consent": True}
    job = studio.submit("owner", body, "improve")
    with studio.repo.connect() as db:
        db.execute("UPDATE studio_jobs SET status=?,provider_id='original-provider-id' WHERE id=?", (status, job["id"]))
    protected = {table: rows(studio, table) for table in ("studio_notes", "studio_jobs", "studio_note_request_aliases")}
    group = NoteLogicalGroup(logical_note_id="logical_page_1")
    assert studio.bind_note_group("owner", saved["id"], group) == studio.bind_note_group("owner", saved["id"], group)
    studio.archive_note_group("owner", "book-0", "logical_page_1")
    assert {table: rows(studio, table) for table in protected} == protected
    assert studio.submit("owner", body, "improve")["id"] == job["id"]


@pytest.mark.parametrize("bad", ["missing_input", "foreign_input", "other_book_input"])
def test_bind_validates_every_owned_same_book_input_before_writing(tmp_path, bad):
    studio = Studio(tmp_path)
    studio.save_note("owner", input_note())
    studio.save_note("other", input_note("foreign_input"))
    studio.save_note("owner", input_note("other_book_input", book_id="book-1"))
    with pytest.raises(HTTPException) as rejected:
        studio.bind_note_group("owner", "input_00001", NoteLogicalGroup(
            logical_note_id="logical_page_1", legacy_note_ids=[bad]))
    assert rejected.value.status_code == 404
    assert rows(studio, "studio_note_logical_inputs") == []


def test_physical_cap_is_explicit_and_does_not_block_old_receipts_or_metadata(tmp_path):
    studio = Studio(tmp_path)
    seed(studio, 2000)
    before = rows(studio, "studio_notes")
    studio.bind_note_group("owner", "legacy_00000", NoteLogicalGroup(logical_note_id="logical_page_1"))
    # The original POST's response can still be recovered at the hard cap.
    assert studio.save_note("owner", input_note("legacy_00000", "logical_page_1"))["revision"] == 1
    with pytest.raises(HTTPException, match="历史输入已达 2000") as rejected:
        studio.save_note("owner", input_note("one_more_snapshot", "logical_page_1"))
    assert rejected.value.status_code == 409
    assert rows(studio, "studio_notes") == before


def test_new_metadata_does_not_break_lost_save_retry_or_allow_cross_group_update(tmp_path):
    studio = Studio(tmp_path)
    original = input_note()
    first = studio.save_note("owner", original)
    after = studio.save_note("owner", original.model_copy(update={"logical_note_id": "logical_page_1"}))
    assert after == first
    protected = rows(studio, "studio_notes")
    with pytest.raises(HTTPException) as rejected:
        studio.save_note("owner", input_note(group="other_logical_page", revision=1, title="不能绕过归属"))
    assert rejected.value.status_code == 409
    assert rows(studio, "studio_notes") == protected


def test_concurrent_new_groups_cannot_exceed_logical_quota(tmp_path):
    studio = Studio(tmp_path)
    seed(studio, 199, grouped=True)

    def save(index):
        try:
            return studio.save_note("owner", input_note(f"concurrent_{index}", f"new_logical_{index}"))["revision"]
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, [1, 2])) == [1, 409]
    with studio.repo.connect() as db:
        assert studio._logical_note_count(db, "owner") == 200


def test_logical_group_and_input_ids_are_bounded_and_legacy_payload_still_valid():
    assert input_note().logical_note_id is None
    for bad in ("short", "not/a/key", "x" * 101):
        with pytest.raises(ValidationError):
            input_note(group=bad)
    with pytest.raises(ValidationError):
        NoteLogicalGroup(logical_note_id="logical_page_1", legacy_note_ids=["input_00001"] * 2)
    with pytest.raises(ValidationError):
        NoteLogicalGroup(logical_note_id="logical_page_1", legacy_note_ids=[f"input_{i:05}" for i in range(201)])


def test_authenticated_group_route_and_paginated_legacy_discovery(setup):
    owner, other, studio = setup
    for index in range(3):
        assert owner.post("/api/studio/notes", json=note(id=f"owned_input_{index}")).status_code == 200
    body = {"logical_note_id": "logical_page_1", "legacy_note_ids": ["owned_input_1"]}
    response = owner.put("/api/studio/notes/owned_input_0/logical-group", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "owned_input_0" and response.json()["revision"] == 1
    assert response.json()["book_id"] == "book-0" and response.json()["linked_inputs"] == 2
    assert other.put("/api/studio/notes/owned_input_0/logical-group", json=body).status_code == 404
    first = owner.get("/api/studio/notes", params={"book_id": "book-0", "limit": 2}).json()
    second = owner.get("/api/studio/notes", params={"book_id": "book-0", "limit": 2, "offset": first["next_offset"]}).json()
    assert len(first["items"]) == 2 and len(second["items"]) == 1 and second["next_offset"] is None
    assert len({row["id"] for row in first["items"] + second["items"]}) == 3
    assert all("strokes" not in row for row in first["items"] + second["items"])
    assert sum(row["logical_note_id"] == "logical_page_1" for row in first["items"] + second["items"]) == 2
    assert owner.get("/api/studio/notes", params={"book_id": "book-0", "limit": 201}).status_code == 422
    owner.post("/api/library/books/book-0/remove")
    assert owner.put("/api/studio/notes/owned_input_0/logical-group", json=body).status_code == 403


def test_archive_releases_quota_but_preserves_inputs_jobs_and_requires_quota_to_restore(tmp_path):
    studio = Studio(tmp_path)
    seed(studio, 200, grouped=True)
    protected = rows(studio, "studio_notes")
    group = "logical_00000"
    first = studio.archive_note_group("owner", "book-0", group)
    assert studio.archive_note_group("owner", "book-0", group) == first
    assert rows(studio, "studio_notes") == protected
    studio.save_note("owner", input_note("replacement_page", "logical_replacement"))
    with pytest.raises(HTTPException) as rejected:
        studio.bind_note_group("owner", "legacy_00000", NoteLogicalGroup(logical_note_id=group))
    assert rejected.value.status_code == 409
    # An archived input still belongs to its original group; it cannot be
    # reassigned to another active group to bypass restoration's quota check.
    with pytest.raises(HTTPException):
        studio.bind_note_group("owner", "legacy_00000", NoteLogicalGroup(logical_note_id="logical_replacement"))
    studio.archive_note_group("owner", "book-0", "logical_replacement")
    assert studio.bind_note_group("owner", "legacy_00000", NoteLogicalGroup(logical_note_id=group))["revision"] == 1
    with studio.repo.connect() as db:
        assert studio._logical_note_count(db, "owner") == 200


def test_archive_route_is_owner_and_book_scoped_and_does_not_delete_saved_input(setup):
    owner, other, _ = setup
    saved = owner.post("/api/studio/notes", json=note(logical_note_id="logical_page_1")).json()
    url = "/api/studio/note-groups/logical_page_1/archive"
    assert other.put(url, json={"book_id": "book-0"}).status_code == 404
    assert owner.put(url, json={"book_id": "book-1"}).status_code == 404
    assert owner.put(url, json={"book_id": "book-0"}).json()["archived"] is True
    assert owner.put(url, json={"book_id": "book-0"}).status_code == 200
    assert owner.get(f"/api/studio/notes/{saved['id']}").json()["revision"] == saved["revision"]
