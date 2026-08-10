from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import unicodedata


ROOT = Path(__file__).resolve().parents[2]
STAGE = Path(__file__).resolve().parent
THRESHOLDS_PATH = ROOT / "quality" / "stage0" / "acceptance_thresholds.json"
OUTPUT_PATH = STAGE / "acceptance_evaluation.json"


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return math.inf
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _invalid_character_ratio(text: str) -> float:
    if not text:
        return 0.0
    invalid = sum(
        character == "\ufffd"
        or (unicodedata.category(character) == "Cc" and character not in {"\n", "\r", "\t"})
        for character in text
    )
    return invalid / len(text)


def _normalized_text(text: str) -> str:
    return " ".join(text.casefold().split())


def _check(value: object, operator: str, threshold: object, passed: bool) -> dict:
    return {
        "value": value,
        "operator": operator,
        "threshold": threshold,
        "passed": bool(passed),
    }


def _atomic_write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    thresholds = _read_json(THRESHOLDS_PATH)
    parser_limits = thresholds["parser_quality"]
    retrieval_limits = thresholds["retrieval"]
    performance_limits = thresholds["performance"]
    chunk_limits = thresholds["chunking"]

    real = _read_json(STAGE / "real_pipeline_probe.json")
    retrieval = _read_json(STAGE / "retrieval_probe.json")
    fallback = _read_json(STAGE / "fallback_probe.json")
    paddle = _read_json(STAGE / "paddle_smoke.json")
    storage = ROOT / Path(real["storage"])
    scenarios = real.get("scenario_results", [])

    source_coverages: list[float] = []
    semantic_coverages: list[float] = []
    semantic_counts: list[int] = []
    page_scores: list[float] = []
    document_scores: list[float] = []
    missing_page_count = 0
    pending_blocks = 0
    total_blocks = 0
    invalid_ratios: list[float] = []
    duplicate_ratios: list[float] = []
    mineru_attempt_seconds: list[float] = []
    progress_gaps: list[float] = []
    observed_types: set[str] = set()
    all_artifacts_present = True
    parser_reports_complete = True
    no_mock_semantic_content = True

    for scenario in scenarios:
        source_coverages.append(float(scenario.get("source_page_coverage", 0.0)))
        semantic_coverages.append(float(scenario.get("usable_semantic_page_coverage", 0.0)))
        semantic_counts.extend(int(value) for value in scenario.get("semantic_characters_by_page", []))
        scenario_scores = [float(value) for value in scenario.get("page_quality_scores", [])]
        page_scores.extend(scenario_scores)
        document_scores.append(sum(scenario_scores) / len(scenario_scores) if scenario_scores else 0.0)
        missing_page_count += len(scenario.get("missing_pages", []))
        pending_blocks += int(scenario.get("ocr_pending_blocks", 0))
        all_artifacts_present = all_artifacts_present and all(scenario.get("artifacts_present", {}).values())
        for attempt in scenario.get("attempts", []):
            parser_reports_complete = parser_reports_complete and all(
                field in attempt for field in ("parser", "scope", "status", "duration_ms", "error_code")
            )
            if attempt.get("parser") == "mineru" and attempt.get("status") in {"succeeded", "degraded"}:
                mineru_attempt_seconds.append(float(attempt["duration_ms"]) / 1000.0)
        previous = 0.0
        for event in scenario.get("progress", []):
            elapsed = float(event.get("elapsed_seconds", previous))
            progress_gaps.append(max(0.0, elapsed - previous))
            previous = elapsed

        artifact_path = storage / "books" / scenario["book_id"] / "artifacts"
        pages = json.loads((artifact_path / "pages.json").read_text(encoding="utf-8"))
        chunks = _read_jsonl(artifact_path / "chunks.jsonl")
        for page in pages:
            text = str(page.get("text", ""))
            invalid_ratios.append(_invalid_character_ratio(text))
            if str(page.get("ocr_provider", "")).lower() == "mock" and text.strip():
                no_mock_semantic_content = False
            for block in page.get("blocks", []):
                total_blocks += 1
                block_type = str(block.get("type", "")).lower()
                observed_types.add(block_type)
                if block_type == "ocr_pending":
                    pending_blocks += 1
                if str(block.get("source_parser", "")).lower() == "mock" and str(block.get("text", "")).strip():
                    no_mock_semantic_content = False
        normalized = [_normalized_text(str(chunk.get("text", ""))) for chunk in chunks]
        normalized = [text for text in normalized if text]
        duplicates = len(normalized) - len(set(normalized))
        duplicate_ratios.append(duplicates / len(normalized) if normalized else 0.0)

    expected_scenarios = set(real.get("scenarios_expected", []))
    observed_scenarios = {str(item.get("scenario")) for item in scenarios}
    mixed = next((item for item in scenarios if item.get("scenario") == "mixed"), {})
    fallback_results = {str(item.get("scenario")): item for item in fallback.get("results", [])}
    retrieval_results = retrieval.get("results", [])
    native_queries = [item for item in retrieval_results if item.get("scenario") == "native"]
    native_recall = (
        sum(bool(item.get("relevant_in_top5")) for item in native_queries) / len(native_queries)
        if native_queries
        else 0.0
    )
    metrics = retrieval.get("retrieval_metrics", {})

    min_source_coverage = min(source_coverages, default=0.0)
    min_semantic_coverage = min(semantic_coverages, default=0.0)
    min_semantic_chars = min(semantic_counts, default=0)
    min_page_score = min(page_scores, default=0.0)
    min_document_score = min(document_scores, default=0.0)
    pending_ratio = pending_blocks / total_blocks if total_blocks else 0.0
    max_invalid_ratio = max(invalid_ratios, default=0.0)
    max_duplicate_ratio = max(duplicate_ratios, default=0.0)
    mineru_p95 = _percentile(mineru_attempt_seconds, 0.95)
    max_progress_gap = max(progress_gaps, default=math.inf)
    pipeline_times = [float(item.get("elapsed_seconds", math.inf)) for item in scenarios]
    cold_pipeline = max(pipeline_times, default=math.inf)

    checks = {
        "probe_reports_pass": _check(
            [bool(real.get("passed")), bool(retrieval.get("passed")), bool(fallback.get("passed")), bool(paddle.get("passed"))],
            "all ==",
            True,
            all(bool(report.get("passed")) for report in (real, retrieval, fallback, paddle)),
        ),
        "scenario_set": _check(sorted(observed_scenarios), "==", sorted(expected_scenarios), observed_scenarios == expected_scenarios),
        "retrieval_pipeline_run": _check(
            retrieval.get("pipeline_run_id"), "==", real.get("run_id"), retrieval.get("pipeline_run_id") == real.get("run_id")
        ),
        "valid_fixture_task_success_rate": _check(
            real.get("valid_fixture_task_success_rate", 0.0),
            ">=",
            parser_limits["valid_fixture_task_success_rate_min"],
            float(real.get("valid_fixture_task_success_rate", 0.0)) >= parser_limits["valid_fixture_task_success_rate_min"],
        ),
        "source_page_coverage": _check(min_source_coverage, ">=", parser_limits["source_page_coverage_min"], min_source_coverage >= parser_limits["source_page_coverage_min"]),
        "usable_semantic_page_coverage": _check(min_semantic_coverage, ">=", parser_limits["usable_semantic_page_coverage_min"], min_semantic_coverage >= parser_limits["usable_semantic_page_coverage_min"]),
        "ocr_pending_ratio": _check(pending_ratio, "<=", parser_limits["ocr_pending_ratio_max"], pending_ratio <= parser_limits["ocr_pending_ratio_max"]),
        "invalid_character_ratio": _check(max_invalid_ratio, "<=", parser_limits["invalid_character_ratio_max"], max_invalid_ratio <= parser_limits["invalid_character_ratio_max"]),
        "document_quality_score": _check(min_document_score, ">=", parser_limits["document_quality_score_min"], min_document_score >= parser_limits["document_quality_score_min"]),
        "page_quality_score": _check(min_page_score, ">=", parser_limits["page_quality_score_min"], min_page_score >= parser_limits["page_quality_score_min"]),
        "usable_semantic_characters": _check(min_semantic_chars, ">=", parser_limits["usable_semantic_characters_min"], min_semantic_chars >= parser_limits["usable_semantic_characters_min"]),
        "missing_page_count": _check(missing_page_count, "<=", parser_limits["missing_page_count_max"], missing_page_count <= parser_limits["missing_page_count_max"]),
        "exact_duplicate_ratio": _check(max_duplicate_ratio, "<=", chunk_limits["exact_duplicate_ratio_max_frozen_fixtures"], max_duplicate_ratio <= chunk_limits["exact_duplicate_ratio_max_frozen_fixtures"]),
        "recall_at_5": _check(metrics.get("recall_at_5", 0.0), ">=", retrieval_limits["recall_at_5_min"], float(metrics.get("recall_at_5", 0.0)) >= retrieval_limits["recall_at_5_min"]),
        "citation_page_accuracy_over_all_queries": _check(metrics.get("citation_page_accuracy_over_all_queries", 0.0), ">=", retrieval_limits["citation_page_accuracy_over_all_queries_min"], float(metrics.get("citation_page_accuracy_over_all_queries", 0.0)) >= retrieval_limits["citation_page_accuracy_over_all_queries_min"]),
        "citation_page_accuracy_conditional_on_retrieval": _check(metrics.get("citation_page_accuracy_conditional_on_retrieval", 0.0), ">=", retrieval_limits["citation_page_accuracy_conditional_on_retrieval_min"], float(metrics.get("citation_page_accuracy_conditional_on_retrieval", 0.0)) >= retrieval_limits["citation_page_accuracy_conditional_on_retrieval_min"]),
        "query_result_coverage": _check(metrics.get("query_result_coverage", 0.0), ">=", retrieval_limits["query_result_coverage_min"], float(metrics.get("query_result_coverage", 0.0)) >= retrieval_limits["query_result_coverage_min"]),
        "native_text_subset_recall_at_5": _check(native_recall, ">=", retrieval_limits["native_text_subset_recall_at_5_min"], native_recall >= retrieval_limits["native_text_subset_recall_at_5_min"]),
        "frozen_query_count": _check(retrieval.get("query_count", 0), "==", retrieval_limits["frozen_query_count"], int(retrieval.get("query_count", 0)) == retrieval_limits["frozen_query_count"]),
        "warm_mineru_p95_seconds": _check(round(mineru_p95, 3), "<=", performance_limits["warm_mineru_fixture_p95_seconds_max"], mineru_p95 <= performance_limits["warm_mineru_fixture_p95_seconds_max"]),
        "cold_pipeline_seconds": _check(cold_pipeline, "<=", performance_limits["cold_pipeline_seconds_max"], cold_pipeline <= performance_limits["cold_pipeline_seconds_max"]),
        "warm_pipeline_p95_seconds": _check(real.get("warm_pipeline_p95_seconds"), "<=", performance_limits["warm_cloudpath_parse_chunk_index_p95_seconds_max"], float(real.get("warm_pipeline_p95_seconds", math.inf)) <= performance_limits["warm_cloudpath_parse_chunk_index_p95_seconds_max"]),
        "gpu_peak_memory_used_mib": _check(real.get("gpu", {}).get("peak_memory_used_mib"), "<=", performance_limits["gpu_peak_memory_used_mib_max"], int(real.get("gpu", {}).get("peak_memory_used_mib", math.inf)) <= performance_limits["gpu_peak_memory_used_mib_max"]),
        "gpu_oom_count": _check(real.get("gpu", {}).get("oom_count"), "<=", performance_limits["gpu_oom_count_max"], int(real.get("gpu", {}).get("oom_count", math.inf)) <= performance_limits["gpu_oom_count_max"]),
        "progress_update_interval_seconds": _check(round(max_progress_gap, 3), "<=", performance_limits["progress_update_interval_seconds_max"], max_progress_gap <= performance_limits["progress_update_interval_seconds_max"]),
        "normalized_artifacts": _check(all_artifacts_present, "==", True, all_artifacts_present),
        "parser_reports_complete": _check(parser_reports_complete, "==", True, parser_reports_complete),
        "mixed_pdf_page_routing": _check(mixed.get("parser_by_page"), "==", {"1": "mineru", "2": "ocr"}, mixed.get("parser_by_page") == {"1": "mineru", "2": "ocr"}),
        "offline_native_fallback": _check(fallback_results.get("native", {}).get("page_parsers"), "==", ["pymupdf", "pymupdf"], fallback_results.get("native", {}).get("page_parsers") == ["pymupdf", "pymupdf"]),
        "offline_scanned_fallback": _check(fallback_results.get("scanned", {}).get("page_parsers"), "==", ["ocr"], fallback_results.get("scanned", {}).get("page_parsers") == ["ocr"] and fallback_results.get("scanned", {}).get("ocr_providers") == ["paddleocr"]),
        "offline_image_fallback": _check(fallback_results.get("image", {}).get("page_parsers"), "==", ["ocr"], fallback_results.get("image", {}).get("page_parsers") == ["ocr"] and fallback_results.get("image", {}).get("ocr_providers") == ["paddleocr"]),
        "real_paddleocr": _check({"provider": paddle.get("provider"), "semantic_characters": paddle.get("semantic_characters")}, "real and usable", {"provider": "paddleocr", "semantic_characters_min": parser_limits["usable_semantic_characters_min"]}, paddle.get("provider") == "paddleocr" and int(paddle.get("semantic_characters", 0)) >= parser_limits["usable_semantic_characters_min"]),
        "mock_content_excluded": _check(no_mock_semantic_content, "==", True, no_mock_semantic_content),
        "structured_mapping_evidence": _check(sorted(observed_types), "contains", ["title", "paragraph", "table", "ocr_text"], {"title", "paragraph", "table", "ocr_text"}.issubset(observed_types)),
    }
    failures = [name for name, result in checks.items() if not result["passed"]]
    payload = {
        "schema_version": 1,
        "metric_protocol_version": thresholds["metric_protocol_version"],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_run_id": real.get("run_id"),
        "fallback_run_id": fallback.get("run_id"),
        "checks": checks,
        "failures": failures,
        "passed": not failures,
    }
    _atomic_write_json(OUTPUT_PATH, payload)
    print(json.dumps({"passed": payload["passed"], "checks": len(checks), "failures": failures}))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
