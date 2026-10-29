from __future__ import annotations

import hashlib
import json
import logging
import re
import statistics
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..llm.book_parallel import ordered_book_map
from ..llm.client import LLMError, OpenAICompatibleClient
from .coverage import OUTPUT_POLICY, coverage_context, page_is_skipped
from .evidence_context import expand_source_quote
from .models import (
    BookStructure,
    ChapterDraft,
    ExtractionMethod,
    PageExtraction,
    PageKind,
    QualityBand,
    SourceQuote,
    TextBlock,
)
from .quality import evaluate_text_quality, quality_band

logger = logging.getLogger(__name__)

_CHINESE_CHAPTER = re.compile(
    r"^\s*(第\s*([一二三四五六七八九十百千万零〇两0-9]+)\s*[章篇部])\s*"
    r"(.{0,80}?)(?:[\.·…\s]{2,}\s*\d+)?\s*$",
    re.IGNORECASE,
)
_ENGLISH_CHAPTER = re.compile(
    r"^\s*((?:chapter|unit)(?=\s|[0-9])\s*([0-9]+|[ivxlcdm]+))\s*(?:[:.\-—]\s*|\s*)"
    r"(.{0,80}?)\s*$",
    re.IGNORECASE,
)
_ENGLISH_PART = re.compile(
    r"^\s*((?:part|book)\s+([0-9]+|[ivxlcdm]+))\s*(?:[:.\-—]\s*|\s+)"
    r"(.{0,80}?)\s*$",
    re.IGNORECASE,
)
_TOC_LABEL = re.compile(r"^\s*(目录|目次|contents?|table\s+of\s+contents)(?:\s+[ivxlcdm0-9]+)?\s*$", re.I)

_CHAPTER_SUMMARY_SYSTEM = """你是书籍章节摘要器。输入只包含同一章的带页码原文。
规则：
1. 只总结输入原文明确表达的内容，不使用外部知识。
2. summary 只用于目录概览，分段概括本章主线，通常 200-600 字，最多 1600 字；它不是课程正文，也不能替代下方完整知识点目录。段落之间用两个换行符分隔，不写标题、不用 Markdown、不重复章名。
3. knowledge_points 按原文章节顺序列出所有可独立学习的重要概念、条件、关系、过程和结论，不为固定数量压缩合并或省略后半章内容。数量随实际内容变化，短章节可只有1项，长章节可超过20项，最多64项；不为凑数重复或编造。每项 text 是可独立理解的完整陈述，保留适用范围、必要条件、否定和例外；复杂过程可拆成多个有各自依据的学习点。每项 evidence_ids 为1-6个编号。
4. 顶层 evidence_ids 选择1-24个支撑摘要的证据编号；编号必须来自输入 evidence，禁止创造编号。不要为了凑引用数量重复或增加无关证据。
5. 不输出小节层级，不输出 chunk id，不出现连续重复标点、词中异常空格或 OCR 残缺片段。
6. 只返回 JSON：
{"summary":"...","evidence_ids":["P1E1","P1E2"],"knowledge_points":[{"text":"概念一","evidence_ids":["P1E1"]},{"text":"概念二","evidence_ids":["P1E2"]},{"text":"概念三","evidence_ids":["P1E1","P1E2"]}]}
"""

_BOOK_SUMMARY_SYSTEM = """你是整书摘要器。输入是已经核验的章节摘要。
只能综合输入摘要，不补充外部知识。面向手机阅读，用 3-5 个短段说明全书主线，每段 1-2 句，
总长通常 180-480 字，最长 680 字；段落之间用两个换行符分隔。不写标题、不重复书名、不逐章机械罗列。
必须覆盖输入 chapters 的每一个条目，包括主章、练习册和附录/索引；附录按其检索或参考作用概述，不把附录冒充主课。
chapter_ids 必须完整返回 required_chapter_ids 的全部标识，每个恰好一次；不得按重要性省略、添加未知标识或重复标识。
只返回 JSON：{"summary":"...","chapter_ids":["chapter_..."]}。
"""


class ChapteringValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ChapterCandidate:
    kind: str
    ordinal: str
    title: str
    page_number: int
    block_id: str
    score: float
    toc_page: bool
    boundary_evidence: dict[str, Any] = field(default_factory=dict)


class KnowledgePointDraft(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, max_length=6)


