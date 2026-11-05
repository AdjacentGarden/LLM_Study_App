from __future__ import annotations

import json
from typing import cast

import pytest

from adaptive_learning.ingestion.chaptering import ChapterReconstructor
from adaptive_learning.ingestion.coverage import coverage_context
from adaptive_learning.ingestion.models import (
    BookStructure,
    ChapterDraft,
    ExtractionMethod,
    PageExtraction,
    PageKind,
    TextBlock,
)
from adaptive_learning.ingestion.quality import evaluate_text_quality, quality_band
from adaptive_learning.ingestion.section_roles import partition_sections, section_context
from adaptive_learning.llm.client import LLMError, OpenAICompatibleClient


def page(number: int, text: str, *, skipped: bool = False) -> PageExtraction:
    quality = evaluate_text_quality(text)
    return PageExtraction(
        page_number=number,
        page_kind=PageKind.SCANNED,
        method=ExtractionMethod.PADDLE_GPU,
        raw_text=text,
        blocks=[
            TextBlock(
                block_id=f"b{number}",
                page_number=number,
                text=text,
                source_method=ExtractionMethod.PADDLE_GPU,
            )
        ],
        quality=quality,
        band=quality_band(quality.overall_score, accept=0.9, review=0.72),
        processing_status="skipped" if skipped else "",
    )


def chapter(key: str, order: int, title: str, start: int, end: int | None = None) -> ChapterDraft:
    return ChapterDraft(
        chapter_id=key,
        order=order,
        title=title,
        start_page=start,
        end_page=end or start,
        summary="已有且可核验的章节内容。",
        knowledge_points=[],
        source_block_ids=[f"b{start}"],
    )


def decisions(chapters: list[ChapterDraft], references: set[str]) -> list[dict]:
    return [
        {
            "chapter_id": item.chapter_id,
            "role": "reference" if item.chapter_id in references else "learning",
            "reason": "纯查询表，无独立教学过程。"
            if item.chapter_id in references
            else "包含独立的概念或方法讲解。",
            "source_pages": [item.start_page],
        }
        for item in chapters
    ]


def test_model_semantics_separate_reference_tables_and_keep_important_appendix() -> None:
    chapters = [
        chapter("intro", 1, "前言", 1),
        chapter("body", 2, "第1章 物质变化", 2),
        chapter("solubility", 3, "附录II 酸碱盐溶解性表", 3),
        chapter("elements", 4, "附录III 元素名称表", 4),
        chapter("masses", 5, "附录IV 原子质量表", 5),
        chapter("safety", 6, "附录V 实验安全与应急操作", 6),
        chapter("untitled_table", 7, "第五章 常用数据", 7),
    ]
    pages = [
        page(index, text)
        for index, text in enumerate(
            [
                "本书出版说明与致谢。",
                "物质变化需要根据实验事实判断。",
                "阳离子 阴离子 溶解性 Na+ Cl- 溶。",
                "氢 H 氦 He 锂 Li。",
                "H 1.008 He 4.003 Li 6.941。",
                "操作前必须佩戴护目镜；酸液溅入眼中时应立即持续冲洗并求助。",
                "标准常数 数值 换算查询表。",
            ],
            1,
        )
    ]
    original = [item.model_dump(mode="json") for item in pages]
    result = partition_sections(
        chapters,
        decisions(chapters, {"intro", "solubility", "elements", "masses", "untitled_table"}),
        section_context(chapters, pages),
        attempted=True,
    )
    assert [item.chapter_id for item in result.chapters] == ["body", "safety"]
    assert [item.order for item in result.chapters] == [1, 2]
    assert {item.chapter_id for item in result.reference_sections} == {
        "intro",
        "solubility",
        "elements",
        "masses",
        "untitled_table",
    }
    assert result.chapters[-1].title.startswith("附录")
    assert result.metadata["reference_page_ranges"] == [[1, 1], [3, 5], [7, 7]]
    assert all(item.section_role == "reference" for item in result.reference_sections)
    assert [item.model_dump(mode="json") for item in pages] == original
    assert coverage_context(pages)["skipped_page_ranges"] == []


def test_teaching_preface_can_be_a_learning_section() -> None:
    chapters = [
        chapter("preface", 1, "前言：先学会量纲分析", 1),
        chapter("body", 2, "第一章 测量", 2),
    ]
    pages = [
        page(1, "先根据单位推导物理量之间的关系，再逐步检验公式的量纲。"),
        page(2, "测量值与误差共同决定结果的表达。"),
    ]
    result = partition_sections(
        chapters, decisions(chapters, set()), section_context(chapters, pages), attempted=True
    )
    assert [item.chapter_id for item in result.chapters] == ["preface", "body"]
    assert result.reference_sections == []


