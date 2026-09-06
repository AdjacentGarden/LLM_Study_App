from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import os
import shutil
import statistics
import time


ROOT = Path(__file__).resolve().parent
STORAGE = ROOT / "cloudpath_baseline" / "storage"
os.environ["BOOKCOURSE_STORAGE_ROOT"] = str(STORAGE)
os.environ["BOOKCOURSE_PARSER_PROVIDER"] = "auto"
os.environ["BOOKCOURSE_OCR_PROVIDER"] = "mock"
os.environ["BOOKCOURSE_EMBEDDING_PROVIDER"] = "hashing"
os.environ.pop("BOOKCOURSE_DATABASE_URL", None)

from app.document.pipeline import parse_document  # noqa: E402
from app.rag.retrieval import retrieve_chunks  # noqa: E402
from app.services.artifact_store import read_assets, read_chapters, read_chunks  # noqa: E402


SCENARIOS = [
    {
        "book_id": "stage0_native",
        "file": "native_text.pdf",
        "queries": [
            {"question": "What regulates transport and communication?", "expected_text": "plasma membrane regulates transport", "expected_page": 1},
            {"question": "What does DNA store?", "expected_text": "dna stores hereditary information", "expected_page": 2},
        ],
    },
    {
        "book_id": "stage0_complex",
        "file": "multicolumn_table_formula.pdf",
        "queries": [
            {"question": "Which process produces haploid cells?", "expected_text": "meiosis contains two divisions", "expected_page": 1},
            {"question": "What happens during G2?", "expected_text": "g2 replicated", "expected_page": 1},
        ],
    },
    {
        "book_id": "stage0_scanned",
        "file": "scanned.pdf",
        "queries": [{"question": "CELL DIAGRAM", "expected_text": "cell diagram", "expected_page": 1}],
    },
    {
        "book_id": "stage0_mixed",
        "file": "mixed.pdf",
        "queries": [
            {"question": "What converts light energy?", "expected_text": "photosynthesis converts light energy", "expected_page": 1},
            {"question": "CELL DIAGRAM", "expected_text": "cell diagram", "expected_page": 2},
        ],
    },
    {
        "book_id": "stage0_image",
        "file": "source_diagram.png",
        "queries": [{"question": "CELL DIAGRAM", "expected_text": "cell diagram", "expected_page": 1}],
    },
]


def _chunk_metrics(chunks) -> dict[str, object]:
    lengths = [len(chunk.text) for chunk in chunks]
    normalized = [" ".join(chunk.text.split()).lower() for chunk in chunks if chunk.text.strip()]
    duplicate_count = len(normalized) - len(set(normalized))
    return {
        "count": len(chunks),
        "content_types": dict(Counter(chunk.content_type for chunk in chunks)),
        "total_characters": sum(lengths),
        "min_characters": min(lengths) if lengths else 0,
        "max_characters": max(lengths) if lengths else 0,
        "mean_characters": round(statistics.mean(lengths), 3) if lengths else 0,
        "duplicate_count": duplicate_count,
        "duplicate_ratio": round(duplicate_count / len(normalized), 6) if normalized else 0,
    }


def main() -> None:
    output_root = ROOT / "cloudpath_baseline"
    if output_root.exists():
        shutil.rmtree(output_root)
    STORAGE.mkdir(parents=True, exist_ok=True)
    fixtures = ROOT / "fixtures"
    report: dict[str, object] = {
        "schema_version": 1,
        "configuration": {
            "parser_provider": "auto",
            "ocr_provider": "mock",
            "embedding_provider": "hashing",
            "database_url": None,
        },
        "scenarios": [],
    }

    for scenario in SCENARIOS:
        book_id = str(scenario["book_id"])
        file_path = fixtures / str(scenario["file"])
        artifact_path = STORAGE / "books" / book_id / "artifacts"
        started = time.perf_counter()
        scan = parse_document(book_id, file_path, artifact_path)
        elapsed = time.perf_counter() - started
        parser_report = json.loads((artifact_path / "parser_report.json").read_text(encoding="utf-8"))
        chunks = read_chunks(book_id)
        chapters = read_chapters(book_id)
        assets = read_assets(book_id)
        query_results = []
        for query in scenario["queries"]:
            question = str(query["question"])
            expected_text = " ".join(str(query["expected_text"]).lower().split())
            expected_page = int(query["expected_page"])
            query_started = time.perf_counter()
            retrieved = retrieve_chunks(book_id, question)
            relevant_ranks = []
            page_correct_ranks = []
            for rank, item in enumerate(retrieved[:5], start=1):
                normalized_text = " ".join(item.chunk.text.lower().split())
                if expected_text in normalized_text:
                    relevant_ranks.append(rank)
                    if item.chunk.page_start <= expected_page <= item.chunk.page_end:
                        page_correct_ranks.append(rank)
            query_results.append(
                {
                    "question": question,
                    "expected_text": expected_text,
                    "expected_page": expected_page,
                    "elapsed_ms": round((time.perf_counter() - query_started) * 1000, 3),
                    "result_count": len(retrieved),
                    "relevant_in_top5": bool(relevant_ranks),
                    "first_relevant_rank": min(relevant_ranks) if relevant_ranks else None,
                    "citation_page_correct": bool(page_correct_ranks),
                    "top_results": [
                        {
                            "chunk_id": item.chunk.chunk_id,
                            "content_type": item.chunk.content_type,
                            "page_start": item.chunk.page_start,
                            "text": item.chunk.text[:300],
                            "bm25_score": round(item.bm25_score, 6),
                            "dense_score": round(item.dense_score, 6),
                            "rerank_score": round(item.rerank_score, 6),
                            "index_name": item.index_name,
                        }
                        for item in retrieved[:5]
                    ],
                }
            )
        report["scenarios"].append(  # type: ignore[union-attr]
            {
                "book_id": book_id,
                "file": file_path.name,
                "parse_elapsed_seconds": round(elapsed, 3),
                "scan": scan.model_dump(),
                "parser_report": parser_report,
                "chapter_count": len(chapters),
                "asset_count": len(assets),
                "chunks": _chunk_metrics(chunks),
                "queries": query_results,
            }
        )

    all_queries = [query for scenario in report["scenarios"] for query in scenario["queries"]]  # type: ignore[index, union-attr]
    query_count = len(all_queries)
    report["retrieval_metrics"] = {
        "query_count": query_count,
        "recall_at_5": round(sum(bool(query["relevant_in_top5"]) for query in all_queries) / query_count, 6),
        "citation_page_accuracy_over_all_queries": round(
            sum(bool(query["citation_page_correct"]) for query in all_queries) / query_count,
            6,
        ),
        "citation_page_accuracy_conditional_on_retrieval": round(
            sum(bool(query["citation_page_correct"]) for query in all_queries if query["relevant_in_top5"])
            / max(1, sum(bool(query["relevant_in_top5"]) for query in all_queries)),
            6,
        ),
        "query_result_coverage": round(sum(int(query["result_count"] > 0) for query in all_queries) / query_count, 6),
    }

    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
