import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_course_compiler import chapter

from adaptive_learning.api.community_routes import community_router
from adaptive_learning.api.schemas import BookCatalogItem
from adaptive_learning.assessment.models import InterviewSession, LearnerProfile
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.community import CommunityRepository, text_fingerprint
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository
from adaptive_learning.personalization.generator import ChapterCourseCompiler
from adaptive_learning.personalization.policy import PersonalizationPolicy


@pytest.fixture
def community(tmp_path, monkeypatch):
    monkeypatch.delenv("RAG_BOOK_INDEX_MANIFEST", raising=False)
    jobs = SQLiteOCRJobRepository(tmp_path / "jobs.sqlite3")
    assessments = SQLiteAssessmentRepository(tmp_path / "assessments.sqlite3")
    entries = []
    for i in range(10):
        book_id = f"book-{i}"
        jobs.register_book(
            book_id=book_id,
            original_name=f"教材{i}.pdf",
            file_path=tmp_path / f"{i}.pdf",
            source_sha256=f"hash-{i}",
        )
        entries.append(
            BookCatalogItem(
                book_id=book_id,
                title=f"教材{i}",
                status="ready",
                page_count=100 + i,
                chapter_count=5,
                summary=f"第{i}本教材的概要",
                diagnostics_ready=True,
            )
        )
    app = FastAPI()
    app.include_router(
        community_router(tmp_path, lambda: entries, lambda: jobs, lambda: assessments)
    )
    repo = CommunityRepository(tmp_path / "state" / "community.sqlite3")
    with TestClient(app) as a, TestClient(app) as b:
        yield a, b, repo, assessments


def feed(client, kind="all"):
    response = client.get("/api/community", params={"kind": kind})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def share(client, **kwargs):
    return client.post(
        "/api/community/share", json={"book_id": "book-0", "rights_confirmed": True, **kwargs}
    )


def private_note(client):
    response = client.post(
        "/api/library/notes",
        json={
            "book_id": "book-0",
            "title": "我的理解",
            "body": "这是一份真实的学习笔记。<script>不要执行我</script>",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_default_five_and_five_cookie_and_persistence(community):
    a, b, repo, _ = community
    response = a.get("/api/library")
    assert [x["book_id"] for x in response.json()] == [f"book-{i}" for i in range(5)]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "private, no-store"
    assert len(feed(a)) == 5
    token = a.cookies.get("zhiwo_visitor")
    owner, _, new = repo.visitor(token, [])
    assert not new
    assert len(CommunityRepository(repo.path).library(owner)) == 5
    assert len(b.get("/api/library").json()) == 5
    assert a.cookies.get("zhiwo_visitor") != b.cookies.get("zhiwo_visitor")
    with repo.connect() as db:
        assert token not in str([tuple(r) for r in db.execute("SELECT * FROM visitors")])


def test_acquisition_is_isolated_idempotent_and_no_assessment_copy(community):
    a, b, _, assessments = community
    post = feed(a)[0]
    assert not a.get(f"/api/community/{post['id']}/check").json()["already_owned"]
    assert a.post(f"/api/community/{post['id']}/acquire").json()["status"] == "added"
    assert a.post(f"/api/community/{post['id']}/acquire").json()["status"] == "already_owned"
    assert len(a.get("/api/library").json()) == 6
    assert len(b.get("/api/library").json()) == 5
    assert a.get(f"/api/community/{post['id']}/check").json()["already_owned"]
    assert next(p for p in feed(a) if p["id"] == post["id"])["downloads"] == 1
    with assessments._connect() as db:
        assert db.execute("SELECT count(*) FROM interview_sessions").fetchone()[0] == 0


def test_all_five_books_acquired_and_removed_without_reseeding(community):
    a, _, repo, _ = community
    posts = feed(a)
    for post in posts:
        assert a.post(f"/api/community/{post['id']}/acquire").status_code == 200
    assert len(a.get("/api/library").json()) == 10
    for post in posts:
        assert a.post(f"/api/library/books/{post['book_id']}/remove").status_code == 200
        assert repo.asset(post["book_id"]) is not None
    assert len(a.get("/api/library").json()) == 5
    a.post("/api/library/books/book-0/remove")
    assert len(a.get("/api/library").json()) == 4  # Defaults apply only to new visitors.


def test_concurrent_acquisition_inserts_once(community):
    a, _, repo, _ = community
    post = feed(a)[0]
    owner, _, _ = repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: repo.acquire(owner, post["id"]), range(24)))
    assert sum(result["status"] == "added" for result in results) == 1
    assert len(repo.library(owner)) == 6
    assert next(p for p in feed(a) if p["id"] == post["id"])["downloads"] == 1


