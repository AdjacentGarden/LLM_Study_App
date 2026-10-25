"""Regression checks for the observed teaching defects, not just API shape."""
import json
import re
import threading
from collections import Counter
from copy import deepcopy

import pytest

from adaptive_learning.assessment.models import LearnerProfile
from adaptive_learning.ingestion.models import ChapterDraft, SourceQuote
from adaptive_learning.llm.client import LLMError
from adaptive_learning.personalization.generator import ChapterCourseCompiler
from adaptive_learning.personalization.models import SourceCitation
from adaptive_learning.personalization.policy import PersonalizationPolicy
from adaptive_learning.personalization.rubric_alignment import RUBRIC_ALIGNMENT_PROMPT
from adaptive_learning.personalization.teaching_content import (
    FACT_REVIEW_PROMPT,
    GENERATION_PROMPT,
    REVIEW_PROMPT,
    TEACHING_CONTENT_VERSION,
    TeachingContentError,
    TeachingContentGenerator,
    _ComponentFactProof,
    _Lesson,
    _Point,
)
from adaptive_learning.personalization.teaching_evidence import TeachingEvidence
from adaptive_learning.personalization.teaching_fact_wire import restore_fact_payload
from adaptive_learning.personalization.teaching_scope import (
    SCOPE_PLANNING_PROMPT,
    TEACHING_SCOPE_VERSION,
    TeachingScopeError,
)


def course(point_count=2):
    labels = [f"容器{i}中的液体在温度不变时体积保持稳定。" for i in range(point_count)]
    quote = "在温度不变的条件下，密闭容器中的液体体积保持稳定。温度改变时，不能直接套用这一结论，必须重新测量。判断时应先核对条件，再比较测量结果。"
    chapter = ChapterDraft(chapter_id="c", order=1, title="条件与结论",
        start_page=1, end_page=2, summary="本章讨论观测条件和测量结论之间的联系。",
        knowledge_points=labels, source_block_ids=["b1"],
        evidence=[SourceQuote(page_number=1, quote=quote)],
        knowledge_point_evidence={label: [SourceQuote(page_number=1, quote=quote)] for label in labels})
    profile = LearnerProfile(user_id="u", book_id="b")
    profile.constraints.minutes_per_day = 120
    bundle = ChapterCourseCompiler().compile(chapter=chapter, profile=profile,
        decision=PersonalizationPolicy().decide(profile, chapter.chapter_id))
    evidence = TeachingEvidence(
        sources={"s1": SourceCitation(page_number=1, block_id="b1", quote=quote)},
        point_sources={point.point_id: ["s1"] for point in bundle.knowledge_points},
        warnings=["原文图表结构不完整；不猜补数值。"], fingerprint="evidence-sha")
    return bundle, chapter, evidence


def point_candidate(item):
    container = re.search(r"容器\d+", item["label"])[0]
    return {"point_id": item["point_id"], "title": "温度条件与体积判断",
        "explanation": f"判断{container}中液体体积是否稳定，必须先说明观测时温度是否保持不变。这里的结论适用于温度不变的密闭容器，不能脱离这个前提。若温度发生改变，原有结论不能直接套用，需要重新测量。学习时可以先列出已知条件，再判断它们是否满足原文的适用范围；这样能够避免把有条件的结论误当成任何情况下都成立的规律。",
        "source_ids": ["s1"],
        "flashcard": {"card_id": item["card_id"], "front": f"使用{container}的体积稳定结论前，应首先核对什么条件？",
            "back": "先核对温度是否保持不变，并确认讨论的是密闭容器；温度改变时需要重新测量。", "source_ids": ["s1"]},
        "practice": {"item_id": item["item_id"], "prompt": f"假设对{container}的一次观测中温度发生了改变，能否直接沿用原先的体积判断？请给出理由。",
            "expected_answer": "不能直接沿用。原结论要求温度保持不变，当前条件已经改变，因此应重新测量液体体积。",
            "rubric": ["指出温度保持不变是适用前提", "说明条件改变后必须重新测量"], "source_ids": ["s1"]}}


def lesson_candidate():
    return {"opening": "本课先辨认结论的前提，再练习判断条件改变后是否仍然适用。",
        "summary": "有条件的测量结论不能任意推广。判断时先列出温度与容器条件，再逐项核对是否符合原文；条件改变时重新测量。",
        "reading_source_ids": ["s1"],
        "worked_examples": [
            {"title": "条件改变后的判断", "problem": "假设密闭容器内的温度已经改变，应如何处理先前的体积判断？",
             "steps": ["先识别原文前提：稳定结论要求温度保持不变。", "再核对题设：温度已经改变，所以不能直接套用原结论。"],
             "answer": "应重新测量，因为题设不再满足原结论的温度前提。", "source_ids": ["s1"]},
            {"title": "核对稳定结论的前提", "problem": "假设两次观测温度不变且容器密闭，判断时要怎样引用原文？",
             "steps": ["第一步核对题设中的温度条件，温度保持不变符合原文前提。", "第二步核对容器条件，密闭条件也符合原文所讨论的情形。"],
             "answer": "可以在所给条件范围内使用液体体积稳定的结论。", "source_ids": ["s1"]}],
        "checkpoints": [
            {"question": "为什么陈述测量结论时需要同时说明适用前提？",
             "expected_answer": "因为原文结论依赖特定条件，省略前提会把有限范围误写成任何情况都适用。", "source_ids": ["s1"]},
            {"question": "比较两次观测时，可以按什么顺序核对结论能否使用？",
             "expected_answer": "先列出温度和容器条件，再核对是否符合原文的适用范围，条件改变时重新测量。", "source_ids": ["s1"]}]}


def selected_proof(source_ids, data, *, point_id=None, component_name=None):
    checks = []
    if point_id is not None and component_name is not None:
        point = next(point for point in data["candidate"]["points"] if point["point_id"] == point_id)
        fields = point["component_evidence"][component_name]["component_fields"]
        field = {"explanation": "explanation", "flashcard": "back", "practice": "expected_answer"}[component_name]
        checks = [{"boundary_id": boundary["boundary_id"], "is_class_definition": True,
            "assigns_class_property": True, "has_same_class_counterexample": True,
            "candidate_explicitly_limited": True, "claim_field": field,
            "claim_quote": fields[field][:150], "status": "bounded", "limitation_field": field,
            "limitation_quote": fields[field][:150],
            "rationale": "该组件自己的句子已明确温度条件及条件改变后的处理，没有借用别项。"}
            for boundary in point.get("scope_boundaries", [])]
    return {"scope_checks": checks,"source_entailment": True, "scope_exceptions": True, "source_ids": source_ids,
        "rationale": "该组件根据自己引用的实际来源核对适用条件，没有借其他组件补足范围。",
        "support_span_ids": [next(span_id for span_id, span in data["support_spans"].items()
                                   if span["source_id"] == source_id) for source_id in source_ids
                             if any(span["source_id"] == source_id for span in data["support_spans"].values())]}


def restore_legacy_fact_payload(data):
    """Keep old fault injectors readable while production receives the new wire."""
    data = restore_fact_payload(data) if "wire_format" in data else deepcopy(data)
    for point in data["candidate"]["points"]:
        actual = _Point.model_validate({key: value for key, value in point.items() if key in _Point.model_fields})
        for name, ids in (("explanation", actual.source_ids), ("flashcard", actual.flashcard.source_ids),
                          ("practice", actual.practice.source_ids)):
            point["component_evidence"].setdefault(name, {
                "component_fields": TeachingContentGenerator._point_component_fields(actual, name),
                "actual_source_text": [{"source_id": key, "quote": data["sources"][key]["quote"]} for key in ids],
                "allowed_support_span_ids": [key for key, value in data["support_spans"].items() if value["source_id"] in ids]})
    return data


