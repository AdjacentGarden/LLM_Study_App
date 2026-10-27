"""Local-only integration harness. Only OCR/model adapters are replaced with fixtures."""

from __future__ import annotations

import json
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pymupdf as fitz
from fastapi import FastAPI

from adaptive_learning.accounts import Accounts
from adaptive_learning.api.account_guard import learning_guard
from adaptive_learning.api.community_routes import community_router
from adaptive_learning.api.native_routes import native_router
from adaptive_learning.api.store import MemoryStore
from adaptive_learning.assessment.engine import AdaptiveAssessmentEngine
from adaptive_learning.assessment.interview import InterviewOrchestrator
from adaptive_learning.assessment.item_generation import stable_knowledge_point_id
from adaptive_learning.assessment.models import DiagnosticItem, ScoredEvidence
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.community import CommunityRepository
from adaptive_learning.ingestion.jobs import OCRRunResult, SQLiteOCRJobRepository
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft, SourceQuote
from adaptive_learning.native import NativeRepository
from adaptive_learning.rag.grounded_qa import GroundedAnswerGenerator

api = import_module("adaptive_learning.api.app")
native_api = import_module("adaptive_learning.api.native_routes")
qa = import_module("adaptive_learning.api.qa_dependency")

QUOTES = [
    "染色体只复制一次，而细胞分裂两次。",
    "减数分裂形成的生殖细胞中染色体数目减半。",
    "受精作用使染色体数目恢复。",
]


def textbook_pdf() -> bytes:
    document = fitz.open()
    for text in QUOTES:
        page = document.new_page()
        page.insert_text((50, 80), text, fontname="china-s", fontsize=14)
    return document.tobytes()


def structure() -> BookStructure:
    points = {
        quote: [SourceQuote(page_number=i + 1, quote=quote)] for i, quote in enumerate(QUOTES)
    }
    chapter = ChapterDraft(
        chapter_id="chapter_meiosis",
        order=1,
        title="第一章 减数分裂",
        start_page=1,
        end_page=3,
        summary="理解减数分裂与受精作用。",
        knowledge_points=QUOTES,
        source_block_ids=[f"block_{i + 1}" for i in range(3)],
        evidence=[q[0] for q in points.values()],
        knowledge_point_evidence=points,
    )
    return BookStructure(
        title="测试生物教材", summary=chapter.summary, chapters=[chapter], source_page_count=3
    )


class FixtureEncoder:
    dimensions = 4
    model_path = "fixture-only"

    def encode_query(self, text):
        return np.ones(4, dtype=np.float32) / 2

    def encode_documents(self, texts):
        return np.ones((len(texts), 4), dtype=np.float32) / 2


class FixtureReranker:
    def score(self, query, texts):
        return [3.5] * len(texts)


class FixtureAnswerModel:
    def structured(self, **kwargs):
        evidence = json.loads(kwargs["user"])["evidence"][0]
        return {
            "status": "supported",
            "confidence": 0.95,
            "claims": [
                {
                    "text": evidence["text"][:300],
                    "citations": [
                        {"source_id": evidence["source_id"], "quote": evidence["text"][:300]}
                    ],
                }
            ],
        }


class FixtureBank:
    def generate(self, value, *, chapter_cache=None, cache_scope=""):
        return [
            DiagnosticItem(
                item_id="diagnostic_" + stable_knowledge_point_id(ch.chapter_id, point),
                chapter_id=ch.chapter_id,
                knowledge_point_ids=[stable_knowledge_point_id(ch.chapter_id, point)],
                knowledge_point_labels=[point],
                prompt="下列哪项准确描述教材中的过程？",
                response_type="single_choice",
                options=[
                    point,
                    "染色体连续复制多次，细胞只分裂一次。",
                    "染色体不发生复制，细胞也不会分裂。",
                    "分裂后全部细胞的染色体数目都增加。",
                ],
                correct_option_ids=["0"],
                expected_answer=point,
                difficulty=-1,
                source_pages=[1],
            )
            for ch in value.chapters
            for point in ch.knowledge_points
        ]


class FixtureTeachingGenerator:
    """Deterministic model boundary for native API and learning-design tests.

    Real generation, independent fact review, and rejection paths are covered by
    the teaching suites. Native integration still exercises the publication gate,
    evidence fingerprints, persisted revisions, and per-workspace design.
    """

    def generate(self, bundle, chapter, evidence):
        from adaptive_learning.personalization.teaching_content import TEACHING_CONTENT_VERSION
        from adaptive_learning.personalization.teaching_scope import TEACHING_SCOPE_VERSION

        output = bundle.model_copy(deep=True)
        output.teaching_quality_version = TEACHING_CONTENT_VERSION
        output.teaching_fact_review = {"accepted": True, "issues": []}
        output.teaching_review = {"accepted": True, "issues": []}
        output.teaching_scope_version = TEACHING_SCOPE_VERSION
        output.teaching_scope_plan = {point.point_id: [] for point in output.knowledge_points}
        return output


