"""Unified assistant tools expose facts without a hidden model/router call."""

import json
import math
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaptive_learning.api import assistant_query_tools
from adaptive_learning.api.assistant_query_tools import QueryTools
from adaptive_learning.community import CommunityRepository
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft
from adaptive_learning.native import NativeRepository
from adaptive_learning.rag.index import lexical_tokens


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


def test_public_catalog_is_not_part_of_the_replay_access_scope(tools):
    query, _ = tools
    assert not any(item.startswith("public_catalog") for item in query.access_scope())


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


def _write_pages(path: Path, pages: list[str]) -> None:
    path.write_text("".join(
        json.dumps({"page_number": number, "cleaned_text": text,
                    "blocks": [{"block_id": f"p{number}-b1", "text": text}]},
                   ensure_ascii=False) + "\n"
        for number, text in enumerate(pages, start=1)
    ), encoding="utf-8")


def _reference_bm25_scores(texts, query):
    """The pre-cache BM25 implementation, kept here as the parity oracle."""
    documents = [Counter(lexical_tokens(text)) for text in texts]
    document_frequency = Counter(
        token for document in documents for token in document
    )
    average_length = sum(sum(document.values()) for document in documents) / max(
        1, len(documents)
    )
    tokens = Counter(lexical_tokens(query))
    scores = []
    for document in documents:
        length = sum(document.values())
        score = 0.0
        for token, query_count in tokens.items():
            frequency = document.get(token, 0)
            if not frequency:
                continue
            documents_with_token = document_frequency[token]
            inverse = math.log(
                1
                + (len(documents) - documents_with_token + 0.5) / (documents_with_token + 0.5)
            )
            normalized = (
                frequency
                * 2.2
                / (frequency + 1.2 * (0.25 + 0.75 * length / max(1, average_length)))
            )
            score += inverse * normalized * min(2, query_count)
        scores.append(score)
    return scores


def _reference_order(texts, query):
    scores = _reference_bm25_scores(texts, query)
    ranking = sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)
    return [texts[index] for index in ranking if scores[index] > 0]


def test_search_ranking_matches_reference_bm25(tools, tmp_path):
    query, services = tools
    pages = [
        "细胞呼吸释放能量，细胞呼吸包括有氧呼吸。",
        "有氧呼吸需要氧气，细胞呼吸产生能量。",
        "光合作用储存能量，光合作用与细胞呼吸不同。",
        "细胞呼吸的场所是线粒体，线粒体参与有氧呼吸。",
        "能量守恒，细胞呼吸释放能量。",
    ]
    _write_pages(tmp_path / "chemistry" / "ocr" / "normalized" / "pages.jsonl", pages)
    text = "细胞呼吸能量"

    result = query.execute("search_learning_materials", {
        "query": text, "book_ids": ["chemistry"], "limit": 12,
    })
    assert [item["quote"] for item in result["evidence"]] == _reference_order(pages, text)

    filtered_structure = BookStructure(
        title="化学必修", summary="", source_page_count=len(pages),
        chapters=[ChapterDraft(chapter_id="chapter_filtered", order=1, title="第二节",
                               start_page=2, end_page=4, summary="",
                               knowledge_points=[], source_block_ids=[])],
    )
    services.job_repository.get_structure = lambda book_id: (
        filtered_structure if book_id == "chemistry" else None
    )
    filtered = query.execute("search_learning_materials", {
        "query": text, "book_ids": ["chemistry"],
        "chapter_id": "chapter_filtered", "limit": 12,
    })
    assert [item["quote"] for item in filtered["evidence"]] == _reference_order(pages[1:4], text)


def test_search_reuses_book_index_until_source_changes(tools, tmp_path, monkeypatch):
    query, _ = tools
    source = tmp_path / "chemistry" / "ocr" / "normalized" / "pages.jsonl"
    calls = {"count": 0}
    original = assistant_query_tools._chunks

    def counting_chunks(raw: str):
        calls["count"] += 1
        return original(raw)

    monkeypatch.setattr(assistant_query_tools, "_chunks", counting_chunks)
    arguments = {"query": "化学反应", "book_ids": ["chemistry"]}
    assert query.execute("search_learning_materials", arguments)["ok"]
    assert query.execute("search_learning_materials", arguments)["ok"]
    assert calls["count"] == 1

    _write_pages(source, ["细胞呼吸释放能量。", "细胞呼吸需要氧气，释放能量。"])
    changed = {"query": "细胞呼吸", "book_ids": ["chemistry"]}
    assert query.execute("search_learning_materials", changed)["ok"]
    assert calls["count"] == 2