def advertised_fact_response(result, data):
    """Translate only after the subclass has injected its final fault.

    Invalid parent booleans/IDs, a parent-only veto, or unsupported rationale
    stay nested so the ordinary production validator must reject them. The
    adapter never invents approval to hide a negative regression fixture.
    """
    if "components" in result:
        return result
    from adaptive_learning.personalization.teaching_content import _FactSelection
    try:
        parsed = _FactSelection.model_validate(result)
    except (ValueError, TypeError):
        return result
    expected_points = data["output_contract"]["point_ids"]
    if len(parsed.points) != len(expected_points) or {row.point_id for row in parsed.points} != set(expected_points):
        return result
    lesson_ids = set(data["output_contract"]["lesson_component_ids"])
    if bool(lesson_ids) != (parsed.lesson is not None) or (parsed.lesson and set(parsed.lesson.components) != lesson_ids):
        return result
    pending = data["output_contract"].get("component_ids_by_point", {})
    for row in parsed.points:
        requested = set(pending.get(row.point_id, ("explanation", "flashcard", "practice")))
        for name, child in row.components.model_dump().items():
            if name not in requested and not (child["source_entailment"] and child["scope_exceptions"] and not child["findings"]):
                # A fake's veto of a cached sibling must not disappear merely
                # because this request advertises another component for repair.
                return result
    parents = [(row, list(row.components.model_dump().values()), {
        key for point in data["candidate"]["points"] if point["point_id"] == row.point_id
        for key in [*point["source_ids"], *point["flashcard"]["source_ids"], *point["practice"]["source_ids"]]}) for row in parsed.points]
    if parsed.lesson:
        parents.append((parsed.lesson, [value.model_dump() for value in parsed.lesson.components.values()],
                        {key for part in data["lesson_components"].values() for key in part["source_ids"]}))
    all_children = []
    for parent, children, actual_sources in parents:
        all_children.extend(children)
        clean = all(child["source_entailment"] and child["scope_exceptions"] and not child["findings"] for child in children)
        if ((not parent.accepted or parent.issues or getattr(parent, "failed_components", [])) and clean):
            return result
        if any(getattr(parent, flag) is False and all(child[flag] for child in children)
               for flag in ("source_entailment", "scope_exceptions")):
            return result
        if clean and not any(key in parent.support_summary for key in actual_sources):
            return result
    if (not parsed.accepted or parsed.issues) and all(
            child["source_entailment"] and child["scope_exceptions"] and not child["findings"] for child in all_children):
        return result
    flat = []
    for point in result["points"]:
        for name in pending.get(point["point_id"], ("explanation", "flashcard", "practice")):
            flat.append({"point_id": point["point_id"], "component_id": name,
                         "findings": [], **deepcopy(point["components"][name])})
    for name, part in data["lesson_components"].items():
        proof = result["lesson"]["components"][name]
        if proof["scope_exceptions"] is not True or proof.get("findings"):
            # Negative lesson fixtures must inject an explicit assertion finding
            # or matrix cell. Never convert an old component veto into alltrue.
            return result
        assertions = part["assertion_contract"]["assertions"]
        flat.append({"point_id": None, "component_id": name,
            "assertion_checks": [{"assertion_id": assertion["assertion_id"], "is_factual": True,
                "source_entailment": proof["source_entailment"],
                "support_span_ids": deepcopy(proof["support_span_ids"]), "findings": []}
                for assertion in assertions],
            # A deterministic mechanical mock, not a semantic quality claim.
            "scope_matrix": [{"assertion_id": assertion["assertion_id"],
                "checks": [[False, False, False, False, None] for _ in part["scope_matrix_contract"]["boundary_ids"]]}
                for assertion in assertions]})
    return {"components": flat}


def positive_rubric_alignment_response(data):
    """Mechanical wire fixture; no claim about the model's semantic judgment."""
    return {"items": [{
        "item_id": item["item_id"],
        "minimal_sufficient_answer": "根据题干判断适用前提；条件改变时不能直接沿用，应重新测量。",
        "rubric_checks": [{
            "rubric_index": index,
            "rubric_text": text,
            "required": True,
            "reason": "离线模拟逐项返回完整的位置与判定，用于验证审核调用链而非评分质量。",
            "prompt_quote": item["prompt"],
            "rubric_quote": text,
            "counterexample_answer": None,
            "repair": None,
        } for index, text in enumerate(item["rubric"])],
    } for item in data["items"]]}


class Client:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.generation_batches = []
        self.review_calls = 0
        self.fact_calls = 0
        self.rubric_calls = 0
        self.payloads = []
        self.systems = []
        self._mock_lock = threading.RLock()

    def __getattribute__(self, name):
        method = object.__getattribute__(self, name)
        if name != "structured":
            return method

        def adapt(*, system, user, **kwargs):
            # Serialize this legacy fake's counters only. Production review jobs
            # remain concurrent; separate executor tests measure concurrency.
            with self._mock_lock:
                data = json.loads(user)
                if system == RUBRIC_ALIGNMENT_PROMPT:
                    # This exact new role has its own recorded real wire. Old
                    # subclass fault injectors still see their original roles.
                    self.payloads.append(data)
                    self.systems.append(system)
                    self.rubric_calls += 1
                    if self.mode == "outage":
                        raise LLMError("upstream unavailable")
                    return positive_rubric_alignment_response(data)
                if system == FACT_REVIEW_PROMPT:
                    data = restore_legacy_fact_payload(data)
                    user = json.dumps(data, ensure_ascii=False)
                result = method(system=system, user=user, **kwargs)
                if system == GENERATION_PROMPT:
                    from teaching_writer_test_support import advertised_writer_response

                    return advertised_writer_response(result, data)
                return advertised_fact_response(result, data) if system == FACT_REVIEW_PROMPT else result

        return adapt

    def structured(self, *, system, user, **kwargs):
        data = json.loads(user)
        self.payloads.append(data)
        self.systems.append(system)
        if self.mode == "outage":
            raise LLMError("upstream unavailable")
        if system == SCOPE_PLANNING_PROMPT:
            return {"points": [{"point_id": item["point_id"], "comparisons": [
                {"source_id": key, "changes_scope": False,
                 "reason": "该候选没有改变当前测试主题的适用对象或成立条件。"}
                for key in item["scope_context_source_ids"]], "conditions": [
                {"condition": "稳定结论只适用于温度不变的密闭容器；条件改变时重新测量。",
                 "boundary_type": "precondition", "scope_effect": "温度改变后不能再直接使用原有稳定结论。",
                 "source_ids": ["s1"]}]} for item in data["knowledge_points"]]}
        if system == GENERATION_PROMPT:
            self.generation_batches.append([point["point_id"] for point in data["batch_points"]])
            points = [point_candidate(item) for item in data["batch_points"]]
            lesson = lesson_candidate() if data["include_lesson"] else None
            if self.mode == "partial_retry" and self.review_calls:
                for point in points:
                    point["practice"]["expected_answer"] += "判断的关键是先核对前提是否成立。"
                    # These unrelated changes must be discarded: only practice
                    # was rejected by the prior independent teacher review.
                    point["explanation"] += "这个额外段落不应该替换已经通过的讲解。"
                    point["flashcard"]["front"] = "这个已经通过的闪卡为何突然被改写成另一个问题？"
            if self.mode == "lesson_retry" and self.review_calls and lesson:
                lesson["worked_examples"][0]["steps"][0] += "所以这一步需要明确核对题设温度。"
            if self.mode == "duplicate_explanation":
                for point in points:
                    point["explanation"] = "判断液体体积是否稳定，必须先说明观测时温度是否保持不变。这里的结论适用于温度不变的密闭容器，不能脱离这个前提。若温度发生改变，原有结论不能直接套用，需要重新测量。学习时可以先列出已知条件，再判断它们是否满足原文的适用范围；这样能够避免把有条件的结论误当成任何情况下都成立的规律。"
            if self.mode == "duplicate_front":
                for point in points:
                    point["flashcard"]["front"] = "使用体积稳定的结论前，应首先核对什么条件？"
            if self.mode == "duplicate_practice":
                for point in points:
                    point["practice"]["prompt"] = "假设一次观测中温度发生了改变，能否直接沿用原先的体积判断？请给出理由。"
            if self.mode == "duplicate":
                points *= 2
            if self.mode == "missing":
                points = points[:-1]
            if self.mode == "unknown_source":
                points[0]["source_ids"] = ["invented"]
            if self.mode == "invented_quote":
                points[0]["quote"] = "模型伪造的原文"
            if self.mode == "changed_card":
                points[0]["flashcard"]["card_id"] = "wrong-card"
            if self.mode == "label_repeat":
                points[0]["explanation"] = data["batch_points"][0]["label"] * 10
            if self.mode == "answer_leak":
                points[0]["practice"]["prompt"] = points[0]["practice"]["expected_answer"] + "对吗？"
            if self.mode == "empty_rubric":
                points[0]["practice"]["rubric"] = ["回答正确", "表达清晰"]
            if self.mode == "invisible":
                points[0]["explanation"] += "\u200b"
            if self.mode == "task_example" and lesson:
                lesson["worked_examples"][0]["steps"][0] = "先阅读原文，再用自己的话解释上述结论。"
            if self.mode == "duplicate_checkpoint" and lesson:
                lesson["checkpoints"][1] = lesson["checkpoints"][0].copy()
            if self.mode == "practice_checkpoint" and lesson:
                draft_point = points[0] if points else point_candidate(data["all_knowledge_points"][0])
                lesson["checkpoints"][0]["question"] = draft_point["practice"]["prompt"]
            return {"points": points, "lesson": lesson}
        if system == FACT_REVIEW_PROMPT:
            self.fact_calls += 1
            points = data["candidate"]["points"]
            rows = [{"point_id": point["point_id"], "accepted": True,
                "source_entailment": True, "scope_exceptions": True, "issues": [],
                "failed_components": [],
                "support_summary": "讲解、闪卡和练习均由 s1 的温度不变条件支持；已核对全章适用范围。"}
                for point in points]
            for point, verdict in zip(points, rows, strict=True):
                verdict["components"] = {}
                for name, source_ids in (("explanation", point["source_ids"]),
                    ("flashcard", point["flashcard"]["source_ids"]),
                    ("practice", point["practice"]["source_ids"])):
                    verdict["components"][name] = selected_proof(source_ids, data, point_id=point["point_id"], component_name=name)
            result = {"accepted": True, "issues": [], "points": rows,
                "lesson": {"accepted": True, "source_entailment": True, "scope_exceptions": True,
                    "issues": [], "support_summary": "例题和检查题由 s1 中先核对条件再重新测量的关系支持。"}
                    if data["candidate"]["lesson"] is not None else None}
            if result["lesson"] is not None:
                result["lesson"]["components"] = {component_id: selected_proof(part["source_ids"], data)
                    for component_id, part in data["lesson_components"].items()}
            if self.mode == "fact_string_bool" and rows:
                rows[0]["source_entailment"] = "true"
            if self.mode == "fact_missing":
                result["points"] = rows[:-1]
            if self.mode == "fact_duplicate":
                result["points"] = rows * 2
            if self.mode == "fact_reject" and rows:
                rows[0].update(accepted=False, source_entailment=False, failed_components=["practice"], issues=["实际引用 s1 不支持新增的反向推理"])
                result["accepted"] = False
            if self.mode == "fact_wrong_rationale" and rows:
                rows[0]["support_summary"] = "这是一般性常识，因此认为讲解和练习全部正确。"
            return result
        assert system == REVIEW_PROMPT, "unknown model role must not become a teacher review"
        self.review_calls += 1
        reviews = [{"point_id": point["point_id"], "explanation_ok": True,
            "flashcard_ok": True, "practice_ok": True, "evidence_ok": True,
            "issues": []} for point in data["candidate"]["points"]]
        result = {"accepted": True, "issues": [], "points": reviews,
            "lesson": {"reading_ok": True, "examples_ok": True,
                       "checkpoints_ok": True, "evidence_ok": True, "issues": []}}
        if self.mode == "string_bool":
            result["accepted"] = "true"
        if self.mode == "missing_review":
            result["points"] = reviews[:-1]
        if self.mode == "duplicate_review":
            result["points"] = reviews * 2
        if self.mode == "issues_with_true":
            reviews[0]["issues"] = ["遗漏适用前提"]
        if self.mode == "rejected" or (self.mode in {"partial_retry", "unchanged_point_retry"} and self.review_calls == 1):
            result["accepted"] = False
            reviews[-1]["practice_ok"] = False
            reviews[-1]["issues"] = ["practice：练习漏写条件改变后重新测量"]
        if self.mode in {"lesson_retry", "unchanged_lesson_retry"} and self.review_calls == 1:
            result["accepted"] = False
            result["lesson"]["examples_ok"] = False
            result["lesson"]["issues"] = ["lesson.worked_examples[0]：例题解答需要列出温度前提"]
        return result


