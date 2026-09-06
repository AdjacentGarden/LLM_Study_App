from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
STAGE = Path(__file__).resolve().parent
PIPELINE_REPORT = json.loads((STAGE / "real_pipeline_probe.json").read_text(encoding="utf-8"))
STORAGE = ROOT / PIPELINE_REPORT["storage"]
os.environ["BOOKCOURSE_STORAGE_ROOT"] = str(STORAGE)
os.environ["BOOKCOURSE_EMBEDDING_PROVIDER"] = "hashing"
os.environ["BOOKCOURSE_RAG_INDEX_PROVIDER"] = "artifact"
os.environ.pop("BOOKCOURSE_DATABASE_URL", None)
sys.path.insert(0, str(ROOT / "backend"))

from app.rag.retrieval import retrieve_chunks  # noqa: E402


QUERIES = {
    "native": [
        ("What regulates transport and communication?", "plasma membrane regulates transport", 1),
        ("What does DNA store?", "dna stores hereditary information", 2),
    ],
    "complex": [
        ("Which process produces haploid cells?", "meiosis contains two divisions", 1),
        ("What happens during G2?", "g2 replicated", 1),
    ],
    "scanned": [("CELL DIAGRAM", "cell diagram", 1)],
    "mixed": [
        ("What converts light energy?", "photosynthesis converts light energy", 1),
        ("CELL DIAGRAM", "cell diagram", 2),
    ],
    "image": [("CELL DIAGRAM", "cell diagram", 1)],
}


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def main() -> int:
    scenario_books = {item["scenario"]: item["book_id"] for item in PIPELINE_REPORT["scenario_results"]}
    results = []
    for scenario, queries in QUERIES.items():
        book_id = scenario_books[scenario]
        for question, expected_text, expected_page in queries:
            started = time.perf_counter()
            retrieved = retrieve_chunks(book_id, question)
            relevant = []
            correct = []
            for rank, item in enumerate(retrieved[:5], start=1):
                if _normalized(expected_text) in _normalized(item.chunk.text):
                    relevant.append(rank)
                    if item.chunk.page_start <= expected_page <= item.chunk.page_end:
                        correct.append(rank)
            results.append(
                {
                    "scenario": scenario,
                    "book_id": book_id,
                    "question": question,
                    "expected_text": expected_text,
                    "expected_page": expected_page,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    "result_count": len(retrieved),
                    "relevant_in_top5": bool(relevant),
                    "first_relevant_rank": min(relevant) if relevant else None,
                    "citation_page_correct": bool(correct),
                    "top_results": [
                        {
                            "chunk_id": item.chunk.chunk_id,
                            "page_start": item.chunk.page_start,
                            "page_end": item.chunk.page_end,
                            "content_type": item.chunk.content_type,
                            "text": item.chunk.text[:300],
                            "index_name": item.index_name,
                        }
                        for item in retrieved[:5]
                    ],
                }
            )
    count = len(results)
    recalled = sum(item["relevant_in_top5"] for item in results)
    citations = sum(item["citation_page_correct"] for item in results)
    coverage = sum(item["result_count"] > 0 for item in results)
    report = {
        "schema_version": 1,
        "pipeline_run_id": PIPELINE_REPORT["run_id"],
        "query_count": count,
        "results": results,
        "retrieval_metrics": {
            "recall_at_5": recalled / count,
            "citation_page_accuracy_over_all_queries": citations / count,
            "citation_page_accuracy_conditional_on_retrieval": citations / max(1, recalled),
            "query_result_coverage": coverage / count,
        },
    }
    passed = all(value == 1.0 for value in report["retrieval_metrics"].values())
    report["passed"] = passed
    (STAGE / "retrieval_probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"retrieval_metrics": report["retrieval_metrics"], "passed": passed}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

