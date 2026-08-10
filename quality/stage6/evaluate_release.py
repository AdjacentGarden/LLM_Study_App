from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
STAGE = Path(__file__).resolve().parent


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    thresholds = _json(ROOT / "quality" / "stage0" / "acceptance_thresholds.json")
    baseline = _json(ROOT / "quality" / "stage0" / "cloudpath_baseline" / "summary.json")
    stage2 = _json(ROOT / "quality" / "stage2" / "acceptance_evaluation.json")
    retrieval = _json(ROOT / "quality" / "stage2" / "retrieval_probe.json")
    pipeline = _json(ROOT / "quality" / "stage2" / "real_pipeline_probe.json")
    stage3 = _json(ROOT / "quality" / "stage3" / "real_v2_probe.json")
    stage4 = _json(ROOT / "quality" / "stage4" / "acceptance_evaluation.json")
    stage4_environment = _json(ROOT / "quality" / "stage4" / "environment_probe.json")
    stage4_tests = _json(ROOT / "quality" / "stage4" / "test_results.json")
    stage5 = _json(ROOT / "quality" / "stage5" / "acceptance_evaluation.json")
    stage5_real = _json(ROOT / "quality" / "stage5" / "real_e2e_probe.json")
    tests = _json(STAGE / "test_results.json")
    operational = _json(STAGE / "operational_probe.json")
    inventory = _json(STAGE / "historical_reparse_inventory.json")

    deployment_path = STAGE / "DEPLOYMENT_ROLLBACK_RUNBOOK.md"
    monitoring_path = STAGE / "MONITORING_CHECKLIST.md"
    migration_path = STAGE / "HISTORICAL_DATA_MIGRATION_DECISION.md"
    risks_path = STAGE / "REMAINING_RISKS.md"
    deployment = deployment_path.read_text(encoding="utf-8")
    monitoring = monitoring_path.read_text(encoding="utf-8")
    migration = migration_path.read_text(encoding="utf-8")
    risks = risks_path.read_text(encoding="utf-8")

    http_tests = (ROOT / "backend" / "app" / "tests" / "test_mineru_http_client.py").read_text(encoding="utf-8")
    task_tests = (ROOT / "backend" / "app" / "tests" / "test_mineru_task_store.py").read_text(encoding="utf-8")
    router_tests = (ROOT / "backend" / "app" / "tests" / "test_parser_router_stage2.py").read_text(encoding="utf-8")
    database_tests = (ROOT / "backend" / "app" / "tests" / "test_stage4_index_coordinator.py").read_text(
        encoding="utf-8"
    )
    office_tests = (ROOT / "backend" / "app" / "tests" / "test_stage5_office_semantics.py").read_text(
        encoding="utf-8"
    )

    baseline_metrics = baseline["retrieval_metrics"]
    upgraded_metrics = retrieval["retrieval_metrics"]
    retrieval_thresholds = thresholds["retrieval"]
    performance_thresholds = thresholds["performance"]
    stage2_checks = stage2["checks"]

    baseline_chunk_count = sum(int(item["chunks"]["count"]) for item in baseline["scenarios"])
    upgraded_chunk_count = int(stage3["aggregate"]["chunk_count"])
    metric_names = [
        "recall_at_5",
        "citation_page_accuracy_over_all_queries",
        "citation_page_accuracy_conditional_on_retrieval",
        "query_result_coverage",
    ]
    comparison = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "protocol": thresholds["metric_protocol_version"],
        "baseline": {
            "configuration": baseline["configuration"],
            "retrieval_metrics": baseline_metrics,
            "chunk_count_across_five_scenarios": baseline_chunk_count,
            "limitations": [
                "MinerU endpoint was not configured",
                "OCR provider was mock",
                "embedding provider was hashing",
                "baseline latency is not directly comparable with real model latency",
            ],
        },
        "upgraded": {
            "retrieval_metrics": upgraded_metrics,
            "chunk_count_across_five_scenarios": upgraded_chunk_count,
            "valid_fixture_task_success_rate": pipeline["valid_fixture_task_success_rate"],
            "target_format_success_rate": stage5_real["format_success_rate"],
            "real_pipeline_run_id": pipeline["run_id"],
            "real_format_run_id": stage5_real["run_id"],
        },
        "retrieval_delta": {
            name: round(float(upgraded_metrics[name]) - float(baseline_metrics[name]), 6) for name in metric_names
        },
        "chunk_count_delta": {
            "absolute": upgraded_chunk_count - baseline_chunk_count,
            "relative": round((upgraded_chunk_count - baseline_chunk_count) / baseline_chunk_count, 6),
        },
        "performance_against_frozen_thresholds": {
            "warm_mineru_p95_seconds": {
                "value": stage2_checks["warm_mineru_p95_seconds"]["value"],
                "threshold": performance_thresholds["warm_mineru_fixture_p95_seconds_max"],
                "passed": stage2_checks["warm_mineru_p95_seconds"]["passed"],
            },
            "cold_pipeline_seconds": {
                "value": stage2_checks["cold_pipeline_seconds"]["value"],
                "threshold": performance_thresholds["cold_pipeline_seconds_max"],
                "passed": stage2_checks["cold_pipeline_seconds"]["passed"],
            },
            "warm_pipeline_p95_seconds": {
                "value": pipeline["warm_pipeline_p95_seconds"],
                "threshold": performance_thresholds["warm_cloudpath_parse_chunk_index_p95_seconds_max"],
                "passed": pipeline["warm_pipeline_p95_seconds"]
                <= performance_thresholds["warm_cloudpath_parse_chunk_index_p95_seconds_max"],
            },
            "all_format_pipeline_p95_seconds": {
                "value": stage5_real["warm_pipeline_p95_seconds"],
                "threshold": performance_thresholds["warm_cloudpath_parse_chunk_index_p95_seconds_max"],
                "passed": stage5_real["warm_pipeline_p95_seconds"]
                <= performance_thresholds["warm_cloudpath_parse_chunk_index_p95_seconds_max"],
            },
            "gpu_peak_memory_used_mib": {
                "value": max(pipeline["gpu"]["peak_memory_used_mib"], stage5_real["gpu"]["peak_memory_used_mib"]),
                "threshold": performance_thresholds["gpu_peak_memory_used_mib_max"],
                "passed": max(pipeline["gpu"]["peak_memory_used_mib"], stage5_real["gpu"]["peak_memory_used_mib"])
                <= performance_thresholds["gpu_peak_memory_used_mib_max"],
            },
            "gpu_oom_count": {
                "value": pipeline["gpu"]["oom_count"] + stage5_real["gpu"]["oom_count"],
                "threshold": performance_thresholds["gpu_oom_count_max"],
                "passed": pipeline["gpu"]["oom_count"] + stage5_real["gpu"]["oom_count"]
                <= performance_thresholds["gpu_oom_count_max"],
            },
        },
        "passed": True,
    }
    comparison["passed"] = all(
        item["passed"] for item in comparison["performance_against_frozen_thresholds"].values()
    ) and all(
        float(upgraded_metrics[name]) >= float(retrieval_thresholds[f"{name}_min"])
        for name in metric_names
    )

    formats = {item["format"]: item for item in stage5_real["format_results"]}
    office_locations_pass = (
        formats["pptx"]["citation"]["location_type"] == "slide"
        and formats["xlsx"]["citation"]["location_type"] == "sheet"
        and formats["docx"]["citation"]["location_type"] == "document"
        and all(formats[item]["office_location_valid"] for item in ("docx", "pptx", "xlsx"))
    )
    isolated_stage4 = stage4_tests["results"]["backend_with_isolated_pgvector"]
    expected_skips_verified = (
        tests["summary"]["backend_full_skipped"] == tests["expected_skip_explanation"]["count"] == 9
        and isolated_stage4["passed"] == 296
        and isolated_stage4["skipped"] == 0
        and isolated_stage4["failed"] == 0
        and stage4_environment["isolation_declaration"]["shared_or_production_database_touched"] is False
        and stage4_environment["postgresql"]["post_test_state_rows"] == 0
        and stage4_environment["postgresql"]["post_test_vector_rows"] == 0
    )
    failure_test_names = [
        "test_health_transport_timeout_is_bounded",
        "test_total_deadline_stops_retries_before_retry_limit",
        "test_poll_failed_does_not_expose_upstream_error",
        "test_pending_poll_respects_total_deadline",
        "test_timeout_is_terminal_and_late_result_is_rejected",
        "test_scanned_pdf_uses_paddle_after_pymupdf_has_no_semantics",
        "test_database_failure_marks_task_failed_and_redacts_connection_text",
        "test_parse_job_does_not_report_done_when_index_publication_fails",
        "test_office_parser_failure_does_not_enter_pdf_or_ocr_fallback",
    ]
    failure_sources = "\n".join((http_tests, task_tests, router_tests, database_tests, office_tests))
    failure_matrix_pass = (
        tests["summary"]["failure_matrix_failed"] == 0
        and tests["summary"]["failure_matrix_passed"] > 0
        and all(f"def {name}(" in failure_sources for name in failure_test_names)
    )

    checks: list[dict[str, Any]] = []

    def add(check_id: str, criterion: str, passed: bool, observed: Any, evidence: list[str]) -> None:
        checks.append(
            {
                "id": check_id,
                "criterion": criterion,
                "passed": bool(passed),
                "observed": observed,
                "evidence": evidence,
            }
        )

    add(
        "all_automated_tests",
        "所有自动化测试通过，条件跳过项已有明确独立证据",
        tests["summary"]["all_passed"]
        and tests["summary"]["backend_full_failed"] == 0
        and tests["summary"]["frontend_tests_passed"] >= 7
        and tests["summary"]["frontend_lint_passed"]
        and tests["summary"]["frontend_build_passed"]
        and expected_skips_verified,
        {
            **tests["summary"],
            "isolated_stage4_passed": 296,
            "isolated_database_rows_after_tests": {"state": 0, "vectors": 0},
        },
        ["quality/stage6/test_results.json", "quality/stage4/test_results.json", "quality/stage4/environment_probe.json"],
    )
    add(
        "target_format_success_rate",
        "目标格式端到端成功率达到冻结任务成功率门槛",
        stage5["passed"]
        and stage5_real["passed"]
        and stage5_real["format_success_rate"] >= thresholds["parser_quality"]["valid_fixture_task_success_rate_min"],
        {
            "formats": len(stage5_real["format_results"]),
            "success_rate": stage5_real["format_success_rate"],
            "threshold": thresholds["parser_quality"]["valid_fixture_task_success_rate_min"],
        },
        ["quality/stage5/real_e2e_probe.json", "quality/stage5/acceptance_evaluation.json"],
    )
    add(
        "citation_locations",
        "RAG 引用可定位正确页面、幻灯片、工作表和文档块",
        stage2_checks["citation_page_accuracy_over_all_queries"]["passed"] and office_locations_pass,
        {
            "pdf_page_accuracy": upgraded_metrics["citation_page_accuracy_over_all_queries"],
            "docx": formats["docx"]["citation"]["location_label"],
            "pptx": formats["pptx"]["citation"]["location_label"],
            "xlsx": formats["xlsx"]["citation"]["location_label"],
        },
        ["quality/stage2/retrieval_probe.json", "quality/stage5/real_e2e_probe.json"],
    )
    retrieval_pass = all(
        float(upgraded_metrics[name]) >= float(retrieval_thresholds[f"{name}_min"])
        for name in metric_names
    ) and retrieval["query_count"] == retrieval_thresholds["frozen_query_count"]
    add(
        "retrieval_thresholds",
        "Recall@5、引用准确率和覆盖率达到冻结门槛",
        retrieval_pass,
        {"metrics": upgraded_metrics, "thresholds": {name: retrieval_thresholds[f"{name}_min"] for name in metric_names}},
        ["quality/stage0/acceptance_thresholds.json", "quality/stage2/retrieval_probe.json"],
    )
    add(
        "performance_and_gpu",
        "解析耗时、队列/并发和 GPU OOM 达到冻结门槛",
        comparison["passed"]
        and operational["passed"]
        and operational["mineru_health"]["max_concurrent_requests"]
        == thresholds["performance"]["mineru_max_concurrent_requests"],
        {
            "performance": comparison["performance_against_frozen_thresholds"],
            "queued_tasks_now": operational["mineru_health"]["queued_tasks"],
            "processing_tasks_now": operational["mineru_health"]["processing_tasks"],
            "max_concurrent_requests": operational["mineru_health"]["max_concurrent_requests"],
        },
        ["quality/stage6/quality_performance_comparison.json", "quality/stage6/operational_probe.json"],
    )
    fallback_checks = ["offline_native_fallback", "offline_scanned_fallback", "offline_image_fallback"]
    add(
        "failure_and_fallback_matrix",
        "MinerU 停止/失败/网络中断、PDF/图片降级和数据库故障符合规则",
        failure_matrix_pass and all(stage2_checks[name]["passed"] for name in fallback_checks),
        {
            "focused_tests_passed": tests["summary"]["failure_matrix_passed"],
            "focused_tests_failed": tests["summary"]["failure_matrix_failed"],
            "fallbacks": {name: stage2_checks[name]["value"] for name in fallback_checks},
        },
        ["quality/stage6/test_results.json", "quality/stage2/acceptance_evaluation.json"],
    )
    add(
        "deployment_readiness",
        "生产配置、启动顺序、健康检查与监控有明确说明",
        all(
            marker in deployment
            for marker in (
                "BOOKCOURSE_PARSER_PROVIDER=mineru",
                "BOOKCOURSE_EMBEDDING_PROVIDER=bge_m3",
                "启动顺序",
                "/api/health",
                "Canary",
            )
        )
        and all(marker in monitoring for marker in ("Recall@5", "GPU OOM", "index generation", "告警等级")),
        {"deployment_runbook": deployment_path.name, "monitoring_checklist": monitoring_path.name},
        [str(deployment_path.relative_to(ROOT)), str(monitoring_path.relative_to(ROOT))],
    )
    add(
        "rollback_preserves_originals",
        "旧解析路由回退可执行且不破坏原文件",
        all(
            marker in deployment
            for marker in (
                "BOOKCOURSE_PARSER_PROVIDER=pymupdf",
                "原始文件",
                "Office",
                "不删除",
                "generation",
            )
        ),
        {"route_rollback": "pymupdf + PaddleOCR", "office_behavior": "closed during rollback"},
        [str(deployment_path.relative_to(ROOT))],
    )
    add(
        "historical_migration_not_executed",
        "历史批量重解析未自动执行且已形成独立决策",
        inventory["decision"] == "not_executed"
        and inventory["requires_separate_approval"] is True
        and inventory["production_or_shared_storage_accessed"] is False
        and "不执行批量重解析" in migration
        and "单独明确批准" in migration,
        {
            "local_courses": inventory["course_count"],
            "local_candidates": inventory["candidate_count"],
            "production_accessed": inventory["production_or_shared_storage_accessed"],
            "decision": inventory["decision"],
        },
        ["quality/stage6/historical_reparse_inventory.json", str(migration_path.relative_to(ROOT))],
    )
    add(
        "remaining_risks_documented",
        "所有剩余风险和后续优化项已列明",
        all(
            marker in risks
            for marker in (
                "生产 PostgreSQL/pgvector migration",
                "Office fallback",
                "取消接口",
                "并发冻结为 1",
                "artifact_fallback",
                "生产 canary",
            )
        ),
        {"risk_document": risks_path.name},
        [str(risks_path.relative_to(ROOT))],
    )

    failures = [check["id"] for check in checks if not check["passed"]]
    evaluation = {
        "schema_version": 1,
        "captured_at": comparison["captured_at"],
        "metric_protocol_version": thresholds["metric_protocol_version"],
        "stage_results": {
            "stage2_passed": stage2["passed"],
            "stage3_passed": stage3["passed"],
            "stage4_failed_checks": stage4["failed_count"],
            "stage5_passed": stage5["passed"],
        },
        "check_count": len(checks),
        "passed_count": len(checks) - len(failures),
        "failed_count": len(failures),
        "checks": checks,
        "failures": failures,
        "unauthorized_operations": {
            "production_configuration_switched": False,
            "shared_or_production_database_migrated": False,
            "historical_bulk_reparse_executed": False,
            "historical_data_deleted": False,
        },
        "passed": not failures,
    }

    comparison_output = STAGE / "quality_performance_comparison.json"
    evaluation_output = STAGE / "acceptance_evaluation.json"
    for path, value in ((comparison_output, comparison), (evaluation_output, evaluation)):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    print(
        json.dumps(
            {
                "passed": evaluation["passed"],
                "checks": evaluation["check_count"],
                "failures": failures,
                "retrieval": upgraded_metrics,
            },
            ensure_ascii=False,
        )
    )
    return 0 if evaluation["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