def configure(root: Path, patch) -> FastAPI:
    # Native workflow tests replace paid adapters and keep cover inference offline.
    patch.setenv("PDF_COVER_ENABLED", "false")
    patch.setenv("AUTH_DEMO_MODE", "1")
    patch.setenv("NATIVE_LOCAL_LOGIN_ENABLED", "0")
    patch.setenv("APP_ENV", "development")
    for key in ("SMTP_HOST", "SMTP_FROM", "SMTP_USER", "SMTP_PASSWORD", "RAG_BOOK_INDEX_MANIFEST"):
        patch.delenv(key, raising=False)
    settings = replace(
        api.settings,
        data_dir=root,
        published_book_ids=(),
        ocr_worker_enabled=False,
        rag_book_id="unused-fixture-primary",
        rag_index_dir=None,
    )
    jobs = SQLiteOCRJobRepository(root / "state" / "ocr.sqlite3")
    assessments = SQLiteAssessmentRepository(root / "state" / "assessments.sqlite3")
    accounts = Accounts(CommunityRepository(root / "state" / "community.sqlite3"))
    for name, value in {
        "settings": settings,
        "job_repository": jobs,
        "assessment_repository": assessments,
        "account_repository": accounts,
        "native_repository": NativeRepository(root / "state" / "native.sqlite3"),
        "store": MemoryStore(),
        "orchestrator": InterviewOrchestrator(AdaptiveAssessmentEngine(min_items=1, max_items=1)),
        "item_generator": FixtureBank(),
        "flashcard_quality": SimpleNamespace(ensure=lambda b: b),
        "teaching_generator": FixtureTeachingGenerator(),
        "open_answer_scorer": SimpleNamespace(
            score=lambda item, answer: ScoredEvidence(score=0.8, scoring_confidence=0.9)
        ),
    }.items():
        patch.setattr(api, name, value)
    components = FixtureEncoder(), FixtureReranker(), GroundedAnswerGenerator(FixtureAnswerModel())
    patch.setattr(qa, "get_settings", lambda: settings)
    patch.setattr(qa, "upload_components", lambda: components)
    patch.setattr(qa, "_book_services", qa.OrderedDict())
    patch.setattr(
        native_api,
        "require_chapter_reconstructor",
        lambda: SimpleNamespace(reconstruct_book=lambda pages, title: structure()),
    )
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"ok": True, "ocr_worker_enabled": True, "ocr_worker_alive": True}
    app.middleware("http")(learning_guard(accounts))
    app.include_router(community_router(root, lambda: [], lambda: jobs, lambda: assessments,
        diagnostic_fingerprint=api.diagnostic_fingerprint))
    app.include_router(native_router(api))
    app.post("/api/books")(api.upload_book)

    @app.post("/api/books/{book_id}/process")
    def process(book_id: str):
        result = api.process_book(book_id)
        job = jobs.claim_next(owner="fixture-ocr", lease_seconds=60)
        if job:
            normalized = job.output_dir / "normalized"
            normalized.mkdir(parents=True, exist_ok=True)
            quality = {
                k: 1
                for k in (
                    "character_score",
                    "language_score",
                    "layout_score",
                    "coverage_score",
                    "agreement_score",
                    "overall_score",
                )
            }
            pages = [
                {
                    "page_number": i + 1,
                    "page_kind": "digital",
                    "method": "native",
                    "band": "accepted",
                    "raw_text": quote,
                    "cleaned_text": quote,
                    "quality": quality,
                    "blocks": [
                        {
                            "block_id": f"block_{i + 1}",
                            "page_number": i + 1,
                            "text": quote,
                            "source_method": "native",
                        }
                    ],
                }
                for i, quote in enumerate(QUOTES)
            ]
            (normalized / "pages.jsonl").write_text(
                "\n".join(json.dumps(p, ensure_ascii=False) for p in pages)
            )
            jobs.succeed(job_id=job.job_id, owner="fixture-ocr", result=OCRRunResult(1, False, 3))
        return api.book_status(book_id) if job else result

    for path, method, endpoint in [
        ("/api/books/{book_id}/status", "GET", api.book_status),
        ("/api/books/{book_id}/process/retry", "POST", api.retry_process_book),
        ("/api/books/{book_id}/qa", "POST", api.answer_book_question),
        ("/api/interviews/{session_id}", "GET", api.get_interview),
        ("/api/interviews/{session_id}/profile", "POST", api.answer_profile),
        ("/api/interviews/{session_id}/next", "POST", api.next_diagnostic),
        ("/api/interviews/{session_id}/respond", "POST", api.respond),
        ("/api/interviews/{session_id}/confirm", "POST", api.confirm),
        ("/api/interviews/{session_id}/courses/{chapter_id}", "POST", api.compile_course),
        ("/api/interviews/{session_id}/courses/{chapter_id}", "GET", api.get_course),
        (
            "/api/interviews/{session_id}/courses/{course_id}/practice/{item_id}",
            "POST",
            api.answer_course_practice,
        ),
        ("/api/interviews/{session_id}/courses/{course_id}/flashcards/{card_id}", "POST", api.review_flashcard),
        ("/api/interviews/{session_id}/learning-records", "GET", api.get_learning_records),
    ]:
        app.add_api_route(path, endpoint, methods=[method])
    return app
