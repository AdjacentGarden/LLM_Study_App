import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_community import community as community
from test_community import private_note
from test_course_compiler import chapter
from test_user_profile import avatar

from adaptive_learning.assessment.models import InterviewSession, LearnerProfile
from adaptive_learning.personalization.generator import ChapterCourseCompiler
from adaptive_learning.personalization.policy import PersonalizationPolicy
from adaptive_learning.social import SocialRepository


def identity(client, visible=True, nickname=None):
    result = client.get("/api/social/me")
    assert result.status_code == 200, result.text
    if nickname:
        p = client.get("/api/user/profile").json()
        assert (
            client.post(
                "/api/user/profile",
                json={
                    "nickname": nickname,
                    "age": 22,
                    "bio": "PRIVATE BIO",
                    "revision": p["revision"],
                },
            ).status_code
            == 200
        )
    if visible:
        assert client.post("/api/social/discoverability", json={"enabled": True}).status_code == 200
    return result.json()["user_id"]


def action(client, peer, what):
    return client.post(f"/api/social/friends/{peer}/{what}")


def friends(a, b):
    aid, bid = identity(a), identity(b)
    assert action(a, bid, "request").json()["relationship"] == "outgoing"
    assert action(b, aid, "accept").json()["relationship"] == "friend"
    return aid, bid


def send(client, peer, nonce="message-0001", text="你好，同学！📚", attachment=None):
    body = {"client_id": nonce, "text": text}
    if attachment is not None:
        body["attachment"] = attachment
    return client.post(f"/api/social/chats/{peer}", json=body)


def attach(kind="book", **extra):
    return {"kind": kind, "book_id": "book-0", "rights_confirmed": True, **extra}


def own_course(client, assessments):
    now = datetime.now(UTC)
    session = InterviewSession(
        session_id="private-social-session",
        profile=LearnerProfile(user_id="SECRET USER", book_id="book-0", goal="SECRET GOAL"),
        created_at=now,
        updated_at=now,
    )
    assessments.create_session(session)
    source = chapter(3)
    course = ChapterCourseCompiler().compile(
        chapter=source,
        profile=session.profile,
        decision=PersonalizationPolicy().decide(session.profile, source.chapter_id),
    )
    assessments.save_course(session.session_id, course)
    assert client.get(f"/api/community/share-candidates/{session.session_id}").status_code == 200
    return session, course


def test_stable_unique_ids_private_default_and_migration(community):
    a, b, repo, _ = community
    aid, bid = identity(a, False), identity(b, False)
    assert re.fullmatch(r"ZW-[A-F0-9]{10}", aid) and aid != bid
    assert not a.get("/api/social/me").json()["discoverable"]
    assert a.get("/api/social/users", params={"query": bid}).json() == []
    token = a.cookies.get("zhiwo_visitor")
    owner, _, _ = repo.visitor(token, [])
    assert token != aid and owner != aid
    assert SocialRepository(repo).me(owner)["user_id"] == aid
    with TestClient(a.app) as restored:
        restored.cookies.update(a.cookies)
        assert identity(restored, False) == aid
    old_owner, _, _ = repo.visitor(None, [])
    migrated = SocialRepository(repo).me(old_owner)
    assert migrated["user_id"] not in {aid, bid} and not migrated["discoverable"]
    with TestClient(a.app) as impersonator:
        impersonator.cookies.set("zhiwo_visitor", aid)
        assert identity(impersonator, False) != aid


def test_search_nickname_duplicate_case_ids_and_private_fields(community):
    a, b, _, _ = community
    identity(a, nickname="同名同学")
    bid = identity(b, nickname="同名同学")
    with TestClient(a.app) as c:
        cid = identity(c, nickname="同名同学")
        rows = a.get("/api/social/users", params={"query": "同名"}).json()
        assert {r["user_id"] for r in rows} == {bid, cid}
        assert all(set(r) == {"user_id", "nickname", "avatar_url", "relationship"} for r in rows)
        assert "PRIVATE" not in json.dumps(rows)
        assert a.get("/api/social/users", params={"query": bid.lower()}).json()[0]["user_id"] == bid
        assert a.get("/api/social/users", params={"query": "%' OR 1=1 --"}).json() == []
        assert a.get("/api/social/users", params={"query": "x"}).status_code == 422


