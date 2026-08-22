from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app.core.errors import AppError
from app.core.logging import get_logger
from app.rag.service import answer_query
from app.schemas.books import AssignmentSubmitRequest, AssignmentSubmitResponse, DiagnosisResponse, MistakeRecord, RagQuery
from app.services.kv_store import _PersistedKVStore


_logger = get_logger("app.assignments")

HOMOLOGOUS = "\u540c\u6e90\u67d3\u8272\u4f53"
MEIOSIS_II = "\u7b2c\u4e8c\u6b21"
SISTER_CHROMATID = "\u59d0\u59b9\u67d3\u8272\u5355\u4f53"


def _grounded_review_hint(rag) -> str:
    """Build a neutral review prompt from the retrieved textbook evidence.

    The deterministic fallback must never leak the biology demo's chromosome
    wording into unrelated courses.  Provider-backed diagnosis can be richer,
    but this path stays useful and source-grounded without an external model.
    """

    if not rag.citations:
        return "请补充你的结论、推理依据和最不确定的一步，再重新提交诊断。"
    citation = rag.citations[0]
    location = citation.location_label or f"第 {citation.page} 页"
    chapter = citation.chapter_title.strip()
    source = f"{location}“{chapter}”" if chapter else location
    return f"先回到{source}的原文，把答案拆成“结论—教材依据—仍不确定点”三步逐项核对。"


