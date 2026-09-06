from __future__ import annotations

import json

from ..llm.client import LLMError, OpenAICompatibleClient
from .models import AssessmentResponse, DiagnosticItem, ScoredEvidence

_GRADING_SYSTEM = """你是学习诊断评分器，不是聊天助手。请严格依据题目、参考要点和评分量规判断回答中实际体现的知识。
要求：
1. 不因表达风格、篇幅、错别字惩罚用户，只判断概念是否成立。
2. 不把参考答案之外的新知识当成错误，但不得臆测用户没有写出的含义。
3. 逐条返回 matched_rubric 与 missing_rubric；误解必须引用用户回答中的依据。
4. scoring_confidence 表示你对评分的把握，不是用户答案质量。
5. 题意不清、回答过短或存在多种合理解释时，needs_follow_up=true，禁止假装确定。
只返回 JSON：
{"score":0到1,"scoring_confidence":0到1,"matched_rubric":[],"missing_rubric":[],"misconception_candidates":[],"needs_follow_up":true或false,"follow_up_question":""}
"""


class OpenAnswerScorer:
    def __init__(self, client: OpenAICompatibleClient, *, minimum_confidence: float = 0.78) -> None:
        self.client = client
        self.minimum_confidence = minimum_confidence

    def score(self, item: DiagnosticItem, response: AssessmentResponse) -> ScoredEvidence:
        request = json.dumps(
            {
                "question": item.prompt,
                "expected_answer": item.expected_answer,
                "rubric": item.rubric,
                "user_answer": response.answer,
            },
            ensure_ascii=False,
        )
        try:
            result = self.client.structured(system=_GRADING_SYSTEM, user=request)
            confidence = _bounded(result.get("scoring_confidence"))
            evidence = ScoredEvidence(
                score=_bounded(result.get("score")),
                scoring_confidence=confidence,
                matched_rubric=_strings(result.get("matched_rubric")),
                missing_rubric=_strings(result.get("missing_rubric")),
                misconception_candidates=_strings(result.get("misconception_candidates")),
                needs_follow_up=bool(result.get("needs_follow_up", False))
                or confidence < self.minimum_confidence,
                follow_up_question=str(result.get("follow_up_question") or "").strip() or None,
            )
            return evidence
        except (LLMError, TypeError, ValueError):
            return ScoredEvidence(score=0.5, scoring_confidence=0, needs_follow_up=True)


def _bounded(value: object) -> float:
    if not isinstance(value, int | float | str):
        raise TypeError("score must be numeric")
    return min(1.0, max(0.0, float(value)))


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:20]
