"""Consent-based friends and durable, participant-scoped messaging."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from typing import Any

from fastapi import HTTPException

from .community import CommunityRepository


class SocialRepository:
    def __init__(self, repo: CommunityRepository) -> None:
        self.repo = repo
        with repo.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS social_users(owner TEXT PRIMARY KEY,public_id TEXT UNIQUE NOT NULL,discoverable INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS friendships(lo TEXT NOT NULL,hi TEXT NOT NULL,requester TEXT NOT NULL,status TEXT NOT NULL,updated REAL NOT NULL,PRIMARY KEY(lo,hi));
                CREATE TABLE IF NOT EXISTS social_blocks(owner TEXT NOT NULL,target TEXT NOT NULL,PRIMARY KEY(owner,target));
                CREATE TABLE IF NOT EXISTS chat_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,lo TEXT NOT NULL,hi TEXT NOT NULL,sender TEXT NOT NULL,client_id TEXT NOT NULL,text TEXT NOT NULL,attachment TEXT,created REAL NOT NULL,UNIQUE(sender,client_id));
                CREATE INDEX IF NOT EXISTS chat_pair_cursor ON chat_messages(lo,hi,id);
                CREATE INDEX IF NOT EXISTS chat_sender_time ON chat_messages(sender,created);
                CREATE INDEX IF NOT EXISTS chat_lo_cursor ON chat_messages(lo,id);
                CREATE INDEX IF NOT EXISTS chat_hi_cursor ON chat_messages(hi,id);
                CREATE TABLE IF NOT EXISTS chat_reads(owner TEXT NOT NULL,peer TEXT NOT NULL,last_id INTEGER NOT NULL,PRIMARY KEY(owner,peer));
                CREATE TABLE IF NOT EXISTS social_rate(owner TEXT NOT NULL,created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS social_rate_time ON social_rate(owner,created);
            """)
            if "request_key" not in {
                row[1] for row in db.execute("PRAGMA table_info(chat_messages)")
            }:
                db.execute(
                    "ALTER TABLE chat_messages ADD COLUMN request_key TEXT NOT NULL DEFAULT ''"
                )
            for row in db.execute(
                "SELECT id FROM visitors WHERE id NOT IN (SELECT owner FROM social_users)"
            ).fetchall():
                self.ensure(db, str(row[0]))

    def ensure(self, db: sqlite3.Connection, owner: str) -> None:
        if db.execute("SELECT 1 FROM social_users WHERE owner=?", (owner,)).fetchone():
            return
        for _ in range(8):
            try:
                db.execute(
                    "INSERT INTO social_users(owner,public_id) VALUES (?,?)",
                    (owner, "ZW-" + secrets.token_hex(5).upper()),
                )
                return
            except sqlite3.IntegrityError:
                if db.execute("SELECT 1 FROM social_users WHERE owner=?", (owner,)).fetchone():
                    return
        raise RuntimeError("unable to allocate a unique public identity")

    @staticmethod
    def pair(owner: str, peer: str) -> tuple[str, str]:
        if owner == peer:
            raise HTTPException(422, "不能添加自己")
        return tuple(sorted((owner, peer)))  # type: ignore[return-value]

    @staticmethod
    def resolve(db: sqlite3.Connection, user_id: str) -> str:
        row = db.execute(
            "SELECT owner FROM social_users WHERE public_id=?", (user_id.upper(),)
        ).fetchone()
        if not row:
            raise HTTPException(404, "未找到这位用户")
        return str(row[0])

    @staticmethod
    def blocked(db: sqlite3.Connection, owner: str, peer: str) -> bool:
        return (
            db.execute(
                "SELECT 1 FROM social_blocks WHERE (owner=? AND target=?) OR (owner=? AND target=?)",
                (owner, peer, peer, owner),
            ).fetchone()
            is not None
        )

    def relationship(self, db: sqlite3.Connection, owner: str, peer: str) -> str:
        if owner == peer:
            return "self"
        if self.blocked(db, owner, peer):
            return "unavailable"
        row = db.execute(
            "SELECT * FROM friendships WHERE lo=? AND hi=?", self.pair(owner, peer)
        ).fetchone()
        if not row or row["status"] == "declined":
            return "none"
        return (
            "friend"
            if row["status"] == "accepted"
            else "outgoing"
            if row["requester"] == owner
            else "incoming"
        )

    def card(self, db: sqlite3.Connection, owner: str, peer: str) -> dict[str, Any]:
        row = db.execute(
            "SELECT s.public_id,s.discoverable,p.nickname,(p.avatar IS NOT NULL) AS has_avatar,p.revision FROM social_users s LEFT JOIN user_profiles p ON p.owner=s.owner WHERE s.owner=?",
            (peer,),
        ).fetchone()
        relation = self.relationship(db, owner, peer)
        return {
            "user_id": row["public_id"],
            "nickname": row["nickname"] or "新同学",
            "avatar_url": f"/api/social/users/{row['public_id']}/avatar?v={row['revision']}"
            if row["has_avatar"] and relation != "unavailable"
            else None,
            "relationship": relation,
        }

    def me(self, owner: str) -> dict[str, Any]:
        with self.repo.connect() as db:
            self.ensure(db, owner)
            result = self.card(db, owner, owner)
            result["discoverable"] = bool(
                db.execute(
                    "SELECT discoverable FROM social_users WHERE owner=?", (owner,)
                ).fetchone()[0]
            )
            return result

    def visibility(self, owner: str, visible: bool) -> dict[str, Any]:
        with self.repo.connect() as db:
            self.ensure(db, owner)
            db.execute(
                "UPDATE social_users SET discoverable=? WHERE owner=?", (int(visible), owner)
            )
        return self.me(owner)

    def search(self, owner: str, query: str) -> list[dict[str, Any]]:
        if len(query.strip()) < 2:
            return []
        with self.repo.connect() as db:
            rows = db.execute(
                """SELECT s.owner FROM social_users s LEFT JOIN user_profiles p ON s.owner=p.owner
                WHERE s.discoverable=1 AND s.owner<>? AND (upper(s.public_id)=upper(?) OR instr(lower(coalesce(p.nickname,'新同学')),lower(?))>0)
                AND NOT EXISTS(SELECT 1 FROM social_blocks b WHERE (b.owner=? AND b.target=s.owner) OR (b.owner=s.owner AND b.target=?))
                ORDER BY upper(s.public_id)=upper(?) DESC,s.public_id LIMIT 20""",
                (owner, query, query, owner, owner, query),
            ).fetchall()
            return [self.card(db, owner, str(row[0])) for row in rows]

    def change_friend(self, owner: str, user_id: str, action: str) -> dict[str, Any]:
        with self.repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.ensure(db, owner)
            peer = self.resolve(db, user_id)
            pair = self.pair(owner, peer)
            row = db.execute("SELECT * FROM friendships WHERE lo=? AND hi=?", pair).fetchone()
            relation = self.relationship(db, owner, peer)
            if action == "block":
                db.execute("INSERT OR IGNORE INTO social_blocks VALUES (?,?)", (owner, peer))
                db.execute("DELETE FROM friendships WHERE lo=? AND hi=?", pair)
            elif action == "unblock":
                db.execute("DELETE FROM social_blocks WHERE owner=? AND target=?", (owner, peer))
            elif action == "remove":
                if relation != "friend":
                    raise HTTPException(409, "你们当前不是好友")
                db.execute("DELETE FROM friendships WHERE lo=? AND hi=?", pair)
            elif self.blocked(db, owner, peer):
                raise HTTPException(403, "暂时无法与这位用户互动")
            elif action == "request":
                if relation in {"friend", "incoming", "outgoing"}:
                    return self.card(db, owner, peer)
                visible = db.execute(
                    "SELECT count(*) FROM social_users WHERE owner IN (?,?) AND discoverable=1",
                    (owner, peer),
                ).fetchone()[0]
                if visible != 2:
                    raise HTTPException(403, "请双方先开启好友查找")
                now = time.time()
                if row and row["status"] == "declined" and now - row["updated"] < 86400:
                    raise HTTPException(429, "请给对方一点时间，明天再试")
                db.execute("DELETE FROM social_rate WHERE created<?", (now - 86400,))
                if (
                    db.execute(
                        "SELECT count(*) FROM social_rate WHERE owner=? AND created>?",
                        (owner, now - 60),
                    ).fetchone()[0]
                    >= 5
                ):
                    raise HTTPException(429, "好友申请太频繁，请稍后再试")
                db.execute("INSERT INTO social_rate VALUES (?,?)", (owner, now))
                db.execute(
                    "INSERT INTO friendships VALUES (?,?,?,'pending',?) ON CONFLICT(lo,hi) DO UPDATE SET requester=excluded.requester,status='pending',updated=excluded.updated",
                    (*pair, owner, now),
                )
            elif action == "accept":
                if relation != "incoming":
                    raise HTTPException(409, "没有待接受的好友申请")
                db.execute(
                    "UPDATE friendships SET status='accepted',updated=? WHERE lo=? AND hi=?",
                    (time.time(), *pair),
                )
            elif action in {"decline", "cancel"}:
                if relation != ("incoming" if action == "decline" else "outgoing"):
                    raise HTTPException(409, "该申请已处理")
                if action == "cancel":
                    db.execute("DELETE FROM friendships WHERE lo=? AND hi=?", pair)
                else:
                    db.execute(
                        "UPDATE friendships SET status='declined',updated=? WHERE lo=? AND hi=?",
                        (time.time(), *pair),
                    )
            return self.card(db, owner, peer)

    def contacts(self, owner: str) -> dict[str, Any]:
        with self.repo.connect() as db:
            self.ensure(db, owner)
            rows = db.execute(
                "SELECT * FROM friendships WHERE (lo=? OR hi=?) AND status IN ('accepted','pending') ORDER BY updated DESC",
                (owner, owner),
            ).fetchall()
            result: dict[str, Any] = {
                "friends": [],
                "incoming": [],
                "outgoing": [],
                "blocked": [],
                "conversations": [],
            }
            for row in rows:
                peer = row["hi"] if row["lo"] == owner else row["lo"]
                card = self.card(db, owner, peer)
                if card["relationship"] == "unavailable":
                    continue
                key = (
                    "friends"
                    if row["status"] == "accepted"
                    else "outgoing"
                    if row["requester"] == owner
                    else "incoming"
                )
                result[key].append(card)
            for row in db.execute("SELECT target FROM social_blocks WHERE owner=?", (owner,)):
                result["blocked"].append(self.card(db, owner, row[0]))
            conversations = db.execute(
                """SELECT m.* FROM chat_messages m JOIN
                (SELECT max(id) id FROM chat_messages WHERE lo=? OR hi=? GROUP BY lo,hi) last ON last.id=m.id
                ORDER BY m.id DESC LIMIT 100""",
                (owner, owner),
            ).fetchall()
            for row in conversations:
                peer = row["hi"] if row["lo"] == owner else row["lo"]
                card = self.card(db, owner, peer)
                read = db.execute(
                    "SELECT last_id FROM chat_reads WHERE owner=? AND peer=?", (owner, peer)
                ).fetchone()
                unread = db.execute(
                    "SELECT count(*) FROM chat_messages WHERE lo=? AND hi=? AND sender<>? AND id>?",
                    (row["lo"], row["hi"], owner, read[0] if read else 0),
                ).fetchone()[0]
                card.update(
                    {
                        "preview": row["text"] or "[学习资料]",
                        "last_at": row["created"],
                        "unread": unread,
                    }
                )
                result["conversations"].append(card)
            return result

    @staticmethod
    def message(row: sqlite3.Row, owner: str) -> dict[str, Any]:
        return {
            "id": row["id"],
            "mine": row["sender"] == owner,
            "client_id": row["client_id"] if row["sender"] == owner else None,
            "text": row["text"],
            "attachment": json.loads(row["attachment"]) if row["attachment"] else None,
            "created": row["created"],
        }

    def chat(self, owner: str, user_id: str, after: int, before: int) -> dict[str, Any]:
        with self.repo.connect() as db:
            peer = self.resolve(db, user_id)
            pair = self.pair(owner, peer)
            relation = self.relationship(db, owner, peer)
            if (
                relation != "friend"
                and not db.execute(
                    "SELECT 1 FROM chat_messages WHERE lo=? AND hi=? LIMIT 1", pair
                ).fetchone()
            ):
                raise HTTPException(403, "成为好友后才能聊天")
            if after:
                rows = db.execute(
                    "SELECT * FROM chat_messages WHERE lo=? AND hi=? AND id>? ORDER BY id LIMIT 51",
                    (*pair, after),
                ).fetchall()
                messages = rows[:50]
            else:
                rows = db.execute(
                    "SELECT * FROM chat_messages WHERE lo=? AND hi=? AND (?=0 OR id<?) ORDER BY id DESC LIMIT 51",
                    (*pair, before, before),
                ).fetchall()
                messages = list(reversed(rows[:50]))
            return {
                "peer": self.card(db, owner, peer),
                "can_chat": relation == "friend",
                "messages": [self.message(row, owner) for row in messages],
                "has_more": len(rows) > 50,
            }

    def replay(
        self, owner: str, user_id: str, client_id: str, request_key: str
    ) -> dict[str, Any] | None:
        with self.repo.connect() as db:
            peer = self.resolve(db, user_id)
            if self.relationship(db, owner, peer) != "friend":
                raise HTTPException(403, "当前无法发送消息，请确认好友关系")
            old = db.execute(
                "SELECT * FROM chat_messages WHERE sender=? AND client_id=?", (owner, client_id)
            ).fetchone()
            if old:
                if (old["lo"], old["hi"]) != self.pair(owner, peer) or old[
                    "request_key"
                ] != request_key:
                    raise HTTPException(409, "这次发送标识已用于其他内容")
                return self.message(old, owner)
            return None

    def send(
        self,
        owner: str,
        user_id: str,
        client_id: str,
        text: str,
        attachment: dict[str, Any] | None,
        request_key: str = "",
    ) -> dict[str, Any]:
        serialized = (
            json.dumps(attachment, ensure_ascii=False, sort_keys=True) if attachment else None
        )
        if serialized and len(serialized.encode()) > 100_000:
            raise HTTPException(413, "资料太多，请减少分享的闪卡数量")
        with self.repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            peer = self.resolve(db, user_id)
            pair = self.pair(owner, peer)
            if self.relationship(db, owner, peer) != "friend":
                raise HTTPException(403, "当前无法发送消息，请确认好友关系")
            old = db.execute(
                "SELECT * FROM chat_messages WHERE sender=? AND client_id=?", (owner, client_id)
            ).fetchone()
            if old:
                if (
                    (old["lo"], old["hi"]) != pair
                    or old["text"] != text
                    or (
                        old["request_key"] != request_key
                        if request_key
                        else old["attachment"] != serialized
                    )
                ):
                    raise HTTPException(409, "这次发送已被使用，请刷新后重试")
                return self.message(old, owner)
            if (
                db.execute(
                    "SELECT count(*) FROM chat_messages WHERE sender=? AND created>?",
                    (owner, time.time() - 60),
                ).fetchone()[0]
                >= 60
            ):
                raise HTTPException(429, "发送太快了，稍等一下再试")
            cursor = db.execute(
                "INSERT INTO chat_messages(lo,hi,sender,client_id,text,attachment,created,request_key) VALUES (?,?,?,?,?,?,?,?)",
                (*pair, owner, client_id, text, serialized, time.time(), request_key),
            )
            return self.message(
                db.execute(
                    "SELECT * FROM chat_messages WHERE id=?", (cursor.lastrowid,)
                ).fetchone(),
                owner,
            )

    def read(self, owner: str, user_id: str, message_id: int) -> None:
        with self.repo.connect() as db:
            peer = self.resolve(db, user_id)
            pair = self.pair(owner, peer)
            if not db.execute(
                "SELECT 1 FROM chat_messages WHERE id=? AND lo=? AND hi=?", (message_id, *pair)
            ).fetchone():
                raise HTTPException(404, "消息不存在")
            db.execute(
                "INSERT INTO chat_reads VALUES (?,?,?) ON CONFLICT(owner,peer) DO UPDATE SET last_id=max(last_id,excluded.last_id)",
                (owner, peer, message_id),
            )

    def acquire(self, owner: str, message_id: int) -> dict[str, Any]:
        with self.repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM chat_messages WHERE id=? AND (lo=? OR hi=?)",
                (message_id, owner, owner),
            ).fetchone()
            if not row or not row["attachment"]:
                raise HTTPException(404, "这份资料不在你的会话中")
            attachment = json.loads(row["attachment"])
            book_id = attachment["book_id"]
            if attachment["kind"] == "book":
                added = db.execute(
                    "INSERT OR IGNORE INTO library_books VALUES (?,?,?)",
                    (owner, book_id, time.time()),
                ).rowcount
                resource_id = None
            else:
                source = f"chat:{message_id}"
                old = db.execute(
                    "SELECT id,archived FROM resources WHERE owner=? AND source_post=?",
                    (owner, source),
                ).fetchone()
                resource_id = old["id"] if old else secrets.token_hex(16)
                added = int(not old or old["archived"])
                if old:
                    db.execute("UPDATE resources SET archived=0 WHERE id=?", (resource_id,))
                else:
                    kind = "flashcards" if attachment["kind"] == "flashcards" else "note"
                    db.execute(
                        "INSERT INTO resources(id,owner,kind,book_id,title,content,source_post,created) VALUES (?,?,?,?,?,?,?,?)",
                        (
                            resource_id,
                            owner,
                            kind,
                            book_id,
                            attachment["title"],
                            json.dumps(attachment["content"], ensure_ascii=False),
                            source,
                            time.time(),
                        ),
                    )
            return {
                "status": "added" if added else "already_owned",
                "book_id": book_id,
                "resource_id": resource_id,
            }
