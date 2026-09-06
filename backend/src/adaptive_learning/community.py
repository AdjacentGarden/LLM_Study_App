"""Private-preview community: shared parsed assets, isolated visitor libraries.

Opaque HttpOnly visitor credentials are not a replacement for production accounts.
Only book/content snapshots are shared, never learner profiles or assessment answers.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast


def text_fingerprint(text: str) -> str | None:
    normalized = "".join(unicodedata.normalize("NFKC", text).split())
    if len(normalized) < 200:
        return None  # Never merge books from a short title or a shared cover caption.
    return hashlib.sha256(normalized.encode()).hexdigest()


class CommunityRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS visitors (
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS user_profiles (
                    owner TEXT PRIMARY KEY, nickname TEXT NOT NULL, age INTEGER,
                    bio TEXT NOT NULL, avatar BLOB, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS assets (
                    book_id TEXT PRIMARY KEY, canonical TEXT NOT NULL,
                    source_hash TEXT NOT NULL, text_hash TEXT, catalog TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS assets_source ON assets(source_hash);
                CREATE INDEX IF NOT EXISTS assets_text ON assets(text_hash);
                CREATE TABLE IF NOT EXISTS library_books (
                    owner TEXT NOT NULL, canonical TEXT NOT NULL, added REAL NOT NULL,
                    PRIMARY KEY(owner, canonical));
                CREATE TABLE IF NOT EXISTS removed_books (
                    owner TEXT NOT NULL, canonical TEXT NOT NULL,
                    PRIMARY KEY(owner, canonical));
                CREATE TABLE IF NOT EXISTS resources (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                    book_id TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL,
                    source_post TEXT, archived INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL, UNIQUE(owner, source_post));
                CREATE TABLE IF NOT EXISTS posts (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                    book_id TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
                    content TEXT NOT NULL, fingerprint TEXT UNIQUE NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS acquisitions (
                    owner TEXT NOT NULL, post_id TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(owner, post_id));
                CREATE TABLE IF NOT EXISTS session_owners (
                    session_id TEXT PRIMARY KEY, owner TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def register_asset(
        self, catalog: dict[str, Any], source_hash: str, text_hash: str | None
    ) -> str:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            match = db.execute(
                "SELECT canonical FROM assets WHERE source_hash=? OR (text_hash IS NOT NULL AND text_hash=?) LIMIT 1",
                (source_hash, text_hash),
            ).fetchone()
            canonical = str(match[0]) if match else str(catalog["book_id"])
            db.execute(
                "INSERT INTO assets VALUES (?,?,?,?,?) ON CONFLICT(book_id) DO UPDATE SET catalog=excluded.catalog",
                (
                    catalog["book_id"],
                    canonical,
                    source_hash,
                    text_hash,
                    json.dumps(catalog, ensure_ascii=False),
                ),
            )
            return canonical

    def asset(self, book_id: str) -> sqlite3.Row | None:
        with self.connect() as db:
            return cast(
                sqlite3.Row | None,
                db.execute("SELECT * FROM assets WHERE book_id=?", (book_id,)).fetchone(),
            )

    def visitor(self, token: str | None, initial: list[str]) -> tuple[str, str, bool]:
        with self.connect() as db:
            if token and len(token) <= 128:
                found = db.execute(
                    "SELECT id FROM visitors WHERE token_hash=?",
                    (hashlib.sha256(token.encode()).hexdigest(),),
                ).fetchone()
                if found:
                    return str(found[0]), token, False
            token = secrets.token_urlsafe(32)
            owner = uuid.uuid4().hex
            db.execute(
                "INSERT INTO visitors VALUES (?,?,?)",
                (owner, hashlib.sha256(token.encode()).hexdigest(), time.time()),
            )
            for canonical in initial:
                db.execute(
                    "INSERT OR IGNORE INTO library_books VALUES (?,?,?)",
                    (owner, canonical, time.time()),
                )
            return owner, token, True

    def library(self, owner: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT a.catalog FROM library_books l JOIN assets a ON a.book_id=l.canonical WHERE l.owner=? ORDER BY l.added,a.book_id",
                (owner,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def owns(self, owner: str, book_id: str) -> bool:
        with self.connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM library_books l JOIN assets a ON a.canonical=l.canonical WHERE l.owner=? AND a.book_id=?",
                    (owner, book_id),
                ).fetchone()
                is not None
            )

    def remove_book(self, owner: str, book_id: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO removed_books SELECT owner,canonical FROM library_books WHERE owner=? AND canonical=(SELECT canonical FROM assets WHERE book_id=?)",
                (owner, book_id),
            )
            db.execute(
                "DELETE FROM library_books WHERE owner=? AND canonical=(SELECT canonical FROM assets WHERE book_id=?)",
                (owner, book_id),
            )

    def restore_book(self, owner: str, book_id: str) -> bool:
        with self.connect() as db:
            return bool(
                db.execute(
                    "INSERT OR IGNORE INTO library_books SELECT owner,canonical,? FROM removed_books WHERE owner=? AND canonical=(SELECT canonical FROM assets WHERE book_id=?)",
                    (time.time(), owner, book_id),
                ).rowcount
            )

    def claim_session(self, owner: str, session_id: str) -> bool:
        # Existing opaque session IDs are bearer capabilities in the legacy app.
        # Bind on first use so another visitor cannot subsequently publish from it.
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO session_owners VALUES (?,?)", (session_id, owner))
            return bool(
                db.execute(
                    "SELECT owner FROM session_owners WHERE session_id=?", (session_id,)
                ).fetchone()[0]
                == owner
            )

    def resources(self, owner: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM resources WHERE owner=? AND archived=0 ORDER BY created DESC",
                (owner,),
            ).fetchall()
        return [self.resource_dict(row) for row in rows]

    @staticmethod
    def resource_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result.pop("owner", None)
        result["content"] = json.loads(result["content"])
        return result

    def resource(self, owner: str, resource_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM resources WHERE owner=? AND id=? AND archived=0",
                (owner, resource_id),
            ).fetchone()
        return self.resource_dict(row) if row else None

    def save_note(
        self, owner: str, book_id: str, title: str, body: str, resource_id: str | None = None
    ) -> str:
        asset = self.asset(book_id)
        if asset is None:
            raise KeyError("book missing")
        book_id = str(asset["canonical"])
        with self.connect() as db:
            if resource_id:
                changed = db.execute(
                    "UPDATE resources SET title=?,content=? WHERE id=? AND owner=? AND book_id=? AND kind='note' AND archived=0 AND source_post IS NULL",
                    (
                        title,
                        json.dumps({"body": body}, ensure_ascii=False),
                        resource_id,
                        owner,
                        book_id,
                    ),
                )
                if changed.rowcount != 1:
                    raise KeyError("note not editable")
                return resource_id
            resource_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO resources(id,owner,kind,book_id,title,content,created) VALUES (?,?,'note',?,?,?,?)",
                (
                    resource_id,
                    owner,
                    book_id,
                    title,
                    json.dumps({"body": body}, ensure_ascii=False),
                    time.time(),
                ),
            )
        return resource_id

    def publish(
        self,
        owner: str,
        kind: str,
        book_id: str,
        title: str,
        description: str,
        content: dict[str, Any],
    ) -> tuple[str, bool]:
        asset = self.asset(book_id)
        if asset is None:
            raise KeyError("book missing")
        canonical = asset["canonical"]
        basis = (
            f"book:{canonical}"
            if kind == "book"
            else json.dumps(
                [owner, kind, canonical, title, content], sort_keys=True, ensure_ascii=False
            )
        )
        fingerprint = hashlib.sha256(basis.encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT id,active,owner FROM posts WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
            if old:
                if not old["active"]:
                    db.execute(
                        "UPDATE posts SET active=1,owner=?,created=? WHERE id=?",
                        (owner, time.time(), old["id"]),
                    )
                return str(old["id"]), bool(old["active"])
            post_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO posts(id,owner,kind,book_id,title,description,content,fingerprint,created) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    post_id,
                    owner,
                    kind,
                    canonical,
                    title,
                    description,
                    json.dumps(content, ensure_ascii=False),
                    fingerprint,
                    time.time(),
                ),
            )
        return post_id, False

    def list_posts(self, owner: str, kind: str, search: str, page: int) -> dict[str, Any]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT p.*,
                (SELECT count(*) FROM acquisitions x WHERE x.post_id=p.id) AS downloads,
                EXISTS(SELECT 1 FROM library_books l WHERE l.owner=? AND l.canonical=p.book_id) AS book_owned,
                EXISTS(SELECT 1 FROM resources r WHERE r.owner=? AND r.source_post=p.id AND r.archived=0) AS resource_owned
                FROM posts p WHERE p.active=1 AND (?='all' OR p.kind=?) AND instr(lower(p.title||' '||p.description),lower(?))>0
                ORDER BY p.created DESC,p.id LIMIT 51 OFFSET ?""",
                (owner, owner, kind, kind, search, page * 50),
            ).fetchall()
        result = []
        for row in rows[:50]:
            item = dict(row)
            item["mine"] = item.pop("owner") == owner
            item["author"] = (
                "体验书库" if row["owner"] == "curated" else f"书友 {str(row['owner'])[:4]}"
            )
            item["in_library"] = bool(
                item.pop("book_owned") if item["kind"] == "book" else item.pop("resource_owned")
            )
            item.pop("book_owned", None)
            item.pop("resource_owned", None)
            item.pop("fingerprint", None)
            item["content"] = json.loads(item["content"])
            asset = self.asset(item["book_id"])
            item["book"] = json.loads(asset["catalog"]) if asset else None
            result.append(item)
        return {"items": result, "has_more": len(rows) > 50}

    def acquire(self, owner: str, post_id: str) -> dict[str, Any]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            post = db.execute("SELECT * FROM posts WHERE id=? AND active=1", (post_id,)).fetchone()
            if not post:
                raise KeyError("post missing")
            resource_id = None
            if post["kind"] == "book":
                inserted = db.execute(
                    "INSERT OR IGNORE INTO library_books VALUES (?,?,?)",
                    (owner, post["book_id"], time.time()),
                ).rowcount
            else:
                old = db.execute(
                    "SELECT id,archived FROM resources WHERE owner=? AND source_post=?",
                    (owner, post_id),
                ).fetchone()
                resource_id = str(old["id"]) if old else uuid.uuid4().hex
                inserted = int(not old or old["archived"])
                if old:
                    db.execute("UPDATE resources SET archived=0 WHERE id=?", (resource_id,))
                else:
                    db.execute(
                        "INSERT INTO resources(id,owner,kind,book_id,title,content,source_post,created) VALUES (?,?,?,?,?,?,?,?)",
                        (
                            resource_id,
                            owner,
                            post["kind"],
                            post["book_id"],
                            post["title"],
                            post["content"],
                            post_id,
                            time.time(),
                        ),
                    )
            db.execute(
                "INSERT OR IGNORE INTO acquisitions VALUES (?,?,?)", (owner, post_id, time.time())
            )
        return {
            "status": "added" if inserted else "already_owned",
            "book_id": post["book_id"],
            "kind": post["kind"],
            "resource_id": resource_id,
        }

    def withdraw(self, owner: str, post_id: str) -> bool:
        with self.connect() as db:
            return bool(
                db.execute(
                    "UPDATE posts SET active=0 WHERE owner=? AND id=?", (owner, post_id)
                ).rowcount
            )

    def archive_resource(self, owner: str, resource_id: str, archived: bool) -> bool:
        with self.connect() as db:
            return bool(
                db.execute(
                    "UPDATE resources SET archived=? WHERE owner=? AND id=?",
                    (int(archived), owner, resource_id),
                ).rowcount
            )
