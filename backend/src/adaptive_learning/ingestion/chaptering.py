from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ..llm.client import LLMError, OpenAICompatibleClient
from .models import (
    BookStructure,
    ChapterDraft,
    ExtractionMethod,
    PageExtraction,
    PageKind,
    SourceQuote,
    TextBlock,
)
from .quality import evaluate_text_quality, quality_band

_CHINESE_CHAPTER = re.compile(
    r"^\s*(第\s*([一二三四五六七八九十百千万零〇两0-9]+)\s*[章篇部])\s*"
    r"(.{0,80}?)(?:[\.·…\s]{2,}\s*\d+)?\s*$",
    re.IGNORECASE,
)
_ENGLISH_CHAPTER = re.compile(
    r"^\s*((?:chapter|unit)\s+([0-9]+|[ivxlcdm]+))\s*(?:[:.\-—]\s*|\s*)"
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
2. summary 用 2-4 句概括本章主线，不超过2000字；knowledge_points 给出 3-12 个本章核心点，每项 text 不超过300字，每项 evidence_ids 为1-6个编号。每项只聚焦一个可独立学习的概念，不合并多个不相关问题。
3. 顶层 evidence_ids 选择2-12个支撑摘要的证据编号；编号必须来自输入 evidence，禁止创造编号。不要超出接口的数量上限。
4. 不输出小节层级，不输出 chunk id。
5. 只返回 JSON：
{"summary":"...","evidence_ids":["P1E1","P1E2"],"knowledge_points":[{"text":"概念一","evidence_ids":["P1E1"]},{"text":"概念二","evidence_ids":["P1E2"]},{"text":"概念三","evidence_ids":["P1E1","P1E2"]}]}
"""

_BOOK_SUMMARY_SYSTEM = """你是整书摘要器。输入是已经核验的章节摘要。
只能综合输入摘要，不补充外部知识。用 3-6 句说明全书主线，并返回实际使用的 chapter_ids。
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


class KnowledgePointDraft(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    evidence_ids: list[str] = Field(min_length=1, max_length=6)


class ChapterSummaryDraft(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)
    knowledge_points: list[KnowledgePointDraft] = Field(min_length=3, max_length=20)
    evidence_ids: list[str] = Field(min_length=2, max_length=12)


class BookSummaryDraft(BaseModel):
    summary: str = Field(min_length=1, max_length=3000)
    chapter_ids: list[str] = Field(min_length=1)


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _split_evidence(text: str, limit: int = 480) -> list[str]:
    normalized = _normalized(text)
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
        page_references = sum(bool(re.search(r"\s\d{1,4}$", line.strip()))
                              for line in page.raw_text.splitlines() if len(line) < 120)
        if has_label or matches >= 3 or dotted_rows >= 3 or page_references >= 5:
            result.add(page.page_number)
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
        return candidates

    def detect(self, pages: list[PageExtraction]) -> tuple[list[ChapterDraft], bool]:
        if not pages:
            raise ValueError("pages must not be empty")
        by_ordinal: dict[tuple[str, str], list[ChapterCandidate]] = {}
        for candidate in self.candidates(pages):
            key = (candidate.kind, candidate.ordinal)
            by_ordinal.setdefault(key, []).append(candidate)
        selected: list[ChapterCandidate] = []
        for occurrences in by_ordinal.values():
            body_occurrences = [
                item for item in occurrences if not item.toc_page and item.score >= 0.7
            ]
            if not body_occurrences:
                continue
            selected.append(max(body_occurrences, key=lambda item: (item.score, -item.page_number)))
        if any(item.kind == "chapter" for item in selected):
            selected = [item for item in selected if item.kind == "chapter"]
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
                    start_page=marker.page_number,
                    end_page=end,
                    summary="",
                    knowledge_points=[],
                    source_block_ids=[
                        marker.block_id,
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
        max_chapter_characters: int = 28_000,
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
        chapters, used_fallback = self.detector.detect(pages)
        if self.client is not None:
            chapters = [self._summarize(chapter, pages) for chapter in chapters]
            book_summary = self._summarize_book(chapters)
        else:
            book_summary = ""
        return BookStructure(
            title=fallback_title.strip() or "未命名书籍",
            summary=book_summary,
            chapters=chapters,
            source_page_count=len(pages),
            used_fallback_chapter=used_fallback,
        )

    def reconstruct(
        self, pages: list[PageExtraction], fallback_title: str
    ) -> tuple[str, list[ChapterDraft]]:
        structure = self.reconstruct_book(pages, fallback_title)
        return structure.title, structure.chapters

    def _summarize(self, chapter: ChapterDraft, pages: list[PageExtraction]) -> ChapterDraft:
        assert self.client is not None
        chapter_pages = [
            page for page in pages if chapter.start_page <= page.page_number <= chapter.end_page
        ]
        evidence_payload, evidence_source = self._evidence_units(chapter_pages)
        payload: dict[str, object] = {
            "chapter_title": chapter.title,
            "evidence": evidence_payload,
        }
        last_error: Exception | None = None
        for attempt in range(self.validation_retries + 1):
            try:
                result = self.client.structured(
                    system=_CHAPTER_SUMMARY_SYSTEM,
                    user=json.dumps(payload, ensure_ascii=False),
                    temperature=0,
                    max_tokens=1800,
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
                        "knowledge_points": list(point_evidence)[:20],
                        "evidence": evidence,
                        "knowledge_point_evidence": point_evidence,
                    }
                )
            except (ValidationError, ChapteringValidationError, LLMError) as error:
                last_error = error
                if attempt >= self.validation_retries:
                    break
                payload["validation_feedback"] = (
                    "上一次输出未通过校验。仅修复下面具体问题，所有引用仍必须来自输入。"
                    + (json.dumps(error.errors(include_input=False, include_url=False), ensure_ascii=False)
                       if isinstance(error, ValidationError) else str(error))[:1600]
                )
        assert last_error is not None
        raise ChapteringValidationError(
            f"chapter summary failed validation: {type(last_error).__name__}"
        ) from last_error

    def _summarize_book(self, chapters: list[ChapterDraft]) -> str:
        assert self.client is not None
        usable = [chapter for chapter in chapters if chapter.summary]
        if not usable:
            return ""
        raw = self.client.structured(
            system=_BOOK_SUMMARY_SYSTEM,
            user=json.dumps(
                {
                    "chapters": [
                        {
                            "chapter_id": chapter.chapter_id,
                            "title": chapter.title,
                            "summary": chapter.summary,
                        }
                        for chapter in usable
                    ]
                },
                ensure_ascii=False,
            ),
            temperature=0,
            max_tokens=1200,
        )
        try:
            draft = BookSummaryDraft.model_validate(raw)
        except ValidationError as error:
            raise ChapteringValidationError("book summary schema is invalid") from error
        valid_ids = {chapter.chapter_id for chapter in usable}
        if set(draft.chapter_ids) != valid_ids:
            raise ChapteringValidationError("book summary must cover every known chapter exactly")
        return draft.summary.strip()

    def _evidence_units(
        self, pages: list[PageExtraction]
    ) -> tuple[list[dict[str, object]], dict[str, SourceQuote]]:
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
            units: list[str] = []
            for block in page.blocks:
                units.extend(_split_evidence(block.text))
            if not units:
                units = _split_evidence(page.cleaned_text or page.raw_text)
            selected: list[str] = []
            used = 0
            left, right = 0, len(units) - 1
            take_left = True
            while left <= right:
                index = left if take_left else right
                unit = units[index]
                if selected and used + len(unit) > quota:
                    break
                selected.append(unit)
                used += len(unit)
                if take_left:
                    left += 1
                else:
                    right -= 1
                take_left = not take_left
            for index, text in enumerate(selected, start=1):
                evidence_id = f"P{page.page_number}E{index}"
                payload.append({"id": evidence_id, "page_number": page.page_number, "text": text})
                sources[evidence_id] = SourceQuote(page_number=page.page_number, quote=text)
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
                raw_blocks = raw.get("blocks", [])
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
                        source_method=ExtractionMethod.MINERU_VLM,
                        metadata={
                            "source_block_index": block_index,
                            "text_level": raw_source.get("text_level"),
                        },
                    )
                )
            text = str(raw.get("text", "")).strip() or "\n".join(block.text for block in blocks)
            signals = evaluate_text_quality(text)
            pages.append(
                PageExtraction(
                    page_number=page_number,
                    page_kind=PageKind.SCANNED,
                    method=ExtractionMethod.MINERU_VLM,
                    raw_text=text,
                    blocks=blocks,
                    quality=signals,
                    band=quality_band(signals.overall_score, accept=0.9, review=0.72),
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
