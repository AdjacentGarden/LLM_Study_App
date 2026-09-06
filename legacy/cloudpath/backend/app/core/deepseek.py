from __future__ import annotations

from typing import Any


def deepseek_payload_extras(thinking: str) -> dict[str, Any]:
    mode = thinking if thinking in {"enabled", "disabled"} else "disabled"
    return {"thinking": {"type": mode}}
