"""Email OTP accounts, revocable sessions, and an explicitly isolated demo realm."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from typing import Any

from fastapi import HTTPException, Request

from .community import CommunityRepository

DEMO_USERS = {
    "xiaolin@zhiwo.test": "小林 · 演示",
    "momo@zhiwo.test": "默默 · 演示",
    "achen@zhiwo.test": "阿辰 · 演示",
}
DEMO_EMAILS = {*DEMO_USERS, "reader@zhiwo.test"}
COOKIE = "zhiwo_account"
TTL = 30 * 86400


class Accounts:
    def __init__(self, repo: CommunityRepository):
        self.repo = repo
        key_path = repo.path.parent / "account-otp.key"
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as file:
                file.write(secrets.token_bytes(32))
        self.key = key_path.read_bytes()
        if len(self.key) != 32:
            raise RuntimeError("invalid OTP signing key")
        with repo.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts(owner TEXT PRIMARY KEY,email TEXT NOT NULL UNIQUE,stage TEXT NOT NULL,interests TEXT NOT NULL,goal TEXT NOT NULL,is_demo INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS account_sessions(token_hash TEXT PRIMARY KEY,owner TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS account_sessions_owner ON account_sessions(owner);
                CREATE TABLE IF NOT EXISTS email_codes(id TEXT PRIMARY KEY,owner TEXT NOT NULL,email TEXT NOT NULL,purpose TEXT NOT NULL,code_hash TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,used INTEGER NOT NULL DEFAULT 0,ready INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS codes_email_time ON email_codes(email,created);
                CREATE TABLE IF NOT EXISTS email_rate(ip_hash TEXT NOT NULL,owner TEXT NOT NULL,created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS email_rate_ip ON email_rate(ip_hash,created);
            """)

    @staticmethod
    def demo_enabled() -> bool:
        return os.getenv("AUTH_DEMO_MODE", "0") == "1"

    @staticmethod
    def smtp_ready() -> bool:
        return all(os.getenv(k) for k in ("SMTP_HOST", "SMTP_FROM", "SMTP_USER", "SMTP_PASSWORD"))

    def digest(self, value: str) -> str:
        return hmac.new(self.key, value.encode(), hashlib.sha256).hexdigest()

    def session_owner(self, token: str | None) -> str | None:
        if not token or len(token) > 128:
            return None
        with self.repo.connect() as db:
            row = db.execute(
                "SELECT s.owner FROM account_sessions s JOIN accounts a ON a.owner=s.owner WHERE token_hash=? AND expires>? AND (a.is_demo=0 OR ?=1)",
                (hashlib.sha256(token.encode()).hexdigest(), time.time(), int(self.demo_enabled())),
            ).fetchone()
            return str(row[0]) if row else None

    def request_owner(self, request: Request) -> str | None:
        if request.cookies.get(COOKIE):
            return self.session_owner(request.cookies[COOKIE])
        token = request.cookies.get("zhiwo_visitor", "")
        if not token or len(token) > 128:
            return None
        with self.repo.connect() as db:
            row = db.execute(
                "SELECT id FROM visitors WHERE token_hash=? AND NOT EXISTS(SELECT 1 FROM accounts WHERE owner=visitors.id)",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
            return str(row[0]) if row else None

    def account(self, owner: str) -> dict[str, Any] | None:
        with self.repo.connect() as db:
            row = db.execute(
                "SELECT email,stage,interests,goal,is_demo FROM accounts WHERE owner=?", (owner,)
            ).fetchone()
        return (
            {
                **dict(row),
                "interests": json.loads(row["interests"]),
                "is_demo": bool(row["is_demo"]),
            }
            if row
            else None
        )

    def has_profile(self, owner: str) -> bool:
        with self.repo.connect() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM user_profiles WHERE owner=? UNION SELECT 1 FROM session_owners WHERE owner=?",
                    (owner, owner),
                ).fetchone()
            )

    def send_code(self, owner: str, email: str, purpose: str, ip: str) -> dict[str, Any]:
        demo = self.demo_enabled() and email in DEMO_EMAILS
        if email.endswith(".test") and not demo:
            raise HTTPException(422, "请选择列表中的演示邮箱")
        if not demo and not self.smtp_ready():
            raise HTTPException(503, "真实邮箱验证暂未开通，请先使用演示账号体验")
        if purpose == "register" and self.account(owner):
            raise HTTPException(409, "请先退出当前账号，再注册新账号")
        if demo and purpose == "register" and self.has_profile(owner):
            raise HTTPException(
                409, "演示邮箱不能绑定已有资料。请使用真实邮箱，或在新的测试窗口注册"
            )
        challenge, code, now = (
            secrets.token_urlsafe(24),
            f"{secrets.randbelow(1000000):06d}",
            time.time(),
        )
        with self.repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM email_codes WHERE created<?", (now - 86400,))
            db.execute("DELETE FROM email_rate WHERE created<?", (now - 86400,))
            last = db.execute(
                "SELECT max(created),count(*) FROM email_codes WHERE email=? AND created>?",
                (email, now - 3600),
            ).fetchone()
            if last[0] and now - last[0] < 60:
                raise HTTPException(429, "请稍等 60 秒再获取验证码")
            limited = db.execute(
                "SELECT count(*) FROM email_rate WHERE (ip_hash=? OR owner=?) AND created>?",
                (self.digest(ip), owner, now - 3600),
            ).fetchone()[0]
            if last[1] >= 5 or limited >= 60:
                raise HTTPException(429, "验证码请求过于频繁，请稍后再试")
            # A resend invalidates only this browser's previous challenge, not someone else's login.
            db.execute(
                "UPDATE email_codes SET used=1 WHERE owner=? AND email=? AND purpose=?",
                (owner, email, purpose),
            )
            db.execute(
                "INSERT INTO email_codes(id,owner,email,purpose,code_hash,created,expires) VALUES(?,?,?,?,?,?,?)",
                (challenge, owner, email, purpose, self.digest(challenge + code), now, now + 600),
            )
            db.execute("INSERT INTO email_rate VALUES(?,?,?)", (self.digest(ip), owner, now))
            exists = bool(db.execute("SELECT 1 FROM accounts WHERE email=?", (email,)).fetchone())
        if not demo and (purpose == "register" or exists):
            try:
                self._email(email, code)
            except (OSError, smtplib.SMTPException, ValueError):
                with self.repo.connect() as db:
                    db.execute("UPDATE email_codes SET used=1 WHERE id=?", (challenge,))
                raise HTTPException(503, "验证邮件发送失败，请稍后再试") from None
        with self.repo.connect() as db:
            db.execute("UPDATE email_codes SET ready=1 WHERE id=?", (challenge,))
        result: dict[str, Any] = {
            "challenge_id": challenge,
            "expires_in": 600,
            "retry_after": 60,
            "delivery": "demo" if demo else "email",
            "message": "演示验证码，不会发送邮件"
            if demo
            else "如果邮箱可用于此操作，验证码将发送到该邮箱",
        }
        if demo:
            result["demo_code"] = code
        return result

    @staticmethod
    def _email(email: str, code: str) -> None:
        message = EmailMessage()
        message["Subject"] = "知我 · 邮箱验证码"
        message["From"] = os.environ["SMTP_FROM"]
        message["To"] = email
        message.set_content(
            f"你的知我邮箱验证码是：{code}\n10 分钟内有效，请勿向他人提供。\n如果不是你本人操作，请忽略这封邮件。"
        )
        host, port = os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "465"))
        context = ssl.create_default_context()
        if os.getenv("SMTP_SECURITY", "ssl") == "starttls":
            with smtplib.SMTP(host, port, timeout=12) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
                smtp.send_message(message)
        else:
            with smtplib.SMTP_SSL(host, port, timeout=12, context=context) as smtp:
                smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
                smtp.send_message(message)

    def complete(
        self,
        owner: str,
        challenge: str,
        code: str,
        registration: dict[str, Any] | None,
        previous_token: str | None,
    ) -> tuple[str, str]:
        failure: tuple[int, str] | None = None
        target, token = "", secrets.token_urlsafe(32)
        with self.repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM email_codes WHERE id=? AND owner=?", (challenge, owner)
            ).fetchone()
            if (
                not row
                or row["used"]
                or not row["ready"]
                or row["expires"] <= time.time()
                or row["attempts"] >= 5
            ):
                failure = (400, "验证码已失效，请重新获取")
            elif row["purpose"] != ("register" if registration else "login"):
                failure = (400, "验证码用途不匹配")
            elif not hmac.compare_digest(row["code_hash"], self.digest(challenge + code)):
                db.execute("UPDATE email_codes SET attempts=attempts+1 WHERE id=?", (challenge,))
                failure = (400, "验证码不正确，请检查后重试")
            else:
                found = db.execute(
                    "SELECT owner FROM accounts WHERE email=?", (row["email"],)
                ).fetchone()
                if registration and found:
                    failure = (409, "该邮箱已有账号，请切换到登录")
                elif not registration and not found:
                    failure = (400, "无法登录，请先注册或重新获取验证码")
                elif row["email"] in DEMO_EMAILS and not self.demo_enabled():
                    failure = (403, "演示登录已关闭")
                elif (
                    registration
                    and db.execute("SELECT 1 FROM accounts WHERE owner=?", (owner,)).fetchone()
                ):
                    failure = (409, "当前资料已绑定账号")
                elif (
                    registration
                    and row["email"] in DEMO_EMAILS
                    and db.execute(
                        "SELECT 1 FROM user_profiles WHERE owner=? UNION SELECT 1 FROM session_owners WHERE owner=?",
                        (owner, owner),
                    ).fetchone()
                ):
                    failure = (409, "不能将已有资料绑定到公开演示账号")
                else:
                    target = owner if registration else str(found[0])
                    if registration:
                        demo = row["email"] in DEMO_EMAILS
                        if demo and not self.demo_enabled():
                            failure = (403, "演示注册已关闭")
                        else:
                            db.execute(
                                "INSERT INTO accounts VALUES(?,?,?,?,?,?,?)",
                                (
                                    target,
                                    row["email"],
                                    registration["stage"],
                                    json.dumps(registration["interests"], ensure_ascii=False),
                                    registration["goal"],
                                    int(demo),
                                    time.time(),
                                ),
                            )
                            nickname = DEMO_USERS.get(row["email"], registration["nickname"])
                            db.execute(
                                "INSERT INTO user_profiles VALUES(?,?,NULL,'',NULL,1) ON CONFLICT(owner) DO UPDATE SET nickname=excluded.nickname,revision=user_profiles.revision+1",
                                (target, nickname),
                            )
                            # Retire the old anonymous credential; account IDs are not credentials.
                            db.execute(
                                "UPDATE visitors SET token_hash=? WHERE id=?",
                                (hashlib.sha256(secrets.token_bytes(32)).hexdigest(), target),
                            )
                    if not failure:
                        db.execute("UPDATE email_codes SET used=1 WHERE id=?", (challenge,))
                        if previous_token:
                            db.execute(
                                "DELETE FROM account_sessions WHERE token_hash=?",
                                (hashlib.sha256(previous_token.encode()).hexdigest(),),
                            )
                        db.execute("DELETE FROM account_sessions WHERE expires<?", (time.time(),))
                        db.execute(
                            "INSERT INTO account_sessions VALUES(?,?,?,?)",
                            (
                                hashlib.sha256(token.encode()).hexdigest(),
                                target,
                                time.time(),
                                time.time() + TTL,
                            ),
                        )
        if failure:
            raise HTTPException(*failure)
        return target, token

    def logout(self, token: str | None) -> None:
        if token:
            with self.repo.connect() as db:
                db.execute(
                    "DELETE FROM account_sessions WHERE token_hash=?",
                    (hashlib.sha256(token.encode()).hexdigest(),),
                )