def calls_for(client, system):
    return [data for actual, data in zip(client.systems, client.payloads, strict=True) if actual == system]


def test_unknown_mock_role_cannot_silently_consume_a_teacher_review():
    client = Client()
    with pytest.raises(AssertionError, match="unknown model role"):
        client.structured(system="unknown model role", user="{}")
    assert client.review_calls == client.fact_calls == client.rubric_calls == 0


def point_batches(client):
    return [batch for batch in client.generation_batches if batch]


def fact_targets(client):
    result = []
    for data in calls_for(client, FACT_REVIEW_PROMPT):
        contract = data["output_contract"]
        for owner in contract["point_ids"]:
            result.extend((owner, name) for name in contract.get("component_ids_by_point", {}).get(
                owner, ("explanation", "flashcard", "practice")))
        result.extend((None, name) for name in contract["lesson_component_ids"])
    return Counter(result)


def test_complete_course_replaces_templates_only_after_independent_review():
    original, chapter, evidence = course()
    before = original.model_dump_json()
    client = Client()
    output = TeachingContentGenerator(client).generate(original, chapter, evidence)
    assert client.review_calls == 1
    assert client.rubric_calls == 1
    rubric_payload = calls_for(client, RUBRIC_ALIGNMENT_PROMPT)[0]
    assert rubric_payload["output_contract"]["item_ids"] == [item.item_id for item in original.practice_items]
    assert rubric_payload["format_feedback"] == []
    assert all("expected_answer" not in item for item in rubric_payload["items"])
    expected = {(point.point_id, name) for point in original.knowledge_points
                for name in ("explanation", "flashcard", "practice")}
    expected.update((None, key) for key in ("opening", "summary", "example_1", "example_2", "checkpoint_1", "checkpoint_2"))
    assert fact_targets(client) == Counter({key: 1 for key in expected})
    assert len(point_batches(client)) == 1
    assert sum(data["include_lesson"] for data in calls_for(client, GENERATION_PROMPT)) == 1
    assert original.model_dump_json() == before
    assert output.teaching_quality_version == TEACHING_CONTENT_VERSION
    assert output.teaching_fact_review["accepted"] is True
    assert output.teaching_evidence_fingerprint == evidence.fingerprint
    assert all(payload["source_warnings"] == evidence.warnings
               for system, payload in zip(client.systems, client.payloads, strict=True)
               if system != RUBRIC_ALIGNMENT_PROMPT)
    assert output.course_id == original.course_id
    for original_point, point in zip(original.knowledge_points, output.knowledge_points, strict=True):
        assert point.point_id == original_point.point_id
        assert len(point.explanation) > len(original_point.explanation) + 50
        assert point.citations == [evidence.sources["s1"]]
    assert [card.card_id for card in output.flashcards] == [card.card_id for card in original.flashcards]
    assert [item.item_id for item in output.practice_items] == [item.item_id for item in original.practice_items]
    assert output.worked_examples[0].content.startswith("题目：")
    assert "1. " in output.worked_examples[0].content and "2. " in output.worked_examples[0].content
    assert "结论：" in output.worked_examples[0].content
    assert evidence.warnings[0] in output.unresolved_source_warnings


@pytest.mark.parametrize("mode", ["duplicate", "missing", "unknown_source", "invented_quote",
    "changed_card", "label_repeat", "answer_leak", "empty_rubric", "invisible", "task_example",
    "duplicate_checkpoint", "practice_checkpoint", "string_bool", "missing_review", "duplicate_review",
    "issues_with_true", "rejected", "outage", "duplicate_explanation", "duplicate_front", "duplicate_practice",
    "fact_string_bool", "fact_missing", "fact_duplicate", "fact_reject", "fact_wrong_rationale"])
def test_regressions_and_malformed_reviews_fail_closed(mode):
    bundle, chapter, evidence = course()
    before = bundle.model_dump_json()
    with pytest.raises(TeachingContentError):
        TeachingContentGenerator(Client(mode), max_attempts=2).generate(bundle, chapter, evidence)
    assert bundle.model_dump_json() == before


def test_partial_rejection_only_regenerates_rejected_point_then_reviews_whole_course():
    bundle, chapter, evidence = course(3)
    client = Client("partial_retry")
    output = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    assert len(output.knowledge_points) == 3
    assert [len(batch) for batch in point_batches(client)] == [3, 1]
    assert client.review_calls == 2
    repair = next(data for data in calls_for(client, GENERATION_PROMPT) if data["previous_candidate"] is not None)
    assert repair["review_feedback"]
    assert repair["previous_candidate"]
    assert repair["include_lesson"] is False
    assert all(len(data["candidate"]["points"]) == 3 for data in calls_for(client, REVIEW_PROMPT))
    assert any(item.expected_answer.endswith("判断的关键是先核对前提是否成立。") for item in output.practice_items)
    assert all("这个额外段落" not in point.explanation for point in output.knowledge_points)
    assert all("突然被改写" not in card.front for card in output.flashcards)
    assert repair["allowed_repairs"] == {repair["batch_points"][0]["point_id"]: ["practice"]}


def test_lesson_only_rejection_keeps_approved_points():
    bundle, chapter, evidence = course(3)
    client = Client("lesson_retry")
    output = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    assert [len(batch) for batch in point_batches(client)] == [3]
    lesson_repairs = [data for data in calls_for(client, GENERATION_PROMPT) if data["previous_candidate"] is not None]
    assert len(lesson_repairs) == 1 and lesson_repairs[0]["include_lesson"] is True
    assert lesson_repairs[0]["batch_points"] == []
    assert client.review_calls == 2
    assert "所以这一步需要明确核对题设温度。" in output.worked_examples[0].content


