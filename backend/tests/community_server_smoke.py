"""Opt-in live smoke: separate visitor cookies, real catalog, reversible test posts.

Run: .venv/bin/python tests/community_server_smoke.py http://127.0.0.1:18100
No credentials or existing learner records are read or modified.
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import httpx


def run(base: str) -> None:
    with httpx.Client(base_url=base, timeout=60) as a, httpx.Client(base_url=base, timeout=60) as b:

        def get(client, path):
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        def post(client, path, payload=None):
            response = client.post(path, json=payload)
            response.raise_for_status()
            return response.json()

        baseline = get(a, "/api/library")
        assert len(baseline) == 5
        assert len(get(b, "/api/library")) == 5
        posts = get(a, "/api/community?kind=book")["items"]
        curated = [p for p in posts if p["author"] == "体验书库"]
        assert len(curated) == 5
        report = {"initial_shelf": 5, "curated_community": 5, "books": []}
        for entry in curated:
            started = time.perf_counter()
            result = post(a, f"/api/community/{entry['id']}/acquire")
            assert result["status"] == "added"
            elapsed = round((time.perf_counter() - started) * 1000)
            structure = get(a, f"/api/books/{entry['book_id']}/structure")
            assert structure["chapters"]
            cover = a.get(entry["book"]["cover_url"])
            assert cover.status_code == 200 and cover.headers["content-type"].startswith(
                "image/jpeg"
            )
            with ThreadPoolExecutor(max_workers=6) as pool:
                duplicates = list(
                    pool.map(lambda _, post_id=entry["id"]: post(a, f"/api/community/{post_id}/acquire"), range(6))
                )
            assert all(result["status"] == "already_owned" for result in duplicates)
            report["books"].append(
                {
                    "title": entry["title"],
                    "join_ms": elapsed,
                    "chapters": len(structure["chapters"]),
                    "cover_ok": True,
                }
            )
        assert len(get(a, "/api/library")) == 10 and len(get(b, "/api/library")) == 5
        assert len(get(a, "/api/library/resources")) == 0

        # Only our newly created note and its explicit public snapshot are touched.
        resource = post(
            a,
            "/api/library/notes",
            {
                "book_id": baseline[0]["book_id"],
                "title": "社区功能验证（临时测试）",
                "body": "这是一篇测试笔记，验证私人保存、分享快照、领取与撤回。",
            },
        )
        share_id = None
        imported = None
        try:
            assert not get(b, "/api/library/resources")
            payload = {
                "kind": "note",
                "book_id": baseline[0]["book_id"],
                "resource_id": resource["id"],
                "rights_confirmed": True,
            }
            share_id = post(a, "/api/community/share", payload)["post_id"]
            imported = post(b, f"/api/community/{share_id}/acquire")["resource_id"]
            assert post(b, f"/api/community/{share_id}/acquire")["status"] == "already_owned"
            assert get(b, "/api/library/resources")[0]["content"] == resource["content"]
            assert b.post(f"/api/community/{share_id}/withdraw").status_code == 403
            post(a, f"/api/community/{share_id}/withdraw")
            assert a.get(f"/api/community/{share_id}/check").status_code == 404
            assert len(get(b, "/api/library/resources")) == 1
            post(b, f"/api/library/resources/{imported}/archive")
            assert get(b, "/api/library/resources") == []
            post(b, f"/api/library/resources/{imported}/archive?archived=false")
            assert get(b, "/api/library/resources")[0]["id"] == imported
            report["notes_lifecycle"] = "passed"
        finally:
            if share_id:
                post(a, f"/api/community/{share_id}/withdraw")
            post(a, f"/api/library/resources/{resource['id']}/archive")
            if imported:
                post(b, f"/api/library/resources/{imported}/archive")
        for entry in curated:
            post(a, f"/api/library/books/{entry['book_id']}/remove")
        assert len(get(a, "/api/library")) == 5
        report["isolation_and_concurrent_duplicates"] = "passed"
        report["test_posts_withdrawn_and_resources_archived"] = True
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run(sys.argv[1])
