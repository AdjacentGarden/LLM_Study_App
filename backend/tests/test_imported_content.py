from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from adaptive_learning.assessment.engine import score_choice
from adaptive_learning.assessment.models import (
    AssessmentResponse,
    DiagnosticItem,
    InterviewPhase,
    InterviewSession,
    LearnerProfile,
    ResponseType,
)
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.ingestion.models import ChapterDraft, SourceQuote
from adaptive_learning.personalization.flashcard_quality import FlashcardQualityGate
from adaptive_learning.personalization.generator import ChapterCourseCompiler, chapter_fingerprint
from adaptive_learning.personalization.imported_content import load_imported_teaching
from adaptive_learning.personalization.models import PublicChapterLearningBundle
from adaptive_learning.personalization.policy import PersonalizationPolicy

BOOK_ID = "book_imported_test"
PDF_HASH = "a" * 64
QUOTE = "染色体只复制一次，而细胞分裂两次"


def _chapter() -> ChapterDraft:
    source = SourceQuote(page_number=1, quote=QUOTE)
    return ChapterDraft(
        chapter_id="c2s1",
        order=1,
        title="减数分裂",
        start_page=1,
        end_page=1,
        summary="来源已核验的章节。",
        knowledge_points=["减数分裂"],
        source_block_ids=["block_1_0"],
        evidence=[source],
        knowledge_point_evidence={"减数分裂": [source]},
    )


