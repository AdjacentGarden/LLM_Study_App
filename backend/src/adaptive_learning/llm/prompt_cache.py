from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def cached_input_tokens(usage: Mapping[str, Any]) -> int:
    """Prompt-cache hits across provider dialects.

    DeepSeek reports ``prompt_cache_hit_tokens`` at the top level; OpenAI-style
    providers nest ``cached_tokens`` under the token details; Anthropic-style
    gateways use ``cache_read_input_tokens``.
    """
    for key in ("cache_read_input_tokens", "prompt_cache_hit_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details")
    if isinstance(details, Mapping):
        value = details.get("cached_tokens")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0

