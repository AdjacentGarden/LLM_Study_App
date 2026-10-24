"""Only program-verified provenance defects may edit a draft before re-review."""

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest
from adaptive_learning.personalization import teaching_content as draft
from adaptive_learning.personalization.models import SourceCitation
from pydantic import ValidationError
from test_teaching_content import course, lesson_candidate, point_candidate
from test_teaching_v15_integration import CandidateClient, Planner


def materialized(point, evidence, *, semantic=True, scope=True, parent=True):
    spans = draft.TeachingContentGenerator._support_spans(evidence)
    own = next(key for key, span in spans.items() if span["source_id"] in point.source_ids)
    foreign = next(key for key, span in spans.items()
                   if span["source_id"] not in point.practice.source_ids)
    parts = {}
    for name in ("explanation", "flashcard", "practice"):
        parts[name] = {
            "source_entailment": semantic if name == "practice" else True,
            "scope_exceptions": scope if name == "practice" else True,
            "support_span_ids": [foreign if name == "practice" else own],
            "rationale": "已经独立核对该组件的具体事实及自身适用条件。",
            "findings": [],
        }
    # A semantic veto must be grounded in a real, localized finding, even when
    # another selected span also exposes a provenance defect.
    if not (semantic and scope):
        parts["practice"]["findings"] = [{
            "kind": "invalid_inference", "candidate_field": "expected_answer",
            "candidate_quote": point.practice.expected_answer[:40],
            "issue": "当前答案的推理不能由所引用的条件直接得到，需要修正。",
            "evidence_source_ids": point.practice.source_ids[:1],
        }]
    failed = [] if semantic and scope else ["practice"]
    selected = draft._FactSelection.model_validate({
        "accepted": parent and not failed, "issues": [], "lesson": None,
        "points": [{"point_id": point.point_id, "accepted": parent and not failed,
                    "source_entailment": semantic, "scope_exceptions": scope,
                    "failed_components": failed,
                    "issues": [] if parent else ["组件之外的整体事实仍有待确认。"],
                    "support_summary": "已经核对自己的原文来源与范围，当前结果按每个组件保留。",
                    "components": parts}],
    })
    return draft.TeachingContentGenerator._materialize_fact_review(
        selected, [point], None, evidence, spans
    )


def teacher_for(point):
    review = draft._Review.model_validate({
        "accepted": True, "issues": [],
        "points": [{"point_id": point.point_id, "explanation_ok": True,
                    "flashcard_ok": True, "practice_ok": True, "evidence_ok": True,
                    "issues": []}],
        "lesson": {"reading_ok": True, "examples_ok": True, "checkpoints_ok": True,
                   "evidence_ok": True, "issues": []},
    })
    review._rubric_alignment = {"items": [{
        "item_id": point.practice.item_id,
        "rubric_checks": [{"rubric_index": index, "rubric_text": text, "required": True}
                          for index, text in enumerate(point.practice.rubric)],
    }]}
    return review


def exact_plan(point):
    return draft._RepairPlan(
        {point.point_id: {"practice"}}, set(),
        draft._RepairRequirements(exact_points={point.point_id: {"practice"}}),
    )