@pytest.mark.parametrize("mode", ["unchanged_point_retry", "unchanged_lesson_retry"])
def test_identical_rejected_content_cannot_be_approved_by_a_later_permissive_review(mode):
    bundle, chapter, evidence = course(3)
    client = Client(mode)
    with pytest.raises(TeachingContentError, match="未通过"):
        TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    # The fixture reviewer would approve its second invocation. Unchanged
    # rejected content must be rejected before reaching that invocation.
    assert client.review_calls == 1
    if mode == "unchanged_point_retry":
        assert [len(batch) for batch in point_batches(client)] == [3, 1, 1]
    else:
        assert [len(batch) for batch in point_batches(client)] == [3]
        assert len([data for data in calls_for(client, GENERATION_PROMPT) if data["previous_candidate"] is not None]) == 2


def test_duplicate_content_is_rejected_even_when_points_are_in_separate_batches():
    bundle, chapter, evidence = course(13)
    assert len(bundle.knowledge_points) > 6
    client = Client("duplicate_explanation")
    with pytest.raises(TeachingContentError):
        TeachingContentGenerator(client, max_attempts=1).generate(bundle, chapter, evidence)
    assert len(client.generation_batches) > 1
    assert client.review_calls == 0


def test_point_batches_are_bounded_and_all_context_is_available_to_each_call():
    bundle, chapter, evidence = course(13)
    client = Client()
    output = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    assert len(output.knowledge_points) == len(bundle.knowledge_points)
    assert client.rubric_calls == 5
    rubric_payloads = calls_for(client, RUBRIC_ALIGNMENT_PROMPT)
    assert Counter(item["item_id"] for payload in rubric_payloads for item in payload["items"]) == Counter(
        {item.item_id: 1 for item in bundle.practice_items})
    assert sorted(len(payload["items"]) for payload in rubric_payloads) == [1, 3, 3, 3, 3]
    assert len(client.generation_batches[0]) <= 6
    assert all(len(batch) <= 6 for batch in client.generation_batches)
    for system, data in zip(client.systems, client.payloads, strict=True):
        if system == RUBRIC_ALIGNMENT_PROMPT:
            assert "sources" not in data
            assert all("expected_answer" not in item for item in data["items"])
            continue
        assert data["sources"]["s1"]["quote"] == evidence.sources["s1"].quote
    generated = [data for data in client.payloads if "batch_points" in data]
    assert generated[-1]["include_lesson"]
    assert not any(data["include_lesson"] for data in generated[:-1])


def test_absent_model_or_missing_point_evidence_cannot_publish_templates():
    bundle, chapter, evidence = course()
    with pytest.raises(TeachingContentError):
        TeachingContentGenerator(None).generate(bundle, chapter, evidence)
    evidence.point_sources.clear()
    with pytest.raises(TeachingContentError):
        TeachingContentGenerator(Client()).generate(bundle, chapter, evidence)


def test_input_duplicate_card_ids_fail_before_model_is_called():
    bundle, chapter, evidence = course()
    bundle.flashcards[1].card_id = bundle.flashcards[0].card_id
    client = Client()
    with pytest.raises(TeachingContentError):
        TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    assert client.payloads == []


def test_exact_raw_citations_survive_display_line_joining():
    bundle, chapter, evidence = course()
    evidence.sources["s1"].quote = "温度保持不变时，密闭容器中的液体\n体积稳定。条件改变时需要重新测量。"
    output = TeachingContentGenerator(Client()).generate(bundle, chapter, evidence)
    assert "液体体积" in output.original_reading[0].content
    assert "液体\n体积" in output.original_reading[0].citations[0].quote


def test_review_sees_actual_selected_citations_and_final_reading_without_immutable_navigation_hints():
    bundle, chapter, evidence = course(2)
    evidence.sources["s2"] = SourceCitation(page_number=2, quote="判断时先核对温度和密闭条件，条件改变后重新测量。")
    evidence.point_sources[bundle.knowledge_points[1].point_id] = ["s2"]
    client = Client()
    output = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    reviewed = calls_for(client, REVIEW_PROMPT)[0]
    assert all(set(item) == {"point_id", "label"} for item in reviewed["knowledge_points"])
    # The editor chose s1, while immutable navigation for the second point was
    # s2. Review must examine its actual s1 citation, not demand changing s2.
    assert all(point["source_ids"] == ["s1"] for point in reviewed["candidate"]["points"])
    assert reviewed["candidate"]["lesson"]["reading_source_ids"] == ["s1", "s2"]
    assert [section.citations[0] for section in output.original_reading] == [evidence.sources["s1"], evidence.sources["s2"]]


def test_fact_audit_blocks_unsupported_extra_then_accepts_citation_only_repair_before_publication():
    bundle, chapter, evidence = course(2)
    evidence.sources["s2"] = SourceCitation(page_number=2, quote="每次测量前应记录观测时刻，以便区分两次观测。")
    target_id = bundle.knowledge_points[0].point_id

    class CitationRepairClient(Client):
        def structured(self, *, system, user, **kwargs):
            data = json.loads(user)
            result = super().structured(system=system, user=user, **kwargs)
            if system == GENERATION_PROMPT:
                for point in result["points"]:
                    if point["point_id"] == target_id:
                        point["explanation"] += "每次测量前还要记录观测时刻，以便区分两次观测。"
                        if self.fact_calls:
                            point["source_ids"].append("s2")
            elif system == FACT_REVIEW_PROMPT:
                for point, verdict in zip(data["candidate"]["points"], result["points"], strict=True):
                    if point["point_id"] == target_id:
                        if "s2" not in point["source_ids"]:
                            verdict.update(accepted=False, source_entailment=False,
                                failed_components=["explanation"],
                                issues=["讲解中的记录观测时刻不受当前 s1 支持；真正来源是 s2，应增加到讲解引用。"])
                            verdict["components"]["explanation"].update(source_entailment=False, findings=[{
                                "kind": "unsupported_claim", "candidate_field": "explanation",
                                "candidate_quote": "每次测量前还要记录观测时刻，以便区分两次观测。",
                                "issue": "记录观测时刻没有获得讲解自身引用支持，需添加s2。",
                                "evidence_source_ids": ["s2"]}])
                            result["accepted"] = False
                        else:
                            verdict["support_summary"] = "讲解的记录观测时刻由 s2 明确支持；其余条件关系由 s1 支持。"
            return result

    client = CitationRepairClient()
    output = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    assert [len(batch) for batch in point_batches(client)] == [2, 1]
    assert fact_targets(client)[(target_id, "explanation")] == 2
    assert all(count == 1 for pair, count in fact_targets(client).items() if pair != (target_id, "explanation"))
    assert client.review_calls == 2  # Both reviewers examine each same candidate; neither can override a veto.
    repaired = next(point for point in output.knowledge_points if point.point_id == target_id)
    assert repaired.citations == [evidence.sources["s1"], evidence.sources["s2"]]
    factual_payloads = [data for system, data in zip(client.systems, client.payloads, strict=True)
                        if system == FACT_REVIEW_PROMPT and data["candidate"]["points"]]
    repair_reviews = [data for data in factual_payloads if data["previous_review_feedback"]]
    assert len(repair_reviews) == 1
    assert repair_reviews[0]["output_contract"]["point_ids"] == [target_id]
    assert output.teaching_fact_review["accepted"] is True


def test_malformed_review_retries_audit_without_regenerating_valid_course():
    bundle, chapter, evidence = course(2)

    class InvalidFirstFactReview(Client):
        def structured(self, *, system, user, **kwargs):
            result = super().structured(system=system, user=user, **kwargs)
            if system == FACT_REVIEW_PROMPT and result["points"] and not getattr(self, "damaged", False):
                self.damaged = True
                result["points"] = []
            return result

    client = InvalidFirstFactReview()
    output = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    assert output.teaching_fact_review["accepted"] is True
    assert len(point_batches(client)) == 1
    factual = calls_for(client, FACT_REVIEW_PROMPT)
    point_reviews = [data for data in factual if data["candidate"]["points"]]
    assert len(point_reviews) == 3
    retry = next(data for data in point_reviews if data["format_feedback"])
    assert Counter(tuple(data["output_contract"]["point_ids"]) for data in point_reviews) == {
        (point.point_id,): (2 if point.point_id == retry["output_contract"]["point_ids"][0] else 1)
        for point in bundle.knowledge_points
    }
    assert len([data for data in factual if data["format_feedback"]]) == 1
    assert client.review_calls == 1


