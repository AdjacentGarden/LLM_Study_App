"""P2 audit tests.

Covers OPTIMIZATION_PLAN_P2.md checklist items A–D:
- A.7  BookCourseScreens.tsx deleted; per-screen files exist; barrel index.ts exists.
- A.8  src/context/AppContext.tsx exists with AppProvider, useAppContext, AppContextValue.
- A.9  App.tsx wraps renderScreen with AppProvider; screen invocations no longer spread sharedProps.
- A.10 Each screen file scans SharedProps usage and useAppContext import.
- C.12 cache.py has required symbols.
- C.13 read_chunks uses chunk cache.
- C.14 second query hits bm25 cache (BM25Index.__init__ called once).
- C.15 second query hits artifact embedding cache (embed_documents called once).
- C.16 write_chunks invalidates cache so new data is read.
- C.17 clear_rag_cache forces rebuild.
- C.18 audit dict carries cache_hit.
- C.19 disabling cache results in len(BM25Index.__init__ calls) == num queries.
- D.20 pytest passes (not part of this file; run by harness).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FRONT_SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"


# ---------- A. Frontend split ----------

def test_p2_a1_bookcourse_screens_file_deleted():
    assert not (FRONT_SRC / "screens" / "BookCourseScreens.tsx").exists()


def test_p2_a2_barrel_index_exists():
    assert (FRONT_SRC / "screens" / "index.ts").exists()
    text = (FRONT_SRC / "screens" / "index.ts").read_text(encoding="utf-8")
    for screen in [
        "HomeScreen","UploadScreen","ParseReadyScreen","ProcessingScreen",
        "ChapterConfirmScreen","CourseReadyScreen","LibraryScreen","CommunityScreen",
        "CommunityBookScreen","CommunityImportScreen","BookCourseScreen","LessonScreen",
        "FlashcardScreen","SourceReaderScreen","AssignmentScreen","DiagnosisScreen",
        "MistakeBookScreen","NotesScreen","ExportPreviewScreen","LessonReportScreen",
        "StudyPlanScreen","ProfileScreen",
    ]:
        assert f"export {{ {screen} }}" in text, f"{screen} missing from barrel"


def test_p2_a3_appcontext_exists():
    p = FRONT_SRC / "context" / "AppContext.tsx"
    assert p.exists(), f"{p} must exist"
    text = p.read_text(encoding="utf-8")
    assert "AppProvider" in text
    assert "useAppContext" in text
    assert "AppContextValue" in text


def test_p2_a4_app_provider_wraps_render():
    p = FRONT_SRC / "App.tsx"
    text = p.read_text(encoding="utf-8")
    assert "<AppProvider value={sharedProps}>" in text or "AppProvider value={sharedProps}" in text
    assert "from \"./screens\"" in text or "import(\"./screens/" in text
    assert "<ScreenTransition" in text
    # screen invocations no longer spread sharedProps
    assert "{...sharedProps}" not in text


def test_p2_a5_no_sharedprops_in_screens():
    screens_dir = FRONT_SRC / "screens"
    tsx_files = list(screens_dir.glob("*.tsx")) + list((screens_dir / "sheets").glob("*.tsx"))
    assert tsx_files, "no screen files generated"
    for f in tsx_files:
        text = f.read_text(encoding="utf-8")
        assert "SharedProps" not in text, f"{f.name} still references SharedProps"


def test_p2_a6_shared_helpers_present():
    p = FRONT_SRC / "screens" / "shared.tsx"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    for sym in [
        "QuickAction","BookMini","CourseCover","SettingsRow","ChapterEvidenceSummary",
        "backendAssetUrl","sourcePageImageUrl","sourcePageLabel","chapterConcepts",
        "liveBookTitle","formatFileSize","getFileKind","apiChapterToChapter",
        "averageConfidence","acceptedCourseFileTypes",
    ]:
        assert f"export function {sym}" in text or f"export const {sym}" in text, f"{sym} missing from shared.tsx"


# ---------- B. Per-screen uses AppContext ----------

@pytest.mark.parametrize("screen", [
    "HomeScreen","UploadScreen","ParseReadyScreen","ProcessingScreen",
    "ChapterConfirmScreen","CourseReadyScreen","LibraryScreen","CommunityScreen",
    "CommunityBookScreen","CommunityImportScreen","BookCourseScreen","LessonScreen",
    "FlashcardScreen","SourceReaderScreen","AssignmentScreen","DiagnosisScreen",
    "MistakeBookScreen","NotesScreen","ExportPreviewScreen","LessonReportScreen",
    "StudyPlanScreen","ProfileScreen",
])
def test_p2_b_screens_use_context(screen):
    p = FRONT_SRC / "screens" / f"{screen}.tsx"
    text = p.read_text(encoding="utf-8")
    if screen == "BookCourseScreen":
        assert "export { StudyScreen as BookCourseScreen }" in text
        study_text = (FRONT_SRC / "screens" / "StudyScreen.tsx").read_text(encoding="utf-8")
        assert "useAppContext" in study_text
    else:
        assert f"export function {screen}() {{" in text
        assert "useAppContext" in text


# ---------- C. Backend cache ----------

def test_p2_c12_cache_symbols_present():
    from app.rag import cache as cache_mod

    for sym in [
        "LRUCache",
        "get_chunks",
        "get_bm25_index",
        "get_artifact_embeddings",
        "clear_rag_cache",
        "invalidate_book",
        "invalidate_chunks",
        "invalidate_bm25",
        "invalidate_artifact_embeddings",
        "is_cache_enabled",
    ]:
        assert hasattr(cache_mod, sym), f"cache.{sym} missing"


def test_p2_c12_lru_basic():
    from app.rag.cache import LRUCache

    c: LRUCache[str, int] = LRUCache(2)
    c.set("a", 1)
    c.set("b", 2)
    # touch 'a' so it becomes most-recently used
    assert c.get("a") == 1
    c.set("c", 3)
    # 'b' should be evicted because 'a' was recently used
    assert c.get("b") is None
    assert c.get("a") == 1
    assert c.get("c") == 3
    c.clear()
    assert len(c) == 0


def test_p2_c13_read_chunks_caches(tmp_path, monkeypatch):
    from app.rag import cache as cache_mod

    cache_mod.clear_rag_cache()
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    from app.core.config import get_settings
    get_settings.cache_clear()
    from app.services import storage as _storage
    # storage may cache settings; if it exposes any helpers, reset them
    _storage.get_settings.cache_clear() if hasattr(_storage, "get_settings") else None

    from app.schemas.books import Chunk
    from app.services.artifact_store import read_chunks, write_chunks

    write_chunks(
        "cache_book",
        [Chunk(chunk_id="c1", book_id="cache_book", chapter_id="ch1", page_start=1, page_end=1, content_type="text", text="hello world", asset_ids=[])],
    )
    # First read loads from disk and caches
    first = read_chunks("cache_book")
    assert first
    # Change the authoritative generation in place without changing the
    # manifest token. The compatibility mirror must remain present: Stage 3
    # deliberately fails closed when either half of the committed pair is
    # missing, regardless of an in-process cache entry.
    artifacts = tmp_path / "books" / "cache_book" / "artifacts"
    if not artifacts.exists():
        artifacts = tmp_path / "cache_book"
    manifest = json.loads((artifacts / ".rag_bundles" / "manifest.json").read_text(encoding="utf-8"))
    chunks_path = artifacts / ".rag_bundles" / manifest["generation"] / "chunks.jsonl"
    chunks_path.write_text("", encoding="utf-8")
    second = read_chunks("cache_book")
    assert second == first, "cache not used"
    # Now invalidate cache; the same generation is reloaded from disk.
    cache_mod.clear_rag_cache()
    third = read_chunks("cache_book")
    assert third == []


def test_p2_c14_bm25_cache_hit(tmp_path, monkeypatch):
    from app.rag import cache as cache_mod

    cache_mod.clear_rag_cache()
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))

    from app.schemas.books import Chunk
    from app.services.artifact_store import write_chunks
    from app.rag.bm25 import BM25Index

    write_chunks(
        "bm25_book",
        [
            Chunk(chunk_id="c1", book_id="bm25_book", chapter_id="ch1", page_start=1, page_end=1, content_type="text", text="cohesion adhesion water", asset_ids=[], key_concepts=["cohesion"]),
            Chunk(chunk_id="c2", book_id="bm25_book", chapter_id="ch1", page_start=2, page_end=2, content_type="text", text="capillary action", asset_ids=[], key_concepts=["capillary"]),
        ],
    )
    # Force cache reset (we just wrote file, invalidate_chunks called -> of no entry yet anyway)
    init_calls = []
    orig_init = BM25Index.__init__

    def counting_init(self, chunks, *args, **kwargs):
        init_calls.append(len(chunks))
        orig_init(self, chunks, *args, **kwargs)

    monkeypatch.setattr(BM25Index, "__init__", counting_init)

    from app.rag.retrieval import retrieve_chunks
    retrieve_chunks("bm25_book", "cohesion")
    retrieve_chunks("bm25_book", "adhesion")
    assert len(init_calls) == 1, f"BM25Index should be built once, got {len(init_calls)}"


def test_p2_c15_artifact_embedding_cache_hit(tmp_path, monkeypatch):
    from app.rag import cache as cache_mod

    cache_mod.clear_rag_cache()
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_PROVIDER", "hashing")

    from app.schemas.books import Chunk
    from app.services.artifact_store import write_chunks
    from app.rag.embedding import HashingEmbeddingService

    write_chunks(
        "art_book",
        [
            Chunk(chunk_id="c1", book_id="art_book", chapter_id="ch1", page_start=1, page_end=1, content_type="text", text="transport across membrane", asset_ids=[], key_concepts=[]),
            Chunk(chunk_id="c2", book_id="art_book", chapter_id="ch1", page_start=2, page_end=2, content_type="text", text="active transport", asset_ids=[], key_concepts=[]),
        ],
    )

    calls = {"n": 0}
    orig = HashingEmbeddingService.embed_documents

    def counting(self, docs):
        calls["n"] += 1
        return orig(self, docs)

    monkeypatch.setattr(HashingEmbeddingService, "embed_documents", counting)

    # Force fallback to ArtifactVectorIndex (use a non-running provider)
    monkeypatch.setenv("BOOKCOURSE_RAG_INDEX_PROVIDER", "artifact")
    from app.core.config import get_settings
    get_settings.cache_clear()
    from app.rag.retrieval import retrieve_chunks
    retrieve_chunks("art_book", "transport")
    retrieve_chunks("art_book", "membrane")
    assert calls["n"] == 1, f"embed_documents should be called once, got {calls['n']}"
    get_settings.cache_clear()


def test_p2_c16_write_chunks_invalidates(tmp_path, monkeypatch):
    from app.rag import cache as cache_mod

    cache_mod.clear_rag_cache()
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))

    from app.schemas.books import Chunk
    from app.services.artifact_store import read_chunks, write_chunks

    write_chunks(
        "inv_book",
        [Chunk(chunk_id="c1", book_id="inv_book", chapter_id="ch1", page_start=1, page_end=1, content_type="text", text="alpha", asset_ids=[])],
    )
    first = read_chunks("inv_book")
    assert len(first) == 1
    write_chunks(
        "inv_book",
        [
            Chunk(chunk_id="c1", book_id="inv_book", chapter_id="ch1", page_start=1, page_end=1, content_type="text", text="alpha", asset_ids=[]),
            Chunk(chunk_id="c2", book_id="inv_book", chapter_id="ch1", page_start=2, page_end=2, content_type="text", text="beta", asset_ids=[]),
        ],
    )
    second = read_chunks("inv_book")
    assert len(second) == 2, "cache not invalidated after write_chunks"


def test_p2_c17_clear_rag_cache_forces_rebuild(tmp_path, monkeypatch):
    from app.rag import cache as cache_mod

    cache_mod.clear_rag_cache()
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))

    from app.schemas.books import Chunk
    from app.services.artifact_store import write_chunks
    from app.rag.bm25 import BM25Index

    write_chunks(
        "clear_book",
        [Chunk(chunk_id="c1", book_id="clear_book", chapter_id="ch1", page_start=1, page_end=1, content_type="text", text="foo", asset_ids=[])],
    )
    init_calls = []
    orig_init = BM25Index.__init__

    def counting_init(self, chunks, *args, **kwargs):
        init_calls.append(1)
        orig_init(self, chunks, *args, **kwargs)

    monkeypatch.setattr(BM25Index, "__init__", counting_init)

    from app.rag.retrieval import retrieve_chunks
    retrieve_chunks("clear_book", "foo")
    retrieve_chunks("clear_book", "foo")
    cache_mod.clear_rag_cache()
    retrieve_chunks("clear_book", "foo")
    assert len(init_calls) == 2, f"expected 2 builds (initial + after clear), got {len(init_calls)}"


def test_p2_c18_audit_has_cache_hit(tmp_path, monkeypatch):
    from app.rag import cache as cache_mod

    cache_mod.clear_rag_cache()
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    from app.core.config import get_settings
    get_settings.cache_clear()

    from app.schemas.books import Asset, Chapter, Chunk, RagQuery
    from app.services.artifact_store import write_assets, write_chapters, write_chunks

    from app.rag import audit
    from app.rag import service as rag_service

    audits: list[dict] = []

    def fake_write(book_id, payload):
        audits.append(payload)
        return None

    # Patch both the audit module reference AND the bound name in service.py so
    # any caching of imports works correctly.
    monkeypatch.setattr(audit, "write_rag_audit", fake_write)
    monkeypatch.setattr(rag_service, "write_rag_audit", fake_write)

    write_chapters("audit_book", [Chapter(chapter_id="ch1", level=2, source_title="s", ai_title="t", page_start=1, page_end=2, confidence=90, status="confirmed", source="test")])
    write_chunks("audit_book", [Chunk(chunk_id="c1", book_id="audit_book", chapter_id="ch1", page_start=1, page_end=1, content_type="text", text="something specific", asset_ids=[])])
    write_assets("audit_book", [])

    answer_query = rag_service.answer_query
    answer_query(RagQuery(book_id="audit_book", question="something", chapter_id=None))
    answer_query(RagQuery(book_id="audit_book", question="specific", chapter_id=None))
    assert len(audits) == 2, f"expected 2 audit records, got {len(audits)}: {audits}"
    assert "cache_hit" in audits[0]
    assert audits[0]["cache_hit"] == "none", f"first audit cache_hit should be none: {audits[0]}"
    assert audits[1]["cache_hit"] in {"bm25_hit", "bm25_vector_hit", "all_hit", "vector_hit"}, f"second audit cache_hit: {audits[1]}"


def test_p2_c19_disabled_cache_no_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_RAG_CACHE_ENABLED", "false")
    monkeypatch.setenv("BOOKCOURSE_RAG_INDEX_PROVIDER", "artifact")
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_PROVIDER", "hashing")
    from app.core.config import get_settings
    get_settings.cache_clear()
    from app.rag import cache as cache_mod

    cache_mod.clear_rag_cache()
    from app.schemas.books import Chunk
    from app.services.artifact_store import write_chunks
    from app.rag.bm25 import BM25Index

    write_chunks(
        "no_cache_book",
        [Chunk(chunk_id="c1", book_id="no_cache_book", chapter_id="ch1", page_start=1, page_end=1, content_type="text", text="query text for embeddings", asset_ids=[])],
    )
    init_calls = []
    orig_init = BM25Index.__init__

    def counting(self, chunks, *args, **kwargs):
        init_calls.append(1)
        orig_init(self, chunks, *args, **kwargs)

    monkeypatch.setattr(BM25Index, "__init__", counting)

    from app.rag.retrieval import retrieve_chunks
    retrieve_chunks("no_cache_book", "query")
    retrieve_chunks("no_cache_book", "text")
    assert len(init_calls) == 2, f"with cache disabled expecting 2 builds, got {len(init_calls)}"
    get_settings.cache_clear()


# ---------- D. Existing test suite still green (run by harness) ----------
