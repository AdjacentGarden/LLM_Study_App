"""Run against a private preview using fresh, disposable cookie identities.

No existing users or community posts are modified. Test identities opt out of
discovery and remove the test friendship on exit; persisted private test messages
remain available only to those discarded cookies.
"""

import argparse
import statistics
import time
import uuid

import httpx


def run(base: str) -> None:
    timings = []
    with (
        httpx.Client(base_url=base, timeout=20) as a,
        httpx.Client(base_url=base, timeout=20) as b,
        httpx.Client(base_url=base, timeout=20) as outsider,
    ):

        def call(c, method, path, body=None, status=200):
            start = time.monotonic()
            r = c.request(method, path, json=body)
            timings.append((time.monotonic() - start) * 1000)
            assert r.status_code == status, (path, r.status_code, r.text[:160])
            return r.json()

        aid = call(a, "GET", "/api/social/me")["user_id"]
        bid = call(b, "GET", "/api/social/me")["user_id"]
        assert aid != bid
        try:
            for c, name in [(a, "社交验收甲"), (b, "社交验收乙")]:
                p = call(c, "GET", "/api/user/profile")
                call(
                    c,
                    "POST",
                    "/api/user/profile",
                    {
                        "nickname": name,
                        "age": None,
                        "bio": "",
                        "revision": p["revision"],
                    },
                )
                call(c, "POST", "/api/social/discoverability", {"enabled": True})
            assert call(a, "GET", f"/api/social/users?query={bid}")[0]["user_id"] == bid
            call(a, "POST", f"/api/social/friends/{bid}/request")
            call(b, "POST", f"/api/social/friends/{aid}/accept")
            nonce = uuid.uuid4().hex
            payload = {
                "client_id": nonce,
                "text": "线上验收：你好！📚\n一起读一本好书。",
            }
            first = call(a, "POST", f"/api/social/chats/{bid}", payload)
            assert (
                call(a, "POST", f"/api/social/chats/{bid}", payload)["id"]
                == first["id"]
            )
            assert (
                call(b, "GET", f"/api/social/chats/{aid}")["messages"][0]["text"]
                == payload["text"]
            )
            call(outsider, "GET", f"/api/social/chats/{aid}", status=403)
            library = call(a, "GET", "/api/library")
            assert len(library) == 5
            book = library[0]["book_id"]
            call(b, "POST", f"/api/library/books/{book}/remove")
            attachment = {"kind": "book", "book_id": book, "rights_confirmed": True}
            msg = call(
                a,
                "POST",
                f"/api/social/chats/{bid}",
                {
                    "client_id": uuid.uuid4().hex,
                    "text": "推荐这本书",
                    "attachment": attachment,
                },
            )
            assert (
                call(b, "POST", f"/api/social/messages/{msg['id']}/acquire")["status"]
                == "added"
            )
            assert (
                call(b, "POST", f"/api/social/messages/{msg['id']}/acquire")["status"]
                == "already_owned"
            )
            assert len(call(b, "GET", "/api/library")) == 5
            note = call(
                b,
                "POST",
                "/api/library/notes",
                {
                    "book_id": book,
                    "title": "一份私信分享笔记",
                    "body": "先建立全书框架，再带着问题阅读。",
                },
            )
            msg = call(
                b,
                "POST",
                f"/api/social/chats/{aid}",
                {
                    "client_id": uuid.uuid4().hex,
                    "text": "这是我的阅读方法。",
                    "attachment": {
                        "kind": "note",
                        "book_id": book,
                        "resource_id": note["id"],
                        "rights_confirmed": True,
                    },
                },
            )
            call(a, "POST", f"/api/social/messages/{msg['id']}/acquire")
            assert (
                call(a, "GET", "/api/library/resources")[0]["content"]["body"]
                == "先建立全书框架，再带着问题阅读。"
            )
            call(a, "POST", f"/api/social/chats/{bid}/read", {"message_id": msg["id"]})
            assert (
                call(a, "GET", "/api/social/contacts")["conversations"][0]["unread"]
                == 0
            )
            with httpx.Client(base_url=base, cookies=b.cookies, timeout=20) as restored:
                assert call(restored, "GET", "/api/social/me")["user_id"] == bid
                assert (
                    len(call(restored, "GET", f"/api/social/chats/{aid}")["messages"])
                    == 3
                )
            for _ in range(10):
                call(a, "GET", f"/api/social/chats/{bid}?after={msg['id']}")
            call(b, "POST", f"/api/social/friends/{aid}/block")
            call(
                a,
                "POST",
                f"/api/social/chats/{bid}",
                {"client_id": uuid.uuid4().hex, "text": "blocked"},
                status=403,
            )
            call(b, "POST", f"/api/social/friends/{aid}/unblock")
            print(
                {
                    "checks": "passed",
                    "requests": len(timings),
                    "median_ms": round(statistics.median(timings), 1),
                    "p95_ms": round(sorted(timings)[int(0.95 * (len(timings) - 1))], 1),
                    "library_books": len(library),
                }
            )
        finally:
            for c in (a, b):
                c.post("/api/social/discoverability", json={"enabled": False})
            a.post(f"/api/social/friends/{bid}/remove")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:18100")
    run(parser.parse_args().base)
