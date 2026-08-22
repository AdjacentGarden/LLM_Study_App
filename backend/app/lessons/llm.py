from __future__ import annotations

from typing import Any, Protocol
import json
import re

from pydantic import ValidationError

from app.core.ai_runtime import AIProviderRuntime, get_ai_runtime, policy_from_settings
from app.core.deepseek import deepseek_payload_extras
from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.books import ChapterSourcePackage, ChapterSourceWindow, Lesson, LessonBlock, LessonCitation


def _decode_provider_json_object(content: str) -> dict[str, Any]:
    """Decode a JSON object even when an OpenAI-compatible relay adds prose or fences."""
    stripped = content.strip()
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        object_start = stripped.find("{")
        if object_start < 0:
            raise
        decoded, _ = json.JSONDecoder().raw_decode(stripped[object_start:])
    if not isinstance(decoded, dict):
        raise ValueError("lesson response must be a JSON object")
    return decoded


class LessonGenerationAdapter(Protocol):
    name: str

    def build_lesson(self, source: ChapterSourcePackage) -> Lesson:
        ...


def _compact_text(text: str, limit: int = 260) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _clean_title(title: str) -> str:
    return re.sub(r"^(?:\s*课程\s*[：:]\s*)+", "", title).strip() or "未命名章节"


def _course_title(title: str) -> str:
    return f"课程：{_clean_title(title)}"


def _normalize_generated_title(title: str) -> str:
    return _course_title(title) if re.match(r"^\s*课程\s*[：:]", title) else title.strip()


def _lesson_confidence(source: ChapterSourcePackage) -> int:
    evidence_quality = min(90, 55 + source.chunk_count * 7)
    confidence = round((max(0, min(100, source.chapter_confidence)) * 0.6) + (evidence_quality * 0.4))
    if source.warnings:
        confidence -= 10
    return max(25, min(95, confidence))


def _window_text(window: ChapterSourceWindow) -> str:
    return (window.plain_text or window.text).strip()


def _source_focus(source: ChapterSourcePackage) -> str:
    lines = [line.strip() for window in source.windows for line in re.split(r"\n+", _window_text(window)) if line.strip()]
    for line in lines:
        if len(line.split()) >= 6 and not re.match(r"^(?:chapter|unit|course)\b", line, re.IGNORECASE):
            return _compact_text(line, 180)
        if len(re.findall(r"[\u4e00-\u9fff]", line)) >= 12:
            return _compact_text(line, 180)
    return _compact_text(lines[0], 180) if lines else "当前章节正文不足"


def _english_concepts(text: str) -> list[str]:
    stopwords = {
        "a", "and", "are", "basic", "contains", "course", "describe", "describes", "diagram", "file", "functional", "is", "life", "of", "or", "small",
        "perform", "performs", "produce", "produces", "regulate", "regulates", "store", "stores", "structural",
        "synthetic", "the", "this", "unit", "units", "with",
    }
    concepts: list[str] = []
    for raw_line in re.split(r"[\n.!?]+", text):
        line = re.sub(r"^(?:chapter|unit)\s+[A-Za-z0-9_-]+\s*", "", raw_line.strip(), flags=re.IGNORECASE)
        if not line or line.lower().endswith(" course"):
            continue
        segment: list[str] = []
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]*", line)
        for token in [*tokens, "and"]:
            if token.lower() in stopwords:
                if segment:
                    candidate = " ".join(segment[:3])
                    if candidate.lower() not in {item.lower() for item in concepts}:
                        concepts.append(candidate)
                    segment = []
                continue
            segment.append(token)
        if len(concepts) >= 10:
            break
    return concepts


def _window_citation(window: ChapterSourceWindow) -> LessonCitation | None:
    if not window.source_chunk_ids:
        return None
    return LessonCitation(
        chunk_id=window.source_chunk_ids[0],
        page_start=window.page_start,
        page_end=window.page_end,
        quote=_compact_text(_window_text(window), 160),
    )


