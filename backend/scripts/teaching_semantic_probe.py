"""Pre-register isolated semantic controls; paid calls require explicit --run.

Run with PYTHONPATH=backend/src. Fixtures and expectations are local audit
data. Only allowlisted source/point navigation and candidate fields reach models.
This probes planning/factual review, not course generation or lesson pedagogy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from copy import deepcopy
from pathlib import Path

from adaptive_learning.ingestion.models import ChapterDraft
from adaptive_learning.personalization.models import SourceCitation
from adaptive_learning.personalization.teaching_content import (
    TEACHING_CONTENT_VERSION,
    TeachingContentGenerator,
    _Point,
)
from adaptive_learning.personalization.teaching_evidence import (
    TeachingEvidence,
    scope_context_source_ids,
)
from adaptive_learning.personalization.teaching_scope import validate_scope_plan

COMPONENTS = ("explanation", "flashcard", "practice")


def build_cases():
    sources = {
        "source_a": {"page_number": 1, "quote": "本章讨论一台测量装置。装置在密闭容器内工作，蓝灯亮起表示可以开始测量。"},
        "source_b": {"page_number": 2, "quote": "这台装置的蓝灯指示只有在温度保持不变时才可沿用。温度改变时不能直接沿用原判断，必须重新校准后再测量。"},
        "source_c": {"page_number": 3, "quote": "研究者经过多次实验提出了蓝灯指示的结论。本段介绍这一结论的发现过程。"},
    }
    point = {"point_id": "point_measurement", "title": "蓝灯指示与测量条件",
        "explanation": "使用这台装置时，应先确认它在密闭容器内工作。蓝灯亮起表示可以开始测量，但直接沿用蓝灯指示还要求温度保持不变。温度改变后，原来的判断不能直接套用，需要先重新校准再测量。判断能否沿用时，要把指示信号与适用条件放在一起核对，不能只看蓝灯是否亮起。",
        "source_ids": ["source_a", "source_b"],
        "flashcard": {"card_id": "card_measurement", "front": "这台装置蓝灯亮起后，直接沿用测量判断还需要满足什么条件？",
            "back": "装置在密闭容器内工作，温度须保持不变；温度改变时应重新校准后再测量。", "source_ids": ["source_a", "source_b"]},
        "practice": {"item_id": "item_measurement", "prompt": "假设这台装置在密闭容器中蓝灯已亮起，但随后温度改变。应直接沿用原判断还是采取其他操作？请说明理由。",
            "expected_answer": "不能直接沿用原判断。蓝灯指示的沿用要求温度保持不变，题设条件已改变，因此应先重新校准后再测量。",
            "rubric": ["指出温度改变后不能直接沿用原判断", "说明应重新校准后再测量"], "source_ids": ["source_a", "source_b"]}}
    common = {"chapter": {"chapter_id": "chapter_measurement", "order": 1, "title": "装置指示与使用范围",
        "start_page": 1, "end_page": 3, "summary": "比较测量指示与其适用条件。",
        "knowledge_points": ["装置蓝灯指示及沿用条件"], "source_block_ids": []},
        "inputs": [{"point_id": point["point_id"], "label": "装置蓝灯指示及沿用条件"}],
        "points": [point], "evidence": {"sources": sources, "point_sources": {point["point_id"]: ["source_a"]},
            "warnings": [], "fingerprint": hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()}}
    good = deepcopy(common)
    bad_scope = deepcopy(common)
    # Preserve correct conditions already written; only the explanation's new
    # universal conclusion is wrong. Rejecting the two correct siblings fails.
    bad_scope["points"][0]["explanation"] += "尽管如此，只要蓝灯亮起，任何温度下都必然可以直接沿用测量判断，不需要重新校准。"
    missing_citation = deepcopy(common)
    missing_citation["points"][0]["practice"]["source_ids"] = ["source_a"]
    good["expectations"] = {"kind": "correct", "failed_components": []}
    bad_scope["expectations"] = {"kind": "scope_error_with_complete_citations", "failed_components": ["explanation"]}
    missing_citation["expectations"] = {"kind": "correct_claim_missing_citation", "failed_components": ["practice"]}
    return {"control_01": good, "control_02": bad_scope, "control_03": missing_citation}


def prepare(raw):
    chapter = ChapterDraft.model_validate(raw["chapter"])
    data = raw["evidence"]
    evidence = TeachingEvidence(sources={key: SourceCitation.model_validate(value) for key, value in data["sources"].items()},
        point_sources=deepcopy(data["point_sources"]), warnings=list(data["warnings"]), fingerprint=data["fingerprint"])
    points = [_Point.model_validate(value) for value in raw["points"]]
    labels = {value["point_id"]: value["label"] for value in raw["inputs"]}
    if len(labels) != len(points) or set(labels) != {point.point_id for point in points}:
        raise ValueError("probe inputs must cover every unique point")
    inputs = [{"point_id": point.point_id, "label": labels[point.point_id],
        "point_source_ids": list(evidence.point_sources[point.point_id]),
        "scope_context_source_ids": scope_context_source_ids(evidence, labels[point.point_id], evidence.point_sources[point.point_id])}
        for point in points]
    return chapter, evidence, points, inputs


def score(review, expected):
    if len(review.points) != 1:
        return {"passed": False, "reason": "wrong_point_coverage"}
    row = review.points[0]
    actual = TeachingContentGenerator._failed_fact_components(row)
    targets = set(expected["failed_components"])
    unaffected = all(getattr(row.components, name).source_entailment is True
        and getattr(row.components, name).scope_exceptions is True
        and not getattr(row.components, name).findings for name in set(COMPONENTS) - targets)
    kind = expected["kind"]
    if kind == "correct":
        passed = review.accepted is True and not review.issues and TeachingContentGenerator._fact_accepted(row)
    else:
        passed = (review.accepted is False and actual == targets and unaffected
                  and all(getattr(row.components, name).findings for name in targets))
        if kind in {"scope_error_with_complete_citations", "actual_scope_error"}:
            # A false universal claim may correctly fail BOTH entailment and
            # scope. Complete citation directories are verified in the fixture,
            # not conflated with a requirement that the false claim be entailed.
            passed = passed and all(getattr(row.components, name).scope_exceptions is False for name in targets)
        elif kind == "correct_claim_missing_citation":
            passed = passed and all(getattr(row.components, name).source_entailment is False
                and getattr(row.components, name).scope_exceptions is True for name in targets)
        else:
            raise ValueError("unregistered control kind")
    return {"passed": bool(passed), "actual_failed_components": sorted(actual), "unaffected_components_passed": unaffected}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def register(directory):
    directory.mkdir(parents=True, exist_ok=False)
    entries = {}
    for name, value in build_cases().items():
        path = directory / f"{name}.json"
        write_json(path, value)
        entries[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "expectations": value["expectations"]}
    write_json(directory / "manifest.json", {"purpose": "pre-registered point factual controls; not production acceptance",
        "teaching_version": TEACHING_CONTENT_VERSION, "cases": entries,
        "planner_expectations": {"required_boundary_source": "source_b", "irrelevant_history_source": "source_c"}})


def run(directory, output):
    # No network client is constructed by import, fixture preparation, scoring
    # or --prepare. Only this explicit entry point can incur inference costs.
    import adaptive_learning.personalization.teaching_content as module
    from adaptive_learning.config import get_settings
    from adaptive_learning.llm.client import LLMConfig, OpenAICompatibleClient
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest["teaching_version"] != TEACHING_CONTENT_VERSION:
        raise ValueError("registry version differs from imported implementation")
    raws = {}
    for name, registered in manifest["cases"].items():
        path = directory / f"{name}.json"
        if hashlib.sha256(path.read_bytes()).hexdigest() != registered["sha256"]:
            raise ValueError("registered fixture changed")
        raws[name] = json.loads(path.read_text())
        if raws[name]["expectations"] != registered["expectations"]:
            raise ValueError("registered expectations changed")
    output.mkdir(parents=True, exist_ok=False)
    settings = get_settings()
    if not settings.text_api_key or not settings.teaching_review_configured:
        raise ValueError("generation and independent review must both be explicitly configured; no fallback")
    client = OpenAICompatibleClient(LLMConfig(settings.text_base_url, settings.text_api_key, settings.text_model,
        proxy_url=settings.llm_https_proxy, timeout_seconds=120, thinking_enabled=settings.text_thinking_enabled,
        reasoning_effort=settings.text_reasoning_effort))
    reviewer = OpenAICompatibleClient(LLMConfig(settings.pucoding_base_url, settings.pucoding_api_key,
        settings.teaching_review_model, proxy_url=settings.llm_https_proxy, timeout_seconds=120,
        reasoning_effort="low"))
    calls, raw_responses = 0, 0
    def observe(target, role, identity):
        original, original_parse = target.structured, target._parse_object
        def observed_parse(content):
            nonlocal raw_responses
            raw_responses += 1
            record = {"model_role": role, **identity, "raw_text": content,
                      "characters": len(content) if isinstance(content, str) else None}
            try:
                parsed = original_parse(content)
                record["parsed_single_object"] = True
                return parsed
            except Exception as error:
                record.update(parsed_single_object=False, error_type=type(error).__name__, detail=str(error)[:500])
                raise
            finally:
                write_json(output / f"raw-response-{raw_responses:02}.json", record)
        def observed(**kwargs):
            nonlocal calls
            calls += 1
            call = calls
            write_json(output / f"request-{call:02}.json", {"model_role": role, **identity,
                "system": kwargs["system"], "user": json.loads(kwargs["user"]), "max_tokens": kwargs.get("max_tokens")})
            result = original(**kwargs)
            write_json(output / f"response-{call:02}.json", result)
            return result
        target._parse_object, target.structured = observed_parse, observed
    observe(client, "generation", {"provider": settings.llm_provider, "model": settings.text_model})
    observe(reviewer, "review", settings.teaching_review_identity)
    generator = TeachingContentGenerator(client, review_client=reviewer,
                                          review_identity=settings.teaching_review_identity)
    result = {"teaching_version": TEACHING_CONTENT_VERSION, "provider": settings.teaching_review_provider,
        "model": settings.teaching_review_model, "generation_provider": settings.llm_provider,
        "generation_model": settings.text_model, "source_sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
        "registry_sha256": hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest(),
        "cases": {}, "completed": False}
    started = time.monotonic()
    try:
        # Identical navigation gets one planning call, independent of answers or
        # case identity. Candidate text is introduced only in factual review.
        _, evidence, _, inputs = prepare(next(iter(raws.values())))
        plans = validate_scope_plan(generator.scope_planner.plan(inputs, evidence), inputs, evidence)
        result["scope_plan"] = plans
        conditions = next(iter(plans.values()))
        used_sources = {source for item in conditions for source in item["source_ids"]}
        plan_oracle = manifest["planner_expectations"]
        result["planner_control_passed"] = (plan_oracle["required_boundary_source"] in used_sources
            and (not plan_oracle.get("irrelevant_history_source")
                 or plan_oracle["irrelevant_history_source"] not in used_sources))
        for name, raw in raws.items():
            case_start = time.monotonic()
            try:
                chapter, evidence, points, inputs = prepare(raw)
                for item in inputs:
                    item["scope_conditions"] = deepcopy(plans[item["point_id"]])
                review = generator._fact_review(points, None, inputs, chapter, evidence, [])
                result["cases"][name] = {"completed": True, "review": review.model_dump(), **score(review, raw["expectations"])}
            except Exception as error:
                result["cases"][name] = {"completed": False, "passed": False,
                    "error_type": type(error).__name__, "detail": str(error), "feedback": getattr(error, "review_feedback", None)}
            result["cases"][name]["seconds"] = time.monotonic() - case_start
            write_json(output / "results.json", result)
        result["completed"] = True
        result["all_controls_passed"] = result["planner_control_passed"] and all(value["passed"] for value in result["cases"].values())
    except Exception as error:
        result.update(error_type=type(error).__name__, detail=str(error), all_controls_passed=False)
    finally:
        by_role = {"generation": client.usage_totals(), "review": reviewer.usage_totals()}
        totals = {key: sum(value[key] for value in by_role.values()) for key in by_role["generation"]}
        result.update(seconds=time.monotonic() - started, usage=totals, usage_by_role=by_role,
            limitation="Literal findings/proof checks do not independently establish semantic correctness or full-course quality.")
        write_json(output / "results.json", result)
        client.close()
        reviewer.close()
    print(json.dumps({key: value for key, value in result.items() if key not in {"cases", "scope_plan"}}, ensure_ascii=False))
    return 0 if result.get("all_controls_passed") else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true", help="write immutable control inputs; no models")
    mode.add_argument("--run", action="store_true", help="explicitly call the configured paid text service sequentially")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.prepare:
        register(args.registry)
    else:
        if args.output is None:
            parser.error("--run requires a fresh --output directory")
        raise SystemExit(run(args.registry, args.output))