def _write_pack(data_dir: Path, *, metadata_key: str = "source_pdf_sha256") -> Path:
    book_root = data_dir / "books" / BOOK_ID
    pages = book_root / "ocr" / "normalized" / "pages.jsonl"
    pages.parent.mkdir(parents=True)
    pages.write_text(
        json.dumps(
            {
                "page_number": 1,
                "text": f"在减数分裂过程中，{QUOTE}。",
                "blocks": [
                    {
                        "block_index": 0,
                        "text": f"在减数分裂过程中，{QUOTE}。",
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    imported = book_root / "imported"
    image = imported / "assets" / "diagram.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    (imported / "assets.json").write_text(
        json.dumps(
            {
                "diagram_1": {
                    "path": "assets/diagram.png",
                    "kind": "image",
                    "caption": "减数分裂示意图",
                    "source_kind": "textbook",
                    "page_number": 1,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    metadata = {metadata_key: PDF_HASH}
    pack = {
        "schema_version": 1,
        "source_book_id": BOOK_ID,
        "source_pdf_sha256": PDF_HASH,
        "lessons": [
            {
                "book_id": BOOK_ID,
                "chapter_id": "c2s1",
                "blocks": [
                    {
                        "title": "先抓住总结构",
                        "content": "减数分裂前复制一次，随后分裂两次。",
                        "ai_generated": True,
                        "asset_ids": ["diagram_1", "../secret"],
                        "citations": [
                            {
                                "page_start": 1,
                                "page_end": 1,
                                "quote": QUOTE,
                                "source_metadata": metadata,
                            }
                        ],
                    }
                ],
            }
        ],
        "flashcards": [
            {
                "book_id": BOOK_ID,
                "chapter_id": "c2s1",
                "card_id": "fc_1",
                "concept": "减数分裂",
                "front": "复制几次？",
                "back": "一次。",
                "source_kind": "textbook",
                "source_quote": QUOTE,
                "source_metadata": metadata,
                "page_start": 1,
                "page_end": 1,
                "due": "today",
                "mastery": 99,
            }
        ],
        "quiz": [
            {
                "book_id": BOOK_ID,
                "chapter_id": "c2s1",
                "question_id": "quiz_1",
                "concept": "减数分裂",
                "prompt": "染色体复制几次？",
                "choices": ["两次", "一次"],
                "answer": "一次",
                "explanation": "教材说明只复制一次。",
                "question_type": "choice",
                "source_kind": "textbook",
                "source_quote": QUOTE,
                "source_metadata": metadata,
                "page_start": 1,
                "page_end": 1,
            }
        ],
    }
    path = imported / "teaching.json"
    path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("metadata_key", ["source_sha256", "source_pdf_sha256"])
def test_imported_course_uses_verified_lesson_cards_and_real_choice_scoring(
    tmp_path: Path, metadata_key: str
) -> None:
    _write_pack(tmp_path, metadata_key=metadata_key)
    chapter = _chapter()
    imported = load_imported_teaching(tmp_path, BOOK_ID, chapter, PDF_HASH)
    assert imported is not None
    assert len(imported.sections) == len(imported.cards) == len(imported.questions) == 1
    assert imported.sections[0].media[0].url == (
        f"/api/books/{BOOK_ID}/imported-assets/diagram_1"
    )
    assert len(imported.sections[0].media) == 1

    profile = LearnerProfile(user_id="student", book_id=BOOK_ID)
    bundle = ChapterCourseCompiler().compile(
        chapter=chapter,
        profile=profile,
        decision=PersonalizationPolicy().decide(profile, chapter.chapter_id),
        imported=imported,
    )
    assert bundle.original_reading[0].title == "先抓住总结构"
    assert "AI 整理" in bundle.original_reading[0].purpose
    assert bundle.flashcards[0].front == "复制几次？"
    assert bundle.flashcards[0].reason_for_user != "today"
    assert bundle.practice_items[0].prompt == "染色体复制几次？"
    assert bundle.practice_items[0].correct_option_ids == ["1"]
    assert bundle.source_fingerprint == chapter_fingerprint(chapter, imported.content_hash)

    public = PublicChapterLearningBundle.from_private(bundle).model_dump_json()
    for private_value in ("correct_option_ids", "expected_answer", "rubric", "教材说明只复制一次"):
        assert private_value not in public
    item = bundle.practice_items[0]
    score = score_choice(
        DiagnosticItem(
            item_id=item.item_id,
            chapter_id=chapter.chapter_id,
            knowledge_point_ids=[item.point_id],
            prompt=item.prompt,
            response_type=ResponseType.SINGLE_CHOICE,
            options=item.options,
            correct_option_ids=item.correct_option_ids,
        ),
        AssessmentResponse(
            item_id=item.item_id,
            answer="",
            selected_option_ids=["1"],
            confidence=0.8,
            response_seconds=20,
        ),
    )
    assert score.score == 1


def test_source_conflict_or_unmatched_quote_cannot_supply_teaching_content(tmp_path: Path) -> None:
    path = _write_pack(tmp_path)
    pack = json.loads(path.read_text(encoding="utf-8"))
    for kind, row in (
        ("lessons", pack["lessons"][0]["blocks"][0]["citations"][0]),
        ("flashcards", pack["flashcards"][0]),
        ("quiz", pack["quiz"][0]),
    ):
        row["source_metadata"]["source_sha256"] = "b" * 64
        assert kind
    path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    assert load_imported_teaching(tmp_path, BOOK_ID, _chapter(), PDF_HASH) is None

    for row in (pack["lessons"][0]["blocks"][0]["citations"][0], pack["flashcards"][0], pack["quiz"][0]):
        row["source_metadata"] = {"source_pdf_sha256": PDF_HASH}
        if "quote" in row:
            row["quote"] = "这段话不在教材里"
        else:
            row["source_quote"] = "这段话不在教材里"
    path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    assert load_imported_teaching(tmp_path, BOOK_ID, _chapter(), PDF_HASH) is None


def test_same_page_quote_from_another_section_is_not_accepted(tmp_path: Path) -> None:
    _write_pack(tmp_path)
    pages_path = tmp_path / "books" / BOOK_ID / "ocr" / "normalized" / "pages.jsonl"
    pages_path.write_text(
        json.dumps(
            {
                "page_number": 1,
                "text": f"当前节无证据。另一节说：{QUOTE}。",
                "blocks": [
                    {"block_index": 0, "text": "当前节无证据。"},
                    {"block_index": 1, "text": f"另一节说：{QUOTE}。"},
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    assert load_imported_teaching(tmp_path, BOOK_ID, _chapter(), PDF_HASH) is None


def test_generated_fallback_cards_receive_verified_block_context(tmp_path: Path) -> None:
    _write_pack(tmp_path)
    chapter = _chapter()
    fallback_label = "两次分裂的衔接"
    chapter.knowledge_points.append(fallback_label)
    chapter.knowledge_point_evidence[fallback_label] = [
        SourceQuote(page_number=1, quote=QUOTE)
    ]
    imported = load_imported_teaching(tmp_path, BOOK_ID, chapter, PDF_HASH)
    assert imported is not None
    profile = LearnerProfile(user_id="student", book_id=BOOK_ID)
    bundle = ChapterCourseCompiler().compile(
        chapter=chapter,
        profile=profile,
        decision=PersonalizationPolicy().decide(profile, chapter.chapter_id),
        imported=imported,
    )
    fallback = next(card for card in bundle.flashcards if "_import_" not in card.card_id)
    assert fallback.citations[0].quote == QUOTE
    assert any(
        citation.block_id == "block_1_0"
        and "在减数分裂过程中" in citation.quote
        and QUOTE in citation.quote
        for citation in fallback.citations[1:]
    )


def test_pack_content_change_invalidates_course_but_demo_progress_does_not(tmp_path: Path) -> None:
    path = _write_pack(tmp_path)
    chapter = _chapter()
    initial = load_imported_teaching(tmp_path, BOOK_ID, chapter, PDF_HASH)
    assert initial is not None
    pack = json.loads(path.read_text(encoding="utf-8"))
    pack["flashcards"][0]["due"] = "next_week"
    pack["flashcards"][0]["mastery"] = 3
    path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    no_progress_import = load_imported_teaching(tmp_path, BOOK_ID, chapter, PDF_HASH)
    assert no_progress_import is not None
    assert no_progress_import.content_hash == initial.content_hash

    pack["lessons"][0]["blocks"][0]["content"] = "更正后的讲解，来源引用仍在。"
    path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    revised = load_imported_teaching(tmp_path, BOOK_ID, chapter, PDF_HASH)
    assert revised is not None
    assert revised.content_hash != initial.content_hash
    assert chapter_fingerprint(chapter, revised.content_hash) != chapter_fingerprint(
        chapter, initial.content_hash
    )
    assert load_imported_teaching(tmp_path, BOOK_ID, chapter, "c" * 64) is None

    no_evidence = _chapter().model_copy(update={"knowledge_point_evidence": {}})
    assert load_imported_teaching(tmp_path, BOOK_ID, no_evidence, PDF_HASH) is not None
    profile = LearnerProfile(user_id="student", book_id=BOOK_ID)
    with pytest.raises(ValueError, match="evidence-backed"):
        ChapterCourseCompiler().compile(
            chapter=no_evidence,
            profile=profile,
            decision=PersonalizationPolicy().decide(profile, chapter.chapter_id),
            imported=revised,
        )


def test_course_api_recompiles_when_verified_teaching_text_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack_path = _write_pack(tmp_path)
    chapter = _chapter()
    module = importlib.import_module("adaptive_learning.api.app")
    repository = SQLiteAssessmentRepository(tmp_path / "assessments.sqlite3")
    session = InterviewSession(
        session_id="session_imported",
        profile=LearnerProfile(user_id="student", book_id=BOOK_ID),
        phase=InterviewPhase.COMPLETE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    monkeypatch.setattr(module, "settings", SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(module, "assessment_repository", repository)
    monkeypatch.setattr(
        module,
        "job_repository",
        SimpleNamespace(
            get_structure=lambda _: SimpleNamespace(chapters=[chapter]),
            source_fingerprint=lambda _: PDF_HASH,
        ),
    )
    monkeypatch.setattr(module, "_session_or_404", lambda _: session)
    monkeypatch.setattr(
        module, "flashcard_quality", SimpleNamespace(ensure=lambda bundle: bundle)
    )
    client = TestClient(module.app)
    url = "/api/interviews/session_imported/courses/c2s1"

    initial = client.post(url)
    assert initial.status_code == 200
    assert initial.json()["version"] == 1
    assert initial.json()["original_reading"][0]["media"][0]["asset_id"] == "diagram_1"
    assert client.get(url).status_code == 200

    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    pack["lessons"][0]["blocks"][0]["content"] = "更新后的减数分裂讲解。"
    pack_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    assert client.get(url).status_code == 404
    revised = client.post(url)
    assert revised.status_code == 200
    assert revised.json()["version"] == 2
    assert revised.json()["original_reading"][0]["content"] == "更新后的减数分裂讲解。"


def test_imported_flashcard_draft_is_reviewed_and_cache_survives_rewritten_text(
    tmp_path: Path,
) -> None:
    _write_pack(tmp_path)
    chapter = _chapter()
    imported = load_imported_teaching(tmp_path, BOOK_ID, chapter, PDF_HASH)
    assert imported is not None
    profile = LearnerProfile(user_id="student", book_id=BOOK_ID)
    bundle = ChapterCourseCompiler().compile(
        chapter=chapter,
        profile=profile,
        decision=PersonalizationPolicy().decide(profile, chapter.chapter_id),
        imported=imported,
    )

    class ReviewingClient:
        def __init__(self) -> None:
            self.calls = 0

        def structured(self, *, system: str, user: str, **kwargs: object) -> dict[str, object]:
            self.calls += 1
            card = json.loads(user)["cards"][0]
            assert card["imported_draft"] == {"front": "复制几次？", "back": "一次。"}
            if "candidate" in card:
                return {"reviews": [{"id": card["id"], "accepted": True, "issues": []}]}
            return {
                "cards": [
                    {
                        "id": card["id"],
                        "front": "减数分裂中染色体复制几次？",
                        "back": "减数分裂中染色体只复制一次。",
                    }
                ]
            }

    client = ReviewingClient()
    gate = FlashcardQualityGate(client, tmp_path / "review.sqlite3", "test-model")
    reviewed = gate.ensure(bundle)
    assert client.calls == 2
    assert reviewed.flashcards[0].front == "减数分裂中染色体复制几次？"
    assert gate.ensure(reviewed) == reviewed
    assert client.calls == 2