def test_factual_review_approval_requires_explicit_scope_and_empty_failed_components():
    bundle, chapter, evidence = course(2)

    class InconsistentFactReview(Client):
        def structured(self, *, system, user, **kwargs):
            result = super().structured(system=system, user=user, **kwargs)
            if system == FACT_REVIEW_PROMPT and result["points"]:
                result["points"][0]["scope_exceptions"] = False
                result["points"][0]["failed_components"] = ["explanation"]
            return result

    client = InconsistentFactReview()
    with pytest.raises(TeachingContentError):
        TeachingContentGenerator(client, max_attempts=2).generate(bundle, chapter, evidence)
    assert client.review_calls == 1  # Concurrent teaching review cannot authorize a rejected fact result.


def test_malformed_partial_repair_keeps_other_points_and_lesson_then_retries_same_target():
    bundle, chapter, evidence = course(3)

    class MalformedPartialRepair(Client):
        def structured(self, *, system, user, **kwargs):
            data = json.loads(user)
            result = super().structured(system=system, user=user, **kwargs)
            if (system == GENERATION_PROMPT and data["batch_points"] and data["previous_candidate"] is not None
                    and not getattr(self, "damaged", False)):
                self.damaged = True
                result["points"] = []
            return result

    client = MalformedPartialRepair("partial_retry")
    output = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    assert [len(batch) for batch in point_batches(client)] == [3, 1, 1]
    assert point_batches(client)[1] == point_batches(client)[2]
    repairs = [data for data in calls_for(client, GENERATION_PROMPT) if data["previous_candidate"] is not None]
    assert [data["include_lesson"] for data in repairs] == [False, False]
    assert len(output.knowledge_points) == 3


def test_malformed_initial_batch_preserves_successful_earlier_batches():
    bundle, chapter, evidence = course(13)

    class MalformedSecondBatch(Client):
        def structured(self, *, system, user, **kwargs):
            result = super().structured(system=system, user=user, **kwargs)
            if system == GENERATION_PROMPT and len(self.generation_batches) == 2:
                result["points"] = []
            return result

    client = MalformedSecondBatch()
    TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    assert [len(batch) for batch in point_batches(client)] == [6, 6, 6, 1]
    assert not set(point_batches(client)[0]) & set(point_batches(client)[2])
    assert point_batches(client)[1] == point_batches(client)[2]


def test_teacher_receives_previous_pedagogical_feedback_and_factual_feedback_stays_in_scope():
    bundle, chapter, evidence = course(13)
    client = Client("partial_retry")
    TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    teacher_payloads = [data for system, data in zip(client.systems, client.payloads, strict=True) if system == REVIEW_PROMPT]
    assert teacher_payloads[0]["previous_review_feedback"] == []
    assert teacher_payloads[1]["previous_review_feedback"][0]["issues"] == ["practice：练习漏写条件改变后重新测量"]
    for system, data in zip(client.systems, client.payloads, strict=True):
        if system != FACT_REVIEW_PROMPT or not data["previous_review_feedback"]:
            continue
        batch_ids = {point["point_id"] for point in data["candidate"]["points"]}
        for row in data["previous_review_feedback"]:
            assert not row.get("point_id") or row["point_id"] in batch_ids


def test_generation_repair_receives_only_current_batch_candidate_and_flat_scoped_feedback():
    bundle, chapter, evidence = course(13)
    client = Client("partial_retry")
    TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    generations = [data for data in client.payloads if "batch_points" in data]
    repair = generations[-1]
    assert len(repair["batch_points"]) == len(repair["previous_candidate"]["points"]) == 1
    assert repair["previous_candidate"]["lesson"] is None
    assert not any("previous_review_feedback" in row for row in repair["review_feedback"])


def test_nonlesson_factual_batch_can_omit_null_lesson_but_last_batch_cannot_omit_review():
    bundle, chapter, evidence = course(5)

    class OmitNullLesson(Client):
        def structured(self, *, system, user, **kwargs):
            result = super().structured(system=system, user=user, **kwargs)
            if system == FACT_REVIEW_PROMPT and result["lesson"] is None:
                del result["lesson"]
            return result

    client = OmitNullLesson()
    TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    assert len([data for data in calls_for(client, FACT_REVIEW_PROMPT)
                if data["candidate"]["points"]]) == len(bundle.knowledge_points)
    assert all(count == 1 for count in fact_targets(client).values())
    assert client.review_calls == 1

    class OmitRequiredLesson(Client):
        def structured(self, *, system, user, **kwargs):
            result = super().structured(system=system, user=user, **kwargs)
            if system == FACT_REVIEW_PROMPT:
                result.pop("lesson", None)
            return result

    missing = OmitRequiredLesson()
    with pytest.raises(TeachingContentError):
        TeachingContentGenerator(missing, max_attempts=2).generate(bundle, chapter, evidence)
    assert missing.review_calls == 1


def test_short_specific_rubric_items_are_valid_without_padding():
    bundle, chapter, evidence = course(2)

    class ShortRubric(Client):
        def structured(self, *, system, user, **kwargs):
            result = super().structured(system=system, user=user, **kwargs)
            if system == GENERATION_PROMPT:
                for point in result["points"]:
                    container = re.search(r"容器\d+", point["explanation"])[0]
                    point["practice"]["prompt"] = f"假设{container}温度已经改变。方案A直接沿用原来的体积结论，方案B重新测量。应选哪种处理并说明理由？"
                    point["practice"]["expected_answer"] = "应选B。原有结论要求温度保持不变，温度已经改变时需要重新测量。"
                    point["practice"]["rubric"] = ["选出B", "说明温度条件"]
            return result

    output = TeachingContentGenerator(ShortRubric()).generate(bundle, chapter, evidence)
    assert output.practice_items[0].rubric == ["选出B", "说明温度条件"]


@pytest.mark.parametrize("fault", ["borrowed_citation", "invented_support_quote", "hidden_scope_failure"])
def test_each_component_needs_its_own_real_citations_exact_quotes_and_scope_approval(fault):
    bundle, chapter, evidence = course(2)
    evidence.sources["other"] = SourceCitation(page_number=2, quote="这是其他来源中的文字，与当前闪卡实际引用不同。")

    class InvalidComponentProof(Client):
        def structured(self, *, system, user, **kwargs):
            data = json.loads(user)
            result = super().structured(system=system, user=user, **kwargs)
            if system == FACT_REVIEW_PROMPT and result["points"]:
                proof = result["points"][0]["components"]["flashcard"]
                if fault == "borrowed_citation":
                    proof["source_ids"] = ["other"]
                    proof["support_span_ids"] = [next(key for key, value in TeachingContentGenerator._support_spans(evidence).items()
                                                     if value["source_id"] == "other")]
                    actual = data["candidate"]["points"][0]["flashcard"]
                    if "other" in actual["source_ids"]:
                        # An exact citation edit is still unapproved. This
                        # second semantic review finds the source unrelated.
                        proof["source_entailment"] = False
                        proof["findings"] = [{
                            "kind": "invalid_inference", "candidate_field": "back",
                            "candidate_quote": actual["back"][:40],
                            "issue": "补入的原文仍不支持本闪卡的具体推理，不能仅因引用存在而通过。",
                            "evidence_source_ids": ["other"],
                        }]
                elif fault == "invented_support_quote":
                    proof["support_span_ids"] = ["invented_span"]
                else:
                    proof["scope_exceptions"] = False
            return result

    client = InvalidComponentProof()
    with pytest.raises(TeachingContentError):
        TeachingContentGenerator(client, max_attempts=2).generate(bundle, chapter, evidence)
    assert client.review_calls == (2 if fault == "borrowed_citation" else 1)


def test_later_fact_batch_format_repair_does_not_repeat_earlier_approved_batches():
    bundle, chapter, evidence = course(7)
    target = bundle.knowledge_points[3].point_id

    class BadQuoteOnceInSecondBatch(Client):
        def structured(self, *, system, user, **kwargs):
            result = super().structured(system=system, user=user, **kwargs)
            if (system == FACT_REVIEW_PROMPT and any(point["point_id"] == target for point in result["points"])
                    and not getattr(self, "damaged", False)):
                self.damaged = True
                result["points"][0]["components"]["flashcard"]["support_span_ids"] = ["invented_span"]
            return result

    client = BadQuoteOnceInSecondBatch()
    TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    facts = [data for system, data in zip(client.systems, client.payloads, strict=True) if system == FACT_REVIEW_PROMPT]
    repaired = [data for data in facts if data["format_feedback"]]
    assert len(repaired) == 1 and target in repaired[0]["output_contract"]["point_ids"]
    assert all(count == (2 if pair[0] in repaired[0]["output_contract"]["point_ids"] else 1)
               for pair, count in fact_targets(client).items())
    assert [len(batch) for batch in point_batches(client)] == [6, 1]
    assert client.review_calls == 1


