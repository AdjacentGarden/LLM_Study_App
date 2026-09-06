from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from adaptive_learning.rag.backends import (
    TransformerPairReranker,
    TransformerQueryEncoder,
)
from adaptive_learning.rag.index import PersistentRAGIndex


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0
    index = min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def run(args: argparse.Namespace) -> None:
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    questions = [str(case["question"]) for case in cases]
    encoder = TransformerQueryEncoder(args.embedding_model, device=args.device)
    reranker = TransformerPairReranker(args.reranker_model, device=args.device)
    index = PersistentRAGIndex.load(
        args.index, encoder=encoder, reranker=reranker
    )

    cold_started = time.monotonic()
    index.search(questions[0])
    cold_ms = (time.monotonic() - cold_started) * 1000
    baseline = {question: index.search(question) for question in questions}
    requests = [questions[position % len(questions)] for position in range(args.requests)]

    def execute(question: str) -> dict[str, Any]:
        started = time.monotonic()
        result = index.search(question)
        elapsed = (time.monotonic() - started) * 1000
        expected = baseline[question]
        deterministic = result.pages == expected.pages and math.isclose(
            result.score, expected.score, abs_tol=1e-5
        )
        return {"duration_ms": elapsed, "deterministic": deterministic}

    started = time.monotonic()
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(execute, question) for question in requests]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as error:  # noqa: BLE001 - load test records all failures
                errors.append(f"{type(error).__name__}: {error}")
    wall_seconds = time.monotonic() - started
    durations = [float(result["duration_ms"]) for result in results]
    report = {
        "requests": args.requests,
        "concurrency": args.concurrency,
        "completed": len(results),
        "error_count": len(errors),
        "deterministic_rate": round(
            sum(bool(result["deterministic"]) for result in results)
            / max(1, len(results)),
            4,
        ),
        "cold_start_ms": round(cold_ms, 2),
        "throughput_requests_per_second": round(len(results) / wall_seconds, 2),
        "latency_ms": {
            "p50": round(_percentile(durations, 0.50), 2),
            "p95": round(_percentile(durations, 0.95), 2),
            "p99": round(_percentile(durations, 0.99), 2),
            "max": round(max(durations, default=0), 2),
        },
        "errors": errors[:10],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors or report["deterministic_rate"] != 1.0:
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load-test persistent RAG retrieval")
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--embedding-model", type=Path, required=True)
    parser.add_argument("--reranker-model", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--requests", type=int, default=64)
    parser.add_argument("--concurrency", type=int, default=8)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