def test_friend_consent_direction_and_cross_requests(community):
    a, b, _, _ = community
    aid, bid = identity(a), identity(b)
    assert action(a, aid, "request").status_code == 422
    assert send(a, bid).status_code == 403
    assert a.get(f"/api/social/chats/{bid}").status_code == 403
    assert action(a, bid, "request").json()["relationship"] == "outgoing"
    assert action(a, bid, "request").json()["relationship"] == "outgoing"
    assert action(a, bid, "accept").status_code == 409
    assert action(b, aid, "request").json()["relationship"] == "incoming"
    assert send(b, aid).status_code == 403
    with TestClient(a.app) as c:
        identity(c)
        assert action(c, aid, "accept").status_code == 409
    assert len(b.get("/api/social/contacts").json()["incoming"]) == 1
    assert action(b, aid, "accept").json()["relationship"] == "friend"
    assert action(b, aid, "accept").status_code == 409
    assert len(a.get("/api/social/contacts").json()["friends"]) == 1


def test_discovery_required_cancel_and_decline_cooldown(community):
    a, b, _, _ = community
    aid, bid = identity(a), identity(b, False)
    assert action(a, bid, "request").status_code == 403
    identity(b)
    action(a, bid, "request")
    assert action(a, bid, "cancel").status_code == 200
    assert action(a, bid, "request").status_code == 200
    assert action(b, aid, "decline").status_code == 200
    assert action(a, bid, "request").status_code == 429


def test_messages_unicode_persistence_idempotency_and_isolation(community):
    a, b, repo, _ = community
    aid, bid = friends(a, b)
    first = send(a, bid, text="第一行\n第二行 <script>alert(1)</script> 📚")
    assert first.status_code == 200, first.text
    row = first.json()
    assert send(a, bid, text=row["text"]).json()["id"] == row["id"]
    assert send(a, bid, text="different").status_code == 409
    received = b.get(f"/api/social/chats/{aid}").json()["messages"][0]
    assert (
        received["text"] == row["text"] and not received["mine"] and received["client_id"] is None
    )
    with TestClient(a.app) as c:
        cid = identity(c)
        assert c.get(f"/api/social/chats/{aid}").status_code == 403
        assert c.post(f"/api/social/messages/{row['id']}/acquire").status_code == 404
        assert (
            c.post(f"/api/social/chats/{aid}/read", json={"message_id": row["id"]}).status_code
            == 404
        )
        action(a, cid, "request")
        action(c, aid, "accept")
        assert send(a, cid, text=row["text"]).status_code == 409
    owner, _, _ = repo.visitor(b.cookies.get("zhiwo_visitor"), [])
    assert SocialRepository(repo).chat(owner, aid, 0, 0)["messages"][0]["text"] == row["text"]


def test_cursor_pagination_unread_and_monotonic_read(community):
    a, b, _, _ = community
    aid, bid = friends(a, b)
    ids = []
    for n in range(55):
        r = send(a, bid, nonce=f"message-{n:04}", text=f"消息{n}")
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    contacts = b.get("/api/social/contacts").json()
    assert contacts["conversations"][0]["unread"] == 55
    page = b.get(f"/api/social/chats/{aid}").json()
    assert [r["id"] for r in page["messages"]] == ids[5:] and page["has_more"]
    older = b.get(f"/api/social/chats/{aid}", params={"before": ids[5]}).json()
    assert [r["id"] for r in older["messages"]] == ids[:5] and not older["has_more"]
    inc = b.get(f"/api/social/chats/{aid}", params={"after": ids[0]}).json()
    assert [r["id"] for r in inc["messages"]] == ids[1:51] and inc["has_more"]
    assert b.get(f"/api/social/chats/{aid}?after=1&before=2").status_code == 422
    assert b.post(f"/api/social/chats/{aid}/read", json={"message_id": ids[-1]}).status_code == 200
    b.post(f"/api/social/chats/{aid}/read", json={"message_id": ids[0]})
    assert b.get("/api/social/contacts").json()["conversations"][0]["unread"] == 0
    assert b.post(f"/api/social/chats/{aid}/read", json={"message_id": 999999}).status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"text": " "},
        {"text": "x" * 2001},
        {"text": "hi", "sender": "someone"},
        {"text": "hi", "client_id": "bad"},
        {"text": "hi", "attachment": attach(rights_confirmed=False)},
        {"text": "hi", "attachment": attach(resource_id="x" * 129)},
        {"text": "hi", "attachment": attach(card_ids=["x" * 129])},
        {"text": "hi", "attachment": attach(kind="unknown")},
    ],
)
def test_message_validation(community, payload):
    a, b, _, _ = community
    _, bid = friends(a, b)
    assert (
        a.post(
            f"/api/social/chats/{bid}", json={"client_id": "message-0001", **payload}
        ).status_code
        == 422
    )


