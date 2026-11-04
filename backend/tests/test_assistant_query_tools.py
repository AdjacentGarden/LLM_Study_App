"""Unified assistant tools expose facts without a hidden model/router call."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaptive_learning.api.assistant_query_tools import QueryTools
from adaptive_learning.community import CommunityRepository
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft
from adaptive_learning.native import NativeRepository


@pytest.fixture
def tools(tmp_path):
    native = NativeRepository(tmp_path / "native.sqlite3")
    community = CommunityRepository(tmp_path / "community.sqlite3")
    structures, stored = {}, {}
    for owner, book, title, text in [
        ("alice", "chemistry", "化学必修", "化学反应过程中常伴随能量变化。放热反应释放能量。"),
        ("alice", "biology", "生物遗传", "DNA 是遗传物质。基因通常是有遗传效应的 DNA 片段。"),
        ("bob", "private", "他人的私密书", "绝不泄漏的其他账号教材原文。"),
    ]:
        wid = "workspace_" + book
        native.save_workspace(owner, wid, title + "课程", {})
        native.add_resource(owner, wid, book)
        community.bind_upload(owner, book)
        directory = tmp_path / book
        source = directory / "ocr" / "normalized" / "pages.jsonl"
        source.parent.mkdir(parents=True)
        source.write_text(json.dumps({"page_number": 1, "cleaned_text": text,
                                      "blocks": [{"block_id": "p1-b1", "text": text}]},
                                     ensure_ascii=False) + "\n", encoding="utf-8")
        stored[book] = SimpleNamespace(original_name=title + ".pdf", file_path=directory / "book.pdf",
                                        status="structured")
        structures[book] = BookStructure(
            title=title, summary="已生成的课程概要", source_page_count=1,
            chapters=[ChapterDraft(chapter_id="chapter_" + book, order=1, title="第一章",
                                   start_page=1, end_page=1, summary="章节概要",
                                   knowledge_points=["知识点"], source_block_ids=[])],
        )
    def forbidden(*args, **kwargs):
        raise AssertionError("A read tool must never invoke the old generated QA service")
    services = SimpleNamespace(
        native_repository=native, account_repository=SimpleNamespace(repo=community),
        job_repository=SimpleNamespace(get_book=stored.get, get_structure=structures.get),
        answer_book_question=forbidden,
    )
    return QueryTools(services, "alice", {"course_id": "workspace_chemistry", "book_id": "chemistry"}), services


def test_list_is_account_wide_not_fixed_to_current_course(tools):
    query, _ = tools
    result = query.execute("list_learning_resources", {})
    assert result["ok"]
    assert {c["name"] for c in result["courses"]} == {"化学必修课程", "生物遗传课程"}
    assert result["account_course_count"] == 2
    assert all(c["books"][0]["progress"]["revision"] == 0 for c in result["courses"])


def test_no_selection_still_lists_courses_and_device_pending_is_only_metadata(tools):
    _, services = tools
    query = QueryTools(services, "alice", {}, [
        {"id": "local-course:one", "name": "本机待上传", "book_ids": ["private"]},
        {"id": "workspace_private", "name": "伪造服务器课程", "book_ids": ["private"]},
    ])
    result = query.execute("list_learning_resources", {})
    assert result["total_courses"] == 3
    pending = result["courses"][-1]
    assert pending["origin"] == "device_unverified"
    assert pending["progress"] is None
    assert query.execute("search_learning_materials", {"query": "账号", "book_ids": ["private"]})["ok"] is False


def test_cross_course_search_returns_exact_source_and_stable_evidence(tools):
    query, _ = tools
    result = query.execute("search_learning_materials", {"query": "遗传物质"})
    assert result["ok"] and result["method"] == "raw_ocr_bm25"
    assert result["evidence"][0]["book_id"] == "biology"
    assert result["evidence"][0]["page_number"] == 1
    assert result["evidence"][0]["block_id"] == "p1-b1"
    assert "DNA 是遗传物质" in result["evidence"][0]["quote"]
    assert result["evidence"] == query.execute("search_learning_materials", {"query": "遗传物质"})["evidence"]


def test_context_and_explicit_ids_cannot_grant_other_account_access(tools):
    _, services = tools
    query = QueryTools(services, "alice", {"course_id": "workspace_private", "book_id": "private"})
    assert not query.execute("get_course_outline", {})["ok"]
    assert not query.execute("get_course_outline", {"book_id": "private"})["ok"]
    assert not query.execute("read_textbook_page", {"book_id": "private", "page_number": 1})["ok"]
    assert "book:private" not in query.access_scope()


def test_specific_other_book_outline_and_page_are_readable(tools):
    query, _ = tools
    outline = query.execute("get_course_outline", {"book_id": "biology"})
    assert outline["ok"] and outline["books"][0]["title"] == "生物遗传"
    assert outline["books"][0]["chapters"][0]["chapter_id"] == "chapter_biology"
    page = query.execute("read_textbook_page", {"book_id": "biology", "page_number": 1})
    assert page["ok"] and "DNA" in page["evidence"][0]["quote"]


def test_multi_book_course_outline_can_read_every_resource(tools):
    query, services = tools
    services.native_repository.add_resource("alice", "workspace_chemistry", "biology")
    first = query.execute("get_course_outline", {"course_id": "workspace_chemistry"})
    assert first["total_books"] == 2 and first["books_truncated"]
    second = query.execute("get_course_outline", {
        "course_id": "workspace_chemistry", "resource_offset": first["next_resource_offset"],
    })
    assert not second["books_truncated"]
    assert {first["books"][0]["book_id"], second["books"][0]["book_id"]} == {"chemistry", "biology"}
    # The active book can differ from the first material in its course.
    query.context = {"course_id": "workspace_chemistry", "book_id": second["books"][0]["book_id"]}
    current = query.execute("get_course_outline", {})
    assert current["books"][0]["book_id"] == query.context["book_id"]


def test_argument_validation_runs_before_reading(tools):
    query, _ = tools
    for name, arguments in [
        ("list_learning_resources", {"limit": 1000}),
        ("list_learning_resources", {"owner": "bob"}),
        ("read_textbook_page", {"book_id": "chemistry", "page_number": -1}),
        ("search_learning_materials", {"query": "化学", "book_ids": "chemistry"}),
    ]:
        assert not query.execute(name, arguments)["ok"]


def test_revoked_book_access_changes_replay_scope_and_prevents_reads(tools):
    query, services = tools
    before = set(query.access_scope())
    assert "book:chemistry" in before
    services.native_repository.delete_workspace("alice", "workspace_chemistry")
    assert "book:chemistry" not in query.access_scope()
    assert not query.execute("read_textbook_page", {"book_id": "chemistry", "page_number": 1})["ok"]


def test_builtin_ocr_uses_shipped_page_map_without_model_initialization(tools):
    query, services = tools
    from adaptive_learning import native_catalog
    services.native_repository.save_workspace("alice", "workspace_builtin", "生物内置课程", {})
    services.native_repository.add_resource("alice", "workspace_builtin", native_catalog.BOOK_ID)
    services.account_repository.repo.bind_upload("alice", native_catalog.BOOK_ID)
    result = query.execute("search_learning_materials", {
        "query": "孟德尔", "book_ids": [native_catalog.BOOK_ID],
    })
    assert result["ok"] and result["evidence"]
    assert all(e["book_id"] == native_catalog.BOOK_ID for e in result["evidence"])
    outline = query.execute("get_course_outline", {"book_id": native_catalog.BOOK_ID})
    book = outline["books"][0]
    assert book["outline_entry_count"] == len(native_catalog.structure().chapters)
    assert "chapter_count" not in book, "Nested section entries must not be reported as top-level chapters"