def test_exhausted_fact_format_repair_fails_without_outer_reaudit_or_teaching_regeneration():
    bundle, chapter, evidence = course(7)
    target = bundle.knowledge_points[3].point_id

    class BadSecondBatch(Client):
        def structured(self, *, system, user, **kwargs):
            result = super().structured(system=system, user=user, **kwargs)
            if system == FACT_REVIEW_PROMPT and any(point["point_id"] == target for point in result["points"]):
                result["points"][0]["components"]["practice"]["support_span_ids"] = ["invented_span"]
            return result

    client = BadSecondBatch()
    with pytest.raises(TeachingContentError, match="本批事实审查"):
        TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    facts = calls_for(client, FACT_REVIEW_PROMPT)
    failed_batch = [data for data in facts if target in data["output_contract"]["point_ids"]]
    assert len(failed_batch) == 2 and failed_batch[1]["format_feedback"]
    failed_owners = set(failed_batch[0]["output_contract"]["point_ids"])
    assert all(count == (2 if pair[0] in failed_owners else 1)
               for pair, count in fact_targets(client).items())
    assert [len(batch) for batch in point_batches(client)] == [6, 1]
    assert client.review_calls == 1


@pytest.mark.parametrize("source,quoted", [
    ("温度保持不变时，密闭容器中的液体\n体积保持稳定。", "温度保持不变时，密闭容器中的液体体积保持稳定。"),
    ("密闭 容器内的液体，在温度不变时体积保持稳定。", "密闭容器内的液体，在温度不变时体积保持稳定。"),
    ("温度不变时，密闭容器中的液体体积保持稳定。", "温度不变时，密闭容器中的液体\n体积 保持稳定。"),
])
def test_whitespace_alignment_restores_exact_contiguous_raw_evidence(source, quoted):
    _, _, evidence = course(1)
    evidence.sources["s1"].quote = source
    before = evidence.sources["s1"].model_dump_json()
    proof = _ComponentFactProof(source_entailment=True, scope_exceptions=True,
        source_ids=["s1"], rationale="引用本来源中温度不变与密闭条件，结论没有超出该前提。",
        support_quotes=[{"source_id": "s1", "quote": quoted}])
    TeachingContentGenerator._validate_component_proof(proof, {"s1"}, evidence)
    assert proof.support_quotes[0].quote == source
    assert evidence.sources["s1"].model_dump_json() == before
    assert proof.support_quotes[0].quote in evidence.sources["s1"].quote


@pytest.mark.parametrize("source,quoted", [
    ("只能识别并转运一种物质。", "每种载体只能识别并转运一种物质。"),
    ("先核对温度\n图示标签\n然后重新测量。", "先核对温度然后重新测量。"),
    ("温度不变时体积保持稳定。", "温度改变时体积保持稳定。"),
    ("先核对条件，最后记录结果。", "先核对条件。最后记录结果。"),
])
def test_whitespace_alignment_never_adds_or_deletes_nonwhitespace_characters(source, quoted):
    _, _, evidence = course(1)
    evidence.sources["s1"].quote = source
    proof = _ComponentFactProof(source_entailment=True, scope_exceptions=True,
        source_ids=["s1"], rationale="审校声明有支持，但引文必须仍然逐字来自当前来源。",
        support_quotes=[{"source_id": "s1", "quote": quoted}])
    with pytest.raises(TeachingContentError, match="逐字原文"):
        TeachingContentGenerator._validate_component_proof(proof, {"s1"}, evidence)


def test_whitespace_alignment_cannot_stitch_two_source_spans():
    _, _, evidence = course(1)
    evidence.sources["s1"].quote = "温度不变时，可使用该结论。"
    evidence.sources["s2"] = SourceCitation(page_number=2, quote="条件改变后需要重新测量。")
    proof = _ComponentFactProof(source_entailment=True, scope_exceptions=True,
        source_ids=["s1", "s2"], rationale="即使两个来源分别真实存在，也不能拼成某一个来源中的连续原文。",
        support_quotes=[{"source_id": "s1", "quote": "温度不变时，可使用该结论。条件改变后需要重新测量。"},
                        {"source_id": "s2", "quote": "条件改变后需要重新测量。"}])
    with pytest.raises(TeachingContentError, match="逐字原文"):
        TeachingContentGenerator._validate_component_proof(proof, {"s1", "s2"}, evidence)


def factual_fixture(client, point_count=2):
    bundle, chapter, evidence = course(point_count)
    generator = TeachingContentGenerator(client)
    inputs = generator._inputs(bundle, evidence)
    points = [_Point.model_validate(point_candidate(item)) for item in inputs]
    lesson = _Lesson.model_validate(lesson_candidate())
    return generator, points, lesson, inputs, chapter, evidence


@pytest.mark.parametrize("fault", ["global_veto", "point_veto", "scope_veto", "failed_component",
                                  "string_boolean", "missing_id", "duplicate_id", "rationale"])
def test_fixture_wire_adapter_never_erases_a_parent_rejection_or_invalid_envelope(fault):
    client = Client()
    generator, points, lesson, inputs, chapter, evidence = factual_fixture(client)
    payload = generator._build_fact_jobs(points, None, inputs, chapter, evidence, [])[0]["payload"]
    # Deliberately call the unwrapped base method to obtain the old envelope,
    # inject the regression, then exercise the test adapter in isolation.
    nested = Client.structured(client, system=FACT_REVIEW_PROMPT, user=json.dumps(payload))
    if fault == "global_veto":
        nested["accepted"] = False
    elif fault == "point_veto":
        nested["points"][0]["accepted"] = False
    elif fault == "scope_veto":
        nested["points"][0]["scope_exceptions"] = False
    elif fault == "failed_component":
        nested["points"][0]["failed_components"] = ["practice"]
    elif fault == "string_boolean":
        nested["points"][0]["source_entailment"] = "true"
    elif fault == "missing_id":
        nested["points"].pop()
    elif fault == "duplicate_id":
        nested["points"].append(deepcopy(nested["points"][0]))
    else:
        nested["points"][0]["support_summary"] = "这是一般性常识，不依赖实际教材来源。"
    converted = advertised_fact_response(nested, payload)
    assert converted == nested and "components" not in converted


def test_fixture_adapter_cannot_discard_a_cached_sibling_veto():
    client = Client()
    generator, points, lesson, inputs, chapter, evidence = factual_fixture(client)
    payload = generator._build_fact_jobs(points, None, inputs, chapter, evidence, [])[0]["payload"]
    nested = Client.structured(client, system=FACT_REVIEW_PROMPT, user=json.dumps(payload))
    first = nested["points"][0]
    first.update(accepted=False, source_entailment=False, issues=["已缓存的闪卡仍有事实错误。"], failed_components=["flashcard"])
    first["components"]["flashcard"]["source_entailment"] = False
    payload["output_contract"]["component_ids_by_point"] = {point.point_id: ["practice"] for point in points}
    converted = advertised_fact_response(nested, payload)
    assert converted == nested and "components" not in converted


def test_support_span_ids_are_stable_short_and_preserve_raw_diagram_text():
    _, _, evidence = course(1)
    raw = ("温度保持不变时\n图注夹字\n，液体体积保持稳定。" * 20)[:499]
    evidence.sources["s1"] = SourceCitation(page_number=1, quote=raw)
    before = evidence.sources["s1"].model_dump_json()
    spans = TeachingContentGenerator._support_spans(evidence)
    assert spans == TeachingContentGenerator._support_spans(evidence)
    assert len(spans) >= 3
    assert all(6 <= len(span["quote"]) <= 200 and span["quote"] in raw
               and span["source_id"] == "s1" for span in spans.values())
    assert any("图注夹字" in span["quote"] for span in spans.values())
    assert evidence.sources["s1"].model_dump_json() == before


def test_model_selects_ids_and_program_materializes_exact_raw_quotes():
    bundle, chapter, evidence = course(2)
    evidence.sources["s1"].quote = "温度不变时，密闭容器中的液体\n图示中的文字\n体积稳定。条件改变时重新测量。"
    output = TeachingContentGenerator(Client()).generate(bundle, chapter, evidence)
    proof = output.teaching_fact_review["points"][0]["components"]["explanation"]
    assert proof["support_quotes"] == [{"source_id": "s1", "quote": evidence.sources["s1"].quote}]
    assert "support_span_ids" not in proof


