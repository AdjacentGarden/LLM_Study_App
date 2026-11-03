#!/usr/bin/env python3
"""Manual paid probe: real-model ``/api/assistant/turn`` end to end.

This is a manual paid probe, not part of pytest. Only the phone side (tool
execution) is simulated; the gateway, ledger, stream assembler and DeepSeek
transport are production code. The API key is read from ``DEEPSEEK_API_KEY`` in
the environment and is never printed or written to the output file.

Run from the backend directory::

    PYTHONPATH=src python scripts/live_assistant_turn_probe.py --out result.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _ensure_importable(module_name: str, directory: Path) -> None:
    if importlib.util.find_spec(module_name) is None:
        sys.path.insert(0, str(directory))


_ensure_importable("native_harness", BACKEND_DIR / "tests")
_ensure_importable("adaptive_learning", BACKEND_DIR / "src")

from native_harness import api, configure  # noqa: E402

from adaptive_learning.api.assistant_routes import assistant_router  # noqa: E402
from adaptive_learning.assistant_ledger import AssistantLedger  # noqa: E402
from adaptive_learning.llm.client import LLMConfig  # noqa: E402
from adaptive_learning.llm.tool_chat import ToolChatClient  # noqa: E402

MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
QUOTES = {
    "ev_p21_a": "减数分裂是进行有性生殖的生物，在产生成熟生殖细胞时进行的染色体数目减半的细胞分裂。"
    "在减数分裂过程中，染色体只复制一次，而细胞分裂两次。",
    "ev_p21_b": "减数第一次分裂中，同源染色体两两配对的现象叫作联会，联会后的每对同源染色体含有四条染色单体，叫作四分体。",
}
SELECTION = QUOTES["ev_p21_a"]


def body(run, step_index, results, request="把选中的内容整理成一条笔记并打开它"):
    return {
        "contract_version": 1,
        "run_id": run,
        "step_id": f"{run}_s{step_index}",
        "step_index": step_index,
        "request": request,
        "context": {
            "book_id": "bio_bixiu2",
            "book_title": "普通高中生物学 必修2",
            "source_version": "sha256:demo-v1",
            "chapter_id": "ch2",
            "page": 21,
            "printed_page": 18,
            "selection": SELECTION,
        },
        "evidence": [
            {"evidence_id": k, "page": 21, "printed_page": 18, "quote": v}
            for k, v in QUOTES.items()
        ],
        "tools": ["save_note", "open_note", "ask_user"],
        "tool_results": results,
    }


def drive(client, run, log):
    results, notes = [], {}
    for index in range(8):
        started = time.monotonic()
        response = client.post("/api/assistant/turn", json=body(run, index, results))
        step = response.json()
        log.append(
            {
                "run": run,
                "step": index,
                "http": response.status_code,
                "state": step.get("state"),
                "elapsed": round(time.monotonic() - started, 2),
                "error": step.get("error"),
                "finish_reason": step.get("finish_reason"),
                "budget": step.get("budget"),
                "tool_calls": [
                    {"name": c["name"], "arguments": c["arguments"]}
                    for c in (step.get("message") or {}).get("tool_calls", [])
                ],
                "content": ((step.get("message") or {}).get("content") or "")[:300],
            }
        )
        print(json.dumps(log[-1], ensure_ascii=False))
        if response.status_code != 200 or step["state"] != "completed":
            return False
        calls = step["message"]["tool_calls"]
        if not calls:
            return True
        for call in calls:
            args = call["arguments"]
            if call["name"] == "save_note":
                bad = [e for e in args["evidence_ids"] if e not in QUOTES]
                assert not bad, f"model cited unknown evidence {bad}"
                note_id = f"note_{run}_{call['id'][-6:]}"
                notes[note_id] = args
                content = {"status": "saved", "note_id": note_id, "revision": 1}
            elif call["name"] == "open_note":
                assert args["note_id"] in notes, f"open_note used non-committed id {args['note_id']}"
                content = {"status": "opened", "note_id": args["note_id"]}
            else:
                content = {"status": "answered", "option": args["options"][0]}
            results.append(
                {
                    "step_index": index,
                    "tool_call_id": call["id"],
                    "content": json.dumps(content, ensure_ascii=False, sort_keys=True),
                }
            )
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a real-model /api/assistant/turn probe (manual, paid)."
    )
    parser.add_argument("--out", required=True, help="Path for the JSON result.")
    args = parser.parse_args(argv)

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print(
            "error: DEEPSEEK_API_KEY is not set; export it before running this script.",
            file=sys.stderr,
        )
        return 2

    root = Path(tempfile.mkdtemp(prefix="gw-live-"))
    log = []
    with pytest.MonkeyPatch.context() as patch:
        app = configure(root, patch)
        app.include_router(assistant_router(api))
        patch.setattr(api, "assistant_ledger", AssistantLedger(root / "state" / "assistant.sqlite3"))
        chat = ToolChatClient(
            LLMConfig(
                "https://api.deepseek.com/v1",
                api_key,
                MODEL,
                timeout_seconds=90,
                max_retries=0,
            )
        )
        patch.setattr(api, "assistant_tool_chat", lambda: chat)
        with TestClient(app) as client:
            challenge = client.post(
                "/api/auth/code", json={"email": "reader@zhiwo.test", "purpose": "register"}
            ).json()
            client.post(
                "/api/auth/verify",
                json={
                    "challenge_id": challenge["challenge_id"],
                    "code": challenge["demo_code"],
                    "registration": {
                        "nickname": "实测",
                        "stage": "university",
                        "interests": [],
                        "goal": "interest",
                    },
                },
            )
            ok1 = drive(client, "live_run_a", log)
            posts = log[-1]["budget"]["posts"]
            # Replay of a stored step must come from the ledger, not a new POST.
            replay = client.post("/api/assistant/turn", json=body("live_run_a", 0, [])).json()
            replay_ok = replay["state"] == "completed" and replay["budget"]["posts"] == posts
            print(
                json.dumps(
                    {
                        "replay_step0_state": replay["state"],
                        "posts_before": posts,
                        "posts_after": replay["budget"]["posts"],
                        "replay_no_new_post": replay_ok,
                    }
                )
            )
            ok2 = drive(client, "live_run_b", log)
    summary = {
        "model": MODEL,
        "run_a_completed": ok1,
        "run_b_completed": ok2,
        "replay_no_new_post": replay_ok,
        "steps": [
            {k: e[k] for k in ("run", "step", "state")}
            | {
                "input": e["budget"]["input_tokens"] if e["budget"] else None,
                "cached": e["budget"]["cached_input_tokens"] if e["budget"] else None,
            }
            for e in log
        ],
    }
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False))
    Path(args.out).write_text(
        json.dumps({"summary": summary, "log": log}, ensure_ascii=False, indent=1)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
