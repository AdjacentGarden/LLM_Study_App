from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
STAGE = Path(__file__).resolve().parent
SOURCES = {
    "thresholds": ROOT / "quality/stage0/acceptance_thresholds.json",
    "tokenizer": STAGE / "tokenizer_probe.json",
    "golden": STAGE / "chunker_golden.json",
    "real_v2": STAGE / "real_v2_probe.json",
    "retrieval": ROOT / "quality/stage2/retrieval_probe.json",
    "tests": STAGE / "test_results.json",
}


def _atomic_write(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _digest(path: Path) -> str | None:
    return sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _all_true(value: object) -> bool:
    return (
        isinstance(value, dict)
        and bool(value)
        and all(
            item is True or (isinstance(item, dict) and item.get("passed") is True)
            for item in value.values()
        )
    )


def _empty_failures(value: object) -> bool:
    return isinstance(value, list) and not value


def _as_float(value: object, default: float = -1.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _real_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for name in ("real_books", "scenario_results", "books", "results"):
        value = payload.get(name)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _scenario_name(record: dict[str, Any]) -> str:
    return str(record.get("scenario") or record.get("name") or record.get("fixture") or "")


def _real_record_passes(record: dict[str, Any]) -> bool:
    if record.get("passed") is not True:
        return False
    if "failures" not in record or not _empty_failures(record.get("failures")):
        return False
    checks = record.get("checks")
    return _all_true(checks)


def _load_golden_chunks(golden: dict[str, Any]) -> tuple[list[dict[str, Any]], Path | None]:
    run_id = str(golden.get("run_id") or "")
    path = STAGE / "runs" / run_id / "artifacts" / "chunks_v2.jsonl"
    if not run_id or not path.exists():
        return [], None
    chunks: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                chunks.append(value)
    return chunks, path


def _quality_protocol_matches(chunks: list[dict[str, Any]], thresholds: dict[str, Any]) -> bool:
    formula = thresholds.get("quality_score_formula", {})
    expected_weights = {
        "valid_character_score": formula.get("valid_character_score_weight"),
        "content_coverage_score": formula.get("content_coverage_score_weight"),
        "ocr_confidence_score": formula.get("ocr_confidence_score_weight"),
        "mapping_completeness_score": formula.get("mapping_completeness_score_weight"),
        "deduplication_score": formula.get("deduplication_score_weight"),
    }
    if not chunks or any(value is None for value in expected_weights.values()):
        return False
    for chunk in chunks:
        metadata = chunk.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("quality_formula_version") != formula.get("version"):
            return False
        components = metadata.get("quality_components")
        if not isinstance(components, dict) or components.get("weights") != expected_weights:
            return False
        recomputed = sum(
            _as_float(components.get(name), -10.0) * _as_float(weight, -10.0)
            for name, weight in expected_weights.items()
        )
        if abs(_as_float(chunk.get("quality_score")) - recomputed) > 0.000002:
            return False
    return True


def main() -> int:
    loaded: dict[str, dict[str, Any]] = {}
    source_errors: dict[str, str] = {}
    for name, path in SOURCES.items():
        try:
            loaded[name] = _load(path)
        except Exception as exc:
            loaded[name] = {}
            source_errors[name] = exc.__class__.__name__

    thresholds = loaded["thresholds"]
    tokenizer = loaded["tokenizer"]
    golden = loaded["golden"]
    real_v2 = loaded["real_v2"]
    retrieval = loaded["retrieval"]
    tests = loaded["tests"]
    chunk_thresholds = thresholds.get("chunking", {})
    retrieval_thresholds = thresholds.get("retrieval", {})
    golden_checks = golden.get("checks", {}) if isinstance(golden.get("checks"), dict) else {}
    test_summary = tests.get("summary", {}) if isinstance(tests.get("summary"), dict) else {}
    metrics = retrieval.get("retrieval_metrics", {}) if isinstance(retrieval.get("retrieval_metrics"), dict) else {}
    real_records = _real_records(real_v2)
    golden_chunks, golden_chunks_path = _load_golden_chunks(golden)

    checks: list[dict[str, Any]] = []

    def add(identifier: str, criterion: str, passed: bool, actual: object, expected: object, evidence: list[str]) -> None:
        checks.append(
            {
                "id": identifier,
                "criterion": criterion,
                "passed": bool(passed),
                "actual": actual,
                "expected": expected,
                "evidence": evidence,
            }
        )

    protocol_values = {
        "target_tokens": chunk_thresholds.get("target_tokens"),
        "max_tokens": chunk_thresholds.get("max_tokens"),
        "min_tokens": chunk_thresholds.get("min_tokens"),
        "overlap_tokens": chunk_thresholds.get("overlap_tokens"),
        "atomic_hard_max_tokens": chunk_thresholds.get("atomic_content_hard_max_tokens"),
        "quality_threshold": chunk_thresholds.get("chunk_quality_threshold"),
    }
    add(
        "stage3_01_normal_chunk_sizes",
        "普通正文符合目标、最大和最小 token 规则",
        golden_checks.get("standard_max") is True
        and golden_checks.get("short_tail_protocol") is True
        and all(golden.get("config", {}).get(key) == value for key, value in protocol_values.items()),
        {"config": golden.get("config"), "short_unexplained": golden.get("short_unexplained")},
        protocol_values,
        ["chunker_golden.json"],
    )
    overlap_ratio = _as_float(golden.get("overlap_duplicate_token_ratio"))
    duplicate_ratio = _as_float(golden.get("exact_duplicate_count")) / max(1, int(golden.get("v2_chunk_count") or 0))
    add(
        "stage3_02_overlap_and_duplicates",
        "相邻 overlap、精确重复率和 overlap 重复率符合冻结阈值",
        golden_checks.get("exact_duplicate_ratio") is True
        and golden_checks.get("overlap_ratio") is True
        and golden_checks.get("per_chunk_overlap_protocol") is True
        and duplicate_ratio <= _as_float(chunk_thresholds.get("exact_duplicate_ratio_max_frozen_fixtures"))
        and overlap_ratio <= _as_float(chunk_thresholds.get("overlap_duplicate_token_ratio_max")),
        {"exact_duplicate_ratio": duplicate_ratio, "overlap_duplicate_token_ratio": overlap_ratio},
        {
            "exact_duplicate_ratio_max": chunk_thresholds.get("exact_duplicate_ratio_max_frozen_fixtures"),
            "overlap_duplicate_token_ratio_max": chunk_thresholds.get("overlap_duplicate_token_ratio_max"),
        },
        ["chunker_golden.json"],
    )
    tokenizer_matches = (
        tokenizer.get("passed") is True
        and tokenizer.get("model") == chunk_thresholds.get("tokenizer_model")
        and tokenizer.get("revision") == chunk_thresholds.get("tokenizer_revision")
        and tokenizer.get("transformers_version") == chunk_thresholds.get("transformers_version")
        and tokenizer.get("tokenizers_version") == chunk_thresholds.get("tokenizers_version")
        and golden_checks.get("token_counts_exact") is True
    )
    add(
        "stage3_03_frozen_tokenizer",
        "所有 token 数使用冻结 tokenizer、revision、依赖和完整 embedding 文本",
        tokenizer_matches,
        {
            key: tokenizer.get(key)
            for key in ("model", "revision", "transformers_version", "tokenizers_version", "passed")
        },
        {
            "model": chunk_thresholds.get("tokenizer_model"),
            "revision": chunk_thresholds.get("tokenizer_revision"),
            "transformers_version": chunk_thresholds.get("transformers_version"),
            "tokenizers_version": chunk_thresholds.get("tokenizers_version"),
        },
        ["tokenizer_probe.json", "chunker_golden.json"],
    )
    add(
        "stage3_04_tail_and_atomic_limits",
        "短尾、表格和公式超限行为符合冻结协议",
        golden_checks.get("short_tail_protocol") is True and golden_checks.get("atomic_hard_max") is True,
        {"token_max": golden.get("token_max"), "warnings": golden.get("warnings")},
        {"atomic_hard_max": chunk_thresholds.get("atomic_content_hard_max_tokens")},
        ["chunker_golden.json", "test_results.json"],
    )
    add(
        "stage3_05_quality_formula",
        "quality score 按 quality-v1 冻结公式计算并记录版本",
        _quality_protocol_matches(golden_chunks, thresholds),
        {"chunk_count_checked": len(golden_chunks)},
        {"formula": thresholds.get("quality_score_formula")},
        ["chunks_v2.jsonl", "acceptance_thresholds.json"],
    )
    add(
        "stage3_06_heading_embedding",
        "标题路径同时进入 embedding 文本和 metadata",
        golden_checks.get("heading_in_embedding") is True,
        golden_checks.get("heading_in_embedding"),
        True,
        ["chunker_golden.json"],
    )
    add(
        "stage3_07_cross_page_ranges",
        "chunk 可跨页且页码范围准确",
        golden_checks.get("cross_page") is True,
        golden_checks.get("cross_page"),
        True,
        ["chunker_golden.json", "test_results.json"],
    )
    add(
        "stage3_08_table_headers",
        "大表格拆分后每段保留表头",
        golden_checks.get("table_split_with_header") is True,
        golden_checks.get("table_split_with_header"),
        True,
        ["chunker_golden.json"],
    )
    add(
        "stage3_09_formula_semantics",
        "公式保留 LaTeX 和附近语义",
        golden_checks.get("formula_latex_and_context") is True,
        golden_checks.get("formula_latex_and_context"),
        True,
        ["chunker_golden.json"],
    )
    add(
        "stage3_10_asset_binding",
        "图片与表格 chunk 正确双向绑定 Asset",
        golden_checks.get("figure_asset_binding") is True
        and golden_checks.get("table_asset_binding") is True
        and golden_checks.get("asset_links_bidirectional") is True,
        {
            "asset_count": golden.get("asset_count"),
            "asset_source_link_count": golden.get("asset_source_link_count"),
        },
        "all asset relationships bidirectional",
        ["chunker_golden.json"],
    )
    add(
        "stage3_11_furniture_filter",
        "页眉、页脚和纯页码不进入普通正文",
        golden_checks.get("furniture_filtered") is True,
        golden_checks.get("furniture_filtered"),
        True,
        ["chunker_golden.json"],
    )
    add(
        "stage3_12_quality_quarantine",
        "低质量内容默认不参与检索",
        golden_checks.get("quality_quarantine") is True
        and int(golden.get("v2_quarantined_count") or 0) >= 1
        and test_summary.get("stage3_failed") == 0,
        {
            "quarantined": golden.get("v2_quarantined_count"),
            "stage3_failed": test_summary.get("stage3_failed"),
        },
        {"quarantined_min": 1, "stage3_failed": 0},
        ["chunker_golden.json", "test_results.json"],
    )
    add(
        "stage3_13_stable_ids",
        "相同输入和配置重复解析产生稳定 chunk ID",
        golden_checks.get("stable_chunk_ids") is True,
        golden_checks.get("stable_chunk_ids"),
        True,
        ["chunker_golden.json"],
    )
    lesson_test_present = any(
        str(path).replace("\\", "/").endswith("test_stage3_lesson_v2_e2e.py")
        for path in tests.get("stage3_test_files", [])
    )
    add(
        "stage3_14_lesson_v2_consumer",
        "课程生成可端到端读取 Chunk V2",
        lesson_test_present and test_summary.get("stage3_failed") == 0 and int(test_summary.get("stage3_passed") or 0) > 0,
        {"lesson_test_present": lesson_test_present, "stage3_passed": test_summary.get("stage3_passed")},
        {"lesson_test_present": True, "stage3_failed": 0},
        ["test_results.json"],
    )

    expected_scenarios = {"native", "complex", "mixed", "scanned", "image"}
    actual_scenarios = {_scenario_name(record) for record in real_records}
    add(
        "stage3_15_real_v2_contract",
        "真实 V2 五场景逐本检查全部通过",
        real_v2.get("passed") is True
        and _empty_failures(real_v2.get("failures"))
        and _all_true(real_v2.get("checks"))
        and actual_scenarios == expected_scenarios
        and len(real_records) == 5
        and all(_real_record_passes(record) for record in real_records),
        {
            "passed": real_v2.get("passed"),
            "scenarios": sorted(actual_scenarios),
            "record_count": len(real_records),
            "failures": real_v2.get("failures"),
        },
        {"passed": True, "scenarios": sorted(expected_scenarios), "record_count": 5, "failures": []},
        ["real_v2_probe.json"],
    )
    add(
        "stage3_16_real_v2_aggregate",
        "真实 V2 聚合指标无缺失且通过",
        isinstance(real_v2.get("aggregate"), dict)
        and bool(real_v2.get("aggregate"))
        and int(real_v2.get("aggregate", {}).get("scenario_count") or 0) == 5
        and int(real_v2.get("aggregate", {}).get("query_count") or 0)
        == int(retrieval_thresholds.get("frozen_query_count") or -1)
        and int(real_v2.get("aggregate", {}).get("chunk_count") or 0) > 0
        and _as_float(real_v2.get("aggregate", {}).get("token_max"))
        <= _as_float(chunk_thresholds.get("atomic_content_hard_max_tokens"))
        and int(real_v2.get("aggregate", {}).get("exact_duplicate_count_within_books") or 0) == 0
        and _as_float(real_v2.get("aggregate", {}).get("overlap_duplicate_token_ratio"))
        <= _as_float(chunk_thresholds.get("overlap_duplicate_token_ratio_max")),
        real_v2.get("aggregate"),
        {
            "scenario_count": 5,
            "query_count": retrieval_thresholds.get("frozen_query_count"),
            "chunk_count_min": 1,
            "token_max": chunk_thresholds.get("atomic_content_hard_max_tokens"),
            "exact_duplicate_count": 0,
            "overlap_ratio_max": chunk_thresholds.get("overlap_duplicate_token_ratio_max"),
        },
        ["real_v2_probe.json"],
    )
    retrieval_passed = (
        retrieval.get("passed") is True
        and int(retrieval.get("query_count") or 0) == int(retrieval_thresholds.get("frozen_query_count") or -1)
        and _as_float(metrics.get("recall_at_5")) >= _as_float(retrieval_thresholds.get("recall_at_5_min"))
        and _as_float(metrics.get("citation_page_accuracy_over_all_queries"))
        >= _as_float(retrieval_thresholds.get("citation_page_accuracy_over_all_queries_min"))
        and _as_float(metrics.get("citation_page_accuracy_conditional_on_retrieval"))
        >= _as_float(retrieval_thresholds.get("citation_page_accuracy_conditional_on_retrieval_min"))
        and _as_float(metrics.get("query_result_coverage"))
        >= _as_float(retrieval_thresholds.get("query_result_coverage_min"))
    )
    add(
        "stage3_17_retrieval_regression",
        "冻结 8 问检索与引用指标达到阈值",
        retrieval_passed,
        {"query_count": retrieval.get("query_count"), "metrics": metrics},
        {"query_count": retrieval_thresholds.get("frozen_query_count"), "thresholds": retrieval_thresholds},
        ["retrieval_probe.json"],
    )
    add(
        "stage3_18_backend_regression",
        "后端全量测试无回归",
        test_summary.get("all_passed") is True
        and int(test_summary.get("backend_full_passed") or 0) > 0
        and test_summary.get("backend_full_failed") == 0,
        {
            "passed": test_summary.get("backend_full_passed"),
            "failed": test_summary.get("backend_full_failed"),
            "skipped": test_summary.get("backend_full_skipped"),
        },
        {"failed": 0},
        ["test_results.json"],
    )
    add(
        "stage3_19_stage3_regression",
        "阶段 3 专项测试无失败",
        int(test_summary.get("stage3_passed") or 0) > 0 and test_summary.get("stage3_failed") == 0,
        {"passed": test_summary.get("stage3_passed"), "failed": test_summary.get("stage3_failed")},
        {"failed": 0},
        ["test_results.json"],
    )
    add(
        "stage3_20_frontend_regression",
        "前端测试、Lint 和生产构建全部通过",
        int(test_summary.get("frontend_tests_passed") or 0) > 0
        and test_summary.get("frontend_lint_passed") is True
        and test_summary.get("frontend_build_passed") is True,
        {
            "tests": test_summary.get("frontend_tests_passed"),
            "lint": test_summary.get("frontend_lint_passed"),
            "build": test_summary.get("frontend_build_passed"),
        },
        {"tests_min": 1, "lint": True, "build": True},
        ["test_results.json"],
    )

    failures = [item["id"] for item in checks if not item["passed"]]
    payload = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "metric_protocol_version": thresholds.get("metric_protocol_version"),
        "source_files": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": _digest(path)}
            for name, path in SOURCES.items()
        },
        "additional_evidence": {
            "golden_chunks": {
                "path": str(golden_chunks_path.relative_to(ROOT)) if golden_chunks_path else None,
                "sha256": _digest(golden_chunks_path) if golden_chunks_path else None,
            }
        },
        "source_errors": source_errors,
        "check_count": len(checks),
        "checks": checks,
        "failures": failures,
        "passed": not source_errors and not failures,
    }
    _atomic_write(STAGE / "acceptance_evaluation.json", payload)
    print(json.dumps({"passed": payload["passed"], "check_count": len(checks), "failures": failures}))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
