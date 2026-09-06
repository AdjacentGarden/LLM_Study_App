from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

from adaptive_learning.rag.backends import (
    TransformerPairReranker,
    TransformerQueryEncoder,
)
from adaptive_learning.rag.index import PersistentRAGIndex


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    position = min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1)
    return ordered[position]


def evaluate(args: argparse.Namespace) -> None:
    encoder = TransformerQueryEncoder(args.embedding_model, device=args.device)
    reranker = TransformerPairReranker(args.reranker_model, device=args.device)
    index = PersistentRAGIndex.load(
        args.index, encoder=encoder, reranker=reranker
    )
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    durations: list[float] = []
    for position, case in enumerate(cases, start=1):
        started = time.monotonic()
        result = index.search(
            str(case["question"]),
            top_pages=args.top_pages,
            max_evidence=args.max_evidence,
        )
        duration_ms = (time.monotonic() - started) * 1000
        durations.append(duration_ms)
        evidence_text = "\n".join(item.text for item in result.evidence)
        row = {
            "id": case["id"],
            "expected_pages": case["expected_pages"],
            "pages": list(result.pages),
            "score": round(result.score, 5),
            "evidence_chunk_ids": [item.chunk_id for item in result.evidence],
            "duration_ms": round(duration_ms, 2),
            "expected_term_coverage": (
                1.0
                if not case["expected_terms"]
                else round(
                    sum(
                        term.lower() in evidence_text.lower()
                        for term in case["expected_terms"]
                    )
                    / len(case["expected_terms"]),
                    4,
                )
            ),
        }
        rows.append(row)
        print(
            f"[{position}/{len(cases)}] {case['id']}: "
            f"pages={row['pages']} score={row['score']} {row['duration_ms']}ms"
        )

    positive = [row for row in rows if row["expected_pages"]]
    negative = [row for row in rows if not row["expected_pages"]]
    reciprocal: list[float] = []
    recall: list[float] = []
    for row in positive:
        gold = set(row["expected_pages"])
        first = next(
            (rank for rank, page in enumerate(row["pages"], start=1) if page in gold),
            None,
        )
        reciprocal.append(0 if first is None else 1 / first)
        recall.append(len(set(row["pages"][:5]) & gold) / len(gold))
    metrics = {
        "case_count": len(rows),
        "mrr": round(sum(reciprocal) / max(1, len(reciprocal)), 4),
        "hit_at_1": round(
            sum(bool(set(row["pages"][:1]) & set(row["expected_pages"])) for row in positive)
            / max(1, len(positive)),
            4,
        ),
        "hit_at_3": round(
            sum(bool(set(row["pages"][:3]) & set(row["expected_pages"])) for row in positive)
            / max(1, len(positive)),
            4,
        ),
        "hit_at_5": round(
            sum(bool(set(row["pages"][:5]) & set(row["expected_pages"])) for row in positive)
            / max(1, len(positive)),
            4,
        ),
        "gold_page_recall_at_5": round(sum(recall) / max(1, len(recall)), 4),
        "expected_term_coverage": round(
            sum(float(row["expected_term_coverage"]) for row in positive)
            / max(1, len(positive)),
            4,
        ),
        "negative_rejection_rate": round(
            sum(float(row["score"]) <= args.refusal_threshold for row in negative)
            / max(1, len(negative)),
            4,
        ),
        "latency_ms": {
            "p50": round(_percentile(durations, 0.50), 2),
            "p95": round(_percentile(durations, 0.95), 2),
            "max": round(max(durations, default=0), 2),
        },
    }
    output = {"metrics": metrics, "results": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the persistent RAG service")
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--embedding-model", type=Path, required=True)
    parser.add_argument("--reranker-model", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--top-pages", type=int, default=5)
    parser.add_argument("--max-evidence", type=int, default=10)
    parser.add_argument("--refusal-threshold", type=float, default=0)
    return parser.parse_args()


if __name__ == "__main__":
    evaluate(parse_args())
