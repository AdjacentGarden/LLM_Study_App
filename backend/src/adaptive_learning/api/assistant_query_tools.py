"""Read-only facts for the single conversational assistant.

These tools do not classify a question or call a text generation service.  The
calling model chooses which facts it needs; ownership is checked independently
of the page from which the conversation was opened.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import native_catalog
from ..ingestion.coverage import page_is_skipped
from ..ingestion.models import BookStructure
from ..rag.index import BM25
from ..rag.upload_index import split_text

_ID = {"type": "string", "minLength": 1, "maxLength": 150}
_OFFSET = {"type": "integer", "minimum": 0, "maximum": 10000}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 15}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [],
            "additionalProperties": False}


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "list_learning_resources": _schema({"offset": _OFFSET, "limit": _LIMIT}),
    "get_course_outline": _schema({
        "course_id": _ID, "book_id": _ID, "chapter_id": _ID,
        "offset": _OFFSET, "limit": _LIMIT, "resource_offset": _OFFSET,
    }),
    "search_learning_materials": _schema({
        "query": {"type": "string", "minLength": 2, "maxLength": 500},
        "book_ids": {"type": "array", "items": _ID, "maxItems": 20,
                     "uniqueItems": True},
        "chapter_id": _ID,
        "limit": {"type": "integer", "minimum": 1, "maximum": 12},
    }, ["query"]),
    "read_textbook_page": _schema({
        "book_id": _ID, "page_number": {"type": "integer", "minimum": 1, "maximum": 100000},
        "offset": _OFFSET,
    }, ["book_id", "page_number"]),
    "list_public_courses": _schema({
        "query": {"type": "string", "maxLength": 100},
        "page": {"type": "integer", "minimum": 0, "maximum": 100},
    }),
}

TOOL_DESCRIPTIONS = {
    "list_learning_resources": (
        "读取当前登录用户的所有课程、教材和已同步学习进度。用户问‘当前有哪些课程’、"
        "‘我在学什么’、整体学习情况时使用；结果不限于当前页面。支持 offset/limit 分页，"
        "本机待上传课程会单独标记，默认页码不代表已经学习。"
    ),
    "get_course_outline": (
        "读取用户有权访问的课程/教材目录、章节概要与学习进度。course_id 和 book_id 来自"
        "list_learning_resources；可传 chapter_id 读取某节。省略 ID 时仅将当前页面作为默认提示，"
        "并非固定范围。一个课程多本书时用 resource_offset 逐本读取，offset/limit 对该书目录分页。"
        "目录概要属于已生成内容，并非教材原文引文。"
    ),
    "search_learning_materials": (
        "跨当前用户的教材检索原始 OCR 文本，不生成答案、不调用另一模型。省略 book_ids "
        "则检索全部可访问教材（最多 20 本，超出会明示）；指定其他书 ID 可跨课程检索。"
        "query 应是要查的具体知识点，可自行换关键词再次查询。结果 evidence 为可引用原文，"
        "引用时保留 [evidence_id]。不包含未授权教材、私人笔记或隐式上传的文件。"
    ),
    "read_textbook_page": (
        "读取用户教材的指定 PDF 页原始 OCR 原文，每次最多 6000 字，可用 offset 继续。"
        "当搜索证据不足、需要页内完整上下文时使用；不是联网搜索，也不调用生成模型。"
    ),
    "list_public_courses": (
        "读取‘发现’中当前公开分享的教材/课程目录，仅公开元数据。与用户自己的课程和个人进度"
        "不同，不能将公开书库当成用户已学习课程；不会自动下载、加入或发布任何教材。"
    ),
}


@dataclass(frozen=True)
class _Chunk:
    page: int
    text: str
    block_id: str | None = None


def _chunks(raw: str) -> tuple[_Chunk, ...]:
    chunks: list[_Chunk] = []
    seen: set[int] = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        page = json.loads(line)
        number = int(page["page_number"])
        if number < 1 or number in seen:
            raise ValueError("invalid page sequence")
        seen.add(number)
        if page_is_skipped(page):
            continue
        blocks = [b for b in page.get("blocks", []) if isinstance(b, dict)]
        text = next((page[k] for k in ("cleaned_text", "raw_text", "text")
                     if isinstance(page.get(k), str) and page[k].strip()), "")
        if not text:
            text = "\n".join(str(b.get("text") or "") for b in blocks)
        for piece in split_text(text, 900):
            owners = [b for b in blocks if piece in str(b.get("text") or "")]
            block_id = str(owners[0]["block_id"]) if len(owners) == 1 and owners[0].get("block_id") else None
            chunks.append(_Chunk(number, piece, block_id))
        if len(chunks) > 30000:
            raise ValueError("textbook exceeds read budget")
    return tuple(chunks)


@lru_cache(maxsize=4)
def _file_chunks(path: str, mtime_ns: int, size: int) -> tuple[_Chunk, ...]:
    # Fingerprinted parsed text is shared in memory, but never bypasses actor ACL.
    if size > 64 * 1024 * 1024:
        raise ValueError("textbook exceeds read budget")
    return _chunks(Path(path).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _builtin_chunks() -> tuple[_Chunk, ...]:
    return _chunks(native_catalog.qa_pages().decode("utf-8"))


class QueryTools:
    schemas = TOOL_SCHEMAS
    descriptions = TOOL_DESCRIPTIONS

    def __init__(self, services: Any, actor: str, context: dict[str, Any] | None = None,
                 local_courses: list[dict[str, Any]] | None = None) -> None:
        self.services, self.actor = services, actor
        self.context = context or {}
        self.local_courses = local_courses or []

    def _owned(self, book_id: str) -> bool:
        repo = self.services.account_repository.repo
        return bool(repo.owns_upload(self.actor, book_id) or repo.owns(self.actor, book_id))

    def _workspaces(self) -> list[dict[str, Any]]:
        return self.services.native_repository.workspaces(self.actor)

    def _resources(self) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for workspace in self._workspaces():
            try:
                resources = self.services.native_repository.resources(self.actor, workspace["id"])
            except KeyError:
                continue  # A concurrent deletion must not turn into an unscoped read.
            for resource in resources:
                if self._owned(str(resource["book_id"])):
                    values.append({**resource, "course_id": workspace["id"],
                                   "course_name": workspace["name"]})
        return values

    def access_scope(self) -> list[str]:
        """Fresh ACL snapshot for replay checks; client labels never grant access."""
        public = hashlib.sha256()
        with self.services.account_repository.repo.connect() as db:
            for row in db.execute("SELECT id FROM posts WHERE active=1 AND kind='book' ORDER BY id"):
                public.update(str(row[0]).encode())
                public.update(b"\0")
        return sorted({*("workspace:" + str(w["id"]) for w in self._workspaces()),
                       *("book:" + str(r["book_id"]) for r in self._resources()),
                       "public_catalog_metadata:" + public.hexdigest()})

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return {"ok": False, "error": code, "message": message, "evidence": []}

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self.schemas or not isinstance(arguments, dict):
            return self._error("invalid_tool", "不支持的只读工具")
        if set(arguments) - set(self.schemas[name]["properties"]):
            return self._error("invalid_arguments", "工具参数包含未知字段")
        try:
            # Also validate direct callers; the model stream performs the same validation.
            from ..llm.tool_stream import validate_arguments
            validate_arguments(self.schemas[name], arguments)
            return getattr(self, "_" + name)(**arguments)
        except (KeyError, TypeError, ValueError):
            return self._error("resource_unavailable", "参数无效，或该资料当前不可访问；可先列出用户课程")
        except OSError:
            return self._error("source_unavailable", "教材原文暂不可读取，请稍后重试")

    def _structure(self, resource: dict[str, Any]) -> BookStructure | None:
        if resource.get("structure_json"):
            return BookStructure.model_validate_json(resource["structure_json"])
        if resource["book_id"] == native_catalog.BOOK_ID:
            return native_catalog.structure()
        value = self.services.job_repository.get_structure(resource["book_id"])
        return value if isinstance(value, BookStructure) else None

    def _book_info(self, resource: dict[str, Any]) -> dict[str, Any]:
        structure = self._structure(resource)
        stored = self.services.job_repository.get_book(resource["book_id"])
        progress = self.services.native_repository.progress(resource["id"])
        return {
            "book_id": resource["book_id"], "resource_id": resource["id"],
            "title": structure.title if structure else stored.original_name if stored else "教材",
            "status": "ready" if resource["book_id"] == native_catalog.BOOK_ID else stored.status if stored else "missing",
            "outline_entry_count": len(structure.chapters) if structure else None,
            "outline_entry_note": "目录条目包括章、节和子主题，条目数不等于章数",
            "page_count": structure.source_page_count if structure else None,
            "progress": {**{key: progress.get(key) for key in (
                "revision", "page", "chapter_id", "lesson_id")},
                "completed_chapter_count": len(progress.get("completed_chapters", [])),
                "completed_chapters": progress.get("completed_chapters", [])[:30]},
        }

    def _listed_book(self, resource: dict[str, Any]) -> dict[str, Any]:
        info = self._book_info(resource)
        info["title"] = info["title"][:120]
        info["progress"].pop("completed_chapters", None)
        return info

    def _list_learning_resources(self, offset: int = 0, limit: int = 15) -> dict[str, Any]:
        workspaces = self._workspaces()
        resources = self._resources()
        local = [row for row in self.local_courses[:100]
                 if isinstance(row, dict) and str(row.get("id", "")).startswith("local-course:")]
        total = len(workspaces) + len(local)
        items: list[dict[str, Any]] = []
        for workspace in workspaces[offset:offset + limit]:
            books = [r for r in resources if r["course_id"] == workspace["id"]]
            items.append({"course_id": workspace["id"], "name": str(workspace["name"])[:120],
                          "origin": "account", "book_count": len(books),
                          "books": [self._listed_book(r) for r in books[:3]],
                          "books_truncated": len(books) > 3})
        start = max(0, offset - len(workspaces))
        remaining = limit - len(items)
        if remaining and offset + limit > len(workspaces):
            items.extend({"course_id": str(row["id"])[:150],
                          "name": str(row.get("name") or "待上传课程")[:120],
                          "origin": "device_unverified", "status": "pending_local",
                          "book_count": min(len(row.get("book_ids", [])), 100),
                          "progress": None}
                         for row in local[start:start + remaining])
        return {"ok": True, "scope": "current_account_and_device_pending_metadata",
                "courses": items, "total_courses": total, "account_course_count": len(workspaces),
                "device_pending_course_count": len(local), "offset": offset,
                "has_more": offset + len(items) < total,
                "note": "设备待上传课程只是客户端提供的名称，不能用于读取服务器教材。课程存在不代表所有课程都在进行中；progress.revision=0 时页码可能只是默认位置。",
                "evidence": []}

    def _get_course_outline(self, course_id: str | None = None, book_id: str | None = None,
                            chapter_id: str | None = None, offset: int = 0,
                            limit: int = 15, resource_offset: int = 0) -> dict[str, Any]:
        if not course_id and not book_id:
            book_id = self.context.get("book_id")
            if not book_id:
                course_id = self.context.get("course_id")
        if not course_id and not book_id:
            return self._error("selection_needed", "尚无当前教材；请先列出课程，再选择 course_id 或 book_id")
        resources = [r for r in self._resources()
                     if (not course_id or r["course_id"] == course_id)
                     and (not book_id or r["book_id"] == book_id)]
        if not resources:
            raise KeyError("resource")
        books = []
        for resource in resources[resource_offset:resource_offset + 1]:
            info = self._book_info(resource)
            structure = self._structure(resource)
            chapters = [c for c in structure.chapters if not chapter_id or c.chapter_id == chapter_id] if structure else []
            info["course_id"], info["course_name"] = resource["course_id"], resource["course_name"]
            info["summary"] = structure.summary[:600] if structure else None
            info["chapters"] = [{"chapter_id": c.chapter_id, "title": c.title[:250],
                                 "start_page": c.start_page, "end_page": c.end_page,
                                 "summary": c.summary[:450],
                                 "knowledge_points": [p[:100] for p in c.knowledge_points[:8]]}
                                for c in chapters[offset:offset + limit]]
            info["has_more_chapters"] = offset + limit < len(chapters)
            books.append(info)
        return {"ok": True, "books": books,
                "resource_offset": resource_offset, "total_books": len(resources),
                "next_resource_offset": resource_offset + len(books),
                "books_truncated": resource_offset + len(books) < len(resources),
                "other_book_ids": [r["book_id"] for r in resources[1:31]],
                "content_kind": "stored_course_outline_and_progress", "evidence": []}

    def _book_chunks(self, book_id: str) -> tuple[_Chunk, ...]:
        # Called only after enumerating currently authorized server resources.
        if book_id == native_catalog.BOOK_ID:
            return _builtin_chunks()
        stored = self.services.job_repository.get_book(book_id)
        if stored is None:
            raise KeyError("book")
        path = stored.file_path.parent / "ocr" / "normalized" / "pages.jsonl"
        info = path.stat()
        return _file_chunks(str(path), info.st_mtime_ns, info.st_size)

    @staticmethod
    def _evidence(book_id: str, chunk: _Chunk, quote: str) -> dict[str, Any]:
        key = hashlib.sha256(f"{book_id}\0{chunk.page}\0{quote}".encode()).hexdigest()[:24]
        result: dict[str, Any] = {"evidence_id": "ev_" + key, "book_id": book_id,
                                  "page_number": chunk.page, "quote": quote}
        if chunk.block_id:
            result["block_id"] = chunk.block_id
        return result

    def _search_learning_materials(self, query: str, book_ids: list[str] | None = None,
                                   chapter_id: str | None = None, limit: int = 8) -> dict[str, Any]:
        resources = self._resources()
        by_book = {str(r["book_id"]): r for r in resources}
        if book_ids and any(book not in by_book for book in book_ids):
            raise KeyError("book")
        selected = list(book_ids) if book_ids else list(by_book)
        selected.sort(key=lambda value: value != self.context.get("book_id"))
        searched = selected[:20]
        candidates: list[tuple[str, _Chunk]] = []
        unavailable: list[str] = []
        for book_id in searched:
            resource = by_book[book_id]
            try:
                chapters = self._structure(resource)
                chapter = next((c for c in chapters.chapters if c.chapter_id == chapter_id), None) if chapters and chapter_id else None
                if chapter_id and chapter is None:
                    continue
                candidates.extend((book_id, chunk) for chunk in self._book_chunks(book_id)
                                  if chapter is None or chapter.start_page <= chunk.page <= chapter.end_page)
            except (OSError, ValueError, KeyError, TypeError):
                unavailable.append(book_id)
        scores = BM25(chunk.text for _, chunk in candidates).scores(query)
        ranking = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        evidence: list[dict[str, Any]] = []
        per_page: dict[tuple[str, int], int] = {}
        for index in ranking:
            if scores[index] <= 0 or len(evidence) >= limit:
                break
            book_id, chunk = candidates[index]
            location = (book_id, chunk.page)
            if per_page.get(location, 0) >= 2:
                continue
            if any(e["book_id"] == book_id and e["page_number"] == chunk.page
                   and e["quote"] in chunk.text for e in evidence):
                continue
            # Keep the complete bounded chunk so matching text cannot be cut off.
            evidence.append({**self._evidence(book_id, chunk, chunk.text),
                             "book_title": self._book_info(by_book[book_id])["title"]})
            per_page[location] = per_page.get(location, 0) + 1
        return {"ok": True, "method": "raw_ocr_bm25", "query": query,
                "searched_book_ids": searched, "total_accessible_books": len(by_book),
                "scope_truncated": len(selected) > 20, "unavailable_book_ids": unavailable,
                "evidence": evidence,
                "note": "仅返回原文检索片段。无命中不代表教材不存在相关知识；可换关键词或读取具体页面。"}

    def _read_textbook_page(self, book_id: str, page_number: int, offset: int = 0) -> dict[str, Any]:
        resources = {r["book_id"]: r for r in self._resources()}
        if book_id not in resources:
            raise KeyError("book")
        chunks = [chunk for chunk in self._book_chunks(book_id) if chunk.page == page_number]
        if not chunks:
            return self._error("page_unavailable", "该页没有可用 OCR 原文，可能尚未解析或已跳过")
        # Chunk overlap is intentionally retained; these remain exact source spans.
        span = 0
        evidence = []
        returned = 0
        for chunk in chunks:
            if span + len(chunk.text) <= offset:
                span += len(chunk.text)
                continue
            start = max(0, offset - span)
            quote = chunk.text[start:start + 6000 - returned]
            if quote:
                evidence.append(self._evidence(book_id, chunk, quote))
                returned += len(quote)
            span += len(chunk.text)
            if returned >= 6000:
                break
        total = sum(len(chunk.text) for chunk in chunks)
        return {"ok": True, "book_id": book_id, "page_number": page_number,
                "offset": offset, "next_offset": offset + returned,
                "has_more": offset + returned < total, "evidence": evidence}

    def _list_public_courses(self, query: str = "", page: int = 0) -> dict[str, Any]:
        repo = self.services.account_repository.repo
        offset = page * 20
        batch, start = divmod(offset, 50)
        result = repo.list_posts(self.actor, "book", query, batch)
        posts = result["items"][start:start + 20]
        has_more = result["has_more"] or start + 20 < len(result["items"])
        if len(posts) < 20 and result["has_more"]:
            following = repo.list_posts(self.actor, "book", query, batch + 1)
            needed = 20 - len(posts)
            posts += following["items"][:needed]
            has_more = following["has_more"] or len(following["items"]) > needed
        entries = []
        builtin = native_catalog.snapshot()["entry"]
        if page == 0 and (not query or query.casefold() in json.dumps(builtin, ensure_ascii=False).casefold()):
            entries.append({"id": native_catalog.CATALOG_ID, "title": builtin["title"],
                            "book_id": native_catalog.BOOK_ID,
                            "description": str(builtin.get("description", ""))[:500],
                            "origin": "public_curated", "personal_progress": None})
        for post in posts:
            entries.append({"id": "community_post_" + str(post["id"]),
                            "title": str(post["title"])[:150], "book_id": post["book_id"],
                            "description": str(post.get("description", ""))[:500],
                            "origin": "public_book_share", "personal_progress": None})
        return {"ok": True, "scope": "public_catalog_metadata", "courses": entries,
                "page": page, "has_more": has_more,
                "note": "公开目录不表示已加入用户课程；不含其他用户的个人学习记录。", "evidence": []}