def test_concurrent_retries_insert_exactly_once(community):
    a, b, _, _ = community
    _, bid = friends(a, b)

    def submit(_):
        with TestClient(a.app) as client:
            client.cookies.update(a.cookies)
            return send(client, bid)

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(submit, range(12)))
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    assert len(a.get(f"/api/social/chats/{bid}").json()["messages"]) == 1


def test_remove_block_unblock_and_discovery_off(community):
    a, b, _, _ = community
    aid, bid = friends(a, b)
    send(a, bid)
    b.post("/api/social/discoverability", json={"enabled": False})
    assert send(a, bid, "message-0002").status_code == 200
    assert action(a, bid, "remove").status_code == 200
    assert send(a, bid, "message-0003").status_code == 403
    assert not a.get(f"/api/social/chats/{bid}").json()["can_chat"]
    assert action(b, aid, "block").status_code == 200
    assert a.get("/api/social/users", params={"query": bid}).json() == []
    assert action(a, bid, "request").status_code == 403
    assert send(b, aid).status_code == 403
    assert len(b.get("/api/social/contacts").json()["blocked"]) == 1
    assert action(b, aid, "unblock").status_code == 200
    assert send(a, bid, "message-0004").status_code == 403
    assert len(a.get(f"/api/social/chats/{bid}").json()["messages"]) == 2


def test_book_sharing_dedup_ownership_and_no_progress_copy(community):
    a, b, _, assessments = community
    _, bid = friends(a, b)
    b.post("/api/library/books/book-0/remove")
    assert send(a, bid, attachment=attach(book_id="book-9")).status_code == 403
    r = send(a, bid, attachment=attach())
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    assert b.post(f"/api/social/messages/{mid}/acquire").json()["status"] == "added"
    assert b.post(f"/api/social/messages/{mid}/acquire").json()["status"] == "already_owned"
    assert len(b.get("/api/library").json()) == 5
    assert len(a.get("/api/community").json()["items"]) == 5
    assert "profile" not in r.text and "session" not in r.text
    assert assessments.get_session("not-copied") is None


def test_notes_immutable_retries_after_edit_archived_restore_and_private(community):
    a, b, _, _ = community
    aid, bid = friends(a, b)
    note = private_note(a)
    spec = attach(kind="note", resource_id=note["id"])
    assert send(b, aid, attachment=spec).status_code == 404
    first = send(a, bid, attachment=spec)
    assert first.status_code == 200, first.text
    mid = first.json()["id"]
    a.post(
        "/api/library/notes",
        json={"book_id": "book-0", "resource_id": note["id"], "title": "修改", "body": "已经修改"},
    )
    assert send(a, bid, attachment=spec).json() == first.json()
    saved = b.post(f"/api/social/messages/{mid}/acquire").json()
    copy = b.get("/api/library/resources").json()[0]
    assert copy["content"] == note["content"] and copy["id"] != note["id"]
    b.post(f"/api/library/resources/{saved['resource_id']}/archive")
    restored = b.post(f"/api/social/messages/{mid}/acquire").json()
    assert restored["resource_id"] == saved["resource_id"] and restored["status"] == "added"
    assert len(b.get("/api/library/resources").json()) == 1
    assert len(a.get("/api/community", params={"kind": "note"}).json()["items"]) == 0


