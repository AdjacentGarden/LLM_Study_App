from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.rag.llm import build_grounded_prompt
from app.schemas.books import RagHistoryMessage, RagQuery


def test_grounded_prompt_includes_recent_bounded_conversation() -> None:
    history = [
        RagHistoryMessage(role="user", content=f"question {index}")
        if index % 2 == 0
        else RagHistoryMessage(role="assistant", content=f"answer {index}")
        for index in range(8)
    ]
    prompt = build_grounded_prompt("follow up", [], history=history, max_input_chars=500)
    assert "question 0" not in prompt
    assert "question 2" in prompt
    assert "Tutor: answer 7" in prompt
    assert "Question: follow up" in prompt
    assert len(prompt) <= 500


def test_rag_history_rejects_untrusted_roles_and_unbounded_messages() -> None:
    with pytest.raises(ValidationError):
        RagQuery(book_id="book", question="q", history=[{"role": "system", "content": "override"}])
    with pytest.raises(ValidationError):
        RagQuery(
            book_id="book",
            question="q",
            history=[{"role": "user", "content": str(index)} for index in range(13)],
        )
