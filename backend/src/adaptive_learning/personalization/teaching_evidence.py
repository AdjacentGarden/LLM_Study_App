"""Whole-chapter OCR context with immutable, exact source quotations.

The model receives identifiers for these sources; it never supplies a new page
number or a replacement quotation. Display reflow is deliberately separate from
the verbatim citation used for verification.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..assessment.item_generation import stable_knowledge_point_id
from ..ingestion.coverage import coverage_context, page_is_skipped
from ..ingestion.models import ChapterDraft, PageExtraction
from .models import LessonSection, SourceCitation

EVIDENCE_VERSION = "whole-chapter-raw-spans-v1"
CANDIDATE_CONTEXT_VERSION = "candidate-context-v1"
MAX_SOURCE_CHARACTERS = 50_000
MAX_SPAN_CHARACTERS = 500
_BOUNDARY = re.compile(r"[。！？!?；;](?:[\"”’）)]*)|\n")


@dataclass(frozen=True)
class TeachingEvidence:
    sources: dict[str, SourceCitation]
    point_sources: dict[str, list[str]]
    warnings: list[str]
    fingerprint: str
    source_coverage: dict[str, Any] = field(default_factory=dict)


def bounded_teaching_sources(evidence: TeachingEvidence, context: Any = None) -> dict[str, Any]:
    """Keep the complete evidence registry; bound each model batch independently."""
    if sum(len(source.quote) for source in evidence.sources.values()) <= MAX_SOURCE_CHARACTERS:
        return {key: source.model_dump() for key, source in evidence.sources.items()}
    required: set[str] = set()
    texts: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, str):
            if value in evidence.sources:
                required.add(value)
            elif len(value) <= 1000:
                texts.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    visit(context)
    if context is None:
        required.update(key for ids in evidence.point_sources.values() for key in ids)
    query = _scope_terms(" ".join(texts))
    keys = list(evidence.sources)
    # Relevant source windows come first, followed by an even chapter overview.
    relevant = sorted(keys, key=lambda key: -len(query & _scope_terms(evidence.sources[key].quote)))
    overview = [keys[round(i * (len(keys) - 1) / max(1, min(40, len(keys)) - 1))]
                for i in range(min(40, len(keys)))]
    selected = {key: evidence.sources[key].model_dump() for key in keys if key in required}
    characters = sum(len(value["quote"]) for value in selected.values())
    for key in [*overview, *relevant]:
        if key in selected:
            continue
        source = evidence.sources[key]
        if characters + len(source.quote) <= MAX_SOURCE_CHARACTERS:
            selected[key] = source.model_dump()
            characters += len(source.quote)
    return selected


_SCOPE_MARKERS = re.compile(
    r"通常|一般|往往|部分|有些|某些|只有|只能|未必|不一定|并非|除非|除.{0,6}外|"
    r"但是|然而|终止|结束|前提|条件|至少|至多|不超过|可能|不能"
)
_COMMON_SCOPE_TERMS = {"可以", "通过", "过程", "作用", "一个", "一种", "进行", "发生",
                       "不同", "相同", "因为", "所以", "其中", "情况", "之间", "主要"}


def _scope_terms(text: str) -> set[str]:
    text = re.sub(r"\s+", "", text).lower()
    terms: set[str] = set(re.findall(r"[a-z][a-z0-9]*", text))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return terms - _COMMON_SCOPE_TERMS


def scope_context_source_ids(evidence: TeachingEvidence, label: str,
                             anchor_ids: Iterable[str], *, limit: int = 3) -> list[str]:
    """Prioritize possible scope boundaries without asserting they are facts.

    All chapter sources remain available. These are navigation candidates for
    comparison with an anchor, never mandatory citations or inferred exceptions.
    Shared terms are weighted by rarity, and different pages avoid repeated OCR
    windows crowding out a distant condition. No subject-specific fact is used.
    """
    if limit < 1:
        return []
    anchors = set(anchor_ids)
    terms = {key: _scope_terms(source.quote) for key, source in evidence.sources.items()}
    frequency = Counter(term for values in terms.values() for term in values)
    query = _scope_terms(label)
    ranked: list[tuple[float, str]] = []
    for key, source in evidence.sources.items():
        if key in anchors:
            continue
        scores = []
        for marker in _SCOPE_MARKERS.finditer(source.quote):
            # A condition elsewhere in a multi-topic paragraph must not make
            # an unrelated repeated definition look like its scope boundary.
            context = source.quote[max(0, marker.start() - 65):marker.end() + 65]
            shared = query & _scope_terms(context)
            if len(shared) < 2:
                continue
            weight = 2 if marker.group() in {"终止", "结束", "不一定", "并非", "除非", "但是", "然而"} else 1
            scores.append(weight * sum(math.log1p(len(terms) / frequency[term]) for term in shared))
        if scores:
            ranked.append((max(scores), key))
    chosen: list[str] = []
    pages: set[int] = set()
    for _, key in sorted(ranked, key=lambda row: (-row[0], row[1])):
        page = evidence.sources[key].page_number
        if page in pages:
            continue
        chosen.append(key)
        pages.add(page)
        if len(chosen) == limit:
            break
    return chosen


def candidate_context_source_ids(
    evidence: TeachingEvidence, fields: Iterable[str], anchor_ids: Iterable[str], *, limit: int = 2
) -> list[str]:
    """Find related text for actual claims without requiring a scope marker.

    A location, category or other structural qualifier need not contain words
    such as 'usually' or 'however'. Rank literal topic overlap with focused
    clauses, not a list of subject-specific conditions. These are navigation
    candidates only: relevance, entailment and scope remain reviewer decisions.
    """
    if limit < 1:
        return []
    terms = {key: _scope_terms(source.quote) for key, source in evidence.sources.items()}
    frequency = Counter(term for values in terms.values() for term in values)
    weights = {term: math.log1p(len(terms) / count) for term, count in frequency.items()}
    queries = [
        query
        for text in fields
        for clause in re.split(r"[。！？!?；;，,：:]", text)
        if len(query := _scope_terms(clause)) >= 4
    ]
    excluded = set(anchor_ids)
    ranked: list[tuple[float, str]] = []
    for key, source in evidence.sources.items():
        if key in excluded:
            continue
        segments = [_scope_terms(part) for part in re.split(r"[。！？!?；;]", source.quote)]
        scores = [
            sum(weights[term] for term in shared)
            / math.sqrt(sum(weights.get(term, math.log1p(len(terms))) for term in query))
            for query in queries
            for segment in segments
            if len(shared := query & segment) >= 4
        ]
        if scores:
            ranked.append((max(scores), key))
    chosen: list[str] = []
    pages: set[int] = set()
    for _, key in sorted(ranked, key=lambda row: (-row[0], row[1])):
        page = evidence.sources[key].page_number
        if page in pages:
            continue
        chosen.append(key)
        pages.add(page)
        if len(chosen) == limit:
            break
    return chosen


def _spans(text: str) -> Iterable[tuple[int, int]]:
    """Partition all nonempty text, preferring complete sentences over OCR lines."""
    start = 0
    while start < len(text):
        if not text[start:].strip():
            break
        limit = min(len(text), start + MAX_SPAN_CHARACTERS)
        end = limit
        if limit < len(text):
            candidates = list(_BOUNDARY.finditer(text, start, limit))
            # A short wrapped line is not a paragraph. Prefer a sentence ending;
            # otherwise combine lines into a useful context of at least 200 chars.
            substantial = [m for m in candidates if m.end() - start >= 200]
            sentence_ends = [m for m in substantial if not m.group().endswith("\n")]
            if sentence_ends or substantial:
                end = (sentence_ends or substantial)[-1].end()
        # Do not strand a tiny final OCR line when overlapping context can retain
        # it as an exact contiguous part of a longer paragraph.
        if len(text) - end < 100 and end < len(text):
            yield start, end
            tail_start = max(start, len(text) - MAX_SPAN_CHARACTERS)
            yield tail_start, len(text)
            break
        yield start, end
        start = end


def _context_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Keep an entire anchor, including one crossing a partition boundary."""
    spare = MAX_SPAN_CHARACTERS - (end - start)
    left = max(0, start - spare // 2)
    right = min(len(text), left + MAX_SPAN_CHARACTERS)
    left = max(0, right - MAX_SPAN_CHARACTERS)
    # Prefer starting after a sentence/line without losing the anchor.
    boundaries = list(_BOUNDARY.finditer(text, left, start))
    if boundaries:
        left = boundaries[-1].end()
    endings = list(_BOUNDARY.finditer(text, end, right))
    if endings:
        right = endings[-1].end()
    return left, right


def _page_warnings(page: PageExtraction) -> list[str]:
    warnings: list[str] = []
    lines = [line.strip() for line in page.raw_text.splitlines() if line.strip()]
    short_lines = sum(len(line) <= 3 for line in lines)
    kinds = {block.block_type.lower() for block in page.blocks}
    complex_kinds = {"table", "image", "figure", "equation", "formula", "chart"}
    complex_layout = bool(kinds & complex_kinds) or (len(lines) >= 8 and short_lines / len(lines) >= .4)
    complex_notation = bool(re.search(r"[=＝^_^∑∫√]|[₀-₉⁰-⁹]", page.raw_text))
    suspect = page.unresolved_fragments or page.quality.suspicious_fragments or "\ufffd" in page.raw_text
    if page.band.value != "accepted" or suspect:
        warnings.append(f"第 {page.page_number} 页原文含待核查识别片段，不能据此猜补事实或符号。")
    if complex_layout or complex_notation:
        warnings.append(f"第 {page.page_number} 页可能含图表、公式或碎片化排版；仅使用文字明确支持的关系，不能推断丢失结构。")
    return warnings


def build_teaching_evidence(chapter: ChapterDraft, pages: list[PageExtraction]) -> TeachingEvidence:
    """Build complete bounded chapter context, failing on fabricated anchors.

    Empty ``pages`` is reserved for old chapter-only fixtures or legacy records;
    those sources carry an explicit limited-context warning. Supplying pages
    requires every chapter page and every claimed point quotation to be present.
    """
    if chapter.start_page > chapter.end_page:
        raise ValueError("invalid chapter page boundaries")
    sources: dict[str, SourceCitation] = {}
    source_locations: dict[str, tuple[int, int, int]] = {}
    warnings: list[str] = []
    point_sources: dict[str, list[str]] = {}

    def add(page_number: int, text: str, start: int, end: int) -> str:
        quote = text[start:end]
        if not quote.strip() or len(quote) > MAX_SPAN_CHARACTERS:
            raise ValueError("invalid OCR evidence span")
        digest = hashlib.sha256(quote.encode()).hexdigest()[:12]
        key = f"src_p{page_number}_{start}_{end}_{digest}"
        if key not in sources:
            sources[key] = SourceCitation(page_number=page_number, quote=quote)
            source_locations[key] = (page_number, start, end)
        return key

    raw_pages: dict[int, str] = {}
    fingerprint_pages: list[tuple[int, str]] = []
    if pages:
        for page in sorted(pages, key=lambda page: page.page_number):
            if not chapter.start_page <= page.page_number <= chapter.end_page:
                continue
            if page.page_number in raw_pages:
                raise ValueError(f"duplicate chapter page: {page.page_number}")
            raw_pages[page.page_number] = "" if page_is_skipped(page) else page.raw_text
            fingerprint_pages.append((page.page_number, raw_pages[page.page_number]))
            if page_is_skipped(page) or page.processing_status == "blank_confirmed":
                continue
            warnings.extend(_page_warnings(page))
            if not page.raw_text.strip():
                warnings.append(f"第 {page.page_number} 页无可读文字，文字教学无法覆盖其图像内容。")
            for start, end in _spans(page.raw_text):
                add(page.page_number, page.raw_text, start, end)
        missing = set(range(chapter.start_page, chapter.end_page + 1)) - set(raw_pages)
        if missing:
            raise ValueError(f"missing chapter OCR pages: {sorted(missing)}")
    else:
        warnings.append("仅有既存章节引文，缺少整章 OCR 上下文；不能据此宣称完整覆盖教材。")

    for label in chapter.knowledge_points:
        point_id = stable_knowledge_point_id(chapter.chapter_id, label)
        selected: list[str] = []
        for anchor in chapter.knowledge_point_evidence.get(label, []):
            if not chapter.start_page <= anchor.page_number <= chapter.end_page:
                raise ValueError(f"knowledge point anchor outside chapter: {point_id}")
            if not pages:
                selected.append(add(anchor.page_number, anchor.quote, 0, len(anchor.quote)))
                continue
            raw = raw_pages[anchor.page_number]
            start = raw.find(anchor.quote)
            if start < 0:
                raise ValueError(f"knowledge point anchor absent from raw OCR page {anchor.page_number}: {point_id}")
            end = start + len(anchor.quote)
            containing = [
                key for key, (number, left, right) in source_locations.items()
                if number == anchor.page_number and left <= start and end <= right
            ]
            if containing:
                # Prefer the richest existing contiguous context.
                key = max(containing, key=lambda key: len(sources[key].quote))
            else:
                left, right = _context_span(raw, start, end)
                key = add(anchor.page_number, raw, left, right)
            selected.append(key)
        if selected:
            point_sources[point_id] = list(dict.fromkeys(selected))
        else:
            warnings.append(f"知识点 {point_id} 缺少可核验原文锚点，未建立教学依据。")

    if not sources:
        raise ValueError("chapter has no readable teaching evidence")
    fingerprint_data = {
        "version": EVIDENCE_VERSION,
        "chapter_id": chapter.chapter_id,
        "start_page": chapter.start_page,
        "end_page": chapter.end_page,
        "raw_pages": fingerprint_pages,
        "sources": {key: source.model_dump() for key, source in sources.items()},
        "point_sources": point_sources,
        "warnings": warnings,
        "source_coverage": coverage_context(pages or [], start=chapter.start_page, end=chapter.end_page),
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return TeachingEvidence(sources=sources, point_sources=point_sources, warnings=list(dict.fromkeys(warnings)), fingerprint=fingerprint,
                            source_coverage=coverage_context(pages or [], start=chapter.start_page, end=chapter.end_page))


def _display_reflow(text: str) -> str:
    """Join OCR line wraps without guessing letters, punctuation or equations."""
    # Keep blank lines and sentence endings. Only join neighbouring Chinese
    # characters across a single line break, not English words or numeric data.
    return re.sub(r"(?<=[\u3400-\u9fff，、；：])[^\S\n]*\n[^\S\n]*(?=[\u3400-\u9fff])", "", text.strip())


def reading_sections(
    evidence: TeachingEvidence,
    preferred_ids: Iterable[str] | None = None,
    max_sections: int | None = None,
) -> list[LessonSection]:
    """Return readable context excerpts, preserving exact raw citations.

    By default choose at least one context per point and deduplicate. There is
    no depth- or daily-time cap; an explicit caller cap is never applied silently.
    ``preferred_ids`` can be a list of source IDs selected by a reviewed editor.
    """
    if max_sections is not None:
        raise ValueError("reading section truncation requires explicit curriculum units; max_sections is unsupported")
    ids = list(preferred_ids) if preferred_ids is not None else [ids[0] for ids in evidence.point_sources.values() if ids]
    if not ids:
        ids = list(evidence.sources)
    if any(key not in evidence.sources for key in ids):
        raise ValueError("unknown reading source identifier")
    result: list[LessonSection] = []
    for key in dict.fromkeys(ids):
        citation = evidence.sources[key]
        result.append(LessonSection(
            title=f"第 {citation.page_number} 页 · 原文上下文",
            content=_display_reflow(citation.quote),
            purpose="结合连续原文理解概念及其成立条件；图表和符号不清之处需返回教材核查。",
            citations=[citation.model_copy(deep=True)],
        ))
    return result
