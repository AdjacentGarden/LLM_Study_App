from __future__ import annotations

from app.rag.service import _quote_window


def test_quote_window_centers_exact_query_identifier() -> None:
    text = "preface " * 80 + "The release code is ORCHID-742 and must be preserved. " + "footer " * 80
    quote = _quote_window(text, "What is the ORCHID-742 release code?")

    assert "ORCHID-742" in quote
    assert len(quote) <= 362
    assert quote.startswith("…")
    assert quote.endswith("…")


def test_quote_window_keeps_short_source_verbatim() -> None:
    assert _quote_window("减数分裂产生单倍体细胞。", "会产生什么细胞？") == "减数分裂产生单倍体细胞。"
