from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
STAGE = Path(__file__).resolve().parent
THRESHOLDS_PATH = ROOT / "quality" / "stage0" / "acceptance_thresholds.json"
LATEST_REAL_RUN_PATH = ROOT / "quality" / "stage2" / "latest_real_run.txt"
REAL_PIPELINE_PATH = ROOT / "quality" / "stage2" / "real_pipeline_probe.json"
RETRIEVAL_PATH = ROOT / "quality" / "stage2" / "retrieval_probe.json"
OUTPUT_PATH = STAGE / "real_v2_probe.json"
EXPECTED_SCENARIOS = {"native", "complex", "mixed", "scanned", "image"}
NORMAL_CONTENT_TYPES = {"text"}
QUALITY_SCORE_NAMES = (
    "valid_character_score",
    "content_coverage_score",
    "ocr_confidence_score",
    "mapping_completeness_score",
    "deduplication_score",
)

sys.path.insert(0, str(ROOT / "backend"))

from app.document.chunk_protocol import (  # noqa: E402
    BgeM3TokenCounter,
    is_chunk_indexable,
    normalize_for_hash,
    render_embedding_text,
)


class InputRecorder:
    def __init__(self) -> None:
        self._files: dict[str, dict[str, object]] = {}

    def read_bytes(self, path: Path) -> bytes:
        resolved = path.resolve(strict=True)
        data = resolved.read_bytes()
        try:
            relative = resolved.relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            relative = str(resolved)
        self._files[relative] = {
            "path": relative,
            "size_bytes": len(data),
            "sha256": sha256(data).hexdigest(),
        }
        return data

    def read_text(self, path: Path) -> str:
        return self.read_bytes(path).decode("utf-8")

    def read_json(self, path: Path) -> Any:
        return json.loads(self.read_text(path))

    def read_jsonl(self, path: Path) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for line_number, line in enumerate(self.read_text(path).splitlines(), start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            rows.append(value)
        return rows

    def as_list(self) -> list[dict[str, object]]:
        return [self._files[key] for key in sorted(self._files)]


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _check(
    observed: object,
    operator: str,
    expected: object,
    passed: bool,
    *,
    violations: list[object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "observed": observed,
        "operator": operator,
        "expected": expected,
        "passed": bool(passed),
    }
    if violations:
        result["violations"] = violations
    return result


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _safe_storage_path(raw_value: object) -> Path:
    value = str(raw_value or "").strip()
    if not value:
        raise ValueError("real_pipeline_probe.json does not declare storage")
    normalized = value.replace("\\", os.sep).replace("/", os.sep)
    candidate = Path(normalized)
    resolved = (candidate if candidate.is_absolute() else ROOT / candidate).resolve()
    resolved.relative_to(ROOT.resolve())
    if not resolved.is_dir():
        raise FileNotFoundError(f"Stage 2 storage does not exist: {resolved}")
    return resolved


def _expected_quality_weights(thresholds: dict[str, object]) -> dict[str, float]:
    formula = _as_dict(thresholds.get("quality_score_formula"))
    return {name: float(formula[f"{name}_weight"]) for name in QUALITY_SCORE_NAMES}


def _quality_violations(
    chunk: dict[str, object],
    *,
    quality_version: str,
    quality_threshold: float,
    expected_weights: dict[str, float],
) -> list[str]:
    chunk_id = str(chunk.get("chunk_id") or "<missing>")
    metadata = _as_dict(chunk.get("metadata"))
    components = _as_dict(metadata.get("quality_components"))
    weights = _as_dict(components.get("weights"))
    failures: list[str] = []

    if metadata.get("quality_version") != quality_version:
        failures.append(f"{chunk_id}: metadata.quality_version")
    if metadata.get("quality_formula_version") != quality_version:
        failures.append(f"{chunk_id}: metadata.quality_formula_version")

    scores: dict[str, float] = {}
    for name in QUALITY_SCORE_NAMES:
        raw_score = components.get(name)
        if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
            failures.append(f"{chunk_id}: quality_components.{name} is not numeric")
            continue
        score = float(raw_score)
        if not 0.0 <= score <= 1.0:
            failures.append(f"{chunk_id}: quality_components.{name} outside [0,1]")
        scores[name] = score

    if set(weights) != set(expected_weights):
        failures.append(f"{chunk_id}: quality weight names")
    for name, expected in expected_weights.items():
        raw_weight = weights.get(name)
        if (
            isinstance(raw_weight, bool)
            or not isinstance(raw_weight, (int, float))
            or not math.isclose(float(raw_weight), expected, abs_tol=1e-12)
        ):
            failures.append(f"{chunk_id}: quality weight {name}")

    if len(scores) == len(QUALITY_SCORE_NAMES):
        computed = round(sum(scores[name] * expected_weights[name] for name in QUALITY_SCORE_NAMES), 6)
        component_score = components.get("weighted_score")
        chunk_score = chunk.get("quality_score")
        if (
            isinstance(component_score, bool)
            or not isinstance(component_score, (int, float))
            or not math.isclose(float(component_score), computed, abs_tol=1e-6)
        ):
            failures.append(f"{chunk_id}: quality_components.weighted_score")
        if (
            isinstance(chunk_score, bool)
            or not isinstance(chunk_score, (int, float))
            or not math.isclose(float(chunk_score), computed, abs_tol=1e-6)
        ):
            failures.append(f"{chunk_id}: quality_score")

    raw_threshold = metadata.get("quality_threshold")
    if (
        isinstance(raw_threshold, bool)
        or not isinstance(raw_threshold, (int, float))
        or not math.isclose(float(raw_threshold), quality_threshold, abs_tol=1e-12)
    ):
        failures.append(f"{chunk_id}: metadata.quality_threshold")

    chunk_score = chunk.get("quality_score")
    expected_indexable = (
        isinstance(chunk_score, (int, float))
        and not isinstance(chunk_score, bool)
        and float(chunk_score) >= quality_threshold
        and bool(str(chunk.get("text") or "").strip())
        and str(chunk.get("content_type") or "").strip().lower() != "ocr_pending"
    )
    if metadata.get("indexable") is not expected_indexable:
        failures.append(f"{chunk_id}: metadata.indexable")
    if bool(is_chunk_indexable(chunk)) is not expected_indexable:
        failures.append(f"{chunk_id}: is_chunk_indexable")
    if not expected_indexable and (
        metadata.get("quarantined") is not True or metadata.get("index_status") != "quarantined"
    ):
        failures.append(f"{chunk_id}: quarantine metadata")
    return failures


def _evaluate_book(
    scenario: dict[str, object],
    *,
    storage: Path,
    recorder: InputRecorder,
    counter: BgeM3TokenCounter,
    thresholds: dict[str, object],
) -> tuple[dict[str, object], dict[str, dict[str, object]], set[str]]:
    scenario_name = str(scenario.get("scenario") or "")
    book_id = str(scenario.get("book_id") or "")
    artifact_path = storage / "books" / book_id / "artifacts"
    bundle_root = artifact_path / ".rag_bundles"
    manifest_path = bundle_root / "manifest.json"
    manifest = recorder.read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError(f"Expected manifest object for {book_id}")
    generation = str(manifest.get("generation") or manifest.get("active_generation") or "")
    if not generation or Path(generation).name != generation or generation in {".", ".."}:
        raise ValueError(f"Unsafe or missing active generation for {book_id}: {generation!r}")
    generation_path = (bundle_root / generation).resolve()
    if generation_path.parent != bundle_root.resolve() or not generation_path.is_dir():
        raise ValueError(f"Active generation is outside bundle root for {book_id}")

    chunks = recorder.read_jsonl(generation_path / "chunks.jsonl")
    assets_value = recorder.read_json(generation_path / "assets.json")
    pages_value = recorder.read_json(artifact_path / "pages.json")
    if not isinstance(assets_value, list) or not all(isinstance(item, dict) for item in assets_value):
        raise ValueError(f"Expected Asset array for {book_id}")
    if not isinstance(pages_value, list) or not all(isinstance(item, dict) for item in pages_value):
        raise ValueError(f"Expected page array for {book_id}")
    assets = [item for item in assets_value if isinstance(item, dict)]
    pages = [item for item in pages_value if isinstance(item, dict)]

    chunk_by_id: dict[str, dict[str, object]] = {}
    duplicate_chunk_ids: list[str] = []
    for chunk in chunks:
        chunk_id = str(chunk.get("chunk_id") or "")
        if not chunk_id or chunk_id in chunk_by_id:
            duplicate_chunk_ids.append(chunk_id or "<missing>")
        else:
            chunk_by_id[chunk_id] = chunk

    asset_by_id: dict[str, dict[str, object]] = {}
    duplicate_asset_ids: list[str] = []
    for asset in assets:
        asset_id = str(asset.get("asset_id") or "")
        if not asset_id or asset_id in asset_by_id:
            duplicate_asset_ids.append(asset_id or "<missing>")
        else:
            asset_by_id[asset_id] = asset

    block_pages: dict[str, int] = {}
    duplicate_block_ids: list[str] = []
    document_pages: set[int] = set()
    for page in pages:
        page_number = int(page.get("page") or 0)
        document_pages.add(page_number)
        for raw_block in _as_list(page.get("blocks")):
            block = _as_dict(raw_block)
            block_id = str(block.get("block_id") or "")
            block_page = int(block.get("page") or page_number)
            if not block_id:
                continue
            if block_id in block_pages and block_pages[block_id] != block_page:
                duplicate_block_ids.append(block_id)
            block_pages[block_id] = block_page

    chunk_limits = _as_dict(thresholds.get("chunking"))
    quality_formula = _as_dict(thresholds.get("quality_score_formula"))
    max_tokens = int(chunk_limits["max_tokens"])
    min_tokens = int(chunk_limits["min_tokens"])
    overlap_limit = int(chunk_limits["overlap_tokens"])
    atomic_hard_limit = int(chunk_limits["atomic_content_hard_max_tokens"])
    overlap_ratio_limit = float(chunk_limits["overlap_duplicate_token_ratio_max"])
    duplicate_ratio_limit = float(chunk_limits["exact_duplicate_ratio_max_frozen_fixtures"])
    quality_threshold = float(chunk_limits["chunk_quality_threshold"])
    quality_version = str(quality_formula["version"])
    expected_weights = _expected_quality_weights(thresholds)

    violations: dict[str, list[object]] = {
        "all_chunks_v2": [],
        "token_count_exact": [],
        "ordinary_token_limit": [],
        "atomic_token_limit": [],
        "short_tail_protocol": [],
        "quality_v1_protocol": [],
        "heading_prefix": [],
        "overlap_protocol": [],
        "source_block_page_ranges": [],
        "asset_links_bidirectional": [],
        "artifact_identity": [],
    }
    rendered_hashes: list[str] = []
    token_total = 0
    overlap_token_total = 0
    ordinary_count = 0
    atomic_count = 0
    token_max = 0
    ordinary_token_max = 0
    atomic_token_max = 0
    previous_ordinary: dict[str, object] | None = None

    for chunk in chunks:
        chunk_id = str(chunk.get("chunk_id") or "<missing>")
        metadata = _as_dict(chunk.get("metadata"))
        content_type = str(chunk.get("content_type") or "").strip().lower()
        body = str(chunk.get("text") or "")
        heading_path = [str(value) for value in _as_list(chunk.get("heading_path"))]
        overlap_text = str(metadata.get("overlap_text") or "")
        rendered = render_embedding_text(body, heading_path, overlap_text)
        recomputed_tokens = counter.count(rendered)
        overlap_tokens = counter.count(overlap_text)
        token_total += recomputed_tokens
        overlap_token_total += overlap_tokens
        token_max = max(token_max, recomputed_tokens)
        rendered_hashes.append(normalize_for_hash(rendered))

        if chunk.get("chunk_version") != "v2":
            violations["all_chunks_v2"].append(chunk_id)
        if chunk.get("book_id") != book_id:
            violations["artifact_identity"].append(f"{chunk_id}: book_id")
        if metadata.get("body") != body:
            violations["token_count_exact"].append(f"{chunk_id}: metadata.body differs from text")
        if metadata.get("tokenizer_model") != chunk_limits.get("tokenizer_model"):
            violations["token_count_exact"].append(f"{chunk_id}: tokenizer_model")
        if metadata.get("tokenizer_revision") != chunk_limits.get("tokenizer_revision"):
            violations["token_count_exact"].append(f"{chunk_id}: tokenizer_revision")
        if chunk.get("token_count") != recomputed_tokens:
            violations["token_count_exact"].append(
                {"chunk_id": chunk_id, "persisted": chunk.get("token_count"), "recomputed": recomputed_tokens}
            )

        if content_type in NORMAL_CONTENT_TYPES:
            ordinary_count += 1
            ordinary_token_max = max(ordinary_token_max, recomputed_tokens)
            if recomputed_tokens > max_tokens:
                violations["ordinary_token_limit"].append(
                    {"chunk_id": chunk_id, "tokens": recomputed_tokens}
                )
            warnings = {str(value) for value in _as_list(metadata.get("warnings"))}
            if recomputed_tokens < min_tokens and "short_tail" not in warnings:
                violations["short_tail_protocol"].append(
                    {"chunk_id": chunk_id, "tokens": recomputed_tokens, "warnings": sorted(warnings)}
                )
            if overlap_text:
                previous_body = (
                    str(_as_dict(previous_ordinary.get("metadata")).get("body") or previous_ordinary.get("text") or "")
                    if previous_ordinary
                    else ""
                )
                if not previous_ordinary or not previous_body.endswith(overlap_text):
                    violations["overlap_protocol"].append(f"{chunk_id}: overlap is not prior suffix")
            previous_ordinary = chunk
        else:
            atomic_count += 1
            atomic_token_max = max(atomic_token_max, recomputed_tokens)
            if recomputed_tokens > atomic_hard_limit:
                violations["atomic_token_limit"].append(
                    {"chunk_id": chunk_id, "tokens": recomputed_tokens}
                )

        overlap_ratio = overlap_tokens / recomputed_tokens if recomputed_tokens else math.inf
        if overlap_tokens > overlap_limit or overlap_ratio > overlap_ratio_limit:
            violations["overlap_protocol"].append(
                {
                    "chunk_id": chunk_id,
                    "overlap_tokens": overlap_tokens,
                    "chunk_tokens": recomputed_tokens,
                    "ratio": round(overlap_ratio, 6),
                }
            )

        heading = " > ".join(value.strip() for value in heading_path if value.strip())
        if heading and not rendered.startswith(heading + "\n\n"):
            violations["heading_prefix"].append(chunk_id)

        violations["quality_v1_protocol"].extend(
            _quality_violations(
                chunk,
                quality_version=quality_version,
                quality_threshold=quality_threshold,
                expected_weights=expected_weights,
            )
        )

        page_start = int(chunk.get("page_start") or 0)
        page_end = int(chunk.get("page_end") or 0)
        if page_start > page_end or any(page not in document_pages for page in range(page_start, page_end + 1)):
            violations["source_block_page_ranges"].append(f"{chunk_id}: invalid chunk page range")
        for source_block_id in (str(value) for value in _as_list(chunk.get("source_block_ids"))):
            source_page = block_pages.get(source_block_id)
            if source_page is None:
                violations["source_block_page_ranges"].append(
                    f"{chunk_id}: missing source block {source_block_id}"
                )
            elif not page_start <= source_page <= page_end:
                violations["source_block_page_ranges"].append(
                    f"{chunk_id}: {source_block_id} page {source_page} outside {page_start}-{page_end}"
                )

        for asset_id in (str(value) for value in _as_list(chunk.get("asset_ids"))):
            asset = asset_by_id.get(asset_id)
            if asset is None:
                violations["asset_links_bidirectional"].append(f"{chunk_id}: missing asset {asset_id}")
            elif chunk_id not in {str(value) for value in _as_list(asset.get("source_chunk_ids"))}:
                violations["asset_links_bidirectional"].append(
                    f"{chunk_id}: absent from asset {asset_id}.source_chunk_ids"
                )

    for asset_id, asset in asset_by_id.items():
        if asset.get("book_id") != book_id:
            violations["artifact_identity"].append(f"{asset_id}: book_id")
        for source_chunk_id in (str(value) for value in _as_list(asset.get("source_chunk_ids"))):
            chunk = chunk_by_id.get(source_chunk_id)
            if chunk is None:
                violations["asset_links_bidirectional"].append(
                    f"{asset_id}: missing source chunk {source_chunk_id}"
                )
            elif asset_id not in {str(value) for value in _as_list(chunk.get("asset_ids"))}:
                violations["asset_links_bidirectional"].append(
                    f"{asset_id}: absent from chunk {source_chunk_id}.asset_ids"
                )

    duplicate_count = len(rendered_hashes) - len(set(rendered_hashes))
    duplicate_ratio = duplicate_count / len(rendered_hashes) if rendered_hashes else 0.0
    global_overlap_ratio = overlap_token_total / token_total if token_total else 0.0
    quarantined_ids = {
        chunk_id for chunk_id, chunk in chunk_by_id.items() if not bool(is_chunk_indexable(chunk))
    }
    active_indexable = {
        chunk_id: chunk for chunk_id, chunk in chunk_by_id.items() if bool(is_chunk_indexable(chunk))
    }

    manifest_violations: list[object] = []
    if manifest.get("state") != "ready":
        manifest_violations.append(f"state={manifest.get('state')!r}")
    if duplicate_chunk_ids:
        manifest_violations.append({"duplicate_chunk_ids": duplicate_chunk_ids})
    if duplicate_asset_ids:
        manifest_violations.append({"duplicate_asset_ids": duplicate_asset_ids})
    if duplicate_block_ids:
        manifest_violations.append({"cross_page_duplicate_block_ids": duplicate_block_ids})
    if len(chunks) != int(scenario.get("chunk_count", -1)):
        manifest_violations.append(
            {"chunk_count": len(chunks), "pipeline_chunk_count": scenario.get("chunk_count")}
        )
    if len(assets) != int(scenario.get("asset_count", -1)):
        manifest_violations.append(
            {"asset_count": len(assets), "pipeline_asset_count": scenario.get("asset_count")}
        )

    checks = {
        "active_generation": _check(
            {"state": manifest.get("state"), "generation": generation},
            "ready generation",
            True,
            not manifest_violations,
            violations=manifest_violations,
        ),
        "all_chunks_v2": _check(len(chunks), "all chunk_version ==", "v2", not violations["all_chunks_v2"], violations=violations["all_chunks_v2"]),
        "token_count_exact": _check(len(chunks), "all persisted == locked BGE recomputation", True, not violations["token_count_exact"], violations=violations["token_count_exact"]),
        "ordinary_token_limit": _check(ordinary_token_max, "<=", max_tokens, not violations["ordinary_token_limit"], violations=violations["ordinary_token_limit"]),
        "atomic_token_limit": _check(atomic_token_max, "<=", atomic_hard_limit, not violations["atomic_token_limit"], violations=violations["atomic_token_limit"]),
        "short_tail_protocol": _check(min_tokens, "short ordinary chunk has warning", "short_tail", not violations["short_tail_protocol"], violations=violations["short_tail_protocol"]),
        "quality_v1_protocol": _check(len(chunks), "all quality records valid", quality_version, not violations["quality_v1_protocol"], violations=violations["quality_v1_protocol"]),
        "heading_prefix": _check(len(chunks), "all rendered text starts with heading path", True, not violations["heading_prefix"], violations=violations["heading_prefix"]),
        "exact_duplicate_ratio": _check(round(duplicate_ratio, 6), "<=", duplicate_ratio_limit, duplicate_ratio <= duplicate_ratio_limit),
        "overlap_protocol": _check(
            {"tokens": overlap_token_total, "ratio": round(global_overlap_ratio, 6)},
            "per chunk <= limits and global ratio <=",
            {"tokens": overlap_limit, "ratio": overlap_ratio_limit},
            not violations["overlap_protocol"] and global_overlap_ratio <= overlap_ratio_limit,
            violations=violations["overlap_protocol"],
        ),
        "source_block_page_ranges": _check(len(block_pages), "contains all chunk sources in chunk range", True, not violations["source_block_page_ranges"], violations=violations["source_block_page_ranges"]),
        "asset_links_bidirectional": _check(len(assets), "all links reciprocal", True, not violations["asset_links_bidirectional"], violations=violations["asset_links_bidirectional"]),
        "artifact_identity": _check(book_id, "all artifact book_id ==", book_id, not violations["artifact_identity"], violations=violations["artifact_identity"]),
    }
    failures = [name for name, result in checks.items() if not bool(result["passed"])]
    report: dict[str, object] = {
        "scenario": scenario_name,
        "book_id": book_id,
        "generation": generation,
        "build_id": manifest.get("build_id"),
        "chunk_count": len(chunks),
        "ordinary_chunk_count": ordinary_count,
        "atomic_chunk_count": atomic_count,
        "asset_count": len(assets),
        "source_block_count": len(block_pages),
        "indexable_chunk_count": len(active_indexable),
        "quarantined_chunk_ids": sorted(quarantined_ids),
        "token_total": token_total,
        "token_max": token_max,
        "ordinary_token_max": ordinary_token_max,
        "atomic_token_max": atomic_token_max,
        "overlap_token_count": overlap_token_total,
        "overlap_duplicate_token_ratio": round(global_overlap_ratio, 6),
        "exact_duplicate_count": duplicate_count,
        "exact_duplicate_ratio": round(duplicate_ratio, 6),
        "checks": checks,
        "failures": failures,
        "passed": not failures,
    }
    return report, active_indexable, quarantined_ids


def _failed_book(scenario: dict[str, object], exc: Exception) -> dict[str, object]:
    message = f"{type(exc).__name__}: {exc}"
    return {
        "scenario": scenario.get("scenario"),
        "book_id": scenario.get("book_id"),
        "checks": {"active_generation": _check(message, "no error", None, False)},
        "failures": ["active_generation"],
        "fatal_error": message,
        "passed": False,
    }


def _evaluate(recorder: InputRecorder) -> dict[str, object]:
    thresholds = recorder.read_json(THRESHOLDS_PATH)
    real = recorder.read_json(REAL_PIPELINE_PATH)
    retrieval = recorder.read_json(RETRIEVAL_PATH)
    latest_run_id = recorder.read_text(LATEST_REAL_RUN_PATH).strip()
    if not isinstance(thresholds, dict) or not isinstance(real, dict) or not isinstance(retrieval, dict):
        raise ValueError("Stage 0/2 probe inputs must be JSON objects")

    chunk_limits = _as_dict(thresholds.get("chunking"))
    retrieval_limits = _as_dict(thresholds.get("retrieval"))
    storage = _safe_storage_path(real.get("storage"))
    counter = BgeM3TokenCounter()
    raw_scenarios = _as_list(real.get("scenario_results"))
    scenarios = [_as_dict(item) for item in raw_scenarios]

    books: list[dict[str, object]] = []
    active_by_book: dict[str, dict[str, dict[str, object]]] = {}
    quarantined_by_book: dict[str, set[str]] = {}
    for scenario in scenarios:
        book_id = str(scenario.get("book_id") or "")
        try:
            book, active_chunks, quarantined_ids = _evaluate_book(
                scenario,
                storage=storage,
                recorder=recorder,
                counter=counter,
                thresholds=thresholds,
            )
        except Exception as exc:
            book = _failed_book(scenario, exc)
            active_chunks = {}
            quarantined_ids = set()
        books.append(book)
        active_by_book[book_id] = active_chunks
        quarantined_by_book[book_id] = quarantined_ids

    scenario_names = {str(item.get("scenario") or "") for item in scenarios}
    scenario_books = {str(item.get("scenario") or ""): str(item.get("book_id") or "") for item in scenarios}
    retrieval_results = [_as_dict(item) for item in _as_list(retrieval.get("results"))]
    retrieval_violations: list[object] = []
    quarantined_result_ids: list[dict[str, str]] = []
    inactive_result_ids: list[dict[str, str]] = []
    for result in retrieval_results:
        scenario_name = str(result.get("scenario") or "")
        book_id = str(result.get("book_id") or "")
        if scenario_books.get(scenario_name) != book_id:
            retrieval_violations.append(f"{scenario_name}: retrieval book_id mismatch")
        if not result.get("relevant_in_top5"):
            retrieval_violations.append(f"{scenario_name}: relevant_in_top5=false")
        if not result.get("citation_page_correct"):
            retrieval_violations.append(f"{scenario_name}: citation_page_correct=false")
        top_results = [_as_dict(item) for item in _as_list(result.get("top_results"))]
        if int(result.get("result_count") or 0) <= 0:
            retrieval_violations.append(f"{scenario_name}: no retrieval results")
        if int(result.get("result_count") or 0) > len(top_results):
            retrieval_violations.append(f"{scenario_name}: result set is not fully recorded")
        for item in top_results:
            chunk_id = str(item.get("chunk_id") or "")
            evidence = {"book_id": book_id, "chunk_id": chunk_id}
            if chunk_id in quarantined_by_book.get(book_id, set()):
                quarantined_result_ids.append(evidence)
            if chunk_id not in active_by_book.get(book_id, {}):
                inactive_result_ids.append(evidence)

    retrieval_metrics = _as_dict(retrieval.get("retrieval_metrics"))
    metric_pairs = {
        "recall_at_5": "recall_at_5_min",
        "citation_page_accuracy_over_all_queries": "citation_page_accuracy_over_all_queries_min",
        "citation_page_accuracy_conditional_on_retrieval": "citation_page_accuracy_conditional_on_retrieval_min",
        "query_result_coverage": "query_result_coverage_min",
    }
    metric_violations = [
        name
        for name, threshold_name in metric_pairs.items()
        if float(retrieval_metrics.get(name, 0.0)) < float(retrieval_limits[threshold_name])
    ]
    if metric_violations:
        retrieval_violations.append({"metrics_below_threshold": metric_violations})

    book_checks = sorted({name for book in books for name in _as_dict(book.get("checks"))})
    aggregate_book_checks: dict[str, dict[str, object]] = {}
    for name in book_checks:
        failed_books = [
            str(book.get("book_id"))
            for book in books
            if not bool(_as_dict(_as_dict(book.get("checks")).get(name)).get("passed"))
        ]
        aggregate_book_checks[name] = _check(
            len(books) - len(failed_books),
            "books passed ==",
            len(books),
            not failed_books,
            violations=failed_books,
        )

    query_count = int(retrieval.get("query_count") or 0)
    expected_query_count = int(retrieval_limits["frozen_query_count"])
    checks: dict[str, dict[str, object]] = {
        "latest_input_alignment": _check(
            {
                "marker": latest_run_id,
                "pipeline": real.get("run_id"),
                "retrieval": retrieval.get("pipeline_run_id"),
            },
            "all equal",
            latest_run_id,
            bool(latest_run_id)
            and latest_run_id == real.get("run_id") == retrieval.get("pipeline_run_id"),
        ),
        "real_scenarios": _check(
            {"count": len(scenarios), "names": sorted(scenario_names), "pipeline_passed": real.get("passed")},
            "==",
            {"count": 5, "names": sorted(EXPECTED_SCENARIOS), "pipeline_passed": True},
            len(scenarios) == 5
            and scenario_names == EXPECTED_SCENARIOS
            and real.get("passed") is True
            and not _as_list(real.get("failures")),
        ),
        "active_generations": _check(
            sum(bool(book.get("passed")) for book in books),
            "books passed ==",
            5,
            len(books) == 5 and all(bool(book.get("passed")) for book in books),
            violations=[str(book.get("book_id")) for book in books if not bool(book.get("passed"))],
        ),
        "retrieval_queries": _check(
            {
                "declared": query_count,
                "recorded": len(retrieval_results),
                "report_passed": retrieval.get("passed"),
            },
            "==",
            {"declared": expected_query_count, "recorded": expected_query_count, "report_passed": True},
            query_count == expected_query_count
            and len(retrieval_results) == expected_query_count
            and retrieval.get("passed") is True
            and not retrieval_violations,
            violations=retrieval_violations,
        ),
        "retrieval_excludes_quarantined": _check(
            {
                "quarantined_result_ids": quarantined_result_ids,
                "inactive_result_ids": inactive_result_ids,
            },
            "==",
            {"quarantined_result_ids": [], "inactive_result_ids": []},
            not quarantined_result_ids and not inactive_result_ids,
        ),
    }
    checks.update(aggregate_book_checks)
    failures = [name for name, result in checks.items() if not bool(result.get("passed"))]
    total_chunks = sum(int(book.get("chunk_count") or 0) for book in books)
    total_assets = sum(int(book.get("asset_count") or 0) for book in books)
    total_tokens = sum(int(book.get("token_total") or 0) for book in books)
    total_overlap = sum(int(book.get("overlap_token_count") or 0) for book in books)
    aggregate = {
        "scenario_count": len(books),
        "query_count": len(retrieval_results),
        "chunk_count": total_chunks,
        "asset_count": total_assets,
        "indexable_chunk_count": sum(int(book.get("indexable_chunk_count") or 0) for book in books),
        "quarantined_chunk_count": sum(len(_as_list(book.get("quarantined_chunk_ids"))) for book in books),
        "ordinary_chunk_count": sum(int(book.get("ordinary_chunk_count") or 0) for book in books),
        "atomic_chunk_count": sum(int(book.get("atomic_chunk_count") or 0) for book in books),
        "token_total": total_tokens,
        "token_max": max((int(book.get("token_max") or 0) for book in books), default=0),
        "overlap_token_count": total_overlap,
        "overlap_duplicate_token_ratio": round(total_overlap / total_tokens, 6) if total_tokens else 0.0,
        "exact_duplicate_count_within_books": sum(int(book.get("exact_duplicate_count") or 0) for book in books),
        "retrieval_metrics": retrieval_metrics,
    }
    return {
        "schema_version": 1,
        "metric_protocol_version": thresholds.get("metric_protocol_version"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_run_id": real.get("run_id"),
        "storage": str(storage.relative_to(ROOT.resolve())).replace("\\", "/"),
        "tokenizer": {
            "model": chunk_limits.get("tokenizer_model"),
            "revision": chunk_limits.get("tokenizer_revision"),
            "implementation": counter._tokenizer.__class__.__name__,
        },
        "thresholds": {
            "normal_max_tokens": chunk_limits.get("max_tokens"),
            "atomic_hard_max_tokens": chunk_limits.get("atomic_content_hard_max_tokens"),
            "min_tokens": chunk_limits.get("min_tokens"),
            "overlap_tokens": chunk_limits.get("overlap_tokens"),
            "overlap_ratio_max": chunk_limits.get("overlap_duplicate_token_ratio_max"),
            "quality_threshold": chunk_limits.get("chunk_quality_threshold"),
            "quality_version": _as_dict(thresholds.get("quality_score_formula")).get("version"),
            "frozen_query_count": expected_query_count,
        },
        "input_files": recorder.as_list(),
        "books": books,
        "aggregate": aggregate,
        "checks": checks,
        "failures": failures,
        "passed": not failures,
    }


def main() -> int:
    recorder = InputRecorder()
    try:
        payload = _evaluate(recorder)
    except Exception as exc:
        payload = {
            "schema_version": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "input_files": recorder.as_list(),
            "checks": {
                "fatal_error": _check(f"{type(exc).__name__}: {exc}", "==", None, False)
            },
            "failures": ["fatal_error"],
            "fatal_error": {"type": type(exc).__name__, "message": str(exc)},
            "passed": False,
        }
    _atomic_write(OUTPUT_PATH, payload)
    print(
        json.dumps(
            {
                "passed": payload["passed"],
                "pipeline_run_id": payload.get("pipeline_run_id"),
                "checks": len(_as_dict(payload.get("checks"))),
                "failures": payload.get("failures"),
                "output": str(OUTPUT_PATH),
            },
            ensure_ascii=False,
        )
    )
    return 0 if bool(payload["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