def test_restore_only_previously_owned_books(community):
    a, b, _, _ = community
    assert a.post("/api/library/books/book-9/restore").status_code == 404
    a.post("/api/library/books/book-0/remove")
    assert len(a.get("/api/library").json()) == 4
    assert a.post("/api/library/books/book-0/restore").status_code == 200
    assert len(a.get("/api/library").json()) == 5
    assert a.post("/api/library/books/book-0/restore").status_code == 200
    assert len(b.get("/api/library").json()) == 5


def test_asset_dedup_is_by_file_or_long_text_not_title(tmp_path):
    repo = CommunityRepository(tmp_path / "community.sqlite3")
    text = "书中有关科学方法的完整论述以及应用。" * 30
    fp = text_fingerprint(text)
    assert fp == text_fingerprint(text.replace("论述", "论 述\n"))
    assert text_fingerprint("同一个书名") is None
    assert text_fingerprint("a+b" * 100) != text_fingerprint("a-b" * 100)
    assert repo.register_asset({"book_id": "a", "title": "原书"}, "pdf-a", fp) == "a"
    assert repo.register_asset({"book_id": "b", "title": "改了文件名"}, "pdf-a", None) == "a"
    assert repo.register_asset({"book_id": "c", "title": "重新打包的原书"}, "pdf-c", fp) == "a"
    assert (
        repo.register_asset(
            {"book_id": "d", "title": "原书"},
            "different-edition",
            text_fingerprint("完全不同的内容" * 100),
        )
        == "d"
    )
    owner, _, _ = repo.visitor(None, ["a"])
    assert repo.owns(owner, "b") and repo.owns(owner, "c")
    p1, _ = repo.publish(owner, "book", "b", "别名", "", {})
    p2, duplicate = repo.publish(owner, "book", "a", "原书", "", {})
    assert duplicate and p1 == p2
    assert repo.acquire(owner, p1)["status"] == "already_owned"
    assert len(repo.library(owner)) == 1


def test_book_share_requires_membership_rights_and_is_deduplicated(community):
    a, b, _, _ = community
    assert share(a, kind="book", book_id="book-8").status_code == 403
    assert share(a, kind="book", rights_confirmed=False).status_code == 422
    first = share(a, kind="book").json()
    assert first["status"] == "shared"
    second = share(b, kind="book").json()
    assert second == {"post_id": first["post_id"], "status": "already_shared"}
    assert b.post(f"/api/community/{first['post_id']}/withdraw").status_code == 403


def test_private_notes_explicit_share_and_download_snapshot(community):
    a, b, _, _ = community
    note = private_note(a)
    assert b.get("/api/library/resources").json() == []
    assert feed(a, "note") == []
    assert share(b, kind="note", resource_id=note["id"]).status_code == 404
    post = share(a, kind="note", resource_id=note["id"]).json()["post_id"]
    assert len(feed(b, "note")) == 1
    acquired = b.post(f"/api/community/{post}/acquire").json()
    assert acquired["status"] == "added"
    assert b.post(f"/api/community/{post}/acquire").json()["status"] == "already_owned"
    copy = b.get("/api/library/resources").json()[0]
    assert copy["id"] != note["id"] and copy["content"] == note["content"]
    assert "owner" not in copy
    assert (
        a.post(
            "/api/library/notes",
            json={
                "book_id": "book-0",
                "resource_id": note["id"],
                "title": "修改后的笔记",
                "body": "原作者后来修改的内容",
            },
        ).status_code
        == 200
    )
    assert b.get("/api/library/resources").json()[0]["content"] == note["content"]
    assert a.post(f"/api/community/{post}/withdraw").status_code == 200
    assert feed(b, "note") == []
    assert b.post(f"/api/community/{post}/acquire").status_code == 404
    assert len(b.get("/api/library/resources").json()) == 1
    assert len(b.get("/api/library").json()) == 5  # Notes do not import a whole book/profile.


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "   ", "body": "内容"},
        {"title": "标题", "body": " \n "},
        {"title": "过长", "body": "x" * 20001},
        {"title": "标题", "body": "内容", "owner": "another"},
    ],
)
def test_note_validation(community, payload):
    a, *_ = community
    assert a.post("/api/library/notes", json={"book_id": "book-0", **payload}).status_code == 422


def test_note_edit_and_archive_enforce_owner_and_book(community):
    a, b, _, _ = community
    note = private_note(a)
    payload = {
        "book_id": "book-0",
        "resource_id": note["id"],
        "title": "非法修改",
        "body": "不能改别人的",
    }
    assert b.post("/api/library/notes", json=payload).status_code == 404
    assert a.post("/api/library/notes", json={**payload, "book_id": "book-1"}).status_code == 404
    assert b.post(f"/api/library/resources/{note['id']}/archive").status_code == 404
    assert a.post(f"/api/library/resources/{note['id']}/archive").status_code == 200
    assert a.get("/api/library/resources").json() == []
    assert a.post(f"/api/library/resources/{note['id']}/archive?archived=false").status_code == 200
    assert a.get("/api/library/resources").json()[0]["id"] == note["id"]


