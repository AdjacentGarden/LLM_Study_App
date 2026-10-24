"""Deterministic, read-only context for OCR line anchors.

Only contiguous spans of the same immutable page can become quotations. Labels,
model summaries, whitespace normalization and guessed column order are never
sources. An unavailable, ambiguous or unsafe anchor retains its original quote.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from .models import BookStructure, PageExtraction, SourceQuote, TextBlock

logger = logging.getLogger(__name__)
MAX_CONTEXT_CHARACTERS = 500
_SENTENCE_END = re.compile(r"[。！？!?]|\.(?=\s|$)")
_BODY_KINDS = {"text", "paragraph", "body"}


def _single_position(text: str, anchor: str) -> int | None:
    start = text.find(anchor)
    if start < 0 or text.find(anchor, start + 1) >= 0:
        return None
    return start


def _same_flow(left: TextBlock, right: TextBlock) -> bool:
    if left.block_type.lower() not in _BODY_KINDS or right.block_type.lower() not in _BODY_KINDS:
        return False
    if left.metadata.get("text_level") == 1 or right.metadata.get("text_level") == 1:
        return False
    if left.bbox is None or right.bbox is None:
        # With no layout evidence, a block is the largest verified unit. The
        # raw page alone cannot disambiguate interleaved columns or captions.
        return False
    lx0, ly0, lx1, ly1 = left.bbox
    rx0, ry0, rx1, ry1 = right.bbox
    widths = (lx1 - lx0, rx1 - rx0)
    heights = (ly1 - ly0, ry1 - ry0)
    if min(*widths, *heights) <= 0:
        return False
    overlap = min(lx1, rx1) - max(lx0, rx0)
    aligned = min(abs(lx0 - rx0), abs(lx1 - rx1)) <= max(24, max(widths) * 0.15)
    return (
        overlap >= min(widths) * 0.6
        and aligned
        and ry0 - ly0 >= min(heights) * 0.5
        and -min(heights) * 0.25 <= ry0 - ly1 <= max(heights) * 1.5
    )


def _flow_bounds(page: PageExtraction, start: int, end: int) -> tuple[int, int] | None:
    text = page.raw_text
    located: list[tuple[int, int, TextBlock]] = []
    for block in page.blocks:
        if not block.text.strip():
            continue
        position = _single_position(text, block.text)
        if position is not None:
            located.append((position, position + len(block.text), block))
    located.sort(key=lambda item: (item[0], item[1]))
    owners = [i for i, (left, right, _) in enumerate(located) if left <= start and end <= right]
    if len(owners) != 1:
        # A page without blocks can still supply one explicit contiguous text
        # paragraph, but never infer links between several raw paragraphs.
        if not page.blocks:
            left = text.rfind("\n\n", 0, start)
            right = text.find("\n\n", end)
            return (0 if left < 0 else left + 2, len(text) if right < 0 else right)
        return None
    first = last = owners[0]
    if located[first][2].block_type.lower() not in _BODY_KINDS:
        return None
    while first > 0:
        previous, current = located[first - 1], located[first]
        if text[previous[1]:current[0]].strip() or not _same_flow(previous[2], current[2]):
            break
        first -= 1
    while last + 1 < len(located):
        current, following = located[last], located[last + 1]
        if text[current[1]:following[0]].strip() or not _same_flow(current[2], following[2]):
            break
        last += 1
    return located[first][0], located[last][1]


def expand_source_quote(source: SourceQuote, page: PageExtraction) -> SourceQuote:
    """Enrich one unique anchor; preserve its exact original text as metadata."""
    if source.page_number != page.page_number:
        return source.model_copy(deep=True)
    anchor = source.anchor_quote or source.quote
    start = _single_position(page.raw_text, anchor)
    if start is None:
        return source.model_copy(deep=True)
    end = start + len(anchor)
    bounds = _flow_bounds(page, start, end)
    if bounds is None:
        return source.model_copy(deep=True)
    flow_start, flow_end = bounds
    ends = [match.end() + flow_start for match in _SENTENCE_END.finditer(page.raw_text[flow_start:flow_end])]
    previous = [position for position in ends if position <= start]
    following = [position for position in ends if position >= end]
    left = previous[-1] if previous else flow_start
    right = following[0] if following else flow_end
    # Keep a preceding sentence when it fits: its negation or condition can be
    # essential even if this OCR line itself already ends in punctuation.
    richer_left = previous[-2] if len(previous) >= 2 else flow_start
    if right - richer_left <= MAX_CONTEXT_CHARACTERS:
        left = richer_left
    if right - left > MAX_CONTEXT_CHARACTERS:
        return source.model_copy(deep=True)
    context = page.raw_text[left:right].strip()
    if anchor not in context or not context or context == source.quote:
        return source.model_copy(deep=True)
    return SourceQuote(page_number=source.page_number, quote=context, anchor_quote=anchor)


def contextualize_structure(structure: BookStructure, pages: list[PageExtraction] | tuple[PageExtraction, ...]) -> BookStructure:
    """Project both fresh and stored structures identically without changing storage."""
    by_page: dict[int, PageExtraction] = {}
    duplicates: set[int] = set()
    for page in pages:
        if page.page_number in by_page:
            duplicates.add(page.page_number)
        by_page[page.page_number] = page
    result = structure.model_copy(deep=True)
    for chapter in result.chapters:
        def expand(source: SourceQuote, start_page: int = chapter.start_page,
                   end_page: int = chapter.end_page) -> SourceQuote:
            page = by_page.get(source.page_number)
            if (page is None or source.page_number in duplicates
                or not start_page <= source.page_number <= end_page):
                return source
            return expand_source_quote(source, page)
        chapter.evidence = [expand(source) for source in chapter.evidence]
        chapter.knowledge_point_evidence = {
            label: [expand(source) for source in sources]
            for label, sources in chapter.knowledge_point_evidence.items()
        }
    return result


@lru_cache(maxsize=16)
def _read_pages(path: str, mtime_ns: int, size: int) -> tuple[PageExtraction, ...]:
    from .chaptering import load_normalized_pages

    return tuple(load_normalized_pages(Path(path)))


def contextualize_stored_structure(structure: BookStructure, output_dir: Path) -> BookStructure:
    """Bounded parsed-page cache; absent or invalid legacy OCR never invents text."""
    path = output_dir / "normalized" / "pages.jsonl"
    try:
        info = path.stat()
        pages = _read_pages(str(path), info.st_mtime_ns, info.st_size)
    except (OSError, ValueError, TypeError, KeyError):
        logger.debug("source context unavailable; retaining stored quotation anchors")
        return structure
    return contextualize_structure(structure, pages)
