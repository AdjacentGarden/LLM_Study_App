"""Serve only indexed, local teaching-pack media to owners of the textbook."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..community import CommunityRepository
from ..ingestion.jobs import SQLiteOCRJobRepository
from ..personalization.imported_content import _media_for, load_imported_teaching
from .source_routes import _actual_pdf_hash

_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
}
_VIDEO_TYPES = {".mp4": "video/mp4", ".webm": "video/webm"}
_MAX_INDEX_BYTES = 2_000_000
_MAX_TEACHING_BYTES = 5_000_000
_SUPPLEMENTARY_NOTICE = "AI补充内容；源PDF缺少本章正文，尚未核验"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


class SupplementaryBlock(BaseModel):
    title: str
    content: str


class SupplementaryCard(BaseModel):
    id: str
    front: str


class SupplementaryQuestion(BaseModel):
    id: str
    prompt: str
    choices: list[str] = Field(default_factory=list)
    instruction: str = ""
    question_type: Literal["choice", "judgment", "short-answer"]


class SupplementaryLessonResponse(BaseModel):
    book_id: str
    chapter_id: str
    title: str
    summary: str
    blocks: list[SupplementaryBlock] = Field(default_factory=list)
    cards: list[SupplementaryCard] = Field(default_factory=list)
    questions: list[SupplementaryQuestion] = Field(default_factory=list)
    notice: str = _SUPPLEMENTARY_NOTICE


class TeachingLessonResponse(BaseModel):
    book_id: str
    chapter_id: str
    title: str
    summary: str
    objectives: list[str]
    blocks: list[dict[str, Any]]
    introduction_media: list[dict[str, Any]]
    source_available: bool


class SupplementaryCardAnswer(BaseModel):
    back: str


class SupplementaryCheckRequest(BaseModel):
    question_id: str = Field(min_length=1, max_length=128)
    answer: str = Field(min_length=1, max_length=2000)


class SupplementaryCheckResponse(BaseModel):
    correct: bool | None
    answer: str
    explanation: str


def _imported_root(data_dir: Path, book_id: str) -> Path:
    # Neither URL parameter is interpreted as a filesystem path. The book name
    # still has to be a single safe directory segment before joining it.
    if (
        not book_id
        or book_id in {".", ".."}
        or any(mark in book_id for mark in ("/", "\\", "\x00"))
    ):
        raise HTTPException(404, "教材配图不存在")
    try:
        books_root = (data_dir / "books").resolve()
        book_root = (books_root / book_id).resolve()
        imported = (book_root / "imported").resolve()
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(404, "教材配图路径无效") from error
    if not book_root.is_relative_to(books_root) or not imported.is_relative_to(book_root):
        raise HTTPException(404, "教材配图路径无效")
    return imported


def _indexed_file(data_dir: Path, book_id: str, asset_id: str) -> tuple[Path, str]:
    imported = _imported_root(data_dir, book_id)
    try:
        index_file = (imported / "assets.json").resolve(strict=True)
        if not index_file.is_relative_to(imported):
            raise HTTPException(404, "教材配图路径无效")
        if index_file.stat().st_size > _MAX_INDEX_BYTES:
            raise ValueError("index too large")
        index: Any = json.loads(index_file.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise HTTPException(404, "这本教材没有可用的导入配图") from error
    except (OSError, RuntimeError, UnicodeError, ValueError) as error:
        raise HTTPException(503, "教材配图索引暂不可用") from error

    row = index.get(asset_id) if isinstance(index, dict) else None
    if not isinstance(row, dict):
        raise HTTPException(404, "教材配图不存在")
    raw_path = row.get("path")
    kind = row.get("kind")
    if not isinstance(raw_path, str) or kind not in {"image", "video"}:
        raise HTTPException(404, "教材配图不存在")
    relative = PurePosixPath(raw_path)
    if (
        not raw_path
        or "\\" in raw_path
        or relative.is_absolute()
        or relative.as_posix() != raw_path
        or len(relative.parts) < 2
        or relative.parts[0] != "assets"
        or any(part in {".", ".."} for part in relative.parts)
    ):
        raise HTTPException(404, "教材配图路径无效")

    media_types = _IMAGE_TYPES if kind == "image" else _VIDEO_TYPES
    media_type = media_types.get(relative.suffix.lower())
    if media_type is None:
        raise HTTPException(404, "教材配图格式不可用")
    try:
        asset_root = (imported / "assets").resolve(strict=True)
        asset = (imported / Path(*relative.parts)).resolve(strict=True)
        if (
            not asset_root.is_relative_to(imported)
            or not asset.is_relative_to(asset_root)
            or not asset.is_file()
        ):
            raise HTTPException(404, "教材配图路径无效")
    except (OSError, RuntimeError) as error:
        raise HTTPException(404, "教材配图文件不存在") from error
    return asset, media_type


def _supplementary_source(
    data_dir: Path, book_id: str, chapter_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not _SAFE_ID.fullmatch(chapter_id):
        raise HTTPException(404, "这章没有源文缺失的 AI 补充讲解")
    imported = _imported_root(data_dir, book_id)
    try:
        teaching_file = (imported / "teaching.json").resolve(strict=True)
        if not teaching_file.is_relative_to(imported):
            raise HTTPException(404, "补充讲解路径无效")
        if teaching_file.stat().st_size > _MAX_TEACHING_BYTES:
            raise ValueError("teaching pack too large")
        teaching: Any = json.loads(teaching_file.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise HTTPException(404, "这本教材没有可读的补充讲解") from error
    except (OSError, RuntimeError, UnicodeError, ValueError) as error:
        raise HTTPException(503, "补充讲解暂不可用") from error
    if not isinstance(teaching, dict):
        raise HTTPException(503, "补充讲解数据不完整")
    version = teaching.get("schema_version")
    if (
        teaching.get("source_book_id") not in (None, book_id)
        or (version is not None and (type(version) is not int or version != 1))
    ):
        raise HTTPException(503, "补充讲解数据不完整")
    lessons = teaching.get("lessons")
    if not isinstance(lessons, list):
        raise HTTPException(503, "补充讲解数据不完整")
    for lesson in lessons:
        if not isinstance(lesson, dict) or not (
            lesson.get("book_id") == book_id
            and lesson.get("chapter_id") == chapter_id
            and type(lesson.get("page_start")) is int
            and lesson["page_start"] == 0
            and type(lesson.get("page_end")) is int
            and lesson["page_end"] == 0
            and lesson.get("status") == "supplemental_draft"
        ):
            continue
        lesson_id = lesson.get("lesson_id")
        if lesson_id is not None and (
            not isinstance(lesson_id, str) or not _SAFE_ID.fullmatch(lesson_id)
        ):
            raise HTTPException(503, "补充讲解数据不完整")
        return teaching, lesson
    raise HTTPException(404, "这章没有源文缺失的 AI 补充讲解")


def _supplementary_items(
    teaching: dict[str, Any],
    lesson: dict[str, Any],
    book_id: str,
    chapter_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cards = teaching.get("flashcards", [])
    questions = teaching.get("quiz", [])
    if not isinstance(cards, list) or not isinstance(questions, list):
        raise HTTPException(503, "补充练习数据不完整")

    def matching(row: object) -> bool:
        return (
            isinstance(row, dict)
            and row.get("book_id") == book_id
            and row.get("chapter_id") == chapter_id
        )

    def shared(row: dict[str, Any]) -> bool:
        lesson_id = lesson.get("lesson_id")
        return (
            row.get("source_kind") == "ai_supplement"
            and type(row.get("page_start")) is int
            and row["page_start"] == 0
            and type(row.get("page_end")) is int
            and row["page_end"] == 0
            and (row.get("source_quote") is None or row.get("source_quote") == "")
            and (lesson_id is None or row.get("lesson_id") == lesson_id)
        )

    selected_cards: list[dict[str, Any]] = []
    seen_cards: set[str] = set()
    for row in cards:
        if not matching(row):
            continue
        card_id = row.get("card_id")
        if (
            not shared(row)
            or not isinstance(card_id, str)
            or not _SAFE_ID.fullmatch(card_id)
            or card_id in seen_cards
            or not _valid_text(row.get("front"), 1000)
            or not _valid_text(row.get("back"), 2000)
        ):
            raise HTTPException(503, "补充闪卡数据不完整")
        seen_cards.add(card_id)
        selected_cards.append(row)

    selected_questions: list[dict[str, Any]] = []
    seen_questions: set[str] = set()
    for row in questions:
        if not matching(row):
            continue
        question_id = row.get("question_id")
        question_type = row.get("question_type")
        choices = row.get("choices")
        answer = row.get("answer")
        valid_choices = isinstance(choices, list) and all(
            _valid_text(choice, 1000) for choice in choices
        )
        if (
            not shared(row)
            or not isinstance(question_id, str)
            or not _SAFE_ID.fullmatch(question_id)
            or question_id in seen_questions
            or not isinstance(question_type, str)
            or question_type not in {"choice", "judgment", "short-answer"}
            or not _valid_text(row.get("prompt"), 2000)
            or not _valid_text(answer, 2000)
            or not valid_choices
            or (question_type == "short-answer" and choices)
            or (question_type != "short-answer" and (
                len(choices) < 2 or choices.count(answer) != 1
            ))
            or not isinstance(row.get("explanation", ""), str)
            or not isinstance(row.get("instruction", ""), str)
        ):
            raise HTTPException(503, "补充练习数据不完整")
        seen_questions.add(question_id)
        selected_questions.append(row)
    return selected_cards, selected_questions


def _valid_text(value: object, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and "\ufffd" not in value


def _supplementary_lesson(
    data_dir: Path, book_id: str, chapter_id: str
) -> SupplementaryLessonResponse:
    teaching, lesson = _supplementary_source(data_dir, book_id, chapter_id)
    source_blocks = lesson.get("blocks")
    if not isinstance(source_blocks, list):
        raise HTTPException(503, "补充讲解数据不完整")
    blocks = [
        SupplementaryBlock(title=block["title"], content=block["content"])
        for block in source_blocks
        if isinstance(block, dict)
        and _valid_text(block.get("title"), 200)
        and _valid_text(block.get("content"), 12_000)
    ]
    if not blocks:
        raise HTTPException(503, "补充讲解内容不可用")
    title = lesson.get("title")
    summary = lesson.get("summary")
    if not isinstance(title, str) or not _valid_text(title, 300):
        raise HTTPException(503, "补充讲解数据不完整")
    cards, questions = _supplementary_items(teaching, lesson, book_id, chapter_id)
    return SupplementaryLessonResponse(
        book_id=book_id,
        chapter_id=chapter_id,
        title=title.strip(),
        summary=summary if isinstance(summary, str) else "",
        blocks=blocks,
        cards=[SupplementaryCard(id=row["card_id"], front=row["front"]) for row in cards],
        questions=[
            SupplementaryQuestion(
                id=row["question_id"],
                prompt=row["prompt"],
                choices=row["choices"],
                instruction=row.get("instruction", ""),
                question_type=row["question_type"],
            ) for row in questions
        ],
    )


def imported_assets_router(
    repo: CommunityRepository,
    data_dir: Path,
    visitor: Callable[..., str],
    jobs: Callable[[], SQLiteOCRJobRepository] | None = None,
) -> APIRouter:
    """Mount under /api; visitor is the existing cookie/account dependency."""
    router = APIRouter(prefix="/books", tags=["imported teaching media"])

    def private_response(response: Response) -> None:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"

    @router.get(
        "/{book_id}/teaching-lessons/{chapter_id}",
        response_model=TeachingLessonResponse,
    )
    def teaching_lesson(
        book_id: str,
        chapter_id: str,
        response: Response,
        owner: str = Depends(visitor),
    ) -> TeachingLessonResponse:
        if not repo.owns(owner, book_id):
            raise HTTPException(403, "请先将这本书加入自己的书架")
        if jobs is None or not _SAFE_ID.fullmatch(chapter_id):
            raise HTTPException(404, "本章没有已导入的讲解")
        imported = _imported_root(data_dir, book_id)
        try:
            pack_path = (imported / "teaching.json").resolve(strict=True)
            if not pack_path.is_relative_to(imported) or pack_path.stat().st_size > _MAX_TEACHING_BYTES:
                raise ValueError("invalid teaching pack")
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise HTTPException(404, "本章没有已导入的讲解") from error
        except (OSError, RuntimeError, UnicodeError, ValueError) as error:
            raise HTTPException(503, "讲解数据暂不可用") from error
        if not isinstance(pack, dict) or pack.get("schema_version") != 1 or pack.get("source_book_id") != book_id:
            raise HTTPException(503, "讲解数据身份不匹配")
        asset_row = repo.asset(book_id)
        source_id = str(asset_row["canonical"]) if asset_row is not None else book_id
        repository = jobs()
        source_sha = repository.source_fingerprint(source_id)
        if not source_sha or pack.get("source_pdf_sha256") != source_sha:
            raise HTTPException(409, "教材原文已变更，请重新核对讲解")
        stored = repository.get_book(source_id)
        try:
            if stored is None or _actual_pdf_hash(stored.file_path) != source_sha:
                raise HTTPException(409, "教材原文已变更，请重新核对讲解")
        except OSError as error:
            raise HTTPException(404, "原始 PDF 文件不可用") from error
        structure = repository.get_structure(source_id)
        chapter = next((item for item in structure.chapters if item.chapter_id == chapter_id), None) if structure else None
        if chapter is None:
            raise HTTPException(404, "本章没有已导入的讲解")
        lessons = pack.get("lessons")
        lesson = next((item for item in lessons if isinstance(item, dict) and item.get("book_id") == book_id and item.get("chapter_id") == chapter_id), None) if isinstance(lessons, list) else None
        if lesson is None:
            raise HTTPException(404, "本章没有已导入的讲解")
        raw_blocks = lesson.get("blocks")
        if not isinstance(raw_blocks, list) or not raw_blocks:
            raise HTTPException(503, "讲解内容不完整")
        source_available = chapter.start_page > 0 and chapter.end_page >= chapter.start_page
        if source_available:
            checked = load_imported_teaching(data_dir, book_id, chapter, source_sha)
            sections = list(checked.sections) if checked else []
            if len(sections) != len(raw_blocks) or any(
                not isinstance(raw, dict) or raw.get("title") != section.title or raw.get("content", "").strip() != section.content
                for raw, section in zip(raw_blocks, sections, strict=True)
            ):
                raise HTTPException(503, "讲解引用未通过原文核验")
            blocks = [section.model_dump(mode="json") for section in sections]
        elif chapter.has_supplementary_content and lesson.get("page_start") == 0 and lesson.get("page_end") == 0:
            supplement = _supplementary_lesson(data_dir, book_id, chapter_id)
            blocks = [
                {"title": block.title, "content": block.content, "purpose": "", "citations": [], "media": []}
                for block in supplement.blocks
            ]
        else:
            raise HTTPException(404, "本章没有已导入的讲解")
        try:
            assets = json.loads((imported / "assets.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            assets = {}
        if not isinstance(assets, dict):
            assets = {}
        introduction_media = [
            item.model_dump(mode="json")
            for item in _media_for(lesson.get("asset_ids"), assets, imported, book_id)
        ]
        if not source_available:
            for raw, block in zip(raw_blocks, blocks, strict=True):
                block["media"] = [
                    item.model_dump(mode="json")
                    for item in _media_for(raw.get("asset_ids"), assets, imported, book_id)
                ]
        private_response(response)
        return TeachingLessonResponse(
            book_id=book_id,
            chapter_id=chapter_id,
            title=str(lesson.get("title", ""))[:300],
            summary=str(lesson.get("summary", ""))[:12000],
            objectives=[str(value)[:500] for value in lesson.get("objectives", []) if isinstance(value, str)][:12],
            blocks=blocks,
            introduction_media=introduction_media,
            source_available=source_available,
        )

    @router.get("/{book_id}/imported-assets/{asset_id}", response_class=FileResponse)
    def imported_asset(
        book_id: str,
        asset_id: str,
        owner: str = Depends(visitor),
    ) -> FileResponse:
        if not repo.owns(owner, book_id):
            raise HTTPException(403, "请先将这本书加入自己的书架")
        asset, media_type = _indexed_file(data_dir, book_id, asset_id)
        return FileResponse(
            asset,
            media_type=media_type,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Cross-Origin-Resource-Policy": "same-origin",
            },
        )

    @router.get(
        "/{book_id}/supplementary-lessons/{chapter_id}",
        response_model=SupplementaryLessonResponse,
    )
    def supplementary_lesson(
        book_id: str,
        chapter_id: str,
        response: Response,
        owner: str = Depends(visitor),
    ) -> SupplementaryLessonResponse:
        if not repo.owns(owner, book_id):
            raise HTTPException(403, "请先将这本书加入自己的书架")
        lesson = _supplementary_lesson(data_dir, book_id, chapter_id)
        private_response(response)
        return lesson

    @router.post(
        "/{book_id}/supplementary-lessons/{chapter_id}/cards/{card_id}/reveal",
        response_model=SupplementaryCardAnswer,
    )
    def reveal_supplementary_card(
        book_id: str,
        chapter_id: str,
        card_id: str,
        response: Response,
        owner: str = Depends(visitor),
    ) -> SupplementaryCardAnswer:
        if not repo.owns(owner, book_id):
            raise HTTPException(403, "请先将这本书加入自己的书架")
        if not _SAFE_ID.fullmatch(card_id):
            raise HTTPException(404, "补充闪卡不存在")
        teaching, lesson = _supplementary_source(data_dir, book_id, chapter_id)
        cards, _questions = _supplementary_items(teaching, lesson, book_id, chapter_id)
        card = next((item for item in cards if item["card_id"] == card_id), None)
        if card is None:
            raise HTTPException(404, "补充闪卡不存在")
        private_response(response)
        return SupplementaryCardAnswer(back=card["back"])

    @router.post(
        "/{book_id}/supplementary-lessons/{chapter_id}/check",
        response_model=SupplementaryCheckResponse,
    )
    def check_supplementary_question(
        book_id: str,
        chapter_id: str,
        request: SupplementaryCheckRequest,
        response: Response,
        owner: str = Depends(visitor),
    ) -> SupplementaryCheckResponse:
        if not repo.owns(owner, book_id):
            raise HTTPException(403, "请先将这本书加入自己的书架")
        if not _SAFE_ID.fullmatch(request.question_id):
            raise HTTPException(404, "补充练习不存在")
        teaching, lesson = _supplementary_source(data_dir, book_id, chapter_id)
        _cards, questions = _supplementary_items(teaching, lesson, book_id, chapter_id)
        question = next(
            (item for item in questions if item["question_id"] == request.question_id), None
        )
        if question is None:
            raise HTTPException(404, "补充练习不存在")
        choices = question["choices"]
        submitted = request.answer.strip()
        if not submitted:
            raise HTTPException(422, "请先填写答案")
        if choices and submitted not in choices:
            raise HTTPException(422, "请选择题目提供的选项")
        # Only closed options permit exact comparison. A free-text answer can
        # have valid paraphrases, so return the reference without fabricating a grade.
        correct = submitted == question["answer"] if choices else None
        private_response(response)
        return SupplementaryCheckResponse(
            correct=correct,
            answer=question["answer"],
            explanation=question.get("explanation", ""),
        )

    return router
