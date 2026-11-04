#!/usr/bin/env python3
"""Manual paid probe: real-model ``/api/assistant/query`` across several courses.

Not part of pytest. The account, courses and OCR pages are small temporary
fixtures; the route, read tools, ledger, stream assembler and DeepSeek transport
are production code. ``DEEPSEEK_API_KEY`` is read from the environment and is
never printed or written to the output file.

Run from the backend directory::

    PYTHONPATH=src python scripts/live_assistant_query_probe.py --out result.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaptive_learning.api.assistant_query_routes import assistant_query_router
from adaptive_learning.assistant_ledger import AssistantLedger
from adaptive_learning.community import CommunityRepository
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft
from adaptive_learning.llm.client import LLMConfig
from adaptive_learning.llm.tool_chat import ToolChatClient
from adaptive_learning.native import NativeRepository

MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
BOOKS = [
    ("chemistry", "化学必修", "化学反应过程中常伴随能量变化。放热反应释放能量，吸热反应吸收能量。"),
    ("biology", "生物遗传", "DNA 是主要的遗传物质。基因通常是有遗传效应的 DNA 片段。"),
    ("physics", "物理力学", "物体在不受外力作用时，总保持静止状态或匀速直线运动状态。"),
]
LOCAL = [{"id": "local-course:pending", "name": "本机待上传的英语课", "book_ids": ["book_local_x1"],
          "status": "pending_local"}]
CONTEXT = {"course_id": "workspace_chemistry", "book_id": "chemistry", "book_ids": ["chemistry"],
           "course_title": "化学必修课程", "chapter_title": "第一章"}
QUESTIONS = [
    ("count", "当前有多少门课程？"),
    ("other_course", "生物那门课里说基因是什么？"),
    ("general", "光在真空中的速度大约是多少？"),
]


def services(root: Path, chat: ToolChatClient) -> SimpleNamespace:
    native = NativeRepository(root / "native.sqlite3")
    community = CommunityRepository(root / "community.sqlite3")
    stored, structures = {}, {}
    for book, title, text in BOOKS:
        workspace = "workspace_" + book
        native.save_workspace("alice", workspace, title + "课程", {})
        native.add_resource("alice", workspace, book)
        community.bind_upload("alice", book)
        pages = root / book / "ocr" / "normalized" / "pages.jsonl"
        pages.parent.mkdir(parents=True)
        pages.write_text(json.dumps({"page_number": 1, "cleaned_text": text,
                                     "blocks": [{"block_id": "p1-b1", "text": text}]},
                                    ensure_ascii=False) + "\n", encoding="utf-8")
        stored[book] = SimpleNamespace(original_name=title + ".pdf", file_path=root / book / "book.pdf",
                                       status="structured")
        structures[book] = BookStructure(
            title=title, summary=title + "概要", source_page_count=1,
            chapters=[ChapterDraft(chapter_id="chapter_" + book, order=1, title="第一章",
                                   start_page=1, end_page=1, summary="章节概要",
                                   knowledge_points=["知识点"], source_block_ids=[])])
    return SimpleNamespace(
        native_repository=native,
        account_repository=SimpleNamespace(repo=community, session_owner=lambda _cookie: "alice"),
        job_repository=SimpleNamespace(get_book=stored.get, get_structure=structures.get),
        assistant_ledger=AssistantLedger(root / "assistant.sqlite3"),
        assistant_tool_chat=lambda: chat,
    )


def budget(ledger: AssistantLedger, run_id: str) -> SimpleNamespace:
    """Run budget read from the last persisted step (posts are run-wide)."""
    last, tools = None, []
    for index in range(8):
        found = ledger.step("alice", run_id, f"query_{index}")
        if found is None:
            break
        last = found[1]
        for call in (found[0].message or {}).get("tool_calls") or []:
            tools.append({"name": call["function"]["name"], "arguments": call["function"]["arguments"]})
    return SimpleNamespace(posts=last.posts if last else 0, input_tokens=last.input_tokens if last else 0,
                           cached_input_tokens=last.cached_input_tokens if last else 0, tools=tools)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a real-model /api/assistant/query probe (paid).")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        print("error: DEEPSEEK_API_KEY is not set", file=sys.stderr)
        return 2
    root = Path(tempfile.mkdtemp(prefix="query-live-"))
    chat = ToolChatClient(LLMConfig("https://api.deepseek.com/v1", key, MODEL,
                                    timeout_seconds=90, max_retries=0))
    deps = services(root, chat)
    app = FastAPI()
    app.include_router(assistant_query_router(deps))
    results = []
    with TestClient(app) as client:
        for name, question in QUESTIONS:
            started = time.monotonic()
            request = {"request_id": "live_" + name, "question": question, "history": [],
                       "context": CONTEXT, "local_courses": LOCAL}
            response = client.post("/api/assistant/query", json=request)
            data = response.json()
            run = budget(deps.assistant_ledger, "query:live_" + name)
            results.append({
                "case": name, "http": response.status_code, "elapsed": round(time.monotonic() - started, 2),
                "answer": data.get("answer"), "source_label": data.get("source_label"),
                "citation_books": [c["book_id"] for c in data.get("citations", [])],
                "tools": run.tools, "posts": run.posts, "input_tokens": run.input_tokens,
                "cached_input_tokens": run.cached_input_tokens,
            })
            print(json.dumps(results[-1], ensure_ascii=False))
        # A replay must be answered from the ledger without another paid POST.
        before = budget(deps.assistant_ledger, "query:live_count").posts
        replay = client.post("/api/assistant/query", json={
            "request_id": "live_count", "question": QUESTIONS[0][1], "history": [],
            "context": CONTEXT, "local_courses": LOCAL})
        after = budget(deps.assistant_ledger, "query:live_count").posts
    chat.close()
    total_in = sum(item["input_tokens"] for item in results)
    total_cached = sum(item["cached_input_tokens"] for item in results)
    summary = {
        "model": MODEL, "cases": results,
        "replay_http": replay.status_code, "replay_new_posts": after - before,
        "cache_hit_ratio": round(total_cached / total_in, 3) if total_in else None,
    }
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "cases"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
