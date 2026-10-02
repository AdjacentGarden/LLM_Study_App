"""Persistent account-scoped compatibility data for the directly ported UI.

This module stores source records, never fixture lessons. Native extraction is
explicitly distinguished from OCR and model generation.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import HTTPException


def now_ms() -> int:
    return int(time.time() * 1000)


def empty_course_state() -> dict[str, Any]:
    return {
        "version": 2,
        "preferences": None,
        "onboardingDraft": {"displayName": "", "primaryGoal": None, "dailyTime": None, "step": 0},
        "courses": [],
        "resources": [],
        "draft": None,
        "activeCourseId": None,
        "dismissedSourceIds": [],
        "revision": 0,
    }


class DemoPortStore:
    def __init__(self, data_dir: Path, *, recover_interrupted: bool = False):
        self.data_dir = data_dir
        self.path = data_dir / "state" / "demo_port.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS demo_documents(owner TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(owner,kind,id));
            CREATE TABLE IF NOT EXISTS demo_credit_accounts(owner TEXT PRIMARY KEY,balance INTEGER NOT NULL DEFAULT 100);
            CREATE TABLE IF NOT EXISTS demo_credits(owner TEXT NOT NULL,id TEXT NOT NULL,action TEXT NOT NULL,amount INTEGER NOT NULL,status TEXT NOT NULL,created INTEGER NOT NULL,task_id TEXT,PRIMARY KEY(owner,id));
            """)
            if recover_interrupted:
                rows = db.execute(
                    "SELECT owner,id,payload FROM demo_documents WHERE kind='job'"
                ).fetchall()
                for row in rows:
                    value = json.loads(row["payload"])
                    if value.get("status") in {"pending", "processing"} and not value.get(
                        "external_ocr"
                    ):
                        value.update(
                            status="failed",
                            stage="interrupted",
                            error="服务重启中断了任务，请重试；原文件和记录已保存",
                        )
                        db.execute(
                            "UPDATE demo_documents SET payload=?,revision=revision+1 WHERE owner=? AND kind='job' AND id=?",
                            (json.dumps(value, ensure_ascii=False), row["owner"], row["id"]),
                        )
                abandoned = db.execute(
                    "SELECT owner,SUM(amount) amount FROM demo_credits WHERE status='reserved' AND task_id IS NULL GROUP BY owner"
                ).fetchall()
                for row in abandoned:
                    db.execute(
                        "UPDATE demo_credit_accounts SET balance=balance+? WHERE owner=?",
                        (row["amount"], row["owner"]),
                    )
                db.execute(
                    "UPDATE demo_credits SET status='refunded' WHERE status='reserved' AND task_id IS NULL"
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, owner: str, kind: str, identity: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload,revision FROM demo_documents WHERE owner=? AND kind=? AND id=?",
                (owner, kind, identity),
            ).fetchone()
        return {**json.loads(row[0]), "revision": row[1]} if row else None

    def put(
        self,
        owner: str,
        kind: str,
        identity: str,
        payload: dict[str, Any],
        expected: int | None = None,
    ) -> dict[str, Any]:
        raw = json.dumps({k: v for k, v in payload.items() if k != "revision"}, ensure_ascii=False)
        if len(raw.encode()) > 8 * 1024 * 1024:
            raise HTTPException(413, "记录过大")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision FROM demo_documents WHERE owner=? AND kind=? AND id=?",
                (owner, kind, identity),
            ).fetchone()
            revision = row[0] if row else 0
            if expected is not None and expected != revision:
                raise HTTPException(409, "记录已更新，请刷新后重试")
            db.execute(
                "INSERT INTO demo_documents VALUES(?,?,?,?,?) ON CONFLICT(owner,kind,id) DO UPDATE SET payload=excluded.payload,revision=excluded.revision",
                (owner, kind, identity, raw, revision + 1),
            )
        return {**payload, "revision": revision + 1}

    def list(self, owner: str, kind: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload,revision FROM demo_documents WHERE owner=? AND kind=? ORDER BY rowid",
                (owner, kind),
            ).fetchall()
        return [{**json.loads(row[0]), "revision": row[1]} for row in rows]

    def delete(self, owner: str, kind: str, identity: str) -> None:
        with self.connect() as db:
            db.execute(
                "DELETE FROM demo_documents WHERE owner=? AND kind=? AND id=?",
                (owner, kind, identity),
            )

    def credit_state(self, owner: str) -> dict[str, Any]:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO demo_credit_accounts VALUES(?,100)", (owner,))
            balance = db.execute(
                "SELECT balance FROM demo_credit_accounts WHERE owner=?", (owner,)
            ).fetchone()[0]
            rows = db.execute(
                "SELECT * FROM demo_credits WHERE owner=? AND status IN ('reserved','spent') ORDER BY created",
                (owner,),
            ).fetchall()
        return {
            "version": 1,
            "balance": balance,
            "transactions": [
                {
                    "id": row["id"],
                    "action": row["action"],
                    "amount": row["amount"],
                    "createdAt": row["created"],
                    "status": row["status"],
                }
                for row in rows
            ],
        }

    def credit(
        self,
        owner: str,
        operation: str,
        identity: str,
        action: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        if not identity or len(identity) > 128:
            raise HTTPException(422, "需要幂等操作 ID")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO demo_credit_accounts VALUES(?,100)", (owner,))
            row = db.execute(
                "SELECT * FROM demo_credits WHERE owner=? AND id=?", (owner, identity)
            ).fetchone()
            if operation == "reserve":
                costs = {"chat": 1, "video": 10}
                if action not in costs:
                    raise HTTPException(422, "不支持的积分操作")
                if row and row["action"] != action:
                    raise HTTPException(409, "操作 ID 已用于其他任务")
                if not row:
                    changed = db.execute(
                        "UPDATE demo_credit_accounts SET balance=balance-? WHERE owner=? AND balance>=?",
                        (costs[action], owner, costs[action]),
                    ).rowcount
                    if not changed:
                        raise HTTPException(409, "积分不足")
                    db.execute(
                        "INSERT INTO demo_credits VALUES(?,?,?,?,?,?,?)",
                        (owner, identity, action, costs[action], "reserved", now_ms(), None),
                    )
            elif row and operation == "refund" and row["status"] == "reserved":
                db.execute(
                    "UPDATE demo_credit_accounts SET balance=balance+? WHERE owner=?",
                    (row["amount"], owner),
                )
                db.execute(
                    "UPDATE demo_credits SET status='refunded' WHERE owner=? AND id=?",
                    (owner, identity),
                )
            elif operation == "complete":
                # A browser cannot certify model/media completion. The real task
                # handler must call settle_credit after storing its result.
                if not row or row["status"] != "spent":
                    raise HTTPException(409, "关联任务尚未完成，不能确认扣费")
            elif operation != "refund":
                raise HTTPException(422, "不支持的积分操作")
        return {**self.credit_state(owner), "reservationId": identity}

    def settle_credit(self, owner: str, identity: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE demo_credits SET status='spent' WHERE owner=? AND id=? AND status='reserved'",
                (owner, identity),
            )


def extract_source(path: Path) -> tuple[list[str], str]:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".csv"}:
        content = path.read_bytes()
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("gb18030")
        return [text], "document"
    if suffix == ".pdf":
        import fitz

        with fitz.open(path) as doc:
            pages = [page.get_text() for page in doc]
        if not any(p.strip() for p in pages):
            raise HTTPException(503, "扫描 PDF 需要 OCR，请使用已配置的文档重建服务")
        return pages, "page"
    if suffix in {".docx", ".pptx", ".xlsx"}:
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist()) > 100 * 1024 * 1024:
                raise HTTPException(413, "解压后的文档过大")
            if suffix == ".docx":
                root = ET.fromstring(archive.read("word/document.xml"))
                paragraphs = ["".join(p.itertext()) for p in root.iter() if p.tag.endswith("}p")]
                return ["\n".join(paragraphs)], "document"
            if suffix == ".pptx":
                names = sorted(
                    (n for n in archive.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                    key=lambda n: int(re.search(r"slide(\d+)", n)[1]),
                )
                return [
                    "\n".join(
                        el.text or ""
                        for el in ET.fromstring(archive.read(n)).iter()
                        if el.tag.endswith("}t")
                    )
                    for n in names
                ], "slide"
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared = [
                    "".join(el.itertext())
                    for el in ET.fromstring(archive.read("xl/sharedStrings.xml"))
                    if el.tag.endswith("}si")
                ]
            names = sorted(
                (n for n in archive.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
                key=lambda n: int(re.search(r"sheet(\d+)", n)[1]),
            )
            pages = []
            for name in names:
                lines = []
                for row in ET.fromstring(archive.read(name)).iter():
                    if not row.tag.endswith("}row"):
                        continue
                    cells = []
                    for cell in row:
                        value = next(
                            (el.text or "" for el in cell.iter() if el.tag.endswith("}v")), ""
                        )
                        if cell.attrib.get("t") == "s" and value.isdigit():
                            value = shared[int(value)]
                        elif cell.attrib.get("t") == "inlineStr":
                            value = "".join(
                                el.text or "" for el in cell.iter() if el.tag.endswith("}t")
                            )
                        cells.append(value)
                    lines.append("\t".join(cells))
                pages.append("\n".join(lines))
            return pages, "sheet"
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}:
        raise HTTPException(503, "图片资料需要已配置的 OCR 服务，原文件已保存")
    raise HTTPException(415, "暂不支持此文档格式")


def source_content(book_id: str, filename: str, pages: list[str], unit: str) -> dict[str, Any]:
    chapters = []
    chunks = []
    for index, text in enumerate(pages, 1):
        chapter_id = f"{book_id}:chapter:{index}"
        title = next(
            (line.strip().lstrip("# ")[:120] for line in text.splitlines() if line.strip()),
            f"{unit} {index}",
        )
        chapters.append(
            {
                "chapter_id": chapter_id,
                "level": 1,
                "source_title": title,
                "ai_title": title,
                "page_start": index,
                "page_end": index,
                "printed_page_start": None,
                "printed_page_end": None,
                "confidence": 0,
                "status": "needs_review",
                "source": "native_source_unit",
                "parent_id": None,
            }
        )
        for offset in range(0, len(text), 3000):
            chunks.append(
                {
                    "chunk_id": f"{book_id}:chunk:{index}:{offset}",
                    "book_id": book_id,
                    "chapter_id": chapter_id,
                    "page_start": index,
                    "page_end": index,
                    "content_type": "text",
                    "text": text[offset : offset + 3000],
                    "asset_ids": [],
                    "key_concepts": [],
                    "source_metadata": {
                        "location_type": unit,
                        "location_label": f"{unit} {index}",
                        "extractor": "native",
                    },
                }
            )
    return {
        "book_id": book_id,
        "filename": filename,
        "pages": pages,
        "unit": unit,
        "chapters": chapters,
        "chunks": chunks,
        "status": "needs_review",
        "updated_at": now_ms(),
        "content_version": 1,
    }


def source_lessons(source: dict[str, Any]) -> list[dict[str, Any]]:
    lessons = []
    for chapter in source.get("chapters", []):
        chunks = [
            c
            for c in source.get("chunks", [])
            if chapter["page_start"] <= c["page_start"] <= chapter["page_end"]
        ]
        lessons.append(
            {
                "book_id": source["book_id"],
                "lesson_id": chapter["chapter_id"] + ":lesson",
                "chapter_id": chapter["chapter_id"],
                "title": chapter["ai_title"],
                "source_title": chapter["source_title"],
                "page_start": chapter["page_start"],
                "page_end": chapter["page_end"],
                "lesson_kind": "lesson",
                "status": "ready",
                "confidence": 0,
                "objectives": [],
                "key_concepts": [],
                "summary": "原文基础课时",
                "blocks": [
                    {
                        "block_id": c["chunk_id"],
                        "block_type": "source_text",
                        "title": "原文",
                        "content": c["text"],
                        "citations": [
                            {
                                "chunk_id": c["chunk_id"],
                                "page_start": c["page_start"],
                                "page_end": c["page_end"],
                                "quote": c["text"][:300],
                                "source_metadata": c.get("source_metadata", {}),
                            }
                        ],
                        "source_chunk_ids": [c["chunk_id"]],
                        "asset_ids": [],
                        "ai_generated": False,
                    }
                    for c in chunks
                ],
                "source_chunk_ids": [c["chunk_id"] for c in chunks],
                "asset_ids": [],
                "warnings": ["基础课时直接展示真实原文；尚未生成个性化讲解"],
            }
        )
    return lessons


def write_export_pdf(path: Path, sections: list[str], ink_notes: list[dict[str, Any]]) -> None:
    """Lay out real content continuously, with measured CJK/Latin wrapping."""
    import os

    import fitz

    candidates = [
        os.getenv("EXPORT_PDF_FONT", ""),
        "C:/Windows/Fonts/msyh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    font_path = next((p for p in candidates if p and Path(p).is_file()), None)
    font = fitz.Font(fontfile=font_path) if font_path else fitz.Font("china-s")
    font_name = "cloudpath" if font_path else "china-s"
    doc = fitz.open()
    page = None
    y = 0.0

    def new_page() -> None:
        nonlocal page, y
        page = doc.new_page(width=595, height=842)
        if font_path:
            page.insert_font(fontname=font_name, fontfile=font_path)
        y = 48.0

    def wrap(text: str, size: float) -> list[str]:
        result, line = [], ""
        for token in re.findall(r"[A-Za-z0-9_:/.-]+\s*|[^A-Za-z0-9_:/.-]", text):
            if line and font.text_length(line + token, fontsize=size) > 499:
                result.append(line.rstrip())
                line = ""
            if font.text_length(token, fontsize=size) > 499:
                for char in token:
                    if line and font.text_length(line + char, fontsize=size) > 499:
                        result.append(line.rstrip())
                        line = ""
                    line += char
            else:
                line += token
        result.append(line.rstrip())
        return result

    for section_index, section in enumerate(sections):
        lines = section.splitlines() or [""]
        if page is None or y > 738:
            new_page()
        if section_index:
            y += 14
        for paragraph_index, paragraph in enumerate(lines):
            size = 17 if section_index == 0 else 12 if paragraph_index == 0 else 10
            spacing = size * 1.6
            wrapped = wrap(paragraph, size)
            if paragraph_index == 0 and y + spacing * min(3, len(wrapped) + 1) > 786:
                new_page()
            for line in wrapped:
                if y + spacing > 786:
                    new_page()
                page.insert_text(
                    (48, y), line, fontsize=size, fontname=font_name, color=(0.13, 0.18, 0.25)
                )
                y += spacing
            y += 4
    for note in ink_notes:
        for label, strokes in note.get("pages", {}).items():
            new_page()
            page.insert_text((48, y), f"{note['title']} / {label}", fontsize=12, fontname=font_name)
            y += 25
            points = [p for s in strokes for p in s.get("points", [])]
            if not points:
                continue
            max_x = max(1, max(float(p["x"]) for p in points))
            max_y = max(1, max(float(p["y"]) for p in points))
            scale = min(499 / max_x, 680 / max_y)
            for stroke in strokes:
                path_points = [
                    fitz.Point(48 + float(p["x"]) * scale, y + float(p["y"]) * scale)
                    for p in stroke.get("points", [])
                ]
                if len(path_points) < 2:
                    continue
                raw_color = str(stroke.get("color", "#243148")).lstrip("#")
                try:
                    color = tuple(int(raw_color[i : i + 2], 16) / 255 for i in (0, 2, 4))
                except ValueError:
                    color = (0.14, 0.19, 0.28)
                shape = page.new_shape()
                shape.draw_polyline(path_points)
                shape.finish(
                    color=color,
                    width=max(0.5, float(stroke.get("width", 2)) * scale),
                    stroke_opacity=max(0, min(1, float(stroke.get("opacity", 1)))),
                )
                shape.commit()
    if not doc.page_count:
        new_page()
    for index, output in enumerate(doc, 1):
        output.insert_text(
            (48, 814),
            f"CloudPath | {index} / {doc.page_count}",
            fontsize=8,
            fontname="helv",
            color=(0.45, 0.49, 0.53),
        )
    try:
        doc.subset_fonts()
    except (ImportError, RuntimeError):
        # Font subsetting is an optimization; an unavailable optional font
        # utility does not invalidate an otherwise complete document.
        pass
    doc.save(path, garbage=4, deflate=True)
    doc.close()
