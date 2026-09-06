from __future__ import annotations

import argparse
import json
import math
import re
import time
from pathlib import Path
from typing import Any

import httpx


def _normalize(text: str) -> str:
    digits = {
        "零": "0",
        "一": "1",
        "二": "2",
        "三": "3",
        "四": "4",
        "五": "5",
        "六": "6",
        "七": "7",
        "八": "8",
        "九": "9",
    }
    compact = re.sub(r"\s+", "", text).lower()
    return "".join(digits.get(character, character) for character in compact)


def _concept_coverage(answer: str, concepts: list[list[str]]) -> float:
    if not concepts:
        return 1.0
    normalized = _normalize(answer)
    return sum(
        any(_normalize(variant) in normalized for variant in variants)
        for variants in concepts
    ) / len(concepts)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0
    index = min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def evaluate(args: argparse.Namespace) -> None:
    loaded_cases = json.loads(args.cases.read_text(encoding="utf-8"))
    selected = (
        [case for case in loaded_cases if case["id"] in set(args.case_id)]
        if args.case_id
        else loaded_cases
    )
    cases = [case for _ in range(args.repeat) for case in selected]
    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=args.timeout) as client:
        for position, case in enumerate(cases, start=1):
            started = time.monotonic()
            response = client.post(
                f"{args.base_url.rstrip('/')}/api/books/{args.book_id}/qa",
                json={"question": case["question"]},
            )
            elapsed_ms = (time.monotonic() - started) * 1000
            payload = response.json()
            expected_status = "supported" if case["expected_pages"] else "insufficient"
            citation_pages = set(payload.get("evidence_pages", []))
            expected_pages = set(case["expected_pages"])
            row = {
                "id": case["id"],
                "http_status": response.status_code,
                "status": payload.get("status"),
                "expected_status": expected_status,
                "status_correct": payload.get("status") == expected_status,
                "citation_page_correct": (
                    bool(citation_pages & expected_pages) if expected_pages else not citation_pages
                ),
                "concept_coverage": _concept_coverage(
                    str(payload.get("answer", "")), case.get("answer_concepts", [])
                ),
                "internal_id_exposed": "chunk_id" in response.text,
                "latency_ms": round(elapsed_ms, 2),
                "retrieval_duration_ms": payload.get("retrieval_duration_ms"),
                "generation_duration_ms": payload.get("generation_duration_ms"),
                "evidence_pages": sorted(citation_pages),
                "answer": payload.get("answer"),
            }
            rows.append(row)
            print(
                f"[{position}/{len(cases)}] {case['id']}: "
                f"http={response.status_code} status={row['status']} {row['latency_ms']}ms"
            )

    positive = [row for row in rows if row["expected_status"] == "supported"]
    negative = [row for row in rows if row["expected_status"] == "insufficient"]
    durations = [float(row["latency_ms"]) for row in rows]
    metrics = {
        "case_count": len(rows),
        "http_success_rate": round(
            sum(row["http_status"] == 200 for row in rows) / max(1, len(rows)), 4
        ),
        "positive_supported_rate": (
            1.0
            if not positive
            else round(sum(row["status_correct"] for row in positive) / len(positive), 4)
        ),
        "negative_refusal_rate": (
            1.0
            if not negative
            else round(sum(row["status_correct"] for row in negative) / len(negative), 4)
        ),
        "citation_page_accuracy": round(
            sum(row["citation_page_correct"] for row in positive)
            / max(1, len(positive)),
            4,
        ),
        "answer_concept_coverage": round(
            sum(float(row["concept_coverage"]) for row in positive)
            / max(1, len(positive)),
            4,
        ),
        "internal_id_exposure_count": sum(row["internal_id_exposed"] for row in rows),
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
    required = (
        metrics["http_success_rate"] == 1.0
        and metrics["positive_supported_rate"] == 1.0
        and metrics["negative_refusal_rate"] == 1.0
        and metrics["citation_page_accuracy"] == 1.0
        and metrics["answer_concept_coverage"] == 1.0
        and metrics["internal_id_exposure_count"] == 0
    )
    if not required:
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="End-to-end RAG API evaluation")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--book-id", default="biology-required-2")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--repeat", type=int, default=1)
    return parser.parse_args()


if __name__ == "__main__":
    evaluate(parse_args())
