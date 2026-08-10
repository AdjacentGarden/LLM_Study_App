from __future__ import annotations

import re


IMPORTANT_TERMS = [
    "\u51cf\u6570\u5206\u88c2",
    "\u6709\u4e1d\u5206\u88c2",
    "\u540c\u6e90\u67d3\u8272\u4f53",
    "\u59d0\u59b9\u67d3\u8272\u5355\u4f53",
    "\u56db\u5206\u4f53",
    "\u53d7\u7cbe\u4f5c\u7528",
    "DNA",
    "RNA",
]


def tokenize(text: str, extra_terms: list[str] | None = None) -> list[str]:
    normalized = text.lower()
    ascii_tokens: list[str] = re.findall(r"[a-z0-9]+", normalized)
    tokens: list[str] = [*ascii_tokens, *_ascii_compounds(ascii_tokens)]
    terms = IMPORTANT_TERMS + [term for term in (extra_terms or []) if term]
    tokens.extend(term.lower() for term in terms if term.lower() in normalized)
    tokens.extend(_optional_jieba_tokens(normalized))
    cjk = [char for char in normalized if "\u4e00" <= char <= "\u9fff"]
    tokens.extend("".join(cjk[index : index + 2]) for index in range(max(0, len(cjk) - 1)))
    return [token for token in tokens if token]


def _ascii_compounds(tokens: list[str]) -> list[str]:
    """Bridge OCR lines that lose inter-word whitespace.

    PaddleOCR can legitimately return ``CELLDIAGRAM`` for visibly separated
    words.  Adding bounded adjacent compounds lets the query ``CELL DIAGRAM``
    match without rewriting the source text or applying domain-specific word
    dictionaries.
    """

    compounds: list[str] = []
    for width in (2, 3):
        for index in range(max(0, len(tokens) - width + 1)):
            value = "".join(tokens[index : index + width])
            if 4 <= len(value) <= 64:
                compounds.append(value)
    return compounds


def unique_tokens(text: str, extra_terms: list[str] | None = None) -> list[str]:
    return list(dict.fromkeys(tokenize(text, extra_terms=extra_terms)))


def _optional_jieba_tokens(text: str) -> list[str]:
    try:
        import jieba
    except Exception:
        return []
    return [token.strip().lower() for token in jieba.cut(text) if token.strip()]
