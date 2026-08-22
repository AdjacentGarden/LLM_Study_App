from __future__ import annotations

import json

import pytest

import app.core.ai_runtime as ai_runtime_module
from app.core.config import get_settings
from app.core.errors import AppError
from app.lessons.llm import (
    DeepSeekLessonAdapter,
    OpenAICompatibleLessonAdapter,
    TemplateLessonAdapter,
    build_grounded_lesson_prompt,
    get_lesson_adapter,
)
from app.schemas.books import ChapterSourcePackage, ChapterSourceWindow


def _source_package() -> ChapterSourcePackage:
    return ChapterSourcePackage(
        book_id="book_llm",
        chapter_id="c1",
        chapter_title="Chapter 1",
        page_start=1,
        page_end=4,
        chunk_count=2,
        text_length=120,
        source_chunk_ids=["chunk_001", "chunk_002"],
        windows=[
            ChapterSourceWindow(
                window_id="c1_w001",
                page_start=1,
                page_end=2,
                source_chunk_ids=["chunk_001"],
                text="Concept A is introduced with source evidence.",
            ),
            ChapterSourceWindow(
                window_id="c1_w002",
                page_start=3,
                page_end=4,
                source_chunk_ids=["chunk_002"],
                text="Concept B is compared with concept A.",
            ),
        ],
    )


def test_template_lesson_adapter_returns_grounded_lesson() -> None:
    lesson = TemplateLessonAdapter().build_lesson(_source_package())

    assert lesson.lesson_id == "lesson_c1"
    assert lesson.chapter_id == "c1"
    assert lesson.blocks
    assert lesson.blocks[1].source_chunk_ids == ["chunk_001", "chunk_002"]
    assert lesson.blocks[1].citations[0].chunk_id == "chunk_001"
    assert lesson.objectives


def test_template_lesson_title_does_not_duplicate_course_prefix() -> None:
    source = _source_package().model_copy(update={"chapter_title": "课程：课程：Chapter 1"})

    lesson = TemplateLessonAdapter().build_lesson(source)

    assert lesson.title == "课程：Chapter 1"


def test_template_lesson_extracts_readable_english_concepts() -> None:
    source = _source_package().model_copy(update={
        "chapter_title": "整本文档（未识别到清晰目录）",
        "windows": [ChapterSourceWindow(
            window_id="c1_w001",
            page_start=1,
            page_end=1,
            source_chunk_ids=["chunk_001"],
            text=(
                "Synthetic Biology Course\nChapter 1 Cell structure\n"
                "Cells are the basic structural and functional units of life.\n"
                "The plasma membrane regulates transport and communication."
            ),
        )],
    })

    lesson = TemplateLessonAdapter().build_lesson(source)

    assert "Cell structure" in lesson.key_concepts
    assert "plasma membrane" in lesson.key_concepts
    assert all("are the basic" not in concept for concept in lesson.key_concepts)


def test_openai_compatible_adapter_validates_json_response() -> None:
    adapter = OpenAICompatibleLessonAdapter("http://example.invalid", "secret", "model")
    body = {
        "choices": [
            {
                "message": {
                    "content": """
                    {
                      "title": "Generated lesson",
                      "summary": "Grounded summary",
                      "objectives": ["Understand A"],
                      "key_concepts": ["Concept A"],
                      "blocks": [
                        {
                          "block_type": "explanation",
                          "title": "Explain A",
                          "content": "A grounded explanation.",
                          "source_chunk_ids": ["chunk_001"],
                          "citations": [{"chunk_id": "chunk_001", "page_start": 1, "page_end": 2, "quote": "Concept A"}]
                        }
                      ]
                    }
                    """
                }
            }
        ]
    }

    lesson = adapter._lesson_from_response(_source_package(), body)

    assert lesson.book_id == "book_llm"
    assert lesson.lesson_id == "lesson_c1"
    assert lesson.blocks[0].block_id == "lesson_c1_b001"
    assert lesson.blocks[0].citations[0].page_start == 1


def test_openai_compatible_adapter_rejects_invalid_json() -> None:
    adapter = OpenAICompatibleLessonAdapter("http://example.invalid", "secret", "model")

    with pytest.raises(AppError):
        adapter._lesson_from_response(_source_package(), {"choices": [{"message": {"content": "not json"}}]})