def test_observed_f9_adds_only_literal_p30_citation_without_granting_approval():
    observed = json.loads((Path(__file__).parent / "fixtures" /
                           "teaching_precise_citation_f9.json").read_text())
    point = draft._Point.model_validate(observed["point"])
    _, _, evidence = course(1)
    evidence.sources.clear()
    evidence.sources.update({
        source_id: SourceCitation(page_number=28, quote="当前测试仅用于保留原有引用与其他组件的既有批准。")
        for source_id in point.practice.source_ids
    })
    evidence.sources[observed["missing_source_id"]] = SourceCitation(**observed["source"])
    fact = materialized(point, evidence)
    proof = fact.points[0].components.practice
    assert proof._missing_citation_source_ids == (observed["missing_source_id"],)
    plan = exact_plan(point)
    points, changes = draft.TeachingContentGenerator._repair_precise_citations(
        [point], draft._Lesson.model_validate(lesson_candidate()), fact, teacher_for(point), plan
    )
    patched = points[0]
    expected = point.model_dump()
    expected["practice"]["source_ids"].append(observed["missing_source_id"])
    assert patched.model_dump() == expected
    assert point.model_dump() == observed["point"]
    assert changes[0]["missing_citation_source_ids"] == [observed["missing_source_id"]]
    assert plan.allowed_points == {}
    # The real citation edit satisfies, rather than disables, the unchanged gate.
    assert plan.requirements.exact_points == {point.point_id: {"practice"}}
    draft.TeachingContentGenerator._validate_point_repair(patched, point, plan.requirements)
    assert not fact.accepted and not proof.source_entailment
    assert "missing_citation_source_ids" not in json.dumps(fact.model_dump())
    assert not draft._ComponentFactProof.model_validate(proof.model_dump())._missing_citation_source_ids


def test_foreign_span_must_first_belong_to_a_real_immutable_source():
    _, _, evidence = course(1)
    selection = draft._ComponentFactSelection(
        source_entailment=True, scope_exceptions=True, support_span_ids=["foreign"]
    )
    for span in ({"source_id": "unknown", "quote": "这不是已存在的来源。"},
                 {"source_id": "s1", "quote": "原文完全没有出现的伪造引文。"}):
        with pytest.raises(draft.TeachingContentError, match="不可变连续原文"):
            draft.TeachingContentGenerator._materialize_proof(
                selection, set(), evidence, {"foreign": span}, []
            )


def test_real_multi_failure_teacher_envelope_still_repairs_only_f9_after_aggregation():
    observed = json.loads((Path(__file__).parent / "fixtures" /
                           "teaching_precise_citation_f9.json").read_text())
    target = draft._Point.model_validate(observed["point"])
    _, _, evidence = course(1)
    evidence.sources.clear()
    evidence.sources.update({source_id: SourceCitation(
        page_number=28, quote="当前测试保留已引用的来源，用于验证组件修复边界。"
    ) for source_id in target.practice.source_ids})
    evidence.sources[observed["missing_source_id"]] = SourceCitation(**observed["source"])
    target_fact = materialized(target, evidence)
    positive = draft._ComponentFactProof(
        source_entailment=True, scope_exceptions=True, rationale="已经核对本组件自身的事实与范围。"
    )
    points, facts = [], []
    for index, metadata in enumerate(observed["point_metadata"]):
        if metadata["point_id"] == target.point_id:
            points.append(target)
            facts.extend(target_fact.points)
        else:
            points.append(draft._Point.model_validate(point_candidate({
                **metadata, "label": f"容器{index}的观察条件",
            })))
            facts.append(draft._PointFactVerdict(
                point_id=metadata["point_id"], accepted=True, source_entailment=True,
                scope_exceptions=True, issues=[], failed_components=[],
                support_summary="已通过组件的内容仅用于保留真实审校中的其他point身份。",
                components=draft._FactComponents(**{
                    name: positive.model_copy(deep=True)
                    for name in ("explanation", "flashcard", "practice")
                }),
            ))
    lesson_data = lesson_candidate()
    # Keep the real component IDs/counts for issue routing; these placeholder
    # bodies are not an attempt to replace or approve the failed lesson.
    lesson_data["worked_examples"] = [deepcopy(lesson_data["worked_examples"][0])
                                      for _ in range(observed["lesson_example_count"])]
    lesson_data["checkpoints"] = [deepcopy(lesson_data["checkpoints"][0])
                                 for _ in range(observed["lesson_checkpoint_count"])]
    lesson = draft._Lesson.model_validate(lesson_data)
    fact = draft._FactReview(
        accepted=False, issues=[], points=facts,
        lesson=draft._LessonFactVerdict(
            accepted=True, source_entailment=True, scope_exceptions=True, issues=[],
            support_summary="本测试只重放原始teacher多处否决，不替换其来源或评分判决。",
            components={name: positive.model_copy(deep=True)
                        for name in draft.TeachingContentGenerator._lesson_components(lesson)},
        ),
    )
    generator = draft.TeachingContentGenerator(None)
    aggregated = generator._aggregate_fact_reviews([fact], points, lesson)
    target_row = next(row for row in aggregated.points if row.point_id == target.point_id)
    assert target_row.components.practice._missing_citation_source_ids == (
        observed["missing_source_id"],
    )
    teacher = draft._Review.model_validate(observed["teacher"])
    teacher._rubric_alignment = {"items": observed["target_rubric_items"]}
    assert len(teacher.issues) == 5
    assert sum(not row.practice_ok for row in teacher.points) == 7
    assert all(generator._teacher_issue_targets(issue, points, lesson) is not None
               for issue in teacher.issues)
    plan = generator._build_repair_plan(aggregated, teacher, points, lesson)
    before = deepcopy(plan.allowed_points)
    lesson_repairs = set(plan.allowed_lesson)
    assert not plan.requirements.global_any
    patched, changes = generator._repair_precise_citations(
        points, lesson, aggregated, teacher, plan
    )
    assert len(changes) == 1 and changes[0]["point_id"] == target.point_id
    del before[target.point_id]
    assert plan.allowed_points == before and plan.allowed_lesson == lesson_repairs
    assert all(after == original for original, after in zip(points, patched, strict=True)
               if original.point_id != target.point_id)