def test_search_course_filters_intersect_books_without_widening_scope(tools):
    query, services = tools
    # The same book may belong to more than one course; return the filtered identity.
    services.native_repository.add_resource("alice", "workspace_chemistry", "biology")
    result = query.execute("search_learning_materials", {
        "query": "遗传物质", "course_ids": ["workspace_chemistry"], "book_ids": ["biology"],
    })
    assert result["ok"] and result["searched_book_ids"] == ["biology"]
    evidence = result["evidence"][0]
    assert evidence["course_id"] == "workspace_chemistry"
    assert evidence["course_ids"] == ["workspace_chemistry"]
    assert evidence["resource_id"]
    assert evidence["source_version"].startswith("ocr:")
    empty = query.execute("search_learning_materials", {
        "query": "化学反应", "course_ids": ["workspace_biology"], "book_ids": ["chemistry"],
    })
    assert empty["ok"] and empty["searched_book_ids"] == [] and empty["evidence"] == []
    for arguments in [
        {"query": "教材", "course_ids": ["workspace_private"]},
        {"query": "教材", "course_ids": ["workspace_chemistry"], "book_ids": ["private"]},
        {"query": "教材", "course_ids": []},
        {"query": "教材", "book_ids": []},
    ]:
        assert not query.execute("search_learning_materials", arguments)["ok"]


def test_search_images_are_bounded_page_descriptors_not_claims_of_loaded_images(tools):
    query, _ = tools
    normal = query.execute("search_learning_materials", {"query": "遗传物质"})
    assert normal["images"] == []
    result = query.execute("search_learning_materials", {"query": "遗传物质", "include_images": True})
    assert len(result["images"]) == 1
    image = result["images"][0]
    assert image["book_id"] == "biology" and image["page"] == 1
    assert image["course_id"] == "workspace_biology" and image["course_resource_id"]
    assert image["kind"] == "page" and image["status"] == "available_to_read"
    assert result["image_search_method"] == "text_hit_page_descriptors"


def test_stage_autosaved_course_is_not_counted_again_as_device_pending(tools):
    import hashlib
    _, services = tools
    local_id = "local-course:stage-save"
    wid = "workspace_" + hashlib.sha256(local_id.encode()).hexdigest()[:32]
    services.native_repository.save_workspace("alice", wid, "创建中课程", {})
    query = QueryTools(services, "alice", {}, [
        {"id": local_id, "name": "创建中课程", "book_ids": ["chemistry"]},
        {"id": "local-course:pending", "name": "另一门课程", "book_ids": ["chemistry"]},
        {"id": "local-course:pending", "name": "重复元数据", "book_ids": ["chemistry"]},
    ])
    result = query.execute("list_learning_resources", {})
    assert result["total_courses"] == 4
    assert result["account_course_count"] == 3 and result["device_pending_course_count"] == 1
    # An explicit mapping supports creationWorkspaceID overrides as well.
    query.local_courses = [{"id": "local-course:override", "workspace_id": wid,
                            "name": "创建中课程", "book_ids": []}]
    result = query.execute("list_learning_resources", {})
    assert result["total_courses"] == 3 and result["device_pending_course_count"] == 0


def test_search_evidence_source_version_changes_with_ocr_corpus(tools, tmp_path):
    query, _ = tools
    arguments = {"query": "遗传物质", "book_ids": ["biology"]}
    first = query.execute("search_learning_materials", arguments)["evidence"][0]
    source = tmp_path / "biology" / "ocr" / "normalized" / "pages.jsonl"
    _write_pages(source, ["DNA 是遗传物质。", "新增的遗传内容。"])
    changed = query.execute("search_learning_materials", arguments)["evidence"][0]
    assert first["source_version"] != changed["source_version"]


def test_course_listing_does_not_infer_confirmation_from_workspace_existence(tools):
    query, services = tools
    services.native_repository.save_creation_draft("alice", "workspace_chemistry", {
        "request_id": "draft_version", "name": "化学", "chapters": {},
    })
    listed = {c["course_id"]: c for c in query.execute("list_learning_resources", {})["courses"]}
    assert listed["workspace_chemistry"]["creation_state"] == "draft"
    assert listed["workspace_biology"]["creation_state"] == "unknown"
    services.native_repository.confirm_creation_draft("alice", "workspace_chemistry", "draft_version")
    listed = {c["course_id"]: c for c in query.execute("list_learning_resources", {})["courses"]}
    assert listed["workspace_chemistry"]["creation_state"] == "confirmed"
