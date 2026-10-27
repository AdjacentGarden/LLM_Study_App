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