@pytest.mark.parametrize(
    "case", ["missing", "duplicate", "unknown", "wrong_page", "skipped_page", "role", "reason"]
)
def test_invalid_optional_classification_keeps_a_nonempty_real_directory(case: str) -> None:
    chapters = [chapter("body", 1, "正文", 1), chapter("table", 2, "查询表", 2, 3)]
    pages = [page(1, "正文解释概念。"), page(2, "查询数据。"), page(3, "", skipped=True)]
    roles = decisions(chapters, {"table"})
    if case == "missing":
        roles.pop()
    elif case == "duplicate":
        roles[1] = roles[0]
    elif case == "unknown":
        roles[1]["chapter_id"] = "invented"
    elif case == "wrong_page":
        roles[1]["source_pages"] = [99]
    elif case == "skipped_page":
        roles[1]["source_pages"] = [3]
    elif case == "role":
        roles[1]["role"] = "discard"
    else:
        roles[1]["reason"] = ""
    result = partition_sections(chapters, roles, section_context(chapters, pages), attempted=True)
    assert [item.chapter_id for item in result.chapters] == ["body", "table"]
    assert result.reference_sections == []
    assert result.metadata["status"] == "invalid"
    assert coverage_context(pages)["skipped_page_ranges"] == [[3, 3]]
    assert result.metadata["reference_page_ranges"] == []


def test_all_reference_response_never_erases_the_directory() -> None:
    chapters = [chapter("only", 1, "附录 数据手册", 1)]
    pages = [page(1, "数值查询表。")]
    result = partition_sections(
        chapters, decisions(chapters, {"only"}), section_context(chapters, pages), attempted=True
    )
    assert [item.chapter_id for item in result.chapters] == ["only"]
    assert result.reference_sections == []
    assert result.metadata["status"] == "retained_all"
    assert result.chapters[0].section_classification["model_role"] == "reference"


def test_role_samples_are_bounded_and_include_middle_and_tail_pages() -> None:
    chapters = [
        chapter(f"c{index}", index + 1, "章节", index * 3 + 1, index * 3 + 3) for index in range(40)
    ]
    pages = [page(index, "原文" * 3000) for index in range(1, 121)]
    contexts = section_context(chapters, pages)
    assert sum(len(sample["text"]) for item in contexts for sample in item["samples"]) <= 12_000
    assert [sample["page_number"] for sample in contexts[0]["samples"]] == [1, 2, 3]
    assert contexts[-1]["samples"][-1]["page_number"] == 120


class SummaryClient:
    def __init__(
        self, reference_ids: set[str], *, invalid_roles: bool = False, provider_error: bool = False
    ):
        self.reference_ids = reference_ids
        self.invalid_roles = invalid_roles
        self.provider_error = provider_error
        self.chapter_calls = 0
        self.book_calls = 0

    def structured(self, *, system, user, **kwargs):
        payload = json.loads(user)
        if "章节摘要器" in system:
            self.chapter_calls += 1
            evidence = payload["evidence"][0]
            return {
                "summary": evidence["text"],
                "knowledge_points": [{"text": evidence["text"], "evidence_ids": [evidence["id"]]}],
                "evidence_ids": [evidence["id"]],
            }
        self.book_calls += 1
        assert "不能仅按标题判断" in system
        assert "sections" in payload and all(item["samples"] for item in payload["sections"])
        if self.provider_error:
            raise LLMError("provider outcome unknown")
        roles = [
            {
                "chapter_id": item["chapter_id"],
                "role": "reference" if item["chapter_id"] in self.reference_ids else "learning",
                "reason": "根据正文内容判定用途。",
                "source_pages": [item["samples"][0]["page_number"]],
            }
            for item in payload["sections"]
        ]
        if self.invalid_roles:
            roles.pop()
        return {
            "summary": "正文提供概念与方法，附录保留查询资料。",
            "chapter_ids": payload["required_chapter_ids"],
            "section_roles": roles,
        }


def test_reconstruction_reuses_one_overview_call_and_preserves_full_page_coverage(
    monkeypatch,
) -> None:
    chapters = [chapter("body", 1, "正文", 1), chapter("appendix", 2, "附录 查询表", 2)]
    pages = [page(1, "物质变化需要依据实验事实。"), page(2, "元素符号与数值用于查询。")]
    client = SummaryClient({"appendix"})
    reconstructor = ChapterReconstructor(cast(OpenAICompatibleClient, client))
    monkeypatch.setattr(reconstructor, "_outline", lambda _: (chapters, False))
    result = reconstructor.reconstruct_book(pages, "化学")
    assert [item.chapter_id for item in result.chapters] == ["body"]
    assert [item.chapter_id for item in result.reference_sections] == ["appendix"]
    assert client.chapter_calls == 2 and client.book_calls == 1
    assert result.source_page_count == 2
    assert result.source_coverage == coverage_context(pages)
    restored = BookStructure.model_validate_json(result.model_dump_json())
    assert restored == result


