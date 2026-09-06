from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "quality" / "stage5"
EXPECTED_FORMATS = [
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "jp2",
    "webp",
    "gif",
    "bmp",
    "tif",
    "tiff",
    "docx",
    "pptx",
    "xlsx",
]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    tests = _read_json(STAGE / "test_results.json")
    real = _read_json(STAGE / "real_e2e_probe.json")
    security_source = (ROOT / "backend" / "app" / "tests" / "test_stage5_upload_security.py").read_text(
        encoding="utf-8"
    )
    semantic_source = (ROOT / "backend" / "app" / "tests" / "test_stage5_office_semantics.py").read_text(
        encoding="utf-8"
    )
    e2e_source = (ROOT / "backend" / "app" / "tests" / "test_stage5_format_e2e.py").read_text(
        encoding="utf-8"
    )
    ocr_source = (ROOT / "backend" / "app" / "tests" / "test_phase5_ocr.py").read_text(encoding="utf-8")

    focused_passed = bool(
        tests["summary"]["all_passed"]
        and tests["summary"]["stage5_failed"] == 0
        and tests["summary"]["stage5_passed"] >= 70
    )
    source_text = "\n".join((security_source, semantic_source, e2e_source, ocr_source))

    def tests_pass(*test_names: str) -> bool:
        return focused_passed and all(f"def {name}(" in source_text for name in test_names)

    results = {item["format"]: item for item in real["format_results"]}
    real_formats_exact = real["formats_expected"] == EXPECTED_FORMATS and set(results) == set(EXPECTED_FORMATS)
    every_real_format_passed = real_formats_exact and all(results[item]["passed"] for item in EXPECTED_FORMATS)
    docx = results.get("docx", {})
    pptx = results.get("pptx", {})
    xlsx = results.get("xlsx", {})

    checks: list[dict[str, Any]] = []

    def add(
        check_id: str,
        criterion: str,
        passed: bool,
        evidence: list[str],
        observed: Any = None,
    ) -> None:
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
        "target_formats_uploadable",
        "全部目标格式可上传",
        every_real_format_passed
        and tests_pass("test_stage5_extension_contract_is_exact", "test_upload_api_accepts_every_target_format"),
        [
            "test_stage5_upload_security.py::test_stage5_extension_contract_is_exact",
            "test_stage5_upload_security.py::test_upload_api_accepts_every_target_format",
            "real_e2e_probe.json",
        ],
        sorted(results),
    )
    add(
        "legacy_and_unknown_rejected",
        ".doc/.ppt/.xls 与未知格式明确拒绝",
        tests_pass("test_stage5_extension_contract_is_exact"),
        ["test_stage5_upload_security.py::test_stage5_extension_contract_is_exact"],
        [".doc", ".ppt", ".xls", ".exe", ".zip", "no extension"],
    )
    add(
        "encrypted_documents_rejected",
        "加密 PDF 与密码 Office 文件明确拒绝",
        tests_pass("test_frozen_rejection_matrix", "test_compound_file_password_container_is_reported_as_encrypted_office"),
        [
            "test_stage5_upload_security.py::test_frozen_rejection_matrix[encrypted.pdf]",
            "test_stage5_upload_security.py::test_compound_file_password_container_is_reported_as_encrypted_office",
        ],
    )
    add(
        "frontend_backend_contract",
        "前端 accept 与后端白名单一致",
        tests_pass("test_stage5_extension_contract_is_exact")
        and tests["summary"]["frontend_tests_passed"] >= 7
        and tests["summary"]["frontend_lint_passed"]
        and tests["summary"]["frontend_build_passed"],
        [
            "test_stage5_upload_security.py::test_stage5_extension_contract_is_exact",
            "frontend/src/screens/shared.test.ts",
            "test_results.json",
        ],
    )
    add(
        "spoofing_rejected",
        "扩展名及 Office 内部类型伪造被拒绝",
        tests_pass("test_image_extension_spoofing_is_rejected", "test_office_internal_type_spoofing_is_rejected"),
        [
            "test_stage5_upload_security.py::test_image_extension_spoofing_is_rejected",
            "test_stage5_upload_security.py::test_office_internal_type_spoofing_is_rejected",
        ],
    )
    add(
        "corrupt_ooxml_rejected",
        "损坏 OOXML 被拒绝",
        tests_pass("test_frozen_rejection_matrix"),
        ["test_stage5_upload_security.py::test_frozen_rejection_matrix[spoofed.docx]"],
    )
    add(
        "zip_path_and_ratio",
        "ZIP 路径穿越及超高压缩比被拒绝",
        tests_pass("test_frozen_rejection_matrix", "test_traversal_directory_entry_and_symlink_are_rejected"),
        [
            "test_stage5_upload_security.py::test_frozen_rejection_matrix[path_traversal/high_compression_ratio]",
            "test_stage5_upload_security.py::test_traversal_directory_entry_and_symlink_are_rejected",
        ],
    )
    add(
        "ooxml_active_content_security",
        "OOXML 类型伪造、外部关系、XXE、嵌套包和加密容器被拒绝",
        tests_pass(
            "test_office_internal_type_spoofing_is_rejected",
            "test_external_relationship_is_rejected",
            "test_xxe_declaration_is_rejected_before_xml_parsing",
            "test_nested_archive_and_active_content_are_rejected",
            "test_compound_file_password_container_is_reported_as_encrypted_office",
        ),
        ["test_stage5_upload_security.py OOXML adversarial tests"],
    )
    add(
        "upload_byte_limits",
        "上传字节边界与超限行为均有测试",
        tests_pass("test_upload_byte_limit_accepts_boundary_and_rejects_overflow"),
        ["test_stage5_upload_security.py::test_upload_byte_limit_accepts_boundary_and_rejects_overflow"],
    )
    add(
        "image_pixel_limits",
        "图片像素边界与超限行为均有测试",
        tests_pass("test_image_pixel_limit_boundary_and_overflow"),
        ["test_stage5_upload_security.py::test_image_pixel_limit_boundary_and_overflow"],
    )
    add(
        "document_unit_limits",
        "PDF/PPTX/XLSX/DOCX 资源上限均有超限拒绝测试",
        tests_pass("test_pdf_page_limit_boundary_and_overflow", "test_pptx_xlsx_and_docx_resource_limits"),
        [
            "test_stage5_upload_security.py::test_pdf_page_limit_boundary_and_overflow",
            "test_stage5_upload_security.py::test_pptx_xlsx_and_docx_resource_limits",
        ],
    )
    add(
        "zip_resource_limits",
        "ZIP 条目、单项、总量与压缩比边界均已验证",
        tests_pass("test_zip_entry_single_total_and_ratio_limits"),
        ["test_stage5_upload_security.py::test_zip_entry_single_total_and_ratio_limits"],
    )
    add(
        "processing_timeouts",
        "MinerU/Office 总时限能终止等待并进入规定错误状态",
        tests_pass("test_office_validation_timeout_is_a_typed_failure")
        and tests["summary"]["backend_full_failed"] == 0,
        [
            "test_stage5_upload_security.py::test_office_validation_timeout_is_a_typed_failure",
            "backend/app/tests/test_mineru_http_client.py timeout/recovery coverage",
            "test_results.json backend full suite",
        ],
    )
    add(
        "gif_tiff_frozen_matrix",
        "GIF/TIFF/TIF 行为符合阶段 0 冻结矩阵",
        tests_pass("test_single_frame_gif_and_single_page_tiff_are_allowed", "test_frozen_rejection_matrix")
        and all(results[item]["passed"] for item in ("gif", "tif", "tiff")),
        [
            "quality/stage0/acceptance_thresholds.json format_decisions",
            "test_stage5_upload_security.py GIF/TIFF tests",
            "real_e2e_probe.json",
        ],
    )
    add(
        "pptx_citation_semantics",
        "PPTX 引用使用幻灯片语义",
        tests_pass("test_citation_labels_never_call_office_locations_pdf_pages")
        and pptx.get("citation", {}).get("location_type") == "slide"
        and "幻灯片" in pptx.get("citation", {}).get("location_label", ""),
        ["test_stage5_office_semantics.py citation test", "real_e2e_probe.json[pptx]"],
        pptx.get("citation", {}).get("location_label"),
    )
    add(
        "xlsx_citation_semantics",
        "XLSX 引用包含工作表或区域",
        tests_pass("test_citation_labels_never_call_office_locations_pdf_pages")
        and xlsx.get("citation", {}).get("location_type") == "sheet"
        and "工作表" in xlsx.get("citation", {}).get("location_label", "")
        and "A1:D5" in xlsx.get("citation", {}).get("location_label", ""),
        ["test_stage5_office_semantics.py citation test", "real_e2e_probe.json[xlsx]"],
        xlsx.get("citation", {}).get("location_label"),
    )
    add(
        "docx_citation_semantics",
        "DOCX 使用标题路径和块序号且不虚构页标签",
        tests_pass("test_citation_labels_never_call_office_locations_pdf_pages")
        and docx.get("citation", {}).get("location_type") == "document"
        and "块" in docx.get("citation", {}).get("location_label", "")
        and "页" not in docx.get("citation", {}).get("location_label", ""),
        ["test_stage5_office_semantics.py citation test", "real_e2e_probe.json[docx]"],
        docx.get("citation", {}).get("location_label"),
    )
    add(
        "office_preview_no_pdf_renderer",
        "Office 无页面图片时不进入 PDF-only 渲染路径",
        tests_pass(
            "test_office_asset_uses_preview_endpoint_not_pdf_renderer",
            "test_office_preview_is_safe_text_svg",
            "test_office_parser_failure_does_not_enter_pdf_or_ocr_fallback",
        ),
        ["test_stage5_office_semantics.py preview/fallback tests"],
    )
    add(
        "real_parse_index_query_all_formats",
        "每种目标格式完成真实解析、Chunk V2、索引和 RAG 查询",
        every_real_format_passed
        and tests_pass("test_every_target_format_completes_parse_index_and_rag_query")
        and not real["failures"],
        ["real_e2e_probe.json", "test_stage5_format_e2e.py"],
        {
            "success_rate": real["format_success_rate"],
            "warm_pipeline_p95_seconds": real["warm_pipeline_p95_seconds"],
            "gpu_peak_memory_used_mib": real["gpu"]["peak_memory_used_mib"],
            "gpu_oom_count": real["gpu"]["oom_count"],
        },
    )

    failures = [check["id"] for check in checks if not check["passed"]]
    evaluation = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "real_run_id": real["run_id"],
        "check_count": len(checks),
        "passed_count": len(checks) - len(failures),
        "failed_count": len(failures),
        "checks": checks,
        "failures": failures,
        "passed": not failures,
    }
    format_matrix = {
        "schema_version": 1,
        "captured_at": evaluation["captured_at"],
        "run_id": real["run_id"],
        "formats": [
            {
                "format": item["format"],
                "validation_passed": item["validation"]["passed"],
                "source_unit": item["scan"]["source_unit"],
                "final_parser": item["parser"]["final_parser"],
                "elapsed_seconds": item["elapsed_seconds"],
                "chunk_count": item["chunk_count"],
                "asset_count": item["asset_count"],
                "citation_location_type": item["citation"]["location_type"],
                "citation_location_label": item["citation"]["location_label"],
                "passed": item["passed"],
            }
            for item in real["format_results"]
        ],
        "success_rate": real["format_success_rate"],
        "warm_pipeline_p95_seconds": real["warm_pipeline_p95_seconds"],
        "gpu": real["gpu"],
        "passed": real["passed"],
    }
    (STAGE / "acceptance_evaluation.json").write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (STAGE / "format_matrix.json").write_text(
        json.dumps(format_matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": evaluation["passed"],
                "checks": evaluation["check_count"],
                "failures": failures,
                "real_formats": len(format_matrix["formats"]),
            },
            ensure_ascii=False,
        )
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