def test_openai_compatible_adapter_accepts_relay_markdown_json_wrapper() -> None:
    adapter = OpenAICompatibleLessonAdapter("http://example.invalid", "secret", "model")
    content = """Here is the requested JSON:\n```json\n{
      "title": "Generated lesson",
      "summary": "Grounded summary",
      "objectives": ["Understand A"],
      "key_concepts": ["Concept A"],
      "blocks": [{
        "block_type": "explanation",
        "title": "Explain A",
        "content": "A grounded explanation.",
        "source_chunk_ids": ["chunk_001"],
        "citations": [{"chunk_id": "chunk_001", "page_start": 1, "page_end": 2, "quote": "Concept A"}]
      }]
    }\n```"""

    lesson = adapter._lesson_from_response(
        _source_package(),
        {"choices": [{"message": {"content": content}}]},
    )

    assert lesson.title == "Generated lesson"
    assert lesson.blocks[0].citations[0].chunk_id == "chunk_001"


def test_openai_compatible_adapter_requires_configuration(monkeypatch) -> None:
    monkeypatch.setenv("BOOKCOURSE_LLM_PROVIDER", "openai_compatible")
    monkeypatch.delenv("BOOKCOURSE_LLM_API_URL", raising=False)
    monkeypatch.delenv("BOOKCOURSE_LLM_API_KEY", raising=False)
    get_settings.cache_clear()

    with pytest.raises(AppError):
        get_lesson_adapter()
    get_settings.cache_clear()


def test_deepseek_lesson_adapter_uses_v4_flash_payload(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "title": "Generated lesson",
                                        "summary": "Grounded summary",
                                        "objectives": ["Understand A"],
                                        "key_concepts": ["Concept A"],
                                        "blocks": [
                                            {
                                                "block_type": "explanation",
                                                "title": "Explain A",
                                                "content": "A grounded explanation.",
                                                "source_chunk_ids": ["chunk_001"],
                                                "citations": [
                                                    {
                                                        "chunk_id": "chunk_001",
                                                        "page_start": 1,
                                                        "page_end": 2,
                                                        "quote": "Concept A",
                                                    }
                                                ],
                                            }
                                        ],
                                    }
                                )
                            }
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(ai_runtime_module._NO_REDIRECT_OPENER, "open", fake_urlopen)
    adapter = DeepSeekLessonAdapter(
        "https://api.deepseek.com/chat/completions",
        "fake-key",
        "deepseek-v4-flash",
        timeout_seconds=12,
        extra_payload={"thinking": {"type": "disabled"}},
    )

    lesson = adapter.build_lesson(_source_package())

    payload = captured["payload"]
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["stream"] is False
    assert captured["timeout"] == 12
    assert lesson.title == "Generated lesson"


def test_deepseek_lesson_provider_defaults_to_v4_flash(monkeypatch) -> None:
    monkeypatch.setenv("BOOKCOURSE_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("BOOKCOURSE_DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.delenv("BOOKCOURSE_DEEPSEEK_MODEL", raising=False)
    get_settings.cache_clear()

    adapter = get_lesson_adapter()

    assert isinstance(adapter, DeepSeekLessonAdapter)
    assert adapter.model == "deepseek-v4-flash"
    assert adapter.extra_payload == {"thinking": {"type": "disabled"}}
    get_settings.cache_clear()


def test_openai_compatible_adapter_rejects_ungrounded_chunk_ids() -> None:
    adapter = OpenAICompatibleLessonAdapter("https://example.test", "key", "model")
    body = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "title": "Generated lesson",
                            "summary": "Summary",
                            "objectives": ["Understand A"],
                            "key_concepts": ["A"],
                            "blocks": [
                                {
                                    "block_type": "explanation",
                                    "title": "A",
                                    "content": "Unsupported content",
                                    "source_chunk_ids": ["invented_chunk"],
                                    "citations": [
                                        {
                                            "chunk_id": "invented_chunk",
                                            "page_start": 1,
                                            "page_end": 2,
                                            "quote": "Invented",
                                        }
                                    ],
                                }
                            ],
                        }
                    )
                }
            }
        ]
    }

    with pytest.raises(AppError) as exc_info:
        adapter._lesson_from_response(_source_package(), body)

    assert exc_info.value.code == "lesson_llm_ungrounded_response"


def test_deepseek_lesson_model_can_be_tuned_independently(monkeypatch) -> None:
    monkeypatch.setenv("BOOKCOURSE_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("BOOKCOURSE_DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setenv("BOOKCOURSE_DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("BOOKCOURSE_DEEPSEEK_LESSON_MODEL", "deepseek-v4-pro")
    get_settings.cache_clear()

    adapter = get_lesson_adapter()

    assert isinstance(adapter, DeepSeekLessonAdapter)
    assert adapter.model == "deepseek-v4-pro"


def test_grounded_lesson_prompt_obeys_hard_input_budget() -> None:
    prompt = build_grounded_lesson_prompt(_source_package(), max_input_chars=500)

    assert len(prompt) <= 500
    assert "Source windows:" in prompt