@pytest.mark.parametrize("kind", ["flashcards", "chapter", "points"])
def test_course_attachment_safe_fields_selection_and_scope(community, kind):
    a, b, _, assessments = community
    aid, bid = friends(a, b)
    session, course = own_course(a, assessments)
    spec = attach(
        kind=kind,
        session_id=session.session_id,
        course_id=course.course_id,
        card_ids=[course.flashcards[0].card_id],
    )
    assert send(b, aid, attachment=spec).status_code == 403
    result = send(a, bid, attachment=spec)
    assert result.status_code == 200, result.text
    assert all(
        term not in result.text
        for term in [
            "SECRET",
            "reason_for_user",
            "mastery",
            "practice_items",
            "correct_answer",
            "user_id",
            "session_id",
        ]
    )
    if kind == "flashcards":
        cards = result.json()["attachment"]["content"]["cards"]
        assert len(cards) == 1 and set(cards[0]) == {"front", "back", "pages"}
        assert (
            send(a, bid, "invalid-cards", attachment={**spec, "card_ids": ["wrong"]}).status_code
            == 422
        )
    mid = result.json()["id"]
    assert b.post(f"/api/social/messages/{mid}/acquire").json()["status"] == "added"
    resource = b.get("/api/library/resources").json()[0]
    assert resource["kind"] == ("flashcards" if kind == "flashcards" else "note")
    assert resource["content"] == result.json()["attachment"]["content"]
    assert send(a, bid, "wrong-book", attachment={**spec, "book_id": "book-1"}).status_code == 422


def test_csrf_and_unknown_actions(community):
    a, b, _, _ = community
    _, bid = friends(a, b)
    assert (
        a.post(
            "/api/social/discoverability",
            json={"enabled": True},
            headers={"Origin": "https://evil.test"},
        ).status_code
        == 403
    )
    assert (
        a.post(
            f"/api/social/chats/{bid}",
            json={"client_id": "message-001", "text": "hi"},
            headers={"Origin": "https://evil.test"},
        ).status_code
        == 403
    )
    assert action(a, bid, "destroy").status_code == 422
    assert action(a, "ZW-NOTFOUND", "request").status_code == 404


def test_message_and_request_rate_limits(community):
    a, b, _, _ = community
    aid, bid = friends(a, b)
    for n in range(60):
        assert send(a, bid, f"message-{n:04}").status_code == 200
    assert send(a, bid, "over-limit").status_code == 429
    assert send(a, bid, "message-0000").status_code == 200
    for n in range(5):
        with TestClient(a.app) as c:
            cid = identity(c)
            assert action(a, cid, "request").status_code == (200 if n < 4 else 429)
    assert b.get(f"/api/social/chats/{aid}").status_code == 200


def test_avatar_disclosure_follows_visibility_friendship_and_block(community):
    a, b, _, _ = community
    aid, bid = identity(a), identity(b, False)
    assert (
        b.post(
            "/api/user/profile",
            json={
                "nickname": "头像同学",
                "age": 22,
                "bio": "PRIVATE BIO",
                "revision": 0,
                "avatar_data_url": avatar(),
            },
        ).status_code
        == 200
    )
    path = f"/api/social/users/{bid}/avatar"
    assert a.get(path).status_code == 404
    identity(b)
    image = a.get(path)
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert image.headers["cache-control"] == "private, no-store"
    action(a, bid, "request")
    action(b, aid, "accept")
    b.post("/api/social/discoverability", json={"enabled": False})
    assert a.get(path).status_code == 200
    action(b, aid, "block")
    assert a.get(path).status_code == 404
    assert a.get("/api/social/contacts").json()["conversations"] == []


def test_block_serializes_before_any_further_messages(community):
    a, b, repo, _ = community
    aid, bid = friends(a, b)
    action(b, aid, "block")

    def submit(n):
        with TestClient(a.app) as c:
            c.cookies.update(a.cookies)
            return send(c, bid, f"blocked-{n:04}").status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert set(pool.map(submit, range(10))) == {403}
    with repo.connect() as db:
        assert db.execute("SELECT count(*) FROM chat_messages").fetchone()[0] == 0


def test_attachment_without_source_and_cross_course_rejected(community):
    a, b, _, assessments = community
    _, bid = friends(a, b)
    assert send(a, bid, attachment=attach(kind="chapter")).status_code == 422
    assert (
        send(
            a, bid, attachment=attach(kind="points", session_id="missing", course_id="missing")
        ).status_code
        == 404
    )
    session, course = own_course(a, assessments)
    assert (
        send(
            a,
            bid,
            attachment=attach(
                kind="chapter", session_id=session.session_id, course_id="other-course"
            ),
        ).status_code
        == 404
    )


def test_oversized_snapshot_does_not_create_partial_message(community):
    a, b, repo, _ = community
    _, bid = friends(a, b)
    owner, _, _ = repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        SocialRepository(repo).send(owner, bid, "large-attachment", "", {"body": "字" * 40000})
    assert error.value.status_code == 413
    assert a.get(f"/api/social/chats/{bid}").json()["messages"] == []
