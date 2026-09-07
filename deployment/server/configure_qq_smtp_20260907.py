"""Validate QQ SMTP credentials and atomically enable real email delivery."""

from __future__ import annotations

import getpass
import os
import re
import shlex
import smtplib
import ssl
import tempfile
from pathlib import Path


SECRET_FILE = Path("/data1/zhenghang/adaptive-book-ocr/secrets/adaptive-book.env")


def replace_assignments(original: str, updates: dict[str, str]) -> str:
    lines = original.splitlines()
    found: set[str] = set()
    result: list[str] = []
    assignment = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
    for line in lines:
        match = assignment.match(line)
        key = match.group(1) if match else None
        if key in updates:
            result.append(f"{key}={shlex.quote(updates[key])}")
            found.add(key)
        else:
            result.append(line)
    if result and result[-1]:
        result.append("")
    for key, value in updates.items():
        if key not in found:
            result.append(f"{key}={shlex.quote(value)}")
    return "\n".join(result) + "\n"


def main() -> None:
    email = input("QQ email: ").strip().lower()
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}@qq\.com", email)
        or ".." in email
    ):
        raise SystemExit("invalid QQ email address")
    app_password = getpass.getpass("QQ SMTP authorization code: ").strip()
    if not app_password:
        raise SystemExit("authorization code must not be empty")

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=15, context=context) as smtp:
        smtp.login(email, app_password)
        smtp.noop()
    print("QQ SMTP authentication: ok")

    updates = {
        "SMTP_HOST": "smtp.qq.com",
        "SMTP_PORT": "465",
        "SMTP_SECURITY": "ssl",
        "SMTP_FROM": email,
        "SMTP_USER": email,
        "SMTP_PASSWORD": app_password,
    }
    original = SECRET_FILE.read_text(encoding="utf-8")
    payload = replace_assignments(original, updates)
    mode = SECRET_FILE.stat().st_mode & 0o777
    fd, temporary = tempfile.mkstemp(prefix="adaptive-book.env.", dir=SECRET_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, SECRET_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("real SMTP configuration enabled; secret values were not displayed")


if __name__ == "__main__":
    main()