def _block(
    lesson_id: str,
    index: int,
    block_type: str,
    title: str,
    content: str,
    windows: list[ChapterSourceWindow],
    *,
    ai_generated: bool = True,
) -> LessonBlock:
    citations = [citation for window in windows if (citation := _window_citation(window)) is not None]
    source_chunk_ids = []
    for window in windows:
        source_chunk_ids.extend(window.source_chunk_ids)
    return LessonBlock(
        block_id=f"{lesson_id}_b{index:03d}",
        block_type=block_type,
        title=title,
        content=content,
        citations=citations,
        source_chunk_ids=source_chunk_ids,
        ai_generated=ai_generated,
    )


def _concepts_from_source(source: ChapterSourcePackage) -> list[str]:
    concepts: list[str] = []
    title = _clean_title(source.chapter_title)
    if title and not title.startswith("整本文档") and title not in {"导读", "未命名章节"}:
        concepts.append(title)
    english = _english_concepts("\n".join(_window_text(window) for window in source.windows))
    for item in english:
        if item not in concepts:
            concepts.append(item)
        if len(concepts) >= 8:
            return concepts
    ignored = {"课程", "章节", "教材", "页面", "source", "chunk", "synthetic", "chapter", "course"}
    for window in source.windows:
        for item in re.findall(r"[\u4e00-\u9fff]{3,10}", _window_text(window)):
            item = re.sub(r"\s+", " ", item).strip(" -_：:")
            if item.lower() in ignored or item in concepts:
                continue
            concepts.append(item)
            if len(concepts) >= 8:
                return concepts
    return concepts[:8]


class TemplateLessonAdapter:
    name = "template"

    def build_lesson(self, source: ChapterSourcePackage) -> Lesson:
        lesson_id = f"lesson_{source.chapter_id}"
        concepts = _concepts_from_source(source)
        windows = source.windows
        first_windows = windows[:3]
        overview = (
            f"本课基于第 {source.page_start}-{source.page_end} 页的 {source.chunk_count} 个可靠片段整理。"
            f"教材核心表述：{_source_focus(source)}"
        )
        source_digest = "\n".join(
            f"{idx + 1}. 第 {window.page_start}-{window.page_end} 页：{_compact_text(_window_text(window))}"
            for idx, window in enumerate(first_windows)
        )
        if not source_digest:
            source_digest = "当前章节没有足够可靠的正文片段，建议先复核 OCR 或章节页码。"

        blocks = [
            _block(
                lesson_id,
                1,
                "overview",
                "学习导入",
                overview,
                first_windows[:1],
            ),
            _block(
                lesson_id,
                2,
                "explanation",
                "全文依据讲解",
                f"把本章内容拆成几个学习窗口后，可以先抓住这些教材证据：\n{source_digest}",
                first_windows,
            ),
            _block(
                lesson_id,
                3,
                "misconception",
                "易错点提醒",
                "如果某个概念只在局部页面出现，复习时要回到引用页核对上下文，避免只背片段而忽略条件、对象和过程顺序。",
                first_windows[:1],
            ),
            _block(
                lesson_id,
                4,
                "practice",
                "本节练习",
                f"请用自己的话说明“{concepts[0] if concepts else source.chapter_title}”的核心含义，并指出答案依据来自哪一页。",
                first_windows[:1],
            ),
        ]
        if source.warnings:
            blocks.append(
                LessonBlock(
                    block_id=f"{lesson_id}_b005",
                    block_type="warning",
                    title="内容质量提示",
                    content="；".join(source.warnings),
                    ai_generated=False,
                )
            )

        return Lesson(
            book_id=source.book_id,
            lesson_id=lesson_id,
            chapter_id=source.chapter_id,
            title=_course_title(source.chapter_title),
            source_title=source.chapter_title,
            page_start=source.page_start,
            page_end=source.page_end,
            status="needs_review" if source.warnings else "ready",
            confidence=_lesson_confidence(source),
            objectives=[
                "梳理本章主线并形成可复述的知识框架",
                "用页码证据核对关键概念",
                "通过练习发现需要回看原文的薄弱点",
            ],
            key_concepts=concepts,
            summary=overview,
            blocks=blocks,
            source_chunk_ids=source.source_chunk_ids,
            warnings=source.warnings,
        )