def test_rejected_component_can_report_missing_support_without_inventing_any_proof():
    class UnsupportedNoProof(Client):
        def structured(self, *, system, user, **kwargs):
            result = super().structured(system=system, user=user, **kwargs)
            if system == FACT_REVIEW_PROMPT and result["points"]:
                row = result["points"][0]
                row.update(accepted=False, source_entailment=False, failed_components=["practice"],
                           issues=["该练习额外要求的结论没有获得其实际引用支持，需删除或补充真实来源。"])
                row["components"]["practice"].update(source_entailment=False, source_ids=[], support_span_ids=[])
                row["components"]["practice"]["findings"] = [{
                    "kind": "invalid_inference", "candidate_field": "expected_answer",
                    "candidate_quote": "不能直接沿用。", "issue": "该题的推断没有获得实际引用中的对应前提支持。",
                    "evidence_source_ids": ["s1"]}]
                result["accepted"] = False
            return result

    client = UnsupportedNoProof()
    generator, points, lesson, inputs, chapter, evidence = factual_fixture(client)
    review = generator._fact_review(points, lesson, inputs, chapter, evidence, [])
    assert all(count == 1 for count in fact_targets(client).values())  # No format repair for a located veto.
    assert not generator._fact_accepted(review.points[0])
    assert review.points[0].components.practice.support_quotes == []
    assert review.points[0].issues


@pytest.mark.parametrize("fault", ["approved_without_proof", "rejected_without_issues", "model_writes_raw_quote"])
def test_span_selection_cannot_bypass_review_contract(fault):
    class BadSelection(Client):
        def structured(self, *, system, user, **kwargs):
            result = super().structured(system=system, user=user, **kwargs)
            if system == FACT_REVIEW_PROMPT and result["points"]:
                row = result["points"][0]
                proof = row["components"]["explanation"]
                if fault == "approved_without_proof":
                    proof["support_span_ids"] = []
                elif fault == "rejected_without_issues":
                    proof.update(source_entailment=False, source_ids=[], support_span_ids=[])
                else:
                    proof["support_quotes"] = [{"source_id": "s1", "quote": "模型自己复制的内容不能作为已选择的原文证明。"}]
            return result

    generator, points, lesson, inputs, chapter, evidence = factual_fixture(BadSelection())
    with pytest.raises(TeachingContentError):
        generator._fact_review(points, lesson, inputs, chapter, evidence, [])


def test_span_cannot_be_borrowed_from_another_components_valid_citation():
    class BorrowOtherComponentsSpan(Client):
        target_id = None

        def structured(self, *, system, user, **kwargs):
            data = json.loads(user)
            result = super().structured(system=system, user=user, **kwargs)
            if (system == FACT_REVIEW_PROMPT and result["points"]
                    and result["points"][0]["point_id"] == self.target_id):
                result["points"][0]["components"]["flashcard"] = selected_proof(["s2"], data)
            return result

    client = BorrowOtherComponentsSpan()
    generator, points, lesson, inputs, chapter, evidence = factual_fixture(client)
    client.target_id = points[0].point_id
    evidence.sources["s2"] = SourceCitation(page_number=2, quote="测量前记录温度条件，以便区分观测范围。")
    points[0].source_ids.append("s2")  # Valid for explanation, not for flashcard.
    review = generator._fact_review(points, lesson, inputs, chapter, evidence, [])
    assert not generator._fact_accepted(review.points[0])
    assert "flashcard" in review.points[0].failed_components
    assert any("s2" in issue for issue in review.points[0].issues)


def test_lesson_fact_review_is_separate_and_every_fixed_component_has_a_proof():
    bundle, chapter, evidence = course(2)
    client = Client()
    output = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    factual = [data for system, data in zip(client.systems, client.payloads, strict=True) if system == FACT_REVIEW_PROMPT]
    point_jobs = [payload for payload in factual if payload["candidate"]["points"]]
    lesson_jobs = [payload for payload in factual if payload["lesson_components"]]
    assert len(point_jobs) == len(bundle.knowledge_points)
    assert all(len(payload["candidate"]["points"]) == 1 for payload in point_jobs)
    assert {payload["candidate"]["points"][0]["point_id"] for payload in point_jobs} == {
        point.point_id for point in bundle.knowledge_points}
    assert all(payload["candidate"]["lesson"] is None for payload in point_jobs)
    assert all(not payload["candidate"]["points"] and payload["candidate"]["lesson"] for payload in lesson_jobs)
    expected = {"opening", "summary", "example_1", "example_2", "checkpoint_1", "checkpoint_2"}
    assert len(lesson_jobs) == len(expected)
    assert all(len(payload["lesson_components"]) == 1 for payload in lesson_jobs)
    assert {key for payload in lesson_jobs for key in payload["lesson_components"]} == set(output.teaching_fact_review["lesson"]["components"]) == expected
    assert all(proof["assertion_checks"] and proof["scope_matrix"] and len(proof["assertion_checks"]) == len(proof["assertions"]) == len(proof["scope_matrix"])
               for proof in output.teaching_fact_review["lesson"]["components"].values())
    assert all(proof["support_quotes"] for proof in output.teaching_fact_review["lesson"]["components"].values())


@pytest.mark.parametrize("fault", ["missing_component", "borrowed_source", "unscoped_summary"])
def test_overall_lesson_permission_cannot_override_a_missing_or_invalid_component(fault):
    class BadLesson(Client):
        def structured(self, *, system, user, **kwargs):
            data = json.loads(user)
            result = super().structured(system=system, user=user, **kwargs)
            if system == FACT_REVIEW_PROMPT and result["lesson"] is not None:
                result = advertised_fact_response(result, data)
                if fault == "missing_component":
                    result["components"] = [row for row in result["components"] if row["component_id"] != "example_2"]
                elif fault == "borrowed_source":
                    for row in result["components"]:
                        if row["component_id"] == "example_1":
                            row["assertion_checks"][0]["support_span_ids"] = [next(
                                key for key, value in TeachingContentGenerator._support_spans(evidence).items()
                                if value["source_id"] == "s2")]
                elif fault == "unscoped_summary":
                    for row in result["components"]:
                        if row["component_id"] == "summary":
                            assert row["scope_matrix"][0]["checks"]
                            row["scope_matrix"][0]["checks"][0] = [True, True, True, False, None]
            return result

    client = BadLesson()
    generator, points, lesson, inputs, chapter, evidence = factual_fixture(client)
    evidence.sources["s2"] = SourceCitation(page_number=2, quote="这是导入阅读的补充来源，不能自动支持其他例题的结论。")
    lesson.reading_source_ids.append("s2")
    if fault == "unscoped_summary":
        for item in inputs:
            item["scope_conditions"] = [{"condition": "稳定结论仅适用于温度不变的密闭容器。",
                                          "boundary_type": "precondition", "source_ids": ["s1"]}]
    if fault in {"unscoped_summary", "borrowed_source"}:
        review = generator._fact_review(points, lesson, inputs, chapter, evidence, [])
        assert not generator._fact_accepted(review.lesson)
    else:
        with pytest.raises(TeachingContentError):
            generator._fact_review(points, lesson, inputs, chapter, evidence, [])
    assert client.review_calls == 0


def test_scope_navigation_is_batch_local_except_when_writing_whole_lesson():
    bundle, chapter, evidence = course(7)
    evidence.sources["s2"] = SourceCitation(page_number=2, quote="容器的温度条件可能发生改变，液体体积不一定仍然保持稳定，须先核对条件。")
    client = Client()
    TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    for system, data in zip(client.systems, client.payloads, strict=True):
        if system == GENERATION_PROMPT:
            from adaptive_learning.personalization.teaching_fact_wire import restore_fact_payload

            data = restore_fact_payload(data)
            relevant = data["all_knowledge_points"] if data["include_lesson"] else data["batch_points"]
            expected = {item["point_id"] for item in relevant}
        elif system == FACT_REVIEW_PROMPT and data["candidate"]["points"]:
            expected = {item["point_id"] for item in data["candidate"]["points"]}
        else:
            continue
        assert {item["point_id"] for item in data["scope_contexts"]} == expected
        for point in data["scope_contexts"]:
            for source in point["sources"]:
                assert source["quote"] == evidence.sources[source["source_id"]].quote


def test_whitespace_canonicalization_never_violates_minimum_quote_length():
    _, _, evidence = course(1)
    evidence.sources["s1"].quote = "请先核对温度。条件改变后必须重新测量。"
    proof = _ComponentFactProof(source_entailment=True, scope_exceptions=True,
        source_ids=["s1"], rationale="恢复原文后仍必须满足已有的最短引文契约。",
        support_quotes=[{"source_id": "s1", "quote": "核对 温度。"}])
    with pytest.raises(TeachingContentError):
        TeachingContentGenerator._validate_component_proof(proof, {"s1"}, evidence)


