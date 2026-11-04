from adaptive_learning.api.public_material_routes import public_material_router
from native_harness import api, textbook_pdf
from test_native_api import native as native


def test_native_upload_owner_can_save_and_read_voice_note_without_claim_or_publication(native):
    owner, other, _ = native
    uploaded = owner.post("/api/native/uploads", files={
        "file": ("private-note-source.pdf", textbook_pdf(), "application/pdf"),
    })
    assert uploaded.status_code == 200
    book = uploaded.json()["book_id"]
    repo = api.account_repository.repo
    assert repo.asset(book) is None
    body = {"id": "private_voice_note_1", "book_id": book, "revision": 0,
            "title": "我的语音笔记", "input_mode": "voice", "strokes": []}
    saved = owner.post("/api/studio/notes", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    assert owner.get("/api/studio/notes/private_voice_note_1").status_code == 200
    assert owner.get("/api/studio/notes", params={"book_id": book}).json()["items"][0]["id"] == body["id"]
    assert other.post("/api/studio/notes", json={**body, "id": "other_voice_note_1"}).status_code == 403
    assert other.get("/api/studio/notes/private_voice_note_1").status_code == 404
    assert other.get("/api/studio/notes", params={"book_id": book}).status_code == 403
    # Only Studio access changed; other community writes still require an
    # explicit shelf claim, and no read created an asset/publication.
    assert owner.post("/api/library/notes", json={"book_id": book, "title": "旧社区笔记", "body": "内容"}).status_code == 403
    assert repo.asset(book) is None and owner.get("/api/library").json() == []
    with repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0


def test_device_course_anchor_cannot_authorize_a_different_book_or_account(native):
    owner, other, _ = native
    book = owner.post("/api/native/uploads", files={
        "file": ("device-course.pdf", textbook_pdf(), "application/pdf"),
    }).json()["book_id"]
    course = "local-course:download_test"
    note = {"id": "device_course_voice", "book_id": book, "course_id": course,
            "course_resource_id": f"{course}:{book}", "revision": 0,
            "title": "课内笔记", "input_mode": "voice", "pages": [1]}
    assert owner.post("/api/studio/notes", json={**note, "course_resource_id": "wrong"}).status_code == 404
    assert other.post("/api/studio/notes", json=note).status_code == 403
    assert owner.post("/api/studio/notes", json=note).status_code == 200
    assert other.get("/api/studio/notes/device_course_voice").status_code == 404


def test_studio_reader_of_public_book_can_only_access_own_personal_notes(native):
    publisher, reader, _ = native
    publisher.app.include_router(public_material_router(api))
    book = publisher.post("/api/native/uploads", files={
        "file": ("shared.pdf", textbook_pdf(), "application/pdf"),
    }).json()["book_id"]
    group = publisher.post("/api/public/library/course-groups", json={
        "id": "group_reader", "title": "共享课程", "book_ids": [book],
    })
    assert group.status_code == 200
    # Isolate Studio authorization from the asynchronous OCR/publishing pipeline.
    with api.account_repository.repo.connect() as db:
        db.execute("UPDATE public_course_groups SET status='ready' WHERE id=?", ("group_reader",))
    course = "local-course:reader"
    response = reader.post("/api/studio/notes", json={"id": "reader_voice_1", "book_id": book,
        "course_id": course, "course_resource_id": f"{course}:{book}", "revision": 0,
        "title": "我自己的学习记录", "input_mode": "voice", "pages": [1]})
    assert response.status_code == 200, response.text
    assert publisher.get("/api/studio/notes/reader_voice_1").status_code == 404
    assert reader.get("/api/studio/notes/reader_voice_1").status_code == 200
    assert publisher.delete("/api/public/library/course-groups/group_reader").status_code == 200
    assert reader.get("/api/studio/notes", params={"book_id": book}).status_code == 403
