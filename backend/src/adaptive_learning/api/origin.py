from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

from fastapi import Request


def trusted_write_origin(request: Request, allowed_origins: Iterable[str] = ()) -> bool:
    """Accept the API host itself and explicitly configured frontend origins."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    normalized = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    configured = {value.strip().rstrip("/") for value in allowed_origins if value.strip()}
    return bool(
        normalized
        and (
            parsed.netloc == request.headers.get("host")
            or normalized.rstrip("/") in configured
        )
    )
