"""Private append-only teaching diagnostics, separate from HTTP publication."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any
import uuid


class PrivateTeachingAudit:
    def __init__(self, directory: Path, *, context: dict[str, str] | None = None,
                 secrets: tuple[str, ...] = ()) -> None:
        self.directory = directory
        self.context = dict(context or {})
        self.secrets = tuple(value for value in secrets if value)

    def _redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: "[redacted]" if key.lower() in {
                "api_key", "authorization", "cookie", "cookies", "password", "private_key", "access_token"
            } else self._redact(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redact(item) for item in value]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[redacted]")
            # Also protect accidental provider-key echoes from malformed output.
            return re.sub(r"\bsk-[A-Za-z0-9_.-]{12,}", "[redacted]", value)
        return value

    def __call__(self, event: dict[str, Any]) -> None:
        value = self._redact({**self.context, **event})
        invocation = value.get("invocation_id") or uuid.uuid4().hex
        if not re.fullmatch(r"[0-9a-f]{32}", invocation):
            raise ValueError("invalid audit invocation")
        group = hashlib.sha256(str(value.get("session_hash") or value.get("course_id")
                                   or value.get("chapter_id") or "unknown").encode()).hexdigest()[:20]
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        parent = self.directory / group
        parent.mkdir(exist_ok=True, mode=0o700)
        directory = parent / invocation
        directory.mkdir(exist_ok=True, mode=0o700)
        path = directory / (str(time.time_ns()) + "-" + uuid.uuid4().hex + ".json")
        encoded = json.dumps({"recorded_unix": time.time(), **value}, ensure_ascii=False,
                             allow_nan=False, indent=2) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