def test_downloaded_resource_reacquire_restores_same_copy(community):
    a, b, _, _ = community
    note = private_note(a)
    post = share(a, kind="note", resource_id=note["id"]).json()["post_id"]
    acquired = b.post(f"/api/community/{post}/acquire").json()
    b.post(f"/api/library/resources/{acquired['resource_id']}/archive")
    assert not b.get(f"/api/community/{post}/check").json()["already_owned"]
    assert b.post(f"/api/community/{post}/acquire").json()["resource_id"] == acquired["resource_id"]
    assert len(b.get("/api/library/resources").json()) == 1


def test_flashcard_selection_shares_only_safe_fields(community):
    a, b, _, assessments = community
    now = datetime.now(UTC)
    session = InterviewSession(
        session_id="private-session",
        profile=LearnerProfile(
            user_id="private-user", book_id="book-0", goal="PRIVATE GOAL NEVER PUBLISH"
        ),
        created_at=now,
        updated_at=now,
    )
    assessments.create_session(session)
    source = chapter(3)
    bundle = ChapterCourseCompiler().compile(
        chapter=source,
        profile=session.profile,
        decision=PersonalizationPolicy().decide(session.profile, source.chapter_id),
    )
    assessments.save_course(session.session_id, bundle)
    response = a.get(f"/api/community/share-candidates/{session.session_id}")
    assert response.status_code == 200, response.text
    assert "PRIVATE GOAL" not in response.text and "private-user" not in response.text
    assert b.get(f"/api/community/share-candidates/{session.session_id}").status_code == 403
    selected = bundle.flashcards[0].card_id
    payload = {
        "kind": "flashcards",
        "session_id": session.session_id,
        "course_id": bundle.course_id,
        "card_ids": [selected],
    }
    assert share(a, **{**payload, "card_ids": ["not-in-course"]}).status_code == 422
    assert share(a, **{**payload, "card_ids": []}).status_code == 422
    assert share(a, **{**payload, "book_id": "book-1"}).status_code == 422
    post = share(a, **payload).json()["post_id"]
    public = feed(b, "flashcards")[0]
    assert len(public["content"]["cards"]) == 1
    assert set(public["content"]["cards"][0]) == {"front", "back", "pages"}
    assert "PRIVATE GOAL" not in json.dumps(public) and "private-user" not in json.dumps(public)
    assert b.post(f"/api/community/{post}/acquire").json()["status"] == "added"
    resource = b.get("/api/library/resources").json()[0]
    assert resource["content"] == public["content"]
    assert resource["kind"] == "flashcards"


def test_csrf_filter_search_unknown_and_pagination(community):
    a, *_ = community
    assert (
        a.post(
            "/api/library/books/book-0/remove", headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        a.post(
            "/api/library/books/book-0/remove", headers={"Origin": "http://testserver"}
        ).status_code
        == 200
    )
    assert a.get("/api/community", params={"kind": "invalid"}).status_code == 422
    assert a.get("/api/community", params={"page": -1}).status_code == 422
    assert a.get("/api/community", params={"page": 1}).json() == {"items": [], "has_more": False}
    assert (
        a.get("/api/community", params={"search": "教材9"}).json()["items"][0]["book_id"]
        == "book-9"
    )
    assert a.get("/api/community/not-a-post/check").status_code == 404
    assert a.post("/api/community/not-a-post/acquire").status_code == 404


def test_same_title_different_content_warns_without_merging(community):
    a, _, repo, _ = community
    a.get("/api/library")
    asset = json.loads(repo.asset("book-9")["catalog"])
    asset["title"] = "教材0"
    repo.register_asset(asset, "hash-9", None)
    post = next(p for p in feed(a) if p["book_id"] == "book-9")
    check = a.get(f"/api/community/{post['id']}/check").json()
    assert check["similar_titles"] == ["教材0"]
    assert not check["already_owned"]


def test_feed_pagination_no_duplicates_and_search_literal(community):
    a, _, repo, _ = community
    a.get("/api/library")
    owner, _, _ = repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    for i in range(52):
        repo.publish(owner, "note", "book-0", f"笔记 {i}", "", {"body": f"内容{i}"})
    first = a.get("/api/community", params={"kind": "note", "page": 0}).json()
    second = a.get("/api/community", params={"kind": "note", "page": 1}).json()
    assert len(first["items"]) == 50 and first["has_more"]
    assert len(second["items"]) == 2 and not second["has_more"]
    assert not ({p["id"] for p in first["items"]} & {p["id"] for p in second["items"]})
    assert a.get("/api/community", params={"search": "%"}).json()["items"] == []