@pytest.mark.parametrize("compatibility", ["omitted", "unused_or_incorrect_catalog"])
def test_support_parent_sources_are_derived_only_from_selected_spans(compatibility):
    class SpanOnlySelection(Client):
        def structured(self, *, system, user, **kwargs):
            result = super().structured(system=system, user=user, **kwargs)
            if system == FACT_REVIEW_PROMPT:
                proofs = [proof for row in result["points"] for proof in row["components"].values()]
                if result["lesson"]:
                    proofs.extend(result["lesson"]["components"].values())
                for proof in proofs:
                    if compatibility == "omitted":
                        proof.pop("source_ids")
                    else:
                        proof["source_ids"] = ["unused_catalog_item", "inaccurate_repeated_parent"]
            return result

    bundle, chapter, evidence = course(2)
    output = TeachingContentGenerator(SpanOnlySelection()).generate(bundle, chapter, evidence)
    proof = output.teaching_fact_review["points"][0]["components"]["explanation"]
    assert proof["source_ids"] == ["s1"]
    assert {quote["source_id"] for quote in proof["support_quotes"]} == {"s1"}


def test_known_unowned_span_rejects_then_exact_citation_edit_is_independently_reviewed():
    bundle, chapter, evidence = course(2)
    evidence.sources["s2"] = SourceCitation(page_number=2, quote="每次测量前应记录观测时刻，以便区分两次观测。")
    target = bundle.knowledge_points[0].point_id

    class CitationRepairAfterProgrammaticRejection(Client):
        def structured(self, *, system, user, **kwargs):
            data = json.loads(user)
            result = super().structured(system=system, user=user, **kwargs)
            if system == GENERATION_PROMPT:
                for point in result["points"]:
                    if point["point_id"] == target:
                        point["source_ids"].append("s2")  # Available to this batch, not initially to practice.
                        point["practice"]["expected_answer"] += "测量前还应记录观测时刻。"
                        if self.fact_calls:
                            point["practice"]["source_ids"].append("s2")
            elif system == FACT_REVIEW_PROMPT:
                for row in result["points"]:
                    if row["point_id"] == target:
                        row["components"]["practice"] = selected_proof(["s2"], data, point_id=row["point_id"], component_name="practice")
            return result

    client = CitationRepairAfterProgrammaticRejection()
    events = []
    output = TeachingContentGenerator(client, audit_callback=events.append).generate(bundle, chapter, evidence)
    assert [len(batch) for batch in point_batches(client)] == [2]
    assert fact_targets(client)[(target, "practice")] == 2
    assert all(count == 1 for pair, count in fact_targets(client).items() if pair != (target, "practice"))
    assert client.review_calls == 2
    repaired = next(item for item in output.practice_items if item.point_id == target)
    assert evidence.sources["s2"] in repaired.citations
    generations = [data for data in client.payloads if "batch_points" in data]
    assert all(not data["allowed_repairs"] for data in generations)
    first_fact = next(event["review"] for event in events if event["event"] == "fact_review")
    rejected = next(row for row in first_fact["points"] if row["point_id"] == target)
    assert not rejected["accepted"] and "s2" in json.dumps(rejected["issues"])
    edit = next(event for event in events if event["event"] == "citation_candidate_repair")
    assert edit["repairs"][0]["missing_citation_source_ids"] == ["s2"]
    assert edit["approved"] is False and edit["requires_independent_review"] is True
    assert all(data["format_feedback"] == [] for system, data in zip(client.systems, client.payloads, strict=True)
               if system == FACT_REVIEW_PROMPT)


def test_factual_payload_filters_support_pool_and_places_actual_text_next_to_each_component():
    bundle, chapter, evidence = course(4)
    evidence.sources["unused"] = SourceCitation(page_number=2, quote="这段原文未被任何学习组件引用，但仍供全章范围检索。")
    client = Client()
    TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    for system, data in zip(client.systems, client.payloads, strict=True):
        if system != FACT_REVIEW_PROMPT:
            continue
        assert "unused" in data["sources"]
        assert not any(span["source_id"] == "unused" for span in data["support_spans"].values())
        for point in data["candidate"]["points"]:
            for name, ids in (("explanation", point["source_ids"]),
                              ("flashcard", point["flashcard"]["source_ids"]),
                              ("practice", point["practice"]["source_ids"])):
                context = point["component_evidence"][name]
                assert context["actual_source_text"] == [{"source_id": source_id,
                    "quote": evidence.sources[source_id].quote} for source_id in ids]
                assert all(data["support_spans"][span_id]["source_id"] in ids
                           for span_id in context["allowed_support_span_ids"])
        for part in data["lesson_components"].values():
            assert part["actual_source_text"] == [{"source_id": source_id,
                "quote": evidence.sources[source_id].quote} for source_id in part["source_ids"]]


def test_lesson_only_generation_has_explicit_empty_point_output_contract():
    bundle, chapter, evidence = course(3)
    client = Client("lesson_retry")
    TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    repair = [data for data in client.payloads if "batch_points" in data][-1]
    assert repair["batch_points"] == []
    assert repair["output_contract"] == {"point_ids": [], "card_ids": [], "practice_ids": [],
        "lesson_required": True, "lesson_only": True, "points_must_be_empty": True}


def test_whole_lesson_proof_can_cover_more_than_eight_real_parent_sources():
    client = Client()
    generator, points, lesson, inputs, chapter, evidence = factual_fixture(client, 1)
    for index in range(2, 11):
        source_id = f"s{index}"
        evidence.sources[source_id] = SourceCitation(page_number=2,
            quote=f"补充条件{index}：核对观测时温度不变，再比较密闭容器内的体积是否稳定。")
        lesson.reading_source_ids.append(source_id)
    review = generator._fact_review(points, lesson, inputs, chapter, evidence, [])
    assert generator._fact_accepted(review.lesson)
    assert len(review.lesson.components["summary"].source_ids) == 10
    assert len(review.lesson.components["summary"].support_quotes) == 10


def test_planner_runs_bounded_batches_once_and_hints_reach_lesson_only_repair_and_fact_review():
    bundle, chapter, evidence = course(7)
    client = Client("lesson_retry")
    generator = TeachingContentGenerator(client)
    assert generator.scope_planner.client is client
    result = generator.generate(bundle, chapter, evidence)
    assert client.systems.count(SCOPE_PLANNING_PROMPT) == 2
    assert result.teaching_scope_version == TEACHING_SCOPE_VERSION
    assert set(result.teaching_scope_plan) == {point.point_id for point in bundle.knowledge_points}
    for system, payload in zip(client.systems, client.payloads, strict=True):
        if system == GENERATION_PROMPT:
            expected = bundle.knowledge_points if payload["include_lesson"] else payload["batch_points"]
            expected_ids = {point.point_id if hasattr(point, "point_id") else point["point_id"] for point in expected}
        elif system == FACT_REVIEW_PROMPT:
            expected_ids = {point["point_id"] for point in payload["knowledge_points"]}
        else:
            continue
        assert set(payload["scope_conditions"]) == expected_ids
        assert all(payload["scope_conditions"][point_id] ==
                   (TeachingContentGenerator._factual_scope_hints({"scope_conditions": result.teaching_scope_plan[point_id]})
                    if system == FACT_REVIEW_PROMPT else result.teaching_scope_plan[point_id]) for point_id in expected_ids)


@pytest.mark.parametrize("failure", ["service", "missing_point", "unknown_source", "extra_field"])
def test_scope_failure_never_starts_teaching_or_mutates_original_bundle(failure):
    bundle, chapter, evidence = course(1)
    before = bundle.model_dump_json()
    client = Client()

    class Planner:
        def plan(self, inputs, evidence):
            if failure == "service":
                raise TeachingScopeError("规划未完成")
            if failure == "missing_point":
                return {}
            condition = {"condition": "核对温度条件。", "source_ids": ["s1"]}
            if failure == "unknown_source":
                condition["source_ids"] = ["invented"]
            else:
                condition["accepted"] = True
            return {inputs[0]["point_id"]: [condition]}

    with pytest.raises(TeachingContentError, match="范围规划"):
        TeachingContentGenerator(client, scope_planner=Planner()).generate(bundle, chapter, evidence)
    assert not client.payloads
    assert bundle.model_dump_json() == before


def test_point_only_fact_probe_does_not_invent_or_review_a_lesson():
    client = Client()
    generator, points, lesson, inputs, chapter, evidence = factual_fixture(client, 1)
    review = generator._fact_review(points, None, inputs, chapter, evidence, [])
    assert review.accepted and review.lesson is None
    assert client.fact_calls == 1
    assert client.payloads[0]["candidate"]["lesson"] is None
    assert client.payloads[0]["lesson_components"] == {}
    with pytest.raises(TeachingContentError, match="空候选"):
        generator._fact_review([], None, inputs, chapter, evidence, [])