def build_grounded_lesson_prompt(source: ChapterSourcePackage, *, max_input_chars: int | None = None) -> str:
    prefix = (
        "You are generating a structured study lesson from textbook OCR/text chunks. "
        "Use only the provided windows. Do not copy long passages. "
        "Every lesson block must cite source_chunk_ids and page ranges. "
        "Return strict JSON matching this shape: "
        "{title, summary, objectives, key_concepts, blocks:[{block_type,title,content,source_chunk_ids,citations:[{chunk_id,page_start,page_end,quote}],asset_ids,ai_generated}]}. "
        f"Book: {source.book_id}\n"
        f"Chapter: {source.chapter_id} {source.chapter_title}\n"
        f"Pages: {source.page_start}-{source.page_end}\n"
        f"Warnings: {source.warnings}\n"
        "Source windows:\n"
    )
    remaining = max_input_chars - len(prefix) if max_input_chars is not None else None
    rendered_windows: list[str] = []
    for window in source.windows:
        rendered = (
            f"[{window.window_id}] pages={window.page_start}-{window.page_end} "
            f"chunks={','.join(window.source_chunk_ids)}\n{_window_text(window)}"
        )
        separator_cost = 2 if rendered_windows else 0
        if remaining is not None and remaining <= separator_cost:
            break
        if remaining is not None and len(rendered) + separator_cost > remaining:
            available = max(0, remaining - separator_cost)
            marker = "\n[window truncated]"
            rendered = (
                rendered[: available - len(marker)].rstrip() + marker
                if available > len(marker)
                else rendered[:available]
            )
        rendered_windows.append(rendered)
        if remaining is not None:
            remaining -= len(rendered) + separator_cost
            if remaining <= 0:
                break
    prompt = prefix + "\n\n".join(rendered_windows)
    return prompt[:max_input_chars] if max_input_chars is not None else prompt


