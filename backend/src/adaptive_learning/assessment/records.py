"""Public learning history: resolve actual records without exposing unused answer keys."""

from collections import defaultdict, deque

from ..personalization.models import ChapterLearningBundle
from .models import DiagnosticItem, InterviewSession


def learning_records(
    session: InterviewSession, items: list[DiagnosticItem], courses: list[ChapterLearningBundle]
) -> dict[str, object]:
    profile = session.profile
    chapters = {item["id"]: item["label"] for item in session.chapter_options}
    labels = {}
    questions = {}
    cards = {}
    for item in items:
        questions[item.item_id] = (item.prompt, "选择题诊断", item.source_pages)
        for i, point_id in enumerate(item.knowledge_point_ids):
            label = (
                item.knowledge_point_labels[i]
                if i < len(item.knowledge_point_labels)
                else item.prompt
            )
            labels[point_id] = (label, item.chapter_id, item.source_pages)
    for course in courses:
        chapters[course.chapter_id] = course.chapter_title
        for point in course.knowledge_points:
            labels[point.point_id] = (
                point.title,
                course.chapter_id,
                [c.page_number for c in point.citations],
            )
        for card in course.flashcards:
            cards[card.card_id] = (card, course.chapter_id)
            questions[card.card_id] = (
                card.front,
                "闪卡复习",
                [c.page_number for c in card.citations],
            )
        for practice in course.practice_items:
            questions[practice.item_id] = (
                practice.prompt,
                "章节练习",
                [c.page_number for c in practice.citations],
            )
    for observation in profile.diagnostic_observations:
        for point_id, label in zip(
            observation.knowledge_point_ids, observation.knowledge_labels, strict=False
        ):
            labels.setdefault(point_id, (label, observation.chapter_id, observation.source_pages))
    knowledge = []
    for point_id, mastery in profile.knowledge_mastery.items():
        if mastery.evidence_count <= 0:
            continue
        label, chapter, pages = labels.get(point_id, ("历史知识点（原内容暂不可用）", "", []))
        knowledge.append(
            {
                "id": point_id,
                "title": label,
                "chapter": chapters.get(chapter, "历史章节"),
                "mastery": mastery.mean,
                "evidence_count": mastery.evidence_count,
                "pages": sorted(set(pages)),
            }
        )
    answers: dict[str, deque[str]] = defaultdict(deque)
    for response in session.responses:
        answers[response.item_id].append(response.answer)
    evidence = []
    for i, observation in enumerate(profile.diagnostic_observations):
        prompt, kind, pages = questions.get(
            observation.item_id,
            ("历史题目（原内容暂不可用）", observation.activity_kind or "学习记录", []),
        )
        historical_answer = (
            answers[observation.item_id].popleft() if answers[observation.item_id] else None
        )
        evidence.append(
            {
                "id": str(i),
                "title": observation.question_snapshot or prompt,
                "kind": kind,
                "chapter": chapters.get(observation.chapter_id, "历史章节"),
                "score": observation.score,
                "confidence": observation.scoring_confidence,
                "seconds": observation.response_seconds,
                "at": observation.observed_at.isoformat(),
                "answer": observation.answer_snapshot
                if observation.answer_snapshot is not None
                else historical_answer,
                "pages": sorted(set(observation.source_pages or pages)),
            }
        )
    reviewed = []
    for card_id, state in profile.flashcard_reviews.items():
        saved = cards.get(card_id)
        saved_card, chapter = saved if saved else (None, "")
        reviewed.append(
            {
                "id": card_id,
                "front": saved_card.front if saved_card else "历史闪卡（原内容暂不可用）",
                "back": saved_card.back if saved_card else None,
                "chapter": chapters.get(chapter, "历史章节"),
                "rating": state.last_rating,
                "due_at": state.due_at.isoformat() if state.due_at else None,
                "reviewed_at": state.last_review_at.isoformat() if state.last_review_at else None,
                "pages": sorted({c.page_number for c in saved_card.citations})
                if saved_card
                else [],
            }
        )
    return {
        "book_id": profile.book_id,
        "knowledge": knowledge,
        "evidence": list(reversed(evidence)),
        "flashcards": sorted(reviewed, key=lambda card: card["reviewed_at"] or "", reverse=True),
    }