@pytest.mark.parametrize("field", ["missing_citation_source_ids", "_missing_citation_source_ids"])
def test_provider_cannot_inject_program_only_repair_provenance(field):
    data = {"source_entailment": False, "scope_exceptions": True,
            field: ["forged"], "support_span_ids": []}
    with pytest.raises(ValidationError):
        draft._ComponentFactSelection.model_validate(data)
    with pytest.raises(ValidationError):
        draft._ComponentFactProof.model_validate({key: value for key, value in data.items()
                                                if key != "support_span_ids"})
    assert field not in draft._ComponentFactSelection.model_json_schema()["properties"]
    assert field not in draft._ComponentFactProof.model_json_schema()["properties"]
    point, _ = simple_point_and_evidence()
    with pytest.raises(ValidationError):
        draft.TeachingContentGenerator._decode_fact_selection({"components": [{
            **data, "point_id": point.point_id, "component_id": "practice",
        }]}, [point], set())


def test_model_source_and_finding_suggestions_do_not_become_verified_missing_citations():
    point, evidence = simple_point_and_evidence()
    spans = draft.TeachingContentGenerator._support_spans(evidence)
    own_span = next(key for key, row in spans.items() if row["source_id"] == "s1")
    selection = draft._ComponentFactSelection(
        source_entailment=False, scope_exceptions=True, source_ids=["s2"],
        support_span_ids=[own_span], findings=[draft._ComponentFinding(
            kind="invalid_inference", candidate_field="expected_answer",
            candidate_quote=point.practice.expected_answer[:20],
            issue="模型建议补另一个来源，但没有选择其真实支持span。",
            evidence_source_ids=["s2"],
        )],
    )
    proof = draft.TeachingContentGenerator._materialize_proof(
        selection, {"s1"}, evidence, spans, ["需要核对原文和当前推理。"]
    )
    assert proof.source_ids == ["s1"]
    assert not proof._missing_citation_source_ids


def simple_point_and_evidence():
    bundle, _, evidence = course(1)
    item = draft.TeachingContentGenerator._inputs(bundle, evidence)[0]
    data = point_candidate(item)
    data["source_ids"].append("s2")
    evidence.sources["s2"] = SourceCitation(page_number=2, quote="条件改变时必须重新观察测量，不能直接沿用先前结论。")
    return draft._Point.model_validate(data), evidence