class OpenAICompatibleLessonAdapter:
    name = "openai_compatible"

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60,
        extra_payload: dict[str, Any] | None = None,
        runtime: AIProviderRuntime | None = None,
    ) -> None:
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.extra_payload = extra_payload or {}
        self.runtime = runtime or AIProviderRuntime(self.name, policy_from_settings())

    def build_lesson(self, source: ChapterSourcePackage) -> Lesson:
        settings = get_settings()
        prompt = build_grounded_lesson_prompt(source, max_input_chars=settings.llm_max_input_chars)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Return only valid JSON. Keep all content grounded in the source windows."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            # OpenAI-compatible relays do not all share the same default.  Some
            # return an SSE stream unless this is explicit, while the shared
            # runtime intentionally accepts one bounded JSON document.
            "stream": False,
            "max_tokens": settings.llm_max_output_tokens,
        }
        payload.update(self.extra_payload)
        body = self.runtime.post_json(
            api_url=self.api_url,
            api_key=self.api_key,
            payload=payload,
            timeout_seconds=self.timeout_seconds,
            error_prefix="lesson_llm",
        )
        return self._lesson_from_response(source, body)

    def _lesson_from_response(self, source: ChapterSourcePackage, body: dict[str, Any]) -> Lesson:
        content = body.get("choices", [{}])[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise AppError("lesson_llm_empty_response", "课程生成大模型返回为空", status_code=502)
        try:
            generated = _decode_provider_json_object(content)
        except (json.JSONDecodeError, ValueError) as exc:
            raise AppError("lesson_llm_invalid_json", "课程生成大模型没有返回合法 JSON", status_code=502) from exc

        lesson_id = f"lesson_{source.chapter_id}"
        generated.setdefault("book_id", source.book_id)
        generated.setdefault("lesson_id", lesson_id)
        generated.setdefault("chapter_id", source.chapter_id)
        generated.setdefault("source_title", source.chapter_title)
        generated.setdefault("page_start", source.page_start)
        generated.setdefault("page_end", source.page_end)
        generated.setdefault("status", "needs_review" if source.warnings else "ready")
        generated.setdefault("confidence", _lesson_confidence(source))
        generated.setdefault("source_chunk_ids", source.source_chunk_ids)
        generated.setdefault("asset_ids", [])
        generated.setdefault("warnings", source.warnings)
        generated.setdefault("blocks", [])
        if not isinstance(generated["blocks"], list) or any(not isinstance(block, dict) for block in generated["blocks"]):
            raise AppError("lesson_llm_invalid_schema", "课程生成结果的 blocks 结构无效", status_code=502)
        for index, block in enumerate(generated["blocks"], start=1):
            block.setdefault("block_id", f"{lesson_id}_b{index:03d}")
            block.setdefault("citations", [])
            block.setdefault("source_chunk_ids", [])
            block.setdefault("asset_ids", [])
            block.setdefault("ai_generated", True)

        try:
            lesson = Lesson.model_validate(generated)
        except ValidationError as exc:
            issues = [{"type": item["type"], "loc": list(item["loc"])} for item in exc.errors(include_input=False)]
            raise AppError(
                "lesson_llm_invalid_schema",
                "课程生成结果不符合课程结构约束",
                details={"issues": issues[:20]},
                status_code=502,
            ) from exc
        if not lesson.blocks:
            raise AppError("lesson_llm_missing_blocks", "课程生成结果缺少内容块", status_code=502)
        allowed_chunk_ids = set(source.source_chunk_ids)
        invalid_references = sorted(
            {
                chunk_id
                for block in lesson.blocks
                for chunk_id in [*block.source_chunk_ids, *(citation.chunk_id for citation in block.citations)]
                if chunk_id not in allowed_chunk_ids
            }
        )
        invalid_pages = [
            (citation.page_start, citation.page_end)
            for block in lesson.blocks
            for citation in block.citations
            if citation.page_start < source.page_start or citation.page_end > source.page_end or citation.page_end < citation.page_start
        ]
        ungrounded_blocks = [
            block.block_id
            for block in lesson.blocks
            if block.ai_generated and (not block.source_chunk_ids or not block.citations)
        ]
        if invalid_references or invalid_pages or ungrounded_blocks:
            raise AppError(
                "lesson_llm_ungrounded_response",
                "课程生成结果包含无法由教材来源验证的内容",
                details={
                    "invalid_chunk_ids": invalid_references,
                    "invalid_page_ranges": invalid_pages,
                    "ungrounded_block_ids": ungrounded_blocks,
                },
                status_code=502,
            )
        return lesson.model_copy(
            update={
                "book_id": source.book_id,
                "lesson_id": lesson_id,
                "chapter_id": source.chapter_id,
                "source_title": source.chapter_title,
                "page_start": source.page_start,
                "page_end": source.page_end,
                "title": _normalize_generated_title(lesson.title),
                "confidence": min(lesson.confidence, _lesson_confidence(source)),
            }
        )


class DeepSeekLessonAdapter(OpenAICompatibleLessonAdapter):
    name = "deepseek"


def get_lesson_adapter() -> LessonGenerationAdapter:
    settings = get_settings()
    if settings.llm_provider == "openai_compatible":
        if not settings.llm_api_url or not settings.llm_api_key:
            raise AppError("lesson_llm_not_configured", "课程生成大模型 API 未配置", status_code=500)
        return OpenAICompatibleLessonAdapter(
            settings.llm_api_url,
            settings.llm_api_key,
            settings.lesson_llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            runtime=get_ai_runtime("openai_compatible"),
        )
    if settings.llm_provider == "deepseek":
        if not settings.deepseek_api_key:
            raise AppError("lesson_llm_not_configured", "DeepSeek API key is not configured", status_code=500)
        return DeepSeekLessonAdapter(
            settings.deepseek_api_url,
            settings.deepseek_api_key,
            settings.deepseek_lesson_model,
            timeout_seconds=settings.llm_timeout_seconds,
            extra_payload=deepseek_payload_extras(settings.deepseek_thinking),
            runtime=get_ai_runtime("deepseek"),
        )
    return TemplateLessonAdapter()
