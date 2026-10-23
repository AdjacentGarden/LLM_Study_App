"""Mixed semantic/structural repair scopes must converge without losing vetoes."""

import json
from copy import deepcopy

from test_teaching_content import course, lesson_candidate, point_candidate
from test_teaching_v15_integration import FIXED, CandidateClient, Planner

from adaptive_learning.personalization import teaching_content as teaching


def test_new_checkpoint_collision_keeps_pending_semantic_summary_editable():
    class MixedClient(CandidateClient):
        def __init__(self):
            super().__init__()
            self.lesson_writes = 0
            self.teacher_requests = []

        def structured(self, *, system, user, **kwargs):
            if system != teaching.GENERATION_PROMPT:
                return super().structured(system=system, user=user, **kwargs)
            data = json.loads(user)
            self.generations.append(deepcopy(data))
            points = [point_candidate(row) for row in data["batch_points"]]
            lesson = None
            if data["include_lesson"]:
                self.lesson_writes += 1
                lesson = deepcopy((data["previous_candidate"] or {}).get("lesson") or lesson_candidate())
                if self.lesson_writes == 2:
                    lesson["checkpoints"][0]["question"] = data["existing_point_content"][0]["practice"]["prompt"]
                elif self.lesson_writes == 3:
                    lesson["summary"] += FIXED
                    lesson["checkpoints"][0] = deepcopy(lesson_candidate()["checkpoints"][0])
                    lesson["checkpoints"][0]["expected_answer"] += FIXED
            return {"points": points, "lesson": lesson}

        def teacher(self, data):
            self.teacher_requests.append(deepcopy(data))
            result = super().teacher(data)
            lesson = data["candidate"]["lesson"]
            if FIXED not in lesson["summary"] or FIXED not in lesson["checkpoints"][0]["expected_answer"]:
                result["accepted"] = False
                result["lesson"]["reading_ok"] = False
                result["lesson"]["checkpoints_ok"] = False
                result["lesson"]["issues"] = [
                    "lesson.summary：总结必须完善。",
                    "lesson.checkpoints：检查题必须完善。",
                ]
            return result

    bundle, chapter, evidence = course(2)
    client = MixedClient()
    events = []
    result = teaching.TeachingContentGenerator(
        client, scope_planner=Planner(), audit_callback=events.append
    ).generate(bundle, chapter, evidence)
    assert client.lesson_writes == 3
    repairs = [row for row in client.generations if row["previous_candidate"] is not None]
    assert repairs[0]["allowed_lesson_repairs"] == ["checkpoints", "reading_source_ids", "summary"]
    assert repairs[1]["allowed_lesson_repairs"] == ["checkpoint_1", "checkpoints", "reading_source_ids", "summary"]
    assert repairs[1]["required_changes"]["lesson_components"] == ["summary"]
    assert "summary" in repairs[1]["previous_candidate"]["lesson"]
    structural = next(
        row["lesson"]["structural_validation"]
        for row in repairs[1]["review_feedback"]
        if isinstance(row, dict) and "structural_validation" in row.get("lesson", {})
    )
    instruction = structural["repair_instruction"]
    assert "required_changes" in instruction
    assert "allowed_lesson_repairs的联合范围" in instruction
    assert "保留其余检查题、导入、总结" not in instruction
    assert structural["lesson_components"] == ["checkpoint_1"]
    assert structural["collisions"][0]["matches"][0]["component"] == "practice"
    assert len(client.teacher_requests) == 2  # No review of the structurally broken middle draft.
    first, final = [event["candidate"] for event in events if event["event"] == "candidate"]
    assert first["points"] == final["points"]
    assert first["lesson"]["opening"] == final["lesson"]["opening"]
    assert first["lesson"]["worked_examples"] == final["lesson"]["worked_examples"]
    assert first["lesson"]["checkpoints"][1] == final["lesson"]["checkpoints"][1]
    assert FIXED in final["lesson"]["summary"]
    assert FIXED in final["lesson"]["checkpoints"][0]["expected_answer"]
    assert result.teaching_review["accepted"] is True
    assert result.teaching_fact_review["accepted"] is True
