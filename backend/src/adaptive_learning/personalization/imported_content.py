"""Read an optional, source-checked teaching pack for one textbook section.

The pack is instructional material, never learning history. Its citations are
checked against the current PDF fingerprint and normalized OCR blocks before
they can appear in a compiled course.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..ingestion.chaptering import load_normalized_pages
from ..ingestion.models import ChapterDraft, PageExtraction
from .models import LessonMedia, LessonSection, SourceCitation

logger = logging.getLogger(__name__)
_MAX_PACK_BYTES = 12_000_000
_MAX_ASSETS_BYTES = 2_000_000
_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


@dataclass(frozen=True)
class ImportedCard:
    source_id: str
    point_label: str
    front: str
    back: str
    reason: str
    citation: SourceCitation
    context: SourceCitation | None


@dataclass(frozen=True)
class ImportedQuestion:
    source_id: str
    point_label: str
    prompt: str
    response_type: str
    options: tuple[str, ...]
    answer: str
    correct_option_ids: tuple[str, ...]
    rubric: tuple[str, ...]
    citation: SourceCitation


@dataclass(frozen=True)
class ImportedTeachingUnit:
    sections: tuple[LessonSection, ...]
    cards: tuple[ImportedCard, ...]
    questions: tuple[ImportedQuestion, ...]
    point_contexts: tuple[tuple[str, SourceCitation], ...]
    content_hash: str


def load_imported_teaching(
    data_dir: Path,
    book_id: str,
    chapter: ChapterDraft,
    source_pdf_sha256: str | None,
) -> ImportedTeachingUnit | None:
    """Return vetted content or None; an absent/broken pack keeps normal courses usable."""
    if not _SAFE_SEGMENT.fullmatch(book_id) or not source_pdf_sha256:
        return None
    imported_dir = data_dir / "books" / book_id / "imported"
    pack_path = imported_dir / "teaching.json"
    if not pack_path.is_file():
        return None
    try:
        pack = _read_json(pack_path, _MAX_PACK_BYTES)
        if (
            not isinstance(pack, dict)
            or pack.get("schema_version") != 1
            or pack.get("source_book_id") != book_id
            or pack.get("source_pdf_sha256") != source_pdf_sha256
        ):
            logger.warning("ignored teaching pack with invalid identity or source hash", extra={"book_id": book_id})
            return None
        pages = load_normalized_pages(
            data_dir / "books" / book_id / "ocr" / "normalized" / "pages.jsonl"
        )
        assets_path = imported_dir / "assets.json"
        assets = _read_json(assets_path, _MAX_ASSETS_BYTES) if assets_path.is_file() else {}
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        logger.warning("ignored unavailable teaching pack", extra={"book_id": book_id, "reason": str(error)})
        return None
    if not isinstance(assets, dict):
        assets = {}

    text_by_page = _chapter_page_text(pages, chapter)
    points = {
        label for label in chapter.knowledge_points if chapter.knowledge_point_evidence.get(label)
    }
    sections: list[LessonSection] = []
    cards: list[ImportedCard] = []
    questions: list[ImportedQuestion] = []
    point_contexts: list[tuple[str, SourceCitation]] = []
    for label in chapter.knowledge_points:
        if label not in points:
            continue
        for quote in chapter.knowledge_point_evidence[label]:
            if not chapter.start_page <= quote.page_number <= chapter.end_page:
                continue
            context = _context_citation(
                SourceCitation(page_number=quote.page_number, quote=quote.quote), chapter, pages
            )
            if context is not None and (label, context) not in point_contexts:
                point_contexts.append((label, context))

    for lesson in _rows(pack.get("lessons")):
        if lesson.get("book_id") != book_id or lesson.get("chapter_id") != chapter.chapter_id:
            continue
        for block in _rows(lesson.get("blocks")):
            title = _short_text(block.get("title"), 200)
            content = _short_text(block.get("content"), 12_000)
            if not title or not content:
                continue
            citations = []
            for raw in _rows(block.get("citations")):
                citation = _verified_citation(raw, source_pdf_sha256, chapter, text_by_page)
                if citation is not None and citation not in citations:
                    citations.append(citation)
            if not citations:
                continue
            media = _media_for(block.get("asset_ids"), assets, imported_dir, book_id)
            origin = "AI 整理讲解" if block.get("ai_generated") is True else "导入教材讲解"
            sections.append(
                LessonSection(
                    title=title,
                    content=content,
                    purpose=f"{origin}；下列原文引文已核验，讲解请结合原文阅读。",
                    citations=citations,
                    media=media,
                )
            )

    for row in _rows(pack.get("flashcards")):
        if row.get("book_id") != book_id or row.get("chapter_id") != chapter.chapter_id:
            continue
        label = _short_text(row.get("concept"), 300)
        front = _short_text(row.get("front"), 1000)
        back = _short_text(row.get("back"), 2000)
        source_id = _short_text(row.get("card_id"), 120)
        citation = _verified_citation(row, source_pdf_sha256, chapter, text_by_page)
        if label not in points or not front or not back or not source_id or citation is None:
            continue
        cards.append(
            ImportedCard(
                source_id=source_id,
                point_label=label,
                front=front,
                back=back,
                reason=_short_text(row.get("reason"), 500),
                citation=citation,
                context=_context_citation(citation, chapter, pages),
            )
        )

    for row in _rows(pack.get("quiz")):
        if row.get("book_id") != book_id or row.get("chapter_id") != chapter.chapter_id:
            continue
        label = _short_text(row.get("concept"), 300)
        prompt = _short_text(row.get("prompt"), 2000)
        answer = _short_text(row.get("answer"), 2000)
        source_id = _short_text(row.get("question_id"), 120)
        citation = _verified_citation(row, source_pdf_sha256, chapter, text_by_page)
        if label not in points or not prompt or not answer or not source_id or citation is None:
            continue
        kind = row.get("question_type")
        options: tuple[str, ...] = ()
        correct_option_ids: tuple[str, ...] = ()
        if kind in {"choice", "judgment"}:
            options = tuple(
                _short_text(value, 1000) for value in _values(row.get("choices"))
            )
            if len(options) < 2 or any(not value for value in options) or options.count(answer) != 1:
                continue
            response_type = "single_choice"
            correct_option_ids = (str(options.index(answer)),)
        elif kind == "short-answer":
            response_type = "short_answer"
        else:
            continue
        explanation = _short_text(row.get("explanation"), 2000)
        questions.append(
            ImportedQuestion(
                source_id=source_id,
                point_label=label,
                prompt=prompt,
                response_type=response_type,
                options=options,
                answer=answer,
                correct_option_ids=correct_option_ids,
                rubric=(explanation,) if explanation else (answer,),
                citation=citation,
            )
        )

    if not (sections or cards or questions):
        return None
    payload = {
        "sections": [item.model_dump(mode="json") for item in sections],
        "cards": [
            item.__dict__
            | {
                "citation": item.citation.model_dump(mode="json"),
                "context": item.context.model_dump(mode="json") if item.context else None,
            }
            for item in cards
        ],
        "questions": [
            item.__dict__ | {"citation": item.citation.model_dump(mode="json")}
            for item in questions
        ],
        "point_contexts": [
            [label, citation.model_dump(mode="json")] for label, citation in point_contexts
        ],
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ImportedTeachingUnit(
        tuple(sections), tuple(cards), tuple(questions), tuple(point_contexts), digest
    )


def _read_json(path: Path, max_bytes: int) -> Any:
    if path.stat().st_size > max_bytes:
        raise ValueError("teaching file exceeds size limit")
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _values(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _short_text(value: object, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _normalized(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split()).casefold()


def _chapter_page_text(pages: list[PageExtraction], chapter: ChapterDraft) -> dict[int, str]:
    allowed = set(chapter.source_block_ids)
    result: dict[int, str] = {}
    for page in pages:
        if not chapter.start_page <= page.page_number <= chapter.end_page:
            continue
        if allowed:
            blocks = [block.text for block in page.blocks if block.block_id in allowed]
            if blocks:
                result[page.page_number] = _normalized(" ".join(blocks))
        else:
            result[page.page_number] = _normalized(page.cleaned_text or page.raw_text)
    return result


def _verified_citation(
    row: dict[str, Any],
    source_pdf_sha256: str,
    chapter: ChapterDraft,
    text_by_page: dict[int, str],
) -> SourceCitation | None:
    if row.get("source_kind", "textbook") != "textbook":
        return None
    metadata = row.get("source_metadata")
    if not isinstance(metadata, dict):
        return None
    source_hash = metadata.get("source_sha256")
    pdf_hash = metadata.get("source_pdf_sha256")
    if (source_hash and pdf_hash and source_hash != pdf_hash) or (
        source_hash or pdf_hash
    ) != source_pdf_sha256:
        return None
    raw_quote = row.get("source_quote", row.get("quote"))
    if not isinstance(raw_quote, str) or len(raw_quote.strip()) > 500:
        return None
    quote = raw_quote.strip()
    start, end = row.get("page_start"), row.get("page_end")
    if (
        not quote
        or isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or not chapter.start_page <= start <= end <= chapter.end_page
        or end - start > 10
    ):
        return None
    normalized_quote = _normalized(quote)
    if len(normalized_quote) < 4:
        return None
    for page_number in range(start, end + 1):
        if normalized_quote in text_by_page.get(page_number, ""):
            return SourceCitation(page_number=page_number, quote=quote)
    return None


def _context_citation(
    citation: SourceCitation, chapter: ChapterDraft, pages: list[PageExtraction]
) -> SourceCitation | None:
    """Add only the OCR block that actually contains the verified short quote."""
    allowed = set(chapter.source_block_ids)
    if not allowed:
        return None
    target = _normalized(citation.quote)
    for page in pages:
        if page.page_number != citation.page_number:
            continue
        for block in page.blocks:
            if block.block_id not in allowed or target not in _normalized(block.text):
                continue
            context = block.text.strip()
            if len(context) > 500:
                position = context.find(citation.quote)
                if position < 0:
                    continue
                start = max(0, position - 180)
                start_boundary = context.rfind("。", start, position)
                if start_boundary >= 0:
                    start = start_boundary + 1
                end = min(len(context), position + len(citation.quote) + 250)
                end_boundary = context.find("。", position + len(citation.quote), end)
                if end_boundary >= 0:
                    end = end_boundary + 1
                context = context[start:end]
                if len(context) > 500:
                    context = context[:500]
            if context != citation.quote and target in _normalized(context):
                return SourceCitation(
                    page_number=page.page_number,
                    block_id=block.block_id,
                    quote=context,
                )
    return None


def _media_for(
    asset_ids: object,
    assets: dict[str, Any],
    imported_dir: Path,
    book_id: str,
) -> list[LessonMedia]:
    media: list[LessonMedia] = []
    for asset_id in _values(asset_ids):
        if not isinstance(asset_id, str) or not _SAFE_SEGMENT.fullmatch(asset_id):
            continue
        row = assets.get(asset_id)
        if not isinstance(row, dict) or row.get("kind") not in {"image", "video"}:
            continue
        raw_path = row.get("path")
        if not isinstance(raw_path, str) or "\\" in raw_path:
            continue
        relative = PurePosixPath(raw_path)
        if (
            relative.is_absolute()
            or len(relative.parts) < 2
            or relative.parts[0] != "assets"
            or any(part in {".", ".."} for part in relative.parts)
        ):
            continue
        asset = (imported_dir / Path(*relative.parts)).resolve()
        if not asset.is_relative_to((imported_dir / "assets").resolve()) or not asset.is_file():
            continue
        page_number = row.get("page_number")
        if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
            page_number = None
        media.append(
            LessonMedia(
                asset_id=asset_id,
                kind=row["kind"],
                caption=_short_text(row.get("caption"), 500),
                url=f"/api/books/{book_id}/imported-assets/{asset_id}",
                source_kind=_short_text(row.get("source_kind"), 80),
                page_number=page_number,
            )
        )
    return media
