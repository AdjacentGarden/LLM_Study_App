import hashlib
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_community import community as community

from adaptive_learning.accounts import COOKIE, Accounts
from adaptive_learning.api.account_guard import learning_guard

REG = {
    "nickname": "新读者",
    "stage": "university",
    "interests": ["science", "language"],
    "goal": "interest",
}


@pytest.fixture(autouse=True)
def demo(monkeypatch):
    monkeypatch.setenv("AUTH_DEMO_MODE", "1")
    for key in ("SMTP_HOST", "SMTP_FROM", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(key, raising=False)


def challenge(client, email="reader@zhiwo.test", purpose="register"):
    r = client.post("/api/auth/code", json={"email": email, "purpose": purpose})
    assert r.status_code == 200, r.text
    return r.json()


def verify(client, value, registration=REG, code=None):
    return client.post(
        "/api/auth/verify",
        json={
            "challenge_id": value["challenge_id"],
            "code": code or value["demo_code"],
            "registration": registration,
        },
    )


def cooldown(repo, email="reader@zhiwo.test"):
    with repo.connect() as db:
        db.execute("UPDATE email_codes SET created=created-61 WHERE email=?", (email,))


def test_signup_persists_unique_id_account_fields_and_rotates_visitor(community):
    a, b, repo, _ = community
    before = a.get("/api/auth/me").json()
    old = a.cookies.get("zhiwo_visitor")
    result = verify(a, challenge(a))
    assert result.status_code == 200, result.text
    assert result.json()["user_id"] == before["user_id"]
    assert result.json()["account"]["interests"] == REG["interests"]
    assert result.json()["account"]["is_demo"]
    cookie = a.cookies.get(COOKIE)
    assert cookie and cookie not in result.text
    assert (
        "HttpOnly" in result.headers["set-cookie"]
        and "SameSite=lax" in result.headers["set-cookie"]
    )
    assert a.get("/api/user/profile").json()["nickname"] == "新读者"
    assert a.get("/api/auth/me").json()["account"]["email"] == "reader@zhiwo.test"
    assert b.get("/api/auth/me").json()["account"] is None
    with TestClient(a.app) as attacker:
        attacker.cookies.set("zhiwo_visitor", old)
        assert attacker.get("/api/auth/me").json()["user_id"] != before["user_id"]
    with repo.connect() as db:
        assert (
            db.execute("SELECT token_hash FROM account_sessions").fetchone()[0]
            == hashlib.sha256(cookie.encode()).hexdigest()
        )
        assert db.execute("SELECT used FROM email_codes").fetchone()[0] == 1


def test_cross_device_login_and_logout_revoke_only_current_session(community):
    a, b, repo, _ = community
    original = verify(a, challenge(a)).json()
    cooldown(repo)
    result = verify(b, challenge(b, purpose="login"), None)
    assert result.status_code == 200, result.text
    assert result.json()["user_id"] == original["user_id"]
    assert a.cookies.get(COOKIE) != b.cookies.get(COOKIE)
    token = b.cookies.get(COOKIE)
    assert b.post("/api/auth/logout").status_code == 200
    assert b.get("/api/auth/me").json()["account"] is None
    assert a.get("/api/auth/me").json()["account"] is not None
    with TestClient(a.app) as stolen:
        stolen.cookies.set(COOKIE, token)
        assert stolen.get("/api/social/me").status_code == 401


def test_wrong_code_attempts_commit_and_lock_after_five(community):
    a, _, repo, _ = community
    c = challenge(a)
    wrong = "000000" if c["demo_code"] != "000000" else "111111"
    for _ in range(5):
        assert verify(a, c, code=wrong).status_code == 400
    assert verify(a, c).status_code == 400
    with repo.connect() as db:
        assert (
            db.execute(
                "SELECT attempts FROM email_codes WHERE id=?", (c["challenge_id"],)
            ).fetchone()[0]
            == 5
        )
        assert db.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0


def test_expiry_resend_and_cross_browser_challenge_binding(community):
    a, b, repo, _ = community
    c = challenge(a)
    assert verify(b, c).status_code == 400
    assert (
        a.post(
            "/api/auth/code", json={"email": "reader@zhiwo.test", "purpose": "register"}
        ).status_code
        == 429
    )
    cooldown(repo)
    second = challenge(a)
    assert verify(a, c).status_code == 400
    with repo.connect() as db:
        db.execute(
            "UPDATE email_codes SET expires=? WHERE id=?", (time.time() - 1, second["challenge_id"])
        )
    assert verify(a, second).status_code == 400


def test_single_use_concurrent_verify(community):
    a, _, repo, _ = community
    c = challenge(a)
    old_cookie = a.cookies.get("zhiwo_visitor")

    def attempt(_):
        with TestClient(a.app) as client:
            client.cookies.set("zhiwo_visitor", old_cookie)
            return verify(client, c).status_code

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(attempt, range(5)))
    assert results.count(200) == 1
    assert all(status in {200, 400, 401} for status in results)
    with repo.connect() as db:
        assert db.execute("SELECT count(*) FROM accounts").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM account_sessions").fetchone()[0] == 1


@pytest.mark.parametrize(
    "email",
    [
        "bad",
        "a@@b.com",
        "x\r\nBcc:y@test.com",
        "a..b@test.com",
        ".a@test.com",
        "a@-test.com",
        "a@te..st.com",
        "a@b.c",
        "a" * 65 + "@test.com",
    ],
)
def test_email_validation(community, email):
    a, *_ = community
    assert a.post("/api/auth/code", json={"email": email, "purpose": "register"}).status_code == 422


