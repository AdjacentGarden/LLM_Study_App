"""Import the user's local Demo textbook, without importing learner history.

Run from backend with its venv. All destinations are explicit; an existing book
with a different PDF or importer provenance is rejected rather than overwritten.
Large licensed source files stay under ignored APP_DATA_DIR, outside Git.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adaptive_learning.assessment.item_generation import (
    stable_knowledge_point_id,
    structure_fingerprint,
)
from adaptive_learning.assessment.models import DiagnosticItem, ResponseType
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft, SourceQuote

BOOK_ID = "book_biology_2"
SOURCE_HASH = "d35ca6844f22e87bef5cd3deb286c7965f386ba13924a8195eddaefa41db533a"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def norm(value: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value)).casefold()


def normalize_pages(entries: list[dict]) -> list[dict]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for index, entry in enumerate(entries):
        page = int(entry["page_idx"]) + 1
        if not 1 <= page <= 125:
            raise ValueError("OCR contains an invalid PDF page")
        parts = [entry.get("text", "")]
        for key in ("image_caption", "table_caption", "table_body", "image_footnote", "table_footnote"):
            value = entry.get(key, "")
            parts.extend(value if isinstance(value, list) else [value])
        text = "\n".join(str(x) for x in parts if x).strip()
        if text:
            grouped[page].append({"block_id": f"block_{page}_{index}", "block_index": index,
                "page_number": page, "block_type": entry.get("type", "text"),
                "type": entry.get("type", "text"), "text": text,
                "source_method": "mineru_pipeline", "metadata": {"imported": True}})
    return [{"page_number": page, "raw_text": "\n".join(b["text"] for b in grouped[page]),
        "cleaned_text": "\n".join(b["text"] for b in grouped[page]), "blocks": grouped[page],
        "printed_page_number": str(page + 5) if page >= 10 else None} for page in range(1, 126)]


def verified_quote(value: dict, pages: list[dict], start: int, end: int) -> SourceQuote | None:
    quote = value.get("source_quote") or value.get("quote")
    page = value.get("page_start")
    if not isinstance(quote, str) or not quote.strip() or not isinstance(page, int):
        return None
    if not start <= page <= end or page < 1 or not norm(quote):
        return None
    last = value.get("page_end", page)
    if not isinstance(last, int) or not page <= last <= min(end, page + 10):
        return None
    for candidate in range(page, last + 1):
        if norm(quote) in norm(pages[candidate - 1]["raw_text"]):
            return SourceQuote(page_number=candidate, quote=quote[:500])
    return None


def build_structure(source: dict, pages: list[dict]) -> BookStructure:
    directory = [x for x in source["chapters"] if re.fullmatch(r"c\d+(?:s\d+)?", x["chapter_id"])]
    lessons = {x["chapter_id"]: x for x in source["lessons"]}
    output = []
    for order, row in enumerate(directory, 1):
        key, start, end = row["chapter_id"], row["page_start"], row["page_end"]
        points: dict[str, list[SourceQuote]] = {}
        lesson = lessons.get(key, {})
        if start > 0:
            for item in source["flashcards"] + source["quiz"]:
                if item["chapter_id"] != key:
                    continue
                quote = verified_quote(item, pages, start, end)
                if quote and item.get("concept"):
                    points.setdefault(item["concept"], []).append(quote)
            for block in lesson.get("blocks", []):
                quotes = [q for c in block.get("citations", []) if (q := verified_quote(c, pages, start, end))]
                if quotes:
                    points.setdefault(block["title"], []).extend(quotes)
            if not points:
                # Source-only sections still get grounded reading, not invented
                # Demo lessons. Every fallback statement is a literal OCR quote.
                for page in pages[start - 1:end]:
                    for block in page["blocks"]:
                        if block["type"] == "text" and 65 <= len(block["text"]) <= 1500:
                            excerpt = block["text"][:400]
                            points[excerpt[:100]] = [SourceQuote(page_number=page["page_number"], quote=excerpt)]
                            if len(points) >= 8:
                                break
                    if len(points) >= 8:
                        break
        points = {k: list({(q.page_number, q.quote): q for q in v}.values()) for k, v in points.items()}
        evidence = list({(q.page_number, q.quote): q for v in points.values() for q in v}.values())
        output.append(ChapterDraft(chapter_id=key, order=order,
            title=row["source_title"],
            start_page=start, end_page=end, parent_id=row["parent_id"], level=row["level"],
            has_supplementary_content=True if start == 0 and lesson else None,
            summary=lesson.get("summary") or ("源 PDF 不含本章正文，Demo AI 补充内容另行保留，不能当作原文证据。" if start == 0 else row["source_title"]),
            knowledge_points=list(points), evidence=evidence, knowledge_point_evidence=points,
            source_block_ids=[b["block_id"] for p in pages[max(0, start - 1):end] for b in p["blocks"]] if start else []))
    # Keep the exact source chapter/section IDs; aggregate genuine child
    # evidence for diagnosis, which operates at the root chapter level.
    for root in output:
        if root.parent_id:
            continue
        children = [x for x in output if x.parent_id == root.chapter_id]
        combined = {k: v for c in children for k, v in c.knowledge_point_evidence.items()}
        if combined:
            root.knowledge_point_evidence = combined
            root.knowledge_points = list(combined)
            root.evidence = list({(q.page_number, q.quote): q for v in combined.values() for q in v}.values())
    return BookStructure(title="人教版高中生物必修 2 · 遗传与进化", source_page_count=125,
        summary="从 Demo 导入的 125 页原教材：7 章、19 节。源 PDF 缺少第 1 章正文，第一章 AI 补充内容保留在导入档案；其余内容按原 PDF 页码阅读。", chapters=output)


def diagnostic_bank(source: dict, structure: BookStructure, pages: list[dict]) -> list[DiagnosticItem]:
    chapters = {c.chapter_id: c for c in structure.chapters}
    items = []
    for row in source["quiz"]:
        chapter = chapters[row["chapter_id"]]
        if not row.get("choices") or row.get("answer") not in row["choices"]:
            continue
        quote = verified_quote(row, pages, chapter.start_page, chapter.end_page)
        if quote is None:
            continue
        root = chapter.parent_id or chapter.chapter_id
        label = row.get("concept") or chapter.title
        items.append(DiagnosticItem(item_id="demo-diagnostic-" + row["question_id"], chapter_id=root,
            knowledge_point_ids=[stable_knowledge_point_id(root, label)], knowledge_point_labels=[label],
            prompt=row["prompt"], response_type=ResponseType.SINGLE_CHOICE, options=row["choices"],
            correct_option_ids=[str(row["choices"].index(row["answer"]))],
            expected_answer=row["answer"], rubric=[row.get("explanation", "")], source_pages=[quote.page_number],
            source_block_ids=chapter.source_block_ids, difficulty=0))
    return items


def import_book(demo: Path, pdf: Path, ocr: Path, data: Path) -> dict:
    if hashlib.sha256(pdf.read_bytes()).hexdigest() != SOURCE_HASH:
        raise ValueError("Source PDF does not match the Demo provenance SHA-256")
    import pymupdf
    with pymupdf.open(pdf) as document:
        if document.page_count != 125:
            raise ValueError("Expected the original 125-page PDF")
    raw = demo / "src/data/generated"
    source = {name: read(raw / (name + ".json")) for name in ("book", "chapters", "lessons", "flashcards", "quiz", "assets")}
    content_file = next(ocr.glob("*_content_list.json"))
    manifest = read(demo / "public/rag/biology-required-2-rag-v1/manifest.json")
    if hashlib.sha256(content_file.read_bytes()).hexdigest() != manifest["mineru"]["content_list_sha256"]:
        raise ValueError("OCR content list differs from the Demo RAG source")
    pages = normalize_pages(read(content_file))
    structure = build_structure(source, pages)
    bank = diagnostic_bank(source, structure, pages)
    if len(structure.chapters) != 26 or not bank:
        raise ValueError("Incomplete directory or diagnosis bank")
    book_dir = data / "books" / BOOK_ID
    imported = book_dir / "imported"
    jobs = SQLiteOCRJobRepository(data / "state/ocr_jobs.sqlite3")
    existing = jobs.get_book(BOOK_ID)
    if existing and (jobs.source_fingerprint(BOOK_ID) != SOURCE_HASH or not (imported / "import-report.json").is_file()):
        raise ValueError("Refusing to replace an existing book without matching import provenance")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = data / "import-backups" / stamp
    backup.mkdir(parents=True, exist_ok=True)
    for database in (data / "state").glob("*.sqlite3"):
        with sqlite3.connect(database) as current, sqlite3.connect(backup / database.name) as snapshot:
            current.backup(snapshot)
    imported.mkdir(parents=True, exist_ok=True)
    if not (book_dir / "source.pdf").exists():
        shutil.copy2(pdf, book_dir / "source.pdf")
    for name in source:
        shutil.copy2(raw / (name + ".json"), imported / ("source-" + name + ".json"))
    teaching = {"schema_version": 1, "source_book_id": BOOK_ID, "source_pdf_sha256": SOURCE_HASH,
        "lessons": source["lessons"], "flashcards": [{k: v for k, v in x.items() if k not in {"due", "mastery", "reason"}} for x in source["flashcards"]], "quiz": source["quiz"]}
    write(imported / "teaching.json", teaching)
    assets = {}
    for item in source["assets"]:
        for field in ("image_url", "thumbnail_url", "source_page_image_url"):
            url = item.get(field)
            if not url or not url.startswith("/assets/"):
                continue
            relative = Path(url.lstrip("/"))
            src = (demo / "public" / relative).resolve()
            if not src.is_relative_to((demo / "public/assets").resolve()) or not src.is_file():
                raise ValueError(f"Missing or unsafe source asset: {url}")
            target = imported / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(src, target)
        url = item.get("image_url", "")
        if url.startswith("/assets/"):
            assets[item["asset_id"]] = {"path": url.lstrip("/"), "kind": "video" if url.endswith(".mp4") else "image",
                "caption": item.get("caption", ""), "source_kind": item.get("source_type", "unknown"),
                "page_number": item.get("page") if isinstance(item.get("page"), int) and item["page"] > 0 else None}
    write(imported / "assets.json", assets)
    from PIL import Image
    cover = data / "covers" / (BOOK_ID + ".jpg")
    cover.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(demo / "public/assets/textbook/biology-cover.webp") as image:
        image.convert("RGB").save(cover, quality=92)
    normalized = book_dir / "ocr/normalized/pages.jsonl"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    normalized.write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in pages) + "\n", encoding="utf-8")
    if not existing:
        jobs.register_book(book_id=BOOK_ID, original_name=pdf.name, file_path=book_dir / "source.pdf", source_sha256=SOURCE_HASH)
    now = time.time()
    # Imported OCR is already complete. Never enqueue it for a live OCR worker.
    with sqlite3.connect(data / "state/ocr_jobs.sqlite3") as db:
        db.execute("""INSERT INTO ocr_jobs(book_id,output_dir,status,progress,current_step,attempts,max_attempts,available_at,created_at,updated_at,finished_at,result_json)
          VALUES(?,?,'succeeded',1,'已导入原教材 OCR',0,1,?,?,?,?,?)
          ON CONFLICT(book_id) DO NOTHING""", (BOOK_ID, str(book_dir / "ocr"), now, now, now, now, json.dumps({"page_count":125})))
    jobs.save_structure(BOOK_ID, structure)
    assessments = SQLiteAssessmentRepository(data / "state/assessments.sqlite3")
    assessments.save_bank(book_id=BOOK_ID, structure_fingerprint=structure_fingerprint(structure), items=bank)
    registry = data / "imported_books.json"
    write(registry, list(dict.fromkeys([*(read(registry) if registry.exists() else []), BOOK_ID])))
    report = {"book_id": BOOK_ID, "source_pdf_sha256": SOURCE_HASH, "source_pdf_pages":125,
        "chapters":7,"sections":19,"archived_directory_nodes":len(source["chapters"]),
        "lessons":len(source["lessons"]),"supplemental_lessons":2,"flashcards":len(source["flashcards"]),
        "quiz":len(source["quiz"]),"diagnostic_items":len(bank),"assets":len(assets),
        "learner_progress_imported":False,"missing_chapter_one_body":True,"database_backup":str(backup),
        "data_dir":str(data),"source_demo":str(demo)}
    write(imported / "import-report.json", report)
    return report


def build_index(data: Path) -> dict:
    """Re-embed original page text with the current server's configured model.

    Browser ONNX vectors are not reused with an unrelated server query model.
    """
    from dataclasses import asdict

    import numpy as np
    from dotenv import dotenv_values
    backend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend.parent / "deployment/rag"))
    from rag_eval import build_chunks, embed, load_embedding_model
    configured = dotenv_values(backend / ".env")
    model_path = Path(configured.get("RAG_EMBEDDING_MODEL_PATH") or "")
    if not model_path.is_absolute():
        model_path = backend / model_path
    if not (model_path / "config.json").is_file():
        raise ValueError("Configured local embedding model is unavailable")
    source = data / "books" / BOOK_ID / "ocr/normalized/pages.jsonl"
    chunks = build_chunks(source, limit=900, child_limit=360)
    tokenizer, model = load_embedding_model(model_path, "cpu")
    vectors = np.asarray(embed([c.text for c in chunks], tokenizer, model, "cpu", query=False), dtype=np.float32)
    output = source.parents[2] / "rag-index"
    write(output / "chunks.json", [asdict(c) for c in chunks])
    np.save(output / "embeddings.npy", vectors)
    report = {"chunk_count": len(chunks), "embedding_dimensions": int(vectors.shape[1]),
        "embedding_model": str(model_path), "source_pdf_sha256": SOURCE_HASH,
        "source_normalized_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "indexed_pages": sorted({c.page_number for c in chunks}), "page_count":125}
    write(output / "index_manifest.json", report)
    return {"index_dir":str(output),"chunk_count":len(chunks),"dimensions":int(vectors.shape[1])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--ocr", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--build-index", action="store_true")
    args = parser.parse_args()
    print(json.dumps(import_book(args.demo.resolve(), args.pdf.resolve(), args.ocr.resolve(), args.data_dir.resolve()), ensure_ascii=False, indent=2))
    if args.build_index:
        print(json.dumps(build_index(args.data_dir.resolve()), ensure_ascii=False, indent=2))