@dataclass
class SubmissionRecord:
    assignment_id: str
    submission_id: str
    user_id: str
    book_id: str
    chapter_id: str | None
    question: str
    answer: str

    def to_dict(self) -> dict:
        return {
            "assignment_id": self.assignment_id,
            "submission_id": self.submission_id,
            "user_id": self.user_id,
            "book_id": self.book_id,
            "chapter_id": self.chapter_id,
            "question": self.question,
            "answer": self.answer,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SubmissionRecord":
        return cls(
            assignment_id=data["assignment_id"],
            submission_id=data["submission_id"],
            user_id=data.get("user_id", ""),
            book_id=data["book_id"],
            chapter_id=data.get("chapter_id"),
            question=data.get("question", ""),
            answer=data.get("answer", ""),
        )


_submissions_kv = _PersistedKVStore("submissions")
_mistakes_kv = _PersistedKVStore("mistakes")


def submissions_for_test() -> dict[str, SubmissionRecord]:
    """Back-compat helper for tests that inspect the public dict directly."""
    return {key: SubmissionRecord.from_dict(value) for key, value in _submissions_kv.all().items()}


def mistakes_for_test() -> list[MistakeRecord]:
    return list(_mistakes_kv.values())


def clear_for_test() -> None:
    for key in list(_submissions_kv.keys()):
        _submissions_kv.remove(key)
    for key in list(_mistakes_kv.keys()):
        _mistakes_kv.remove(key)
    _submissions_kv.clear_cache()
    _mistakes_kv.clear_cache()


def submit_assignment(assignment_id: str, payload: AssignmentSubmitRequest) -> AssignmentSubmitResponse:
    submission_id = f"sub_{uuid4().hex[:12]}"
    record = SubmissionRecord(
        assignment_id=assignment_id,
        submission_id=submission_id,
        user_id=payload.user_id,
        book_id=payload.book_id,
        chapter_id=payload.chapter_id,
        question=payload.question,
        answer=payload.answer,
    )
    _submissions_kv.upsert(submission_id, record.to_dict())
    return AssignmentSubmitResponse(assignment_id=assignment_id, submission_id=submission_id, status="submitted")


def get_submission(submission_id: str) -> SubmissionRecord | None:
    data = _submissions_kv.get(submission_id)
    return SubmissionRecord.from_dict(data) if data is not None else None


def diagnose_assignment(assignment_id: str, submission_id: str) -> DiagnosisResponse:
    record = get_submission(submission_id)
    if record is None:
        raise AppError("submission_not_found", "submission_id not found", status_code=404)
    if record.assignment_id != assignment_id:
        raise AppError(
            "assignment_mismatch",
            "submission_id does not belong to this assignment",
            status_code=400,
            details={"expected_assignment_id": record.assignment_id},
        )

    rag = answer_query(RagQuery(book_id=record.book_id, chapter_id=record.chapter_id, question=f"{record.question} {record.answer}"))
    answer_text = record.answer
    if len(answer_text.strip()) < 4 or not rag.citations:
        review_hint = _grounded_review_hint(rag)
        return DiagnosisResponse(
            assignment_id=assignment_id,
            submission_id=submission_id,
            result="\u76ee\u524d\u7b54\u6848\u4fe1\u606f\u4e0d\u8db3\uff0c\u8fd8\u4e0d\u80fd\u7ed9\u51fa\u7a33\u5b9a\u8bca\u65ad\u3002",
            stuck_point="\u9700\u8981\u8865\u5145\u63a8\u7406\u6b65\u9aa4\u6216\u5173\u952e\u6982\u5ff5\u3002",
            knowledge_points=[],
            review_citations=rag.citations,
            related_assets=rag.related_assets,
            hint=review_hint,
            needs_followup=True,
            followup_question="你的答案里哪一步最不确定？请同时写出支持当前判断的教材原句或页码。",
            mistake_recorded=False,
        )

    is_misconception = HOMOLOGOUS in record.question and MEIOSIS_II in answer_text
    if is_misconception:
        stuck_point = f"\u6df7\u6dc6\u4e86{HOMOLOGOUS}\u5206\u79bb\u4e0e{SISTER_CHROMATID}\u5206\u79bb\u3002"
        result = (
            "\u4f60\u5df2\u7ecf\u5b9a\u4f4d\u5230\u5206\u88c2\u9636\u6bb5\u95ee\u9898\uff0c"
            "\u4f46\u9700\u8981\u5148\u5206\u6e05\u9898\u76ee\u5728\u95ee\u54ea\u4e00\u7c7b\u5206\u79bb\u5bf9\u8c61\u3002"
            "\u8bf7\u5bf9\u7167\u5f15\u7528\u7247\u6bb5\u91cc\u7684\u4e24\u4e2a\u5206\u79bb\u5bf9\u8c61\uff0c\u518d\u81ea\u5df1\u5224\u65ad\u9636\u6bb5\u3002"
        )
        knowledge_points = [HOMOLOGOUS, SISTER_CHROMATID, "\u5206\u79bb\u5bf9\u8c61\u8fa8\u6790"]
    else:
        stuck_point = "\u9700\u8981\u7ed3\u5408\u6559\u6750\u6765\u6e90\u7ee7\u7eed\u6838\u5bf9\u5173\u952e\u6982\u5ff5\u3002"
        result = "\u5df2\u6839\u636e\u6559\u6750\u7247\u6bb5\u5b8c\u6210\u521d\u6b65\u8bca\u65ad\uff0c\u5efa\u8bae\u5bf9\u7167\u6765\u6e90\u9875\u91cd\u65b0\u68c0\u67e5\u63a8\u7406\u6b65\u9aa4\u3002"
        knowledge_points = []

    hint = (
        f"先判断分离对象是{HOMOLOGOUS}，还是复制后连在一起的{SISTER_CHROMATID}。"
        if is_misconception
        else _grounded_review_hint(rag)
    )
    diagnosis = DiagnosisResponse(
        assignment_id=assignment_id,
        submission_id=submission_id,
        result=result,
        stuck_point=stuck_point,
        knowledge_points=knowledge_points,
        review_citations=rag.citations,
        related_assets=rag.related_assets,
        hint=hint,
        mistake_recorded=is_misconception,
    )
    if not is_misconception:
        return diagnosis

    mistake = MistakeRecord(
        mistake_id=f"mistake_{uuid4().hex[:12]}",
        user_id=record.user_id,
        book_id=record.book_id,
        assignment_id=assignment_id,
        question=record.question,
        answer=record.answer,
        stuck_point=stuck_point,
        knowledge_points=knowledge_points,
        citation_ids=[citation.chunk_id for citation in rag.citations],
    )
    _mistakes_kv.upsert(mistake.mistake_id, mistake.model_dump(mode="json"))
    return diagnosis


def list_mistakes(user_id: str | None = None, book_id: str | None = None) -> list[MistakeRecord]:
    records = [MistakeRecord.model_validate(item) for item in _mistakes_kv.values()]
    if user_id:
        records = [mistake for mistake in records if mistake.user_id == user_id]
    if book_id:
        records = [mistake for mistake in records if mistake.book_id == book_id]
    return records