@pytest.mark.parametrize("fault", ["semantic", "scope", "parent", "teacher", "rubric", "teacher_top",
                                  "no_rubric", "global", "citation_changed", "finding_only"])
def test_semantic_scope_teacher_rubric_or_unverified_source_suggestions_never_auto_repair(fault):
    point, evidence = simple_point_and_evidence()
    fact = materialized(point, evidence, semantic=fault != "semantic", scope=fault != "scope",
                        parent=fault != "parent")
    teacher = teacher_for(point)
    plan = exact_plan(point)
    if fault == "teacher":
        teacher.points[0].explanation_ok = False
    elif fault == "teacher_top":
        teacher.issues = [f"practice_{point.practice.item_id}：练习仍包含无依据的推理。"]
    elif fault == "rubric":
        teacher._rubric_alignment["items"][0]["rubric_checks"][0]["required"] = False
    elif fault == "no_rubric":
        teacher._rubric_alignment = {}
    elif fault == "global":
        plan.requirements.global_any = True
    elif fault == "citation_changed":
        point.practice.source_ids.append("s2")
    elif fault == "finding_only":
        proof = fact.points[0].components.practice
        proof._missing_citation_source_ids = ()
        proof.findings = [draft._ComponentFinding(
            kind="invalid_inference", candidate_field="expected_answer",
            candidate_quote=point.practice.expected_answer[:20],
            issue="审校员建议另一个来源，但没有被程序验证的引用缺口。",
            evidence_source_ids=["s2"],
        )]
    before = point.model_dump()
    patched, changes = draft.TeachingContentGenerator._repair_precise_citations(
        [point], draft._Lesson.model_validate(lesson_candidate()), fact, teacher, plan
    )
    assert not changes and patched[0].model_dump() == before
    assert plan.allowed_points == {point.point_id: {"practice"}}


class ForeignPractice(CandidateClient):
    def __init__(self, target, evidence, *, reject_after_repair=None, **kwargs):
        super().__init__(**kwargs)
        self.target = target
        self.spans = draft.TeachingContentGenerator._support_spans(evidence)
        self.foreign_span = next(key for key, row in self.spans.items() if row["source_id"] == "s2")
        self.reject_after_repair = reject_after_repair

    def structured(self, *, system, user, **kwargs):
        result = super().structured(system=system, user=user, **kwargs)
        if system == draft.GENERATION_PROMPT:
            for point in result["points"]:
                if point["point_id"] == self.target:
                    point["source_ids"].append("s2")
        elif (system == draft.RUBRIC_ALIGNMENT_PROMPT
              and self.reject_after_repair == "rubric"
              and (draft._AUDIT_CONTEXT.get() or {}).get("attempt") == 2):
            for item in result["items"]:
                check = item["rubric_checks"][0]
                check.update(required=False, reason="该评分点要求题干未问及的额外目标。",
                             counterexample_answer="不包含该独立目标也能完成题干要求的受控答案。",
                             repair="删除这一额外要求并重新审校。")
        return result

    def fact(self, data):
        result = super().fact(data)
        for row in result["components"]:
            if (row["point_id"], row["component_id"]) != (self.target, "practice"):
                continue
            point = next(item for item in data["candidate"]["points"]
                         if item["point_id"] == self.target)
            row["support_span_ids"] = [self.foreign_span]
            if (self.reject_after_repair in {"semantic", "scope"}
                    and "s2" in point["practice"]["source_ids"]):
                row.update(source_entailment=self.reject_after_repair != "semantic",
                           scope_exceptions=self.reject_after_repair != "scope", findings=[{
                    "kind": "invalid_inference", "candidate_field": "expected_answer",
                    "candidate_quote": point["practice"]["expected_answer"][:40],
                    "issue": "重新审校发现该推理仍缺少必要条件，不能以补引用代替事实通过。",
                    "evidence_source_ids": ["s2"],
                }])
        return result


    def teacher(self, data):
        result = super().teacher(data)
        if self.reject_after_repair == "teacher":
            for point, verdict in zip(data["candidate"]["points"], result["points"], strict=True):
                if point["point_id"] == self.target and "s2" in point["practice"]["source_ids"]:
                    verdict.update(practice_ok=False, issues=["practice：题目仍有教学质量问题。"])
                    result["accepted"] = False
        return result