@pytest.mark.parametrize("provider_error", [False, True])
def test_optional_role_failure_does_not_fail_book_or_purchase_extra_calls(
    monkeypatch, provider_error: bool
) -> None:
    chapters = [chapter("body", 1, "正文", 1), chapter("appendix", 2, "附录", 2)]
    pages = [page(1, "物质变化需要依据实验事实。"), page(2, "元素符号用于查询。")]
    client = SummaryClient({"appendix"}, invalid_roles=True, provider_error=provider_error)
    reconstructor = ChapterReconstructor(cast(OpenAICompatibleClient, client), validation_retries=3)
    monkeypatch.setattr(reconstructor, "_outline", lambda _: (chapters, False))
    result = reconstructor.reconstruct_book(pages, "化学")
    assert [item.chapter_id for item in result.chapters] == ["body", "appendix"]
    assert result.reference_sections == []
    assert client.chapter_calls == 2 and client.book_calls == 1
    assert result.section_classification["attempted"] is True


def test_reconciliation_classifies_saved_directory_once_and_keeps_legacy_ids(monkeypatch) -> None:
    old = [chapter("legacy_body", 1, "正文", 1), chapter("legacy_table", 2, "附录 表格", 2)]
    detected = [item.model_copy(update={"chapter_id": "new_" + item.chapter_id}) for item in old]
    pages = [page(1, "正文解释现象。"), page(2, "查询表提供数值。")]
    client = SummaryClient({"legacy_table"})
    reconstructor = ChapterReconstructor(cast(OpenAICompatibleClient, client))
    monkeypatch.setattr(reconstructor, "_outline", lambda _: (detected, False))
    original = BookStructure(title="化学", summary="旧摘要", chapters=old, source_page_count=2)
    result = reconstructor.reconcile_book(pages, original)
    assert [item.chapter_id for item in result.chapters] == ["legacy_body"]
    assert [item.chapter_id for item in result.reference_sections] == ["legacy_table"]
    assert client.chapter_calls == 0 and client.book_calls == 1
    again = reconstructor.reconcile_book(pages, result)
    assert again is result
    assert client.book_calls == 1


@pytest.mark.parametrize("invalid_roles", [False, True])
def test_preparation_caches_success_and_fallback_until_source_changes(
    monkeypatch, tmp_path, invalid_roles: bool
) -> None:
    from contextlib import nullcontext
    from importlib import import_module
    from types import SimpleNamespace

    api = import_module("adaptive_learning.api.app")
    chapters = [chapter("body", 1, "正文", 1), chapter("appendix", 2, "附录 查询表", 2)]
    pages = [page(1, "正文解释物质变化。"), page(2, "查询表列出元素数值。")]
    current = SimpleNamespace(
        structure=BookStructure(
            title="化学", summary="原摘要", chapters=chapters, source_page_count=2
        )
    )
    client = SummaryClient({"appendix"}, invalid_roles=invalid_roles)
    reconstructor = ChapterReconstructor(cast(OpenAICompatibleClient, client))
    monkeypatch.setattr(reconstructor, "_outline", lambda _: (chapters, False))
    monkeypatch.setattr(reconstructor.detector, "detect", lambda _: (chapters, False))

    def save_structure(_, value):
        current.structure = value

    monkeypatch.setattr(
        api,
        "job_repository",
        SimpleNamespace(
            get_structure=lambda _: current.structure,
            get_job=lambda _: SimpleNamespace(output_dir=tmp_path),
            save_structure=save_structure,
        ),
    )
    monkeypatch.setattr(api, "load_normalized_pages", lambda _: pages)
    monkeypatch.setattr(api, "require_chapter_reconstructor", lambda: reconstructor)
    monkeypatch.setattr(
        api,
        "assessment_repository",
        SimpleNamespace(
            get_bank=lambda *args, **kwargs: None,
            _connect=lambda: nullcontext(SimpleNamespace(execute=lambda _: [])),
        ),
    )
    reconciliations = []
    monkeypatch.setattr(
        api,
        "native_repository",
        SimpleNamespace(reconcile_book_structure=lambda *args: reconciliations.append(args)),
    )
    api._prepare_structure("book")
    assert client.book_calls == 1 and len(reconciliations) == 1
    assert current.structure.section_classification["attempted"] is True
    assert current.structure.section_classification["input_fingerprint"]
    api._prepare_structure("book")
    assert client.book_calls == 1 and len(reconciliations) == 1
    pages[0] = pages[0].model_copy(update={"raw_text": "正文新增了实际变化条件。"})
    api._prepare_structure("book")
    assert client.book_calls == 2 and len(reconciliations) == 2
    api._prepare_structure("book")
    assert client.book_calls == 2 and len(reconciliations) == 2


def test_classification_fingerprint_ignores_its_own_grouping_and_order_changes() -> None:
    from adaptive_learning.ingestion.section_roles import classification_fingerprint

    chapters = [chapter("intro", 1, "前言", 1), chapter("body", 2, "正文", 2)]
    pages = [page(1, "版本说明。"), page(2, "概念教学。")]
    before = classification_fingerprint(chapters, pages)
    selected = partition_sections(
        chapters,
        decisions(chapters, {"intro"}),
        section_context(chapters, pages),
        attempted=True,
        input_fingerprint=before,
    )
    assert selected.chapters[0].order == 1
    assert (
        classification_fingerprint([*selected.chapters, *selected.reference_sections], pages)
        == before
    )