def test_missing_smtp_never_returns_real_email_code(community, monkeypatch):
    a, *_ = community
    r = a.post("/api/auth/code", json={"email": "real@example.com", "purpose": "register"})
    assert r.status_code == 503 and "demo_code" not in r.text
    monkeypatch.setenv("AUTH_DEMO_MODE", "0")
    assert (
        a.post(
            "/api/auth/code", json={"email": "reader@zhiwo.test", "purpose": "register"}
        ).status_code
        == 422
    )
    assert a.get("/api/auth/me").json()["demo_users"] == []


def test_demo_must_not_take_over_existing_guest_profile(community):
    a, *_ = community
    a.post(
        "/api/user/profile",
        json={"nickname": "真实资料", "age": 22, "bio": "保留我", "revision": 0},
    )
    assert (
        a.post(
            "/api/auth/code", json={"email": "reader@zhiwo.test", "purpose": "register"}
        ).status_code
        == 409
    )
    assert a.get("/api/user/profile").json()["bio"] == "保留我"


def test_smtp_transport_no_code_leak_and_guest_upgrade_preserves_data(community, monkeypatch):
    a, b, repo, _ = community
    captured = []
    monkeypatch.setattr(Accounts, "smtp_ready", staticmethod(lambda: True))
    monkeypatch.setattr(
        Accounts, "_email", staticmethod(lambda email, code: captured.append((email, code)))
    )
    a.post(
        "/api/user/profile", json={"nickname": "原昵称", "age": 22, "bio": "原简介", "revision": 0}
    )
    aid = a.get("/api/auth/me").json()["user_id"]
    booklist = a.get("/api/library").json()
    c = challenge(a, "REAL@example.com")
    assert c["delivery"] == "email" and "demo_code" not in c
    assert captured[0][0] == "real@example.com"
    assert verify(a, c, code=captured[0][1]).status_code == 200
    assert a.get("/api/auth/me").json()["user_id"] == aid
    assert a.get("/api/user/profile").json()["age"] == 22
    assert a.get("/api/user/profile").json()["bio"] == "原简介"
    assert a.get("/api/library").json() == booklist
    cooldown(repo, "real@example.com")
    c2 = challenge(b, "real@example.com", purpose="login")
    assert verify(b, c2, None, code=captured[-1][1]).status_code == 200
    assert b.get("/api/user/profile").json()["age"] == 22


def test_delivery_failure_invalidates_code(community, monkeypatch):
    a, _, repo, _ = community
    monkeypatch.setattr(Accounts, "smtp_ready", staticmethod(lambda: True))

    def fail(email, code):
        raise OSError("smtp network failed")

    monkeypatch.setattr(Accounts, "_email", staticmethod(fail))
    assert (
        a.post(
            "/api/auth/code", json={"email": "real@example.com", "purpose": "register"}
        ).status_code
        == 503
    )
    with repo.connect() as db:
        assert db.execute("SELECT used,ready FROM email_codes").fetchone()[:] == (1, 0)


def test_disabling_demo_rejects_existing_demo_session(community, monkeypatch):
    a, *_ = community
    verify(a, challenge(a))
    monkeypatch.setenv("AUTH_DEMO_MODE", "0")
    assert a.get("/api/auth/me").status_code == 401
    assert a.post("/api/auth/logout").status_code == 200


def test_csrf_registration_and_logout(community):
    a, *_ = community
    for path, body in [
        ("/api/auth/code", {"email": "reader@zhiwo.test", "purpose": "register"}),
        ("/api/auth/logout", {}),
    ]:
        assert a.post(path, json=body, headers={"Origin": "https://evil.test"}).status_code == 403


def test_learning_guard_and_account_session_listing(community):
    a, b, repo, assessments = community
    from test_social import own_course

    verify(a, challenge(a))
    session, _ = own_course(a, assessments)
    state = a.get("/api/auth/me").json()
    assert state["learning_sessions"]["book-0"] == session.session_id
    app = FastAPI()
    app.middleware("http")(learning_guard(Accounts(repo)))

    @app.get("/api/interviews/{sid}")
    def private(sid: str):
        return {"private": True}

    with TestClient(app) as client:
        assert client.get("/api/interviews/" + session.session_id).status_code == 403
        client.cookies.update(a.cookies)
        assert client.get("/api/interviews/" + session.session_id).status_code == 200
        assert (
            client.get(
                "/api/interviews/" + session.session_id, headers={"Origin": "http://elsewhere"}
            ).status_code
            == 200
        )


def test_demo_friends_are_explicit_idempotent_and_labelled(community):
    a, b, repo, _ = community
    for email in ["xiaolin@zhiwo.test", "momo@zhiwo.test", "achen@zhiwo.test"]:
        with TestClient(a.app) as seed:
            assert verify(seed, challenge(seed, email)).status_code == 200
    aid = a.get("/api/auth/me").json()["user_id"]
    assert a.get("/api/social/contacts").json()["friends"] == []
    result = a.post("/api/auth/demo-friends")
    assert result.status_code == 200, result.text
    assert result.json()["count"] == 3
    contacts = a.get("/api/social/contacts").json()
    assert all("演示" in x["nickname"] for x in contacts["friends"])
    assert len(contacts["conversations"]) == 3
    assert a.post("/api/auth/demo-friends").json()["count"] == 3
    with repo.connect() as db:
        assert db.execute("SELECT count(*) FROM chat_messages").fetchone()[0] == 3
    assert b.get("/api/social/contacts").json()["friends"] == []
    assert a.get("/api/auth/me").json()["user_id"] == aid