@pytest.mark.parametrize("reject", [None, "semantic", "scope", "teacher", "rubric"])
def test_citation_edit_changes_candidate_identity_and_reaudits_before_publication(reject):
    bundle, chapter, evidence = course(2)
    evidence.sources["s2"] = SourceCitation(page_number=2, quote="条件改变时必须重新观察测量，不能直接沿用先前结论。")
    target = bundle.knowledge_points[0].point_id
    client = ForeignPractice(target, evidence, reject_after_repair=reject)
    events = []
    generator = draft.TeachingContentGenerator(
        client, scope_planner=Planner(), max_attempts=2,
        audit_callback=lambda event: events.append(deepcopy(event)),
    )
    if reject:
        with pytest.raises(draft.TeachingContentError):
            generator.generate(bundle, chapter, evidence)
        assert bundle.teaching_fact_review == {} and bundle.teaching_quality_version == ""
    else:
        result = generator.generate(bundle, chapter, evidence)
        assert result.teaching_fact_review["accepted"]
        assert result.teaching_fact_review["points"][0]["components"]["practice"]["source_entailment"]
    # No writer request is made to redo an already accepted task and rubric.
    assert len(client.generations) == 2
    assert not any(request["allowed_repairs"] for request in client.generations)
    teachers = [payload for job, payload, *_ in client.calls if job[0] == "teacher"]
    rubrics = [payload for job, payload, *_ in client.calls if job[0] == "rubric"]
    facts = [payload for job, payload, *_ in client.calls if job[0] == "fact"]
    assert len(teachers) == len(rubrics) == 2
    first, second = [payload["candidate"] for payload in teachers]
    expected = deepcopy(first)
    expected["points"][0]["practice"]["source_ids"].append("s2")
    assert second == expected
    pairs = Counter((owner, name) for payload in facts
                    for owner, names in payload["output_contract"]["component_ids_by_point"].items()
                    for name in names)
    assert pairs[target, "practice"] == 2
    assert pairs[target, "explanation"] == pairs[target, "flashcard"] == 1
    edit = next(event for event in events if event["event"] == "citation_candidate_repair")
    assert edit["candidate_sha256"] != edit["previous_candidate_sha256"]
    assert edit["approved"] is False and edit["requires_independent_review"] is True
    assert (events[-1]["event"] == "failed") is bool(reject)


def test_same_component_teacher_veto_preserves_writer_and_unchanged_guard():
    bundle, chapter, evidence = course(1)
    evidence.sources["s2"] = SourceCitation(page_number=2, quote="条件改变时必须重新观察测量，不能直接沿用先前结论。")
    target = bundle.knowledge_points[0].point_id
    client = ForeignPractice(target, evidence, teacher_failures={(target, "practice")},
                             unchanged={(target, "practice")})
    events = []
    generator = draft.TeachingContentGenerator(client, scope_planner=Planner(), max_attempts=2,
                                               audit_callback=events.append)
    with pytest.raises(draft.TeachingContentError):
        generator.generate(bundle, chapter, evidence)
    assert not any(event["event"] == "citation_candidate_repair" for event in events)
    repair = next(payload for payload in client.generations if payload["allowed_repairs"])
    assert repair["allowed_repairs"] == {target: ["practice"]}
    assert "未修改上次被拒绝的组件" in json.dumps(events[-1], ensure_ascii=False)
