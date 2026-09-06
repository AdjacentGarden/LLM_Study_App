from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from adaptive_learning.llm.client import LLMConfig, LLMError, OpenAICompatibleClient
from adaptive_learning.rag.grounded_qa import (
    AnswerStatus,
    EvidenceChunk,
    GroundedAnswerGenerator,
    GroundedAnswerValidationError,
)


def _tokens(text: str) -> set[str]:
    cjk = re.findall(r"[\u3400-\u9fff]", text)
    bigrams = {cjk[index] + cjk[index + 1] for index in range(len(cjk) - 1)}
    words = {word.lower() for word in re.findall(r"[A-Za-z0-9]{2,}", text)}
    return bigrams | words


def _f1(left: str, right: str) -> float:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return 0
    overlap = len(left_tokens & right_tokens)
    precision = overlap / len(left_tokens)
    recall = overlap / len(right_tokens)
    return 0 if not precision + recall else 2 * precision * recall / (precision + recall)


def _normalized_for_match(text: str) -> str:
    digit_words = {
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
    return "".join(digit_words.get(character, character) for character in compact)


def _concept_coverage(answer: str, concepts: list[list[str]]) -> float:
    """Score explicit answer concepts while allowing audited equivalent wording."""
    if not concepts:
        return 1.0
    normalized_answer = _normalized_for_match(answer)
    covered = sum(
        any(_normalized_for_match(variant) in normalized_answer for variant in variants)
        for variants in concepts
    )
    return covered / len(concepts)


def _select_evidence_ids(
    case: dict[str, Any], chunks: dict[str, dict[str, Any]], max_evidence: int
) -> list[str]:
    """Keep page coverage before spending the budget on sibling child chunks."""
    ranked_ids = [str(chunk_id) for chunk_id in case["final_chunk_ids"]]
    representatives: list[str] = []
    for page in case["final_pages"]:
        representative = next(
            (
                chunk_id
                for chunk_id in ranked_ids
                if int(chunks[chunk_id]["page_number"]) == int(page)
            ),
            None,
        )
        if representative is not None:
            representatives.append(representative)
    ordered = list(dict.fromkeys([*representatives, *ranked_ids]))
    return ordered[:max_evidence]


def _metrics(results: list[dict[str, Any]]) -> dict[str, float | int]:
    positive = [result for result in results if result["expected_pages"]]
    negative = [result for result in results if not result["expected_pages"]]
    valid = [result for result in results if not result.get("error")]
    positive_supported = [
        result for result in positive if result.get("status") == AnswerStatus.SUPPORTED
    ]
    negative_refused = [
        result for result in negative if result.get("status") == AnswerStatus.INSUFFICIENT
    ]
    citation_page_correct: list[float] = []
    term_coverage: list[float] = []
    concept_coverage: list[float] = []
    reference_f1: list[float] = []
    for result in positive_supported:
        citation_pages = set(result["citation_pages"])
        citation_page_correct.append(
            float(bool(citation_pages & set(result["expected_pages"])))
        )
        terms = result["expected_terms"]
        answer = result["answer"].lower()
        term_coverage.append(
            1.0 if not terms else sum(term.lower() in answer for term in terms) / len(terms)
        )
        concept_coverage.append(
            _concept_coverage(result["answer"], result.get("answer_concepts", []))
        )
        reference_f1.append(_f1(result["answer"], result["reference_answer"]))

    return {
        "case_count": len(results),
        "valid_result_count": len(valid),
        "generation_error_count": sum(bool(result.get("error")) for result in results),
        "positive_supported_rate": round(
            len(positive_supported) / max(1, len(positive)), 4
        ),
        "negative_refusal_rate": round(len(negative_refused) / max(1, len(negative)), 4),
        "citation_page_accuracy": round(
            sum(citation_page_correct) / max(1, len(citation_page_correct)), 4
        ),
        "answer_expected_term_coverage": round(
            sum(term_coverage) / max(1, len(term_coverage)), 4
        ),
        "answer_concept_coverage": round(
            sum(concept_coverage) / max(1, len(concept_coverage)), 4
        ),
        "reference_bigram_f1": round(
            sum(reference_f1) / max(1, len(reference_f1)), 4
        ),
    }


def evaluate(args: argparse.Namespace) -> None:
    retrieval = json.loads(args.retrieval.read_text(encoding="utf-8"))
    chunks = {
        item["chunk_id"]: item
        for item in json.loads(args.chunks.read_text(encoding="utf-8"))
    }
    client = OpenAICompatibleClient(
        LLMConfig(
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            timeout_seconds=args.timeout,
        )
    )
    generator = GroundedAnswerGenerator(
        client,
        refusal_score_threshold=args.refusal_threshold,
        use_evidence_planner=True,
    )
    reusable: dict[str, dict[str, Any]] = {}
    if args.reuse_from:
        previous = json.loads(args.reuse_from.read_text(encoding="utf-8"))
        reusable = {str(item["id"]): item for item in previous["results"]}
    results: list[dict[str, Any]] = []
    started = time.monotonic()
    cases = retrieval["results"][: args.case_limit or None]
    for case in cases:
        evidence: list[EvidenceChunk] = []
        audit_sources: list[dict[str, object]] = []
        selected_ids = _select_evidence_ids(case, chunks, args.max_evidence)
        for position, chunk_id in enumerate(selected_ids, start=1):
            chunk = chunks[chunk_id]
            source_id = f"E{position}"
            evidence.append(
                EvidenceChunk(
                    source_id=source_id,
                    page_number=int(chunk["page_number"]),
                    text=str(chunk["text"]),
                )
            )
            audit_sources.append(
                {
                    "source_id": source_id,
                    "chunk_id": chunk_id,
                    "page_number": int(chunk["page_number"]),
                }
            )
        score = float(case["hits"][0]["rerank_score"]) if case["hits"] else -100
        row: dict[str, Any] = {
            "id": case["id"],
            "question": case["question"],
            "expected_pages": case["expected_pages"],
            "expected_terms": case["expected_terms"],
            "answer_concepts": case.get("answer_concepts", []),
            "reference_answer": case["reference_answer"],
            "retrieval_score": score,
            "audit_sources": audit_sources,
        }
        previous_row = reusable.get(str(case["id"]))
        can_reuse = bool(
            previous_row
            and previous_row.get("question") == case["question"]
            and previous_row.get("audit_sources") == audit_sources
            and not previous_row.get("error")
            and case["id"] not in args.regenerate_case
        )
        if can_reuse and previous_row is not None:
            for key in (
                "status",
                "answer",
                "claims",
                "confidence",
                "insufficiency_reason",
                "citation_pages",
            ):
                row[key] = previous_row.get(key)
            row["reused_verified_answer"] = True
        else:
            try:
                answer = generator.answer(
                    question=case["question"],
                    evidence=evidence,
                    retrieval_score=score,
                )
                row.update(answer.model_dump(mode="json"))
                row["citation_pages"] = sorted(
                    {
                        citation.page_number
                        for claim in answer.claims
                        for citation in claim.citations
                    }
                )
            except (LLMError, GroundedAnswerValidationError, ValueError) as error:
                row["error"] = f"{type(error).__name__}: {error}"
        results.append(row)
        output = {
            "provider": {"base_url": args.base_url, "model": args.model},
            "metrics": _metrics(results),
            "duration_seconds": round(time.monotonic() - started, 3),
            "results": results,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        suffix = " (reused)" if row.get("reused_verified_answer") else ""
        print(
            f"[{len(results)}/{len(cases)}] {case['id']}: "
            f"{row.get('status', 'error')}{suffix}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate evidence-grounded textbook answers")
    parser.add_argument("--retrieval", type=Path, required=True)
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--base-url", default=os.getenv("PUCODING_BASE_URL", "https://pucoding.com/v1")
    )
    parser.add_argument("--api-key", default=os.getenv("PUCODING_API_KEY", ""))
    parser.add_argument("--model", default=os.getenv("PUCODING_TEXT_MODEL", "gpt-5.6-sol"))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--refusal-threshold", type=float, default=0)
    parser.add_argument("--max-evidence", type=int, default=10)
    parser.add_argument("--case-limit", type=int, default=0)
    parser.add_argument(
        "--reuse-from",
        type=Path,
        help="Reuse previously verified answers when question and evidence are unchanged",
    )
    parser.add_argument(
        "--regenerate-case",
        action="append",
        default=[],
        help="Case id to regenerate even when --reuse-from is set; may be repeated",
    )
    return parser.parse_args()


if __name__ == "__main__":
    evaluate(parse_args())
