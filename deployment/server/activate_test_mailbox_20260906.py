"""Enable the loopback-only SMTP capture service without exposing secret values."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


SECRET_FILE = Path("/data1/zhenghang/adaptive-book-ocr/secrets/adaptive-book.env")
UPDATES = {
    "SMTP_HOST": "127.0.0.1",
    "SMTP_PORT": "1025",
    "SMTP_SECURITY": "plain",
    "SMTP_FROM": "cloudpath@test.invalid",
}


def main() -> None:
    original = SECRET_FILE.read_text(encoding="utf-8")
    lines = original.splitlines()
    found: set[str] = set()
    updated: list[str] = []
    assignment = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
    for line in lines:
        match = assignment.match(line)
        key = match.group(1) if match else None
        if key in UPDATES:
            updated.append(f"{key}={UPDATES[key]}")
            found.add(key)
        else:
            updated.append(line)
    if updated and updated[-1]:
        updated.append("")
    for key, value in UPDATES.items():
        if key not in found:
            updated.append(f"{key}={value}")
    payload = "\n".join(updated) + "\n"

    current_mode = SECRET_FILE.stat().st_mode & 0o777
    fd, temporary = tempfile.mkstemp(prefix="adaptive-book.env.", dir=SECRET_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, current_mode)
        os.replace(temporary, SECRET_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("configured loopback test SMTP keys:", ", ".join(UPDATES))


if __name__ == "__main__":
    main()
