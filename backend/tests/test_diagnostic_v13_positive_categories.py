"""Actual H blockers and controlled plans; scripted verdicts do not prove quality."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_diagnostic_chapter_generation_cache import MemoryApprovals
from test_diagnostic_v6_repair import structure_for

from adaptive_learning.assessment import item_generation as generation

SUTTON, PLOIDY, POPULATION = (
    "chapter_5f29761fe19bff97",
    "chapter_d58164c4b1599fca",
    "chapter_445b90708e561e06",
)


@pytest.fixture
def recorded():
    data = json.loads(
        (Path(__file__).parent / "fixtures" / "diagnostic_v12_h_blocking_plans.json").read_text()
    )
    return {case["source"]["chapter_id"]: case for case in data["cases"]}


def plan_for(request):
    point = request["knowledge_points"][2]
    directory = request["application_source_refs"][point["knowledge_point_id"]]
    mode = (
        "positive_source_categories" if request["chapter_id"] == PLOIDY else "required_conditions"
    )
    source = next(row for row in directory if mode in row["plan_hints"])
    basis = {
        "source_ref": source["ref"],
        "input_role": "给定记录满足原文完整判定条件的情况",
        "output_role": "来源规定的类型",
    }
    if mode == "positive_source_categories":
        return {
            "mode": mode,
            "basis": basis,
            "quantity": "染色体组",
            "shared_conditions": ["由受精卵发育而成的个体"],
            "rules": [
                {
                    "condition_quote": "体细胞中含有两个染色体组",
                    "category": "二倍体",
                    "operator": "eq",
                    "threshold": 2,
                },
                {
                    "condition_quote": "体细胞中含有三个或三个以上染色体组",
                    "category": "多倍体",
                    "operator": "gte",
                    "threshold": 3,
                },
            ],
            "records": [{"value": 2}, {"value": 4}],
        }
    terms = (
        ["观察", "实验"]
        if request["chapter_id"] == SUTTON
        else ["一定区域", "同种生物", "全部个体"]
    )
    basis["input_role"] = (
        "判断类比推论所需的检验条件"
        if request["chapter_id"] == SUTTON
        else "判定一个群体为种群的条件"
    )
    return {
        "mode": mode,
        "basis": basis,
        "required": terms,
        "records": [
            {"available": list(range(len(terms) - 1))},
            {"available": list(range(1, len(terms)))},
        ],
    }


class Writer:
    def __init__(self, transform=None):
        self.calls, self.transform = [], transform

    def structured(self, *, system, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        assert request["knowledge_points"][2]["response_shape"] == "private_application_plan"
        assert set(request["application_output_shapes"]) == {
            "required_conditions",
            "role_transfer",
            "positive_source_categories",
        }
        rows = [
            {
                "knowledge_point_id": point["knowledge_point_id"],
                "type": "choice",
                "prompt": "以下哪项符合这条受控离线回归前提？",
                "options": [point["label"], "受控干扰一", "受控干扰二", "受控干扰三"],
                "expected_answer": point["label"],
                "correct_index": 0,
                "rubric": [],
                "difficulty": 0,
                "estimated_seconds": 45,
            }
            for point in request["knowledge_points"]
        ]
        rows[2] = {
            "knowledge_point_id": request["knowledge_points"][2]["knowledge_point_id"],
            "type": "choice",
            "application_task": plan_for(request),
            "difficulty": 0,
            "estimated_seconds": 45,
        }
        response = {"items": rows}
        return self.transform(request, response, len(self.calls)) if self.transform else response


class Reviewer:
    def __init__(self, accept=True, issue="Controlled source semantics veto"):
        self.calls, self.accept, self.issue = [], accept, issue

    def structured(self, *, system, user, **kwargs):
        request = json.loads(user)
        self.calls.append(request)
        return {
            "reviews": [
                {
                    "knowledge_point_id": row["knowledge_point_id"],
                    "accepted": self.accept
                    or row["cognitive_target"]["id"] != "source_application",
                    "issues": []
                    if self.accept or row["cognitive_target"]["id"] != "source_application"
                    else [self.issue],
                }
                for row in request["items"]
            ]
        }


def generator(writer, reviewer, retries=0, **kwargs):
    return generation.DiagnosticItemGenerator(
        writer,
        review_client=reviewer,
        allow_grounded_answer_rewrite=True,
        validation_retries=retries,
        **kwargs,
    )


@pytest.mark.parametrize("chapter_id", [SUTTON, PLOIDY, POPULATION])
@pytest.mark.parametrize("accepted", [True, False])
def test_actual_h_sources_have_closed_candidate_paths_and_review_is_still_mandatory(
    recorded, chapter_id, accepted
):
    writer, reviewer, cache, events = Writer(), Reviewer(accepted), MemoryApprovals(), []
    subject = generator(writer, reviewer, audit_callback=events.append)
    value = structure_for(recorded[chapter_id]["source"])
    if accepted:
        bank = subject.generate(value, chapter_cache=cache, cache_scope="controlled-H")
        assert subject.generate(value, chapter_cache=cache, cache_scope="controlled-H") == bank
        task = reviewer.calls[0]["items"][2]["application_task"]
        if chapter_id == PLOIDY:
            assert bank[2].expected_answer == "甲：二倍体；乙：多倍体"
            assert all(
                "由受精卵发育而成的个体" in row["conditions"]
                for row in task["derivation"]["records"]
            )
            assert "数量为4" in bank[2].prompt
            assert all(category not in bank[2].prompt for category in ("二倍体", "多倍体"))
            assert task["rules"][1]["threshold"] == 3 and task["records"][1]["value"] == 4
        else:
            assert set(task["required"]) == (
                {"观察", "实验"} if chapter_id == SUTTON else {"一定区域", "同种生物", "全部个体"}
            )
        assert not any("application_task" in item.model_dump() for item in bank)
    else:
        with pytest.raises(generation.DiagnosticGenerationError, match="semantics veto"):
            subject.generate(value, chapter_cache=cache, cache_scope="controlled-H")
        assert cache.outcomes == ["rejected"] and not cache.records
    assert len(writer.calls) == len(reviewer.calls) == 1


@pytest.mark.parametrize("chapter_id", [SUTTON, PLOIDY, POPULATION])
def test_actual_h_original_free_items_and_answer_leaking_repairs_remain_rejected(
    recorded, chapter_id
):
    case, requests, reviews = recorded[chapter_id], [], []

    class ArchivedWriter:
        def structured(self, *, user, **kwargs):
            payload = json.loads(user)
            requests.append(payload)
            if len(requests) > 1:
                assert "repair_feedback" in payload and "previous_candidate" in payload
            return deepcopy(case["attempts"][len(requests) - 1]["response"])

    class ArchivedReviewer:
        def structured(self, *, user, **kwargs):
            payload = json.loads(user)
            reviews.append(payload)
            ids = {row["knowledge_point_id"] for row in payload["items"]}
            verdict = case["attempts"][len(reviews)]["review"]
            return {
                "reviews": [
                    row for row in deepcopy(verdict["reviews"]) if row["knowledge_point_id"] in ids
                ]
            }

    cache = MemoryApprovals()
    with pytest.raises(generation.DiagnosticGenerationError, match="review rejected"):
        generator(ArchivedWriter(), ArchivedReviewer(), retries=2).generate(
            structure_for(case["source"]), chapter_cache=cache, cache_scope="H-archive"
        )
    assert len(requests) == 3 and len(reviews) == 2 and not cache.records
    assert requests[1]["repair_feedback"]["stage"] == "item_validation"
    assert requests[2]["repair_feedback"]["reviews"] == case["attempts"][1]["review"]["reviews"]


@pytest.mark.parametrize("value", [True, False, -1, 10001, 2.0, "2", float("nan"), float("inf")])
def test_numeric_records_are_strict_bounded_integers(recorded, value):
    def transform(request, response, call):
        response["items"][2]["application_task"]["records"][0]["value"] = value
        return response

    writer, reviewer = Writer(transform), Reviewer()
    with pytest.raises(generation.DiagnosticGenerationError, match="batch validation failed"):
        generator(writer, reviewer).generate(structure_for(recorded[PLOIDY]["source"]))
    assert reviewer.calls == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ("两个", {2}),
        ("三个或三个以上", {3}),
        ("十二个", {12}),
        ("两千零二十四", {2024}),
        ("十万", set()),
        ("一亿", set()),
        ("负二", set()),
        ("-2", set()),
        ("2.5", set()),
        ("二点五", set()),
        ("9" * 1000, set()),
    ],
)
def test_source_cardinals_do_not_parse_negative_decimal_or_large_number_fragments(text, expected):
    assert generation.DiagnosticItemGenerator._source_numbers(text) == expected


@pytest.mark.parametrize(
    "defect",
    [
        "unknown_source",
        "nonliteral_condition",
        "nonliteral_category",
        "nonliteral_scope",
        "empty_scope",
        "threshold",
        "operator",
        "overlap",
        "same_rule",
        "out_of_range",
        "wrong_id",
        "answer_in_record",
        "derivation",
    ],
)
def test_numeric_plan_rejects_unverified_sources_ranges_scope_and_override_channels(
    recorded, defect
):
    def transform(request, response, call):
        task = response["items"][2]["application_task"]
        if defect == "unknown_source":
            task["basis"]["source_ref"] = "invented-ref"
        elif defect == "nonliteral_condition":
            task["rules"][0]["condition_quote"] = "原文没有的数量条件"
        elif defect == "nonliteral_category":
            task["rules"][0]["category"] = "原文没有的类别"
        elif defect == "nonliteral_scope":
            task["shared_conditions"] = ["由未受精卵发育而成的个体"]
        elif defect == "empty_scope":
            task["shared_conditions"] = []
        elif defect == "threshold":
            task["rules"][1]["threshold"] = 4
        elif defect == "operator":
            task["rules"][0]["operator"] = "gte"
        elif defect == "overlap":
            task["rules"][0]["threshold"] = 3
            task["rules"][0]["condition_quote"] = task["rules"][1]["condition_quote"]
            task["rules"][0]["operator"] = "eq"
        elif defect == "same_rule":
            task["records"][1]["value"] = 2
        elif defect == "out_of_range":
            task["rules"][1]["operator"] = "eq"
            task["rules"][1]["condition_quote"] = "三个"
            task["records"][1]["value"] = 4
        elif defect == "wrong_id":
            task["records"][0]["id"] = "r2"
        elif defect == "answer_in_record":
            task["shared_conditions"] = ["二倍体"]
        else:
            task["derivation"] = {"mapping": ["a", "a"], "approved": True}
        return response

    writer, reviewer, cache = Writer(transform), Reviewer(accept=defect != "derivation"), MemoryApprovals()
    failure = "source semantics veto" if defect == "derivation" else "batch validation failed"
    with pytest.raises(generation.DiagnosticGenerationError, match=failure):
        generator(writer, reviewer).generate(
            structure_for(recorded[PLOIDY]["source"]), chapter_cache=cache, cache_scope="H"
        )
    if defect == "derivation":
        assert len(reviewer.calls) == 1
        assert reviewer.calls[0]["items"][2]["application_task"]["derivation"]["mapping"] == ["a", "b"]
    else:
        assert reviewer.calls == []
    assert not cache.records


@pytest.mark.parametrize(
    "defect", ["omit_scope", "incorrect_category_mapping", "untrusted_condition_semantics"]
)
def test_source_membership_and_correct_calculation_never_replace_independent_semantic_review(
    recorded, defect
):
    def transform(request, response, call):
        task = response["items"][2]["application_task"]
        if defect == "omit_scope":
            task["shared_conditions"] = ["体细胞"]
        elif defect == "incorrect_category_mapping":
            task["rules"][0]["category"], task["rules"][1]["category"] = (
                task["rules"][1]["category"],
                task["rules"][0]["category"],
            )
        else:
            task["basis"]["output_role"] = "全部后代的遗传类型"
        return response

    writer, reviewer, cache = (
        Writer(transform),
        Reviewer(False, "完整前提或类别关系不符合来源"),
        MemoryApprovals(),
    )
    with pytest.raises(generation.DiagnosticGenerationError, match="不符合来源"):
        generator(writer, reviewer).generate(
            structure_for(recorded[PLOIDY]["source"]), chapter_cache=cache, cache_scope="H"
        )
    assert len(writer.calls) == len(reviewer.calls) == 1 and not cache.records


@pytest.mark.parametrize("phrase", ["超过三个染色体组", "大于三个染色体组"])
def test_strict_greater_source_does_not_authorize_inclusive_gte(recorded, phrase):
    case = deepcopy(recorded[PLOIDY])
    point = case["source"]["knowledge_points"][2]
    point["evidence"][0]["quote"] = (
        "由受精卵发育而成的个体，体细胞中含有两个染色体组的叫做二倍体；体细胞中含有"
        + phrase
        + "的叫做多倍体。"
    )

    def transform(request, response, call):
        response["items"][2]["application_task"]["rules"][1]["condition_quote"] = phrase
        return response

    # Source hints are navigation only, so provide the explicit negative plan
    # even when this unsupported operator intentionally has no numeric hint.
    writer = Writer(transform)
    original = generation.DiagnosticItemGenerator._application_sources

    class NegativeWriter(Writer):
        def structured(self, **kwargs):
            request = json.loads(kwargs["user"])
            for rows in request["application_source_refs"].values():
                for row in rows:
                    row["plan_hints"] = ["positive_source_categories"]
            return super().structured(
                **(kwargs | {"user": json.dumps(request, ensure_ascii=False)})
            )

    writer, reviewer = NegativeWriter(transform), Reviewer()
    with pytest.raises(generation.DiagnosticGenerationError, match="explicit inclusive gte"):
        generator(writer, reviewer).generate(structure_for(case["source"]))
    assert (
        reviewer.calls == [] and generation.DiagnosticItemGenerator._application_sources is original
    )


def test_numeric_private_plan_is_exactly_preserved_and_binds_every_new_component_to_approval(recorded):
    events = []

    def transform(request, response, call):
        if call > 1:
            response["items"][2] = deepcopy(
                next(row for row in request["preserve_accepted_items"] if "application_task" in row)
            )
        return response

    class RepairReviewer(Reviewer):
        def structured(self, *, user, **kwargs):
            request = json.loads(user)
            self.calls.append(request)
            return {
                "reviews": [
                    {
                        "knowledge_point_id": row["knowledge_point_id"],
                        "accepted": len(self.calls) > 1
                        or row["cognitive_target"]["id"] != "relation_understanding",
                        "issues": ["Controlled ordinary-item repair"]
                        if len(self.calls) == 1
                        and row["cognitive_target"]["id"] == "relation_understanding"
                        else [],
                    }
                    for row in request["items"]
                ]
            }

    writer, reviewer = Writer(transform), RepairReviewer()
    subject = generator(writer, reviewer, retries=1, audit_callback=events.append)
    value = structure_for(recorded[PLOIDY]["source"])
    subject.generate(value)
    assert len(writer.calls) == len(reviewer.calls) == 2
    assert [row["cognitive_target"]["id"] for row in reviewer.calls[1]["items"]] == [
        "relation_understanding"
    ]
    candidates = [row["items"][2] for row in events if row["event"] == "candidate"]
    assert candidates[0] == candidates[1]
    candidate = candidates[0]
    chapter = value.chapters[0]
    item = subject._validate_item(
        generation.ItemDraft.model_validate(candidate),
        chapter,
        {point[0]: point for point in subject._select_points(chapter)},
        allow_rewrite=True,
        structured_application=True,
    )
    key = subject._approval_key(writer.calls[0], item, candidate["application_task"])
    for component in ("basis", "quantity", "shared_conditions", "rules", "records", "derivation"):
        task = deepcopy(candidate["application_task"])
        task[component] = "changed private component"
        assert subject._approval_key(writer.calls[0], item, task) != key
