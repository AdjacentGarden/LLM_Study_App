# Assistant live DeepSeek probe results (2026-11-03)

Probes were run against the implementation pushed as `feature/assistant-local-tools`
`5f0f10de`, with base `origin/main` `4c338c1`.

Model: `deepseek-flash` (DeepSeek-V4.1-Flash, from `GET /models`). The key was
supplied through `DEEPSEEK_API_KEY` in the environment for the run and is not
stored here. The default `DEEPSEEK_MODEL=deepseek-chat` still answers; the
response reports model `deepseek-flash`.

Both scripts below are manual paid probes, not part of pytest. The key is read
from the environment and is never printed.

## 1. `/api/assistant/turn` E2E (`backend/scripts/live_assistant_turn_probe.py`)

Run from the backend directory:

```bash
cd backend
PYTHONPATH=src python scripts/live_assistant_turn_probe.py --out gateway-e2e-result.json
```

Production code: router, ledger, `ToolChatClient` and stream assembly. The phone
side (tool execution) is a script, not iOS `AgentRuntime`.

- 2 runs, each save_note -> open_note -> final answer, 3 real POSTs each.
  - The streamed tool arguments were assembled and validated.
  - evidence_ids only used the registered ids.
  - open_note used the note_id returned by the tool result.
  - source_label: run a = mixed (general knowledge marked in the body); run b = textbook.
- Replaying stored step 0 was answered from the ledger: posts stayed 3 -> 3.

Cached input tokens per step:

| step | run a | run b |
|---|---|---|
| s0 | 0/979 (cold) | 768/979 |
| s1 | 1152/1382 | 1152/1361 |
| s2 | 1280/1463 | 1152/1440 |

Total cached: 5504/7604 = 72.4%. Excluding the cold first call: 5504/6625 = 83.1%.
(The budget fields in the log are cumulative per run; the table shows per-step deltas.)

## 2. Grounded /qa A/B (`backend/scripts/live_qa_cache_probe.py`, same 6 evidence chunks, 4 questions)

Run from the backend directory for the new implementation:

```bash
cd backend
PYTHONPATH=src python scripts/live_qa_cache_probe.py --variant new --out qa-cache-new.json
```

For the base variant, point `PYTHONPATH` at the base checkout instead:

```bash
cd backend
PYTHONPATH=<base checkout>/backend/src python scripts/live_qa_cache_probe.py --variant base --out qa-cache-base.json
```

| variant | hit, all calls | hit, calls 2-4 |
|---|---|---|
| base 4c338c1 | 3072/14940 = 20.6% | 27.4% (only the system prompt was cached) |
| new | 11776/14920 = 78.9% | 96.1% |

Not run: real-device iOS, the iOS DeepSeek fallback path, the teaching_unit_engine live run, deploy.