class ChapterSummaryDraft(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    summary: str = Field(min_length=1, max_length=2000)
    knowledge_points: list[KnowledgePointDraft] = Field(min_length=1, max_length=64)
    evidence_ids: list[str] = Field(min_length=1, max_length=24)


class BookSummaryDraft(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    summary: str = Field(min_length=1, max_length=4000)
    chapter_ids: list[str] = Field(min_length=1)


class ChapterOpeningDraft(BaseModel):
    start_page: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=100)


class ChapterOutlineDraft(BaseModel):
    chapters: list[ChapterOpeningDraft] = Field(min_length=1)


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _split_evidence(text: str, limit: int = 480) -> list[str]:
    # A quotation is a literal span, including OCR line breaks and spacing.
    # Normalizing them can hide a split negation and break anchor lookup.
    normalized = text.strip()
    if not normalized:
        return []
    units: list[str] = []
    start = 0
    while start < len(normalized):
        hard_end = min(len(normalized), start + limit)
        end = hard_end
        if hard_end < len(normalized):
            window = normalized[start:hard_end]
            boundaries = [match.end() for match in re.finditer(r"[。！？.!?]", window)]
            useful = [position for position in boundaries if position >= limit // 3]
            if useful:
                end = start + useful[-1]
        unit = normalized[start:end].strip()
        if unit:
            units.append(unit)
        start = end
        while start < len(normalized) and normalized[start].isspace():
            start += 1
    return units


def _stable_chapter_id(title: str, start_page: int) -> str:
    digest = hashlib.sha256(f"{start_page}:{_normalized(title)}".encode()).hexdigest()[:16]
    return f"chapter_{digest}"


def _heading(text: str) -> tuple[str, str, str] | None:
    line = _normalized(text.splitlines()[0] if text.strip() else "")
    chinese = _CHINESE_CHAPTER.fullmatch(line)
    if chinese:
        prefix, ordinal, suffix = chinese.groups()
        title = _normalized(f"{prefix} {suffix}".strip())
        kind = "chapter" if "章" in prefix else "part"
        return kind, _normalized(ordinal).lower(), title
    english = _ENGLISH_CHAPTER.fullmatch(line)
    if english:
        prefix, ordinal, suffix = english.groups()
        title = _normalized(f"{prefix} {suffix}".strip())
        return "chapter", _normalized(ordinal).lower(), title
    english_part = _ENGLISH_PART.fullmatch(line)
    if english_part:
        prefix, ordinal, suffix = english_part.groups()
        title = _normalized(f"{prefix} {suffix}".strip())
        return "part", _normalized(ordinal).lower(), title
    return None


def _toc_pages(pages: list[PageExtraction]) -> set[int]:
    result: set[int] = set()
    for page in pages:
        matches = sum(bool((heading := _heading(block.text)) and heading[0] == 'chapter') for block in page.blocks)
        has_label = any(_TOC_LABEL.fullmatch(block.text.strip()) for block in page.blocks)
        dotted_rows = len(re.findall(r"(?:\.{3,}|…+|·{3,})\s*\d+", page.raw_text))
        if has_label or matches >= 3 or dotted_rows >= 3:
            result.add(page.page_number)
    # A following TOC leaf can omit the label, but retains several printed
    # page references. Do not classify an ordinary next chapter as TOC.
    for page in pages:
        if page.page_number - 1 in result:
            references = sum(bool(re.fullmatch(r"(?:p\.?\s*)?\d+", b.text.strip(), re.I)) for b in page.blocks)
            if references >= 5:
                result.add(page.page_number)
    return result

def _display_key(text: str) -> str:
    return re.sub(r"[\s•·]+", "", text).casefold()


def _toc_text(text: str) -> str:
    if re.fullmatch(r"(?:unit|chapter)\s*[0-9ivxlcdm]+|第\s*\S+\s*章", text.strip(), re.I):
        return text.strip()
    text = re.sub(r"^\s*p\.?\s*\d+\s*", "", text, flags=re.I)
    return re.sub(r"(?:[.·…]{2,}|\s+)\d+\s*$", "", text).strip()


def _infer_text_line_headings(pages: list[PageExtraction], toc_pages: set[int]) -> list[ChapterCandidate]:
    """Cross-check display text and actual TOC rows; never rewrite the OCR source."""
    entries: list[dict[str, Any]] = []
    auxiliary = re.compile(r"^(?:workbook|appendices|附录.+)$", re.I)
    for page in pages:
        if page.page_number not in toc_pages:
            continue
        headings = [(b, _heading(_toc_text(b.text))) for b in page.blocks]
        headings = [(b, h) for b, h in headings if h and h[0] == "chapter"]
        workbook = next((b for b in page.blocks if _display_key(b.text) == "workbook"), None)
        for block, parsed in headings:
            kind, ordinal, full_title = parsed
            prefix = re.match(r"^(?:第\s*\S+\s*章|(?:unit|chapter)\s*\S+)", full_title, re.I)
            if prefix is None:
                continue
            title = full_title[prefix.end():].strip()
            sources = [block.block_id]
            scope = ("WORKBOOK" if workbook and block.bbox and workbook.bbox
                     and block.bbox[1] >= workbook.bbox[1] - 8 else "")
            if not title and not scope and block.bbox:
                later = [other.bbox[1] for other, _ in headings if other.bbox
                         and other.bbox[1] > block.bbox[1] + 8]
                bottom = min(later, default=block.bbox[1] + 140)
                pieces = []
                for other in page.blocks:
                    if other.block_id == block.block_id or not other.bbox:
                        continue
                    if not (block.bbox[3] - 3 <= other.bbox[1] < bottom):
                        continue
                    if not (block.bbox[0] - 80 <= other.bbox[0] <= block.bbox[0] + 25
                            and other.bbox[2] <= block.bbox[0] + 225):
                        continue
                    value = _toc_text(other.text)
                    if value and re.fullmatch(r"[A-Z][A-Z\s&'’\-]+", value):
                        pieces.append((other.bbox[1], other.bbox[0], value, other.block_id))
                pieces.sort()
                title = " ".join(value for _, _, value, _ in pieces)
                sources.extend(key for _, _, _, key in pieces)
            # A bare ordinal is still useful evidence that the chapter exists.
            title = title.strip(" .·…")
            refs = []
            if block.bbox:
                for other in page.blocks:
                    if other.bbox and (match := re.fullmatch(r"(?:p\.?\s*)?(\d+)", other.text.strip(), re.I)):
                        distance = abs(other.bbox[1] - block.bbox[1])
                        if distance <= (6 if scope else 85):
                            refs.append((distance, int(match[1]), other.block_id))
            printed = min(refs)[1] if refs else None
            if refs:
                sources.append(min(refs)[2])
            if scope and workbook:
                sources.append(workbook.block_id)
            entries.append({"kind": kind, "ordinal": ordinal, "title": title,
                            "prefix": prefix[0], "scope": scope, "printed": printed,
                            "toc_page": page.page_number, "toc_blocks": sources})
        for block in page.blocks:
            title = _toc_text(block.text)
            if auxiliary.fullmatch(title):
                refs = [(abs(other.bbox[1] - block.bbox[1]), int(match[1]), other.block_id)
                        for other in page.blocks if block.bbox and other.bbox
                        and abs(other.bbox[1] - block.bbox[1]) < 8
                        and (match := re.fullmatch(r"(?:p\.?\s*)?(\d+)", other.text.strip(), re.I))]
                entries.append({"kind": "appendix", "ordinal": _display_key(title),
                                "title": title, "match_title": re.sub(r"^附录\s*[ⅠⅡⅢⅣIVXivx皿™]*\s*", "", title), "prefix": "", "scope": "", "printed": min(refs)[1] if refs else None,
                                "toc_page": page.page_number, "toc_blocks": [block.block_id, *([min(refs)[2]] if refs else [])]})

    result: list[ChapterCandidate] = []
    offsets: list[tuple[int, str]] = []
    for page in pages:
        if page.page_number in toc_pages:
            continue
        if page.method not in {ExtractionMethod.APPLE_VISION, ExtractionMethod.PADDLE_GPU,
                               ExtractionMethod.RAPIDOCR_ONNX, ExtractionMethod.VISION_RESCUE}:
            continue
        heights = [b.bbox[3] - b.bbox[1] for b in page.blocks if b.bbox
                   and len(b.text.strip()) >= 12 and 260 < b.bbox[1] < 930]
        if not heights:
            heights = [b.bbox[3] - b.bbox[1] for b in page.blocks if b.bbox and len(b.text.strip()) >= 12]
        body_height = statistics.median(heights) if heights else None
        display = [b for b in page.blocks if (
            b.metadata.get("text_level") == 1 and b.source_method == ExtractionMethod.VISION_RESCUE
        ) or (body_height and b.bbox and b.bbox[1] < 300
              and b.bbox[3] - b.bbox[1] >= 1.7 * body_height)]
        # A large explicit chapter/appendix marker may have a smaller subtitle.
        markers = [b for b in display if re.fullmatch(r"第\s*\S+\s*章|unit|chapter|附录\s*\S*", b.text.strip(), re.I)]
        if markers and body_height:
            display += [b for b in page.blocks if b not in display and b.bbox
                        and b.bbox[1] < 300 and b.bbox[3] - b.bbox[1] >= 1.1 * body_height
                        and len(b.text) <= 80]
        display.sort(key=lambda b: ((b.bbox[1] if b.bbox else -1), (b.bbox[0] if b.bbox else 0)))
        pieces = []
        for block in display:
            value = block.text.strip()
            if re.fullmatch(r"\d+|unit|chapter|第\s*\S+\s*章|附录\s*\S*", value, re.I):
                continue
            if parsed := _heading(value):
                value = re.sub(r"^(?:第\s*\S+\s*章|(?:unit|chapter)\s*[0-9ivxlcdm]+)\s*", "", parsed[2], flags=re.I)
            value = re.sub(r"^(appendices|appendix)\s*附录$", r"\1", value, flags=re.I)
            pieces.append(_display_key(value))
        spans = {"".join(pieces[start:end]) for start in range(len(pieces))
                 for end in range(start + 1, len(pieces) + 1)}
        matches = [e for e in entries if not e["scope"] and e["title"]
                   and _display_key(e.get("match_title", e["title"])) in spans]
        # Repeated running headers cannot match the enlarged display geometry.
        by_title = {(_display_key(e["title"]), e["ordinal"]): e for e in matches}
        if len(by_title) != 1:
            continue
        entry = next(iter(by_title.values()))
        title = (entry["prefix"] + " " + entry["title"]).strip()
        body_ids = [b.block_id for b in display]
        result.append(ChapterCandidate(entry["kind"], entry["ordinal"], title,
            page.page_number, body_ids[0], .95, False,
            {"method": "exact_toc_title_and_display_heading", "body_block_ids": body_ids,
             "toc_page": entry["toc_page"], "toc_block_ids": entry["toc_blocks"]}))
        if entry["printed"] is not None:
            offsets.append((page.page_number - entry["printed"], body_ids[0]))
    # TOC subdivisions may use normal-size Unit headers. Require a page offset
    # corroborated by two display openers plus the actual matching Unit header.
    agreed = {offset for offset, _ in offsets}
    if len(offsets) >= 2 and len(agreed) == 1:
        offset = next(iter(agreed))
        page_map = {p.page_number: p for p in pages}
        for entry in entries:
            if (not entry["scope"] and entry["kind"] != "appendix") or entry["printed"] is None:
                continue
            page = page_map.get(entry["printed"] + offset)
            if page is None or page.page_number in toc_pages:
                continue
            if entry["kind"] == "appendix":
                body = [b for b in page.blocks if b.bbox is None or b.bbox[1] < 300]
                body_text = "".join(_display_key(b.text) for b in body)
                if _display_key(entry.get("match_title", entry["title"])) not in body_text:
                    continue
                title, ordinal, kind = entry["title"], entry["ordinal"], "appendix"
            else:
                body = [b for b in page.blocks if (b.bbox is None or b.bbox[1] < 180)
                        and (h := _heading(b.text)) and h[1] == entry["ordinal"]]
                title = entry["scope"] + " " + entry["prefix"]
                ordinal, kind = entry["scope"] + ":" + entry["ordinal"], "chapter"
            skipped_start = not body and page.processing_status == "skipped"
            if not body and not skipped_start:
                continue
            result.append(ChapterCandidate(kind, ordinal,
                title, page.page_number, body[0].block_id if body else entry["toc_blocks"][0], .96, False,
                {"method": ("toc_subdivision_verified_offset_skipped_start" if skipped_start
                            else "toc_subdivision_and_verified_page_offset"),
                 "start_page_skipped": skipped_start, "body_block_ids": [b.block_id for b in body],
                 "toc_page": entry["toc_page"], "toc_block_ids": entry["toc_blocks"],
                 "printed_page": entry["printed"], "pdf_page_offset": offset,
                 "offset_evidence_block_ids": [key for _, key in offsets]}))
    return result


def _display_chapter_candidates(pages: list[PageExtraction], toc_pages: set[int]) -> list[ChapterCandidate]:
    """Recognize enlarged body openers independently of damaged TOC titles."""
    result: list[ChapterCandidate] = []
    for page in pages:
        if page.page_number in toc_pages:
            continue
        heights = [b.bbox[3] - b.bbox[1] for b in page.blocks
                   if b.bbox and len(b.text.strip()) >= 12 and 180 < b.bbox[1] < 930]
        if not heights:
            continue
        body_height = statistics.median(heights)
        for index, block in enumerate(page.blocks):
            parsed = _heading(block.text)
            if parsed is None or parsed[0] != "chapter" or not block.bbox:
                continue
            if block.bbox[1] > 260 or block.bbox[3] - block.bbox[1] < 1.6 * body_height:
                continue
            kind, ordinal, title = parsed
            ids = [block.block_id]
            previous = block
            if re.fullmatch(r"第\s*\S+\s*章|(?:chapter|unit)\s*\S+", title, re.I):
                for following in page.blocks[index + 1:index + 4]:
                    if (not following.bbox or len(following.text.strip()) > 80
                            or _heading(following.text)
                            or re.match(r"\d+[.．]|第.+节", following.text)
                            or following.bbox[1] > 330
                            or abs(following.bbox[0] - block.bbox[0]) > 120
                            or not -5 <= following.bbox[1] - previous.bbox[3] <= 80
                            or following.bbox[3] - following.bbox[1] < 1.25 * body_height):
                        break
                    title += " " + _normalized(following.text)
                    ids.append(following.block_id)
                    previous = following
            result.append(ChapterCandidate(kind, ordinal, title, page.page_number,
                block.block_id, .96 if len(ids) > 1 else .85, False,
                {"method": "body_display_geometry", "body_block_ids": ids}))
    return result


class ChapterBoundaryDetector:
    """Generic chapter-level detector: rules propose boundaries; invariants accept them."""

    def candidates(self, pages: list[PageExtraction]) -> list[ChapterCandidate]:
        toc_pages = _toc_pages(pages)
        candidates: list[ChapterCandidate] = []
        for page in pages:
            for position, block in enumerate(page.blocks):
                parsed = _heading(block.text)
                if parsed is None:
                    continue
                kind, ordinal, title = parsed
                block_type = block.block_type.lower()
                if block_type in {"header", "footer"}:
                    # Chapter openers can put a bare ordinal in the header and the title
                    # in a separate body heading. Running headers alone are never boundaries.
                    bare = re.fullmatch(r"第\s*[一二三四五六七八九十百0-9]+\s*章", title)
                    bodies = [b for b in page.blocks if b.block_type not in {"header", "footer", "page_number"}]
                    if block_type != "header" or not bare or not bodies:
                        continue
                    first = bodies[0]
                    if (first.metadata.get("text_level") != 1 or len(first.text) > 80
                        or re.match(r"\d+[.．]|第.+[节章]|(?:chapter|unit|part)\b", first.text, re.I)
                        or (first.bbox is not None and first.bbox[1] > 330)):
                        continue
                    candidates.append(ChapterCandidate(kind, ordinal, f"{title} {_normalized(first.text)}",
                        page.page_number, first.block_id, 0.95, page.page_number in toc_pages))
                    continue
                if re.fullmatch(r"第\s*\S+\s*[章篇部]|(?:chapter|unit)\s+\S+", title, re.I):
                    previous_block = block
                    for next_block in page.blocks[position + 1:position + 4]:
                        if (next_block.metadata.get("text_level") == 1 and not _heading(next_block.text)
                            and len(next_block.text) <= 80
                            and not re.match(r"\d+[.．]|第.+节", next_block.text)
                            and (previous_block.bbox is None or next_block.bbox is None
                                 or next_block.bbox[1] - previous_block.bbox[3] <= 60)):
                            title += " " + _normalized(next_block.text)
                            previous_block = next_block
                        else:
                            break
                score = 0.35
                text_level = block.metadata.get("text_level")
                if text_level == 1:
                    score += 0.35
                if block_type in {"title", "heading"}:
                    score += 0.2
                if block.bbox is not None and block.bbox[1] <= 260:
                    score += 0.2
                if len(_normalized(block.text)) <= 60:
                    score += 0.1
                toc_page = page.page_number in toc_pages
                if toc_page:
                    score -= 0.75
                candidates.append(
                    ChapterCandidate(
                        kind=kind,
                        ordinal=ordinal,
                        title=title[:100],
                        page_number=page.page_number,
                        block_id=block.block_id,
                        score=max(0, min(1, score)),
                        toc_page=toc_page,
                    )
                )
        candidates.extend(_infer_text_line_headings(pages, toc_pages))
        candidates.extend(_display_chapter_candidates(pages, toc_pages))
        return candidates

    def detect(self, pages: list[PageExtraction], *,
               candidates: list[ChapterCandidate] | None = None) -> tuple[list[ChapterDraft], bool]:
        if not pages:
            raise ValueError("pages must not be empty")
        by_ordinal: dict[tuple[str, str], list[ChapterCandidate]] = {}
        for candidate in self.candidates(pages) if candidates is None else candidates:
            key = (candidate.kind, candidate.ordinal)
            by_ordinal.setdefault(key, []).append(candidate)
        selected: list[ChapterCandidate] = []
        for occurrences in by_ordinal.values():
            body_occurrences = [
                item for item in occurrences if not item.toc_page and item.score >= 0.7
            ]
            if not body_occurrences:
                continue
            chosen = max(body_occurrences, key=lambda item: (item.score, -item.page_number))
            # Geometry can stop at a sidebar between split title lines. On the
            # selected opener page, a complete title corroborated by the TOC
            # takes precedence over that partial geometric title.
            corroborated = [item for item in body_occurrences
                            if item.page_number == chosen.page_number
                            and item.boundary_evidence.get("method") == "exact_toc_title_and_display_heading"]
            selected.append(max(corroborated, key=lambda item: item.score) if corroborated else chosen)
        if any(item.kind == "chapter" for item in selected):
            selected = [item for item in selected if item.kind != "part"]
        selected.sort(key=lambda item: item.page_number)
        # OCR can corrupt an ordinal while preserving the same visible title. One boundary per page.
        selected = list({item.page_number: item for item in selected}.values())
        selected.sort(key=lambda item: item.page_number)
        maximum_page = max(page.page_number for page in pages)
        if not selected:
            sources = [block.block_id for page in pages for block in page.blocks]
            return (
                [
                    ChapterDraft(
                        chapter_id=_stable_chapter_id("全书内容", pages[0].page_number),
                        order=1,
                        title="全书内容",
                        start_page=pages[0].page_number,
                        end_page=maximum_page,
                        summary="",
                        knowledge_points=[],
                        source_block_ids=sources,
                    )
                ],
                True,
            )
        chapters: list[ChapterDraft] = []
        for index, marker in enumerate(selected):
            end = selected[index + 1].page_number - 1 if index + 1 < len(selected) else maximum_page
            if end < marker.page_number:
                raise ChapteringValidationError("chapter boundaries overlap")
            sources = [
                block.block_id
                for page in pages
                if marker.page_number <= page.page_number <= end
                for block in page.blocks
            ]
            chapters.append(
                ChapterDraft(
                    chapter_id=_stable_chapter_id(marker.title, marker.page_number),
                    order=index + 1,
                    title=marker.title,
                    boundary_evidence=marker.boundary_evidence,
                    start_page=marker.page_number,
                    end_page=end,
                    summary="",
                    knowledge_points=[],
                    source_block_ids=[
                        *([marker.block_id] if marker.block_id in sources else []),
                        *[item for item in sources if item != marker.block_id],
                    ],
                )
            )
        self._validate_boundaries(chapters, maximum_page)
        return chapters, False

    @staticmethod
    def _validate_boundaries(chapters: list[ChapterDraft], maximum_page: int) -> None:
        for index, chapter in enumerate(chapters):
            if chapter.start_page < 1 or chapter.end_page > maximum_page:
                raise ChapteringValidationError("chapter boundary is outside the book")
            if index and chapter.start_page != chapters[index - 1].end_page + 1:
                raise ChapteringValidationError("chapter ranges are not contiguous")


class ChapterReconstructor:
    def __init__(
        self,
        client: OpenAICompatibleClient | None = None,
        *,
        max_chapter_characters: int = 64_000,
        validation_retries: int = 1,
    ) -> None:
        if max_chapter_characters < 2000:
            raise ValueError("max_chapter_characters is too small")
        if validation_retries < 0:
            raise ValueError("validation_retries must not be negative")
        self.client = client
        self.max_chapter_characters = max_chapter_characters
        self.validation_retries = validation_retries
        self.detector = ChapterBoundaryDetector()

    def reconstruct_book(self, pages: list[PageExtraction], fallback_title: str) -> BookStructure:
        chapters, used_fallback = self._outline(pages)
        chapters = [chapter.model_copy(update={"source_coverage": coverage_context(
            pages, start=chapter.start_page, end=chapter.end_page)}) for chapter in chapters
            if any(page.raw_text.strip() and not page_is_skipped(page) for page in pages
                   if chapter.start_page <= page.page_number <= chapter.end_page)]
        if not chapters:
            raise ChapteringValidationError("document contains no readable chapter content")
        if self.client is not None:
            chapters = ordered_book_map(lambda chapter: self._summarize(chapter, pages), chapters)
            book_summary = self._summarize_book(chapters)
        else:
            book_summary = ""
        return BookStructure(
            title=fallback_title.strip() or "未命名书籍",
            summary=book_summary,
            chapters=chapters,
            source_page_count=len(pages),
            used_fallback_chapter=used_fallback,
            source_coverage=coverage_context(pages),
        )

    def _outline(self, pages: list[PageExtraction]) -> tuple[list[ChapterDraft], bool]:
        candidates = self.detector.candidates(pages)
        chapters, fallback = self.detector.detect(pages, candidates=candidates)
        toc_ordinals = {c.ordinal for c in candidates if c.toc_page and c.kind == "chapter"}
        body_ordinals = {h[1] for c in chapters if (h := _heading(c.title))}
        damaged = any(c.toc_page and not re.sub(r"[.·…\s]", "", re.sub(
            r"^第\s*\S+\s*章", "", c.title)) for c in candidates)
        if self.client is None or not (fallback or damaged or toc_ordinals - body_ordinals):
            return chapters, fallback
        # The model repairs names and associations, selecting actual body pages.
        # A provider interruption retains independently supported body openers.
        pool: dict[int, ChapterCandidate] = {}
        for candidate in candidates:
            if candidate.toc_page or candidate.score < .6:
                continue
            old = pool.get(candidate.page_number)
            if old is None or candidate.score > old.score:
                pool[candidate.page_number] = candidate
        if not pool:
            return chapters, fallback
        required = {c.start_page for c in chapters} if not fallback else set()
        page_map = {p.page_number: p for p in pages}
        payload: dict[str, Any] = {
            "toc": [{"page": p.page_number, "text": p.raw_text[:6000]}
                    for p in pages if p.page_number in _toc_pages(pages)],
            "openers": [{"start_page": number, "title": c.title,
                         "text": page_map[number].raw_text[:1400]}
                        for number, c in sorted(pool.items())],
            "required_start_pages": sorted(required),
        }
        for _attempt in range(self.validation_retries + 1):
            try:
                raw = self.client.structured(system=(
                    "整理教材章节目录。目录OCR可能只有章号或缺少章名，正文章号和标题可能分行。"
                    "结合目录、正文开篇和小节编号补全名称，保留每个真实章节。"
                    "起点必须选自openers，必须包含required_start_pages，不得创造页码；"
                    "名称可按正文总结，保留原章号。只返回JSON："
                    '{"chapters":[{"start_page":1,"title":"第一章 名称"}]}'),
                    user=json.dumps(payload, ensure_ascii=False), temperature=0, max_tokens=2200)
                draft = ChapterOutlineDraft.model_validate(raw)
                starts = [c.start_page for c in draft.chapters]
                if len(starts) != len(set(starts)) or not set(starts) <= set(pool) or not required <= set(starts):
                    raise ChapteringValidationError("outline must retain known actual body openers")
                markers = []
                for opening in draft.chapters:
                    base = pool[opening.start_page]
                    heading = _heading(opening.title)
                    if heading is None or heading[1] != base.ordinal:
                        raise ChapteringValidationError("outline must preserve the observed chapter ordinal")
                    markers.append(ChapterCandidate(base.kind, base.ordinal, opening.title,
                        base.page_number, base.block_id, 1, False,
                        {**base.boundary_evidence, "method": "model_reconciled_body_openers"}))
                return self.detector.detect(pages, candidates=markers)
            except (ValueError, LLMError) as error:
                logger.info("chapter outline repair retained body evidence", extra={"error_type": type(error).__name__})
                if isinstance(error, LLMError):
                    break
                payload["feedback"] = str(error)[:700]
        return chapters, fallback

    def reconcile_book(self, pages: list[PageExtraction], existing: BookStructure) -> BookStructure:
        """Repair boundaries while preserving unchanged source-backed chapters."""
        chapters, fallback = self._outline(pages)
        old_by_start = {c.start_page: c for c in existing.chapters}
        repaired = []
        changed = False
        for chapter in chapters:
            old = old_by_start.get(chapter.start_page)
            if old is not None:
                chapter = chapter.model_copy(update={"chapter_id": old.chapter_id, "title": old.title})
            coverage = coverage_context(pages, start=chapter.start_page, end=chapter.end_page)
            chapter = chapter.model_copy(update={"source_coverage": coverage})
            reusable = old is not None and all(
                chapter.start_page <= quote.page_number <= chapter.end_page
                for quote in [*old.evidence, *(q for refs in old.knowledge_point_evidence.values() for q in refs)])
            if reusable:
                chapter = old.model_copy(update={"order": chapter.order, "end_page": chapter.end_page,
                    "source_block_ids": chapter.source_block_ids, "source_coverage": coverage,
                    "boundary_evidence": chapter.boundary_evidence})
            elif self.client is not None:
                chapter = self._summarize(chapter, pages)
                if old is not None and all(
                    chapter.start_page <= quote.page_number <= chapter.end_page
                    for refs in old.knowledge_point_evidence.values() for quote in refs
                ):
                    # A summary may span the removed chapter while its existing
                    # diagnostic points still have valid, unchanged evidence.
                    chapter = chapter.model_copy(update={"knowledge_points": old.knowledge_points,
                        "knowledge_point_evidence": old.knowledge_point_evidence})
            changed = changed or old is None or old.end_page != chapter.end_page or old.order != chapter.order
            repaired.append(chapter)
        if not changed and len(repaired) == len(existing.chapters):
            return existing
        summary = self._summarize_book(repaired) if self.client is not None else existing.summary
        return existing.model_copy(update={"chapters": repaired, "summary": summary,
            "used_fallback_chapter": fallback, "source_coverage": coverage_context(pages)})

    def reconstruct(
        self, pages: list[PageExtraction], fallback_title: str
    ) -> tuple[str, list[ChapterDraft]]:
        structure = self.reconstruct_book(pages, fallback_title)
        return structure.title, structure.chapters

    def _summarize(self, chapter: ChapterDraft, pages: list[PageExtraction]) -> ChapterDraft:
        assert self.client is not None
        chapter_pages = [
            page for page in pages if chapter.start_page <= page.page_number <= chapter.end_page
            and not page_is_skipped(page)
        ]
        evidence_payload, evidence_source = self._evidence_units(chapter_pages)
        payload: dict[str, object] = {
            "chapter_title": chapter.title,
            "evidence": evidence_payload,
            "source_coverage": chapter.source_coverage,
        }
        last_error: Exception | None = None
        for attempt in range(self.validation_retries + 1):
            try:
                result = self.client.structured(
                    system=_CHAPTER_SUMMARY_SYSTEM + "\n" + OUTPUT_POLICY,
                    user=json.dumps(payload, ensure_ascii=False),
                    temperature=0,
                    max_tokens=12_000,
                )
                draft = ChapterSummaryDraft.model_validate(result)
                evidence: list[SourceQuote] = []
                for evidence_id in dict.fromkeys(draft.evidence_ids):
                    source = evidence_source.get(evidence_id)
                    if source is None:
                        raise ChapteringValidationError("summary cites an unknown evidence id")
                    evidence.append(source)
                point_evidence: dict[str, list[SourceQuote]] = {}
                for point in draft.knowledge_points:
                    point_sources: list[SourceQuote] = []
                    for evidence_id in dict.fromkeys(point.evidence_ids):
                        source = evidence_source.get(evidence_id)
                        if source is None:
                            raise ChapteringValidationError(
                                "knowledge point cites an unknown evidence id"
                            )
                        point_sources.append(source)
                    point_evidence.setdefault(point.text.strip(), point_sources)
                return chapter.model_copy(
                    update={
                        "summary": draft.summary.strip(),
                        "knowledge_points": list(point_evidence),
                        "evidence": evidence,
                        "knowledge_point_evidence": point_evidence,
                    }
                )
            except (ValidationError, ChapteringValidationError, LLMError) as error:
                last_error = error
                if isinstance(error, LLMError) or attempt >= self.validation_retries:
                    break
                payload["validation_feedback"] = (
                    "上一次输出未通过校验。仅修复下面具体问题，所有引用仍必须来自输入。"
                    + (json.dumps(error.errors(include_input=False, include_url=False), ensure_ascii=False)
                       if isinstance(error, ValidationError) else str(error))[:1600]
                )
        assert last_error is not None
        # A rejected enhancement must not erase independently detected chapter
        # boundaries. Use complete, literal body excerpts rather than accepting
        # a malformed model response or inventing replacement teaching facts.
        excerpts: dict[str, SourceQuote] = {}
        by_page = {page.page_number: page for page in chapter_pages}
        for source in evidence_source.values():
            page = by_page.get(source.page_number)
            text = source.quote.strip()
            if (page is None or text not in page.raw_text
                    or not re.search(r'[A-Za-z\u3400-\u9fff]', text)
                    or not re.search(r'[。！？!?]|\.(?=\s|$)', text)
                    or not re.search(r'[。！？!?\.][”’"\')）\]]*$', text)):
                continue
            start = page.raw_text.find(text)
            if page.blocks:
                starts_in_body = False
                for block in page.blocks:
                    if (block.block_type.lower() not in {'text', 'paragraph', 'body'}
                            or block.metadata.get('text_level') == 1):
                        continue
                    block_start = page.raw_text.find(block.text)
                    if block_start < 0 or not block_start <= start < block_start + len(block.text):
                        continue
                    prefix = page.raw_text[block_start:start].rstrip()
                    if not prefix or re.search(r'[。！？!?\.]$', prefix):
                        starts_in_body = True
                        break
                if not starts_in_body:
                    continue
            else:
                prefix = page.raw_text[:start].rstrip()
                if prefix and not re.search(r'[。！？!?\.]$', prefix) and not page.raw_text[:start].endswith('\n\n'):
                    continue
            excerpts.setdefault(text, source)
        values = list(excerpts.values())
        if not values:
            logger.info('chapter directory retained without invented learning content', extra={
                'chapter_id': chapter.chapter_id, 'error_type': type(last_error).__name__})
            return chapter.model_copy(update={
                'summary': '', 'knowledge_points': [], 'evidence': [], 'knowledge_point_evidence': {},
            })
        count = min(6, len(values))
        selected = [values[round(index * (len(values) - 1) / max(1, count - 1))]
                    for index in range(count)]
        logger.info('chapter enhancement retained literal source excerpts', extra={
            'chapter_id': chapter.chapter_id, 'error_type': type(last_error).__name__})
        summary = selected[0].quote
        if len(selected) > 1 and len(summary) + len(selected[1].quote) + 2 <= 680:
            summary += '\n\n' + selected[1].quote
        return chapter.model_copy(update={
            'summary': summary,
            'knowledge_points': [source.quote for source in selected],
            'evidence': selected,
            'knowledge_point_evidence': {source.quote: [source] for source in selected},
        })

    def _summarize_book(self, chapters: list[ChapterDraft]) -> str:
        assert self.client is not None
        usable = [chapter for chapter in chapters if chapter.summary]
        if not usable:
            return ""
        valid_ids = {chapter.chapter_id for chapter in usable}
        payload: dict[str, Any] = {
            "chapters": [
                {"chapter_id": chapter.chapter_id, "title": chapter.title,
                 "summary": chapter.summary, "source_coverage": chapter.source_coverage}
                for chapter in usable
            ],
            "required_chapter_ids": [chapter.chapter_id for chapter in usable],
        }
        last_error: ValueError | LLMError | None = None
        for attempt in range(self.validation_retries + 1):
            try:
                raw = self.client.structured(
                    system=_BOOK_SUMMARY_SYSTEM + "\n" + OUTPUT_POLICY,
                    user=json.dumps(payload, ensure_ascii=False),
                    temperature=0,
                    max_tokens=900,
                )
                draft = BookSummaryDraft.model_validate(raw)
                returned = set(draft.chapter_ids)
                if returned != valid_ids or len(draft.chapter_ids) != len(valid_ids):
                    raise ChapteringValidationError("book summary must cover every known chapter exactly")
                return draft.summary.strip()
            except (ValidationError, ChapteringValidationError, LLMError) as error:
                last_error = error
                if isinstance(error, LLMError) or attempt >= self.validation_retries:
                    break
                if isinstance(error, ValidationError):
                    feedback: Any = error.errors(include_input=False, include_url=False)
                else:
                    feedback = {"missing_chapter_ids": sorted(valid_ids - returned),
                                "unknown_chapter_ids": sorted(returned - valid_ids),
                                "duplicate_chapter_ids": sorted({value for value in draft.chapter_ids
                                                                  if draft.chapter_ids.count(value) > 1})}
                payload["validation_feedback"] = (
                    "上一次整书摘要未通过校验。请覆盖全部输入章节（包括练习册、附录和索引），"
                    "并完整返回 required_chapter_ids，每个恰好一次，不得新增未知标识。具体问题："
                    + json.dumps(feedback, ensure_ascii=False)[:1600]
                )
        assert last_error is not None
        # Every real chapter remains in the directory. An optional overview is
        # recoverable from its existing summaries without approving unknown IDs
        # or repeating a provider request with an uncertain outcome.
        summaries = []
        for chapter in usable:
            match = re.search(r'[。！？!?]|\.(?=\s|$)', chapter.summary)
            sentence = chapter.summary[:match.end()] if match else chapter.summary
            summaries.append(chapter.title + '：' + sentence)
        logger.info('book enhancement retained chapter summaries', extra={
            'error_type': type(last_error).__name__})
        return '\n\n'.join(summaries)

    def _evidence_units(
        self, pages: list[PageExtraction]
    ) -> tuple[list[dict[str, object]], dict[str, SourceQuote]]:
        pages = [page for page in pages if not page_is_skipped(page)]
        if not pages:
            return [], {}
        # Keep an actual global budget, including books incorrectly detected as one chapter.
        # Sample pages evenly when the book exceeds the budget; never take 600 chars * every page.
        count = min(len(pages), max(1, self.max_chapter_characters // 600))
        selected_pages = [pages[round(i * (len(pages)-1) / max(1, count-1))] for i in range(count)]
        quota = self.max_chapter_characters // count
        payload: list[dict[str, object]] = []
        sources: dict[str, SourceQuote] = {}
        for page in selected_pages:
            units: list[SourceQuote] = []
            for block in page.blocks:
                units.extend(expand_source_quote(SourceQuote(page_number=page.page_number, quote=text), page)
                             for text in _split_evidence(block.text))
            if not units:
                units = [SourceQuote(page_number=page.page_number, quote=text)
                         for text in _split_evidence(page.raw_text)]
            # Several OCR lines may now refer to the same complete context.
            # Keep one literal unit rather than spending the budget on repeats.
            distinct: dict[str, SourceQuote] = {}
            for unit in units:
                distinct.setdefault(unit.quote, unit)
            units = list(distinct.values())
            # Cover the middle as well as the two ends of a long page. A unit
            # that does not fit must not discard later, shorter source spans.
            typical = max(1, int(statistics.median(len(unit.quote) for unit in units))) if units else 1
            sample_count = min(len(units), max(1, quota // typical))
            priority = [round(i * (len(units) - 1) / max(1, sample_count - 1))
                        for i in range(sample_count)]
            order = list(dict.fromkeys([*priority, *range(len(units))]))
            chosen: set[int] = set()
            used = 0
            for index in order:
                unit = units[index]
                if used + len(unit.quote) > quota:
                    continue
                chosen.add(index)
                used += len(unit.quote)
            selected = [unit for index, unit in enumerate(units) if index in chosen]
            for index, source in enumerate(selected, start=1):
                evidence_id = f"P{page.page_number}E{index}"
                payload.append({"id": evidence_id, "page_number": page.page_number, "text": source.quote})
                sources[evidence_id] = source
        return payload, sources


def load_normalized_pages(path: Path) -> list[PageExtraction]:
    pages: list[PageExtraction] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                raw: Any = json.loads(line)
                page_number = int(raw["page_number"])
                if page_is_skipped(raw):
                    raw = {**raw, "text": "", "raw_text": "", "cleaned_text": "", "blocks": []}
                raw_blocks = raw.get("blocks", [])
                method = {
                    "paddle_gpu": ExtractionMethod.PADDLE_GPU,
                    "rapidocr_onnx": ExtractionMethod.RAPIDOCR_ONNX,
                    "apple_vision": ExtractionMethod.APPLE_VISION,
                    "vision_rescue": ExtractionMethod.VISION_RESCUE,
                }.get(raw.get("extraction_method"), ExtractionMethod.MINERU_VLM)
                text_line_ocr = method in {ExtractionMethod.PADDLE_GPU, ExtractionMethod.RAPIDOCR_ONNX,
                                          ExtractionMethod.APPLE_VISION}
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                raise ValueError(f"invalid normalized page at line {line_number}") from error
            blocks: list[TextBlock] = []
            for index, block in enumerate(raw_blocks):
                if not isinstance(block, dict) or not str(block.get("text", "")).strip():
                    continue
                raw_value = block.get("raw")
                raw_source: dict[str, Any] = raw_value if isinstance(raw_value, dict) else {}
                bbox = block.get("bbox")
                normalized_bbox = None
                if isinstance(bbox, list) and len(bbox) == 4:
                    normalized_bbox = (
                        float(bbox[0]),
                        float(bbox[1]),
                        float(bbox[2]),
                        float(bbox[3]),
                    )
                block_index = int(block.get("block_index", index))
                blocks.append(
                    TextBlock(
                        block_id=f"block_{page_number}_{block_index}",
                        page_number=page_number,
                        block_type=str(block.get("type", "text")),
                        bbox=normalized_bbox,
                        text=str(block["text"]).strip(),
                        source_method=method,
                        confidence=raw_source.get("confidence") if text_line_ocr else None,
                        metadata={
                            "source_block_index": block_index,
                            "text_level": raw_source.get("text_level"),
                            **({"ocr_backend": raw_source.get("ocr_backend"),
                                "heading_inference_source": raw_source.get("heading_inference_source"),
                                "source_ocr_line_ids": raw_source.get("source_ocr_line_ids"),
                                "toc_evidence": raw_source.get("toc_evidence"),
                                "ocr_capabilities": raw.get("ocr_capabilities")}
                               if text_line_ocr else {}),
                        },
                    )
                )
            text = str(raw.get("text", "")).strip() or "\n".join(block.text for block in blocks)
            signals = evaluate_text_quality(text)
            pages.append(
                PageExtraction(
                    page_number=page_number,
                    page_kind=PageKind.SCANNED,
                    method=method,
                    raw_text=text,
                    processing_status=str(raw.get("processing_status", "")),
                    blocks=blocks,
                    quality=signals,
                    band=(QualityBand.ACCEPTED if raw.get("processing_status") in {"accepted", "corrected", "blank_confirmed", "skipped"}
                          else quality_band(signals.overall_score, accept=0.9, review=0.72)),
                )
            )
    if not pages:
        raise ValueError("normalized pages file is empty")
    numbers = [page.page_number for page in pages]
    if numbers != sorted(set(numbers)):
        raise ValueError("normalized pages must have unique ascending page numbers")
    return pages


def page_to_block(page: PageExtraction) -> TextBlock:
    text = page.cleaned_text or page.raw_text
    return TextBlock(
        block_id=f"page_{page.page_number}_{uuid.uuid4().hex[:10]}",
        page_number=page.page_number,
        text=text,
        confidence=page.quality.overall_score,
        source_method=page.method,
    )
