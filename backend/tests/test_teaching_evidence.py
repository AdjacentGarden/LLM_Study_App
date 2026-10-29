import pytest

from adaptive_learning.assessment.item_generation import stable_knowledge_point_id
from adaptive_learning.ingestion.models import (
    ChapterDraft,
    ExtractionMethod,
    PageExtraction,
    PageKind,
    QualityBand,
    QualitySignals,
    SourceQuote,
)
from adaptive_learning.personalization.teaching_evidence import (
    MAX_SOURCE_CHARACTERS,
    bounded_teaching_sources,
    build_teaching_evidence,
    reading_sections,
)


def page(number: int, text: str) -> PageExtraction:
    return PageExtraction(
        page_number=number, page_kind=PageKind.SCANNED,
        method=ExtractionMethod.PADDLE_GPU, raw_text=text,
        quality=QualitySignals(character_score=1, language_score=1, layout_score=1,
                               coverage_score=1, agreement_score=1, overall_score=1),
        band=QualityBand.ACCEPTED,
    )


def chapter(text: str, *, start: int = 1, end: int = 1) -> ChapterDraft:
    label = "终止密码子不编码氨基酸"
    return ChapterDraft(chapter_id="c1", order=1, title="基因表达", start_page=start,
                        end_page=end, summary="密码子的作用。", knowledge_points=[label],
                        source_block_ids=[], knowledge_point_evidence={
                            label: [SourceQuote(page_number=start, quote=text)]})


def test_wrapped_context_preserves_verbatim_source_and_reflows_only_display() -> None:
    anchor = "终止密码子不编码\n氨基酸。"
    raw = "密码子由三个相邻碱基组成。\n" + anchor + "\n它表示翻译在此结束。"
    evidence = build_teaching_evidence(chapter(anchor), [page(1, raw)])
    point_id = stable_knowledge_point_id("c1", "终止密码子不编码氨基酸")
    sources = [evidence.sources[key] for key in evidence.point_sources[point_id]]
    assert any(anchor in source.quote and "翻译在此结束" in source.quote for source in sources)
    assert all(source.quote in raw for source in evidence.sources.values())
    sections = reading_sections(evidence)
    assert "终止密码子不编码氨基酸" in sections[0].content
    assert "不编码\n氨基酸" in sections[0].citations[0].quote


def test_cross_boundary_anchor_kept_whole_and_all_chapter_pages_available() -> None:
    anchor = "这段完整关系必须跨过默认切分位置但不能被切断"
    raw = "甲" * 490 + anchor + "乙" * 520
    value = chapter(anchor, end=2)
    second = "第二页包含补充条件和例外，不可以只读取第一张页面。"
    evidence = build_teaching_evidence(value, [page(1, raw), page(2, second)])
    ids = next(iter(evidence.point_sources.values()))
    assert any(anchor in evidence.sources[key].quote for key in ids)
    assert all(len(source.quote) <= 500 for source in evidence.sources.values())
    assert any(source.page_number == 2 and second in source.quote for source in evidence.sources.values())
    first_page_sources = [source.quote for source in evidence.sources.values() if source.page_number == 1]
    assert any(source.startswith(raw[:100]) for source in first_page_sources)
    assert any(source.endswith(raw[-100:]) for source in first_page_sources)


def test_fabricated_anchor_and_missing_pages_fail_closed() -> None:
    with pytest.raises(ValueError, match="absent from raw OCR"):
        build_teaching_evidence(chapter("没有出现的事实"), [page(1, "真实原文。")])
    with pytest.raises(ValueError, match="missing chapter OCR pages"):
        build_teaching_evidence(chapter("真实", end=2), [page(1, "真实原文。")])


def test_large_chapter_keeps_complete_registry_and_bounds_only_model_batch() -> None:
    raw = "原文。" + "".join(f"连续段落{i:05d}包含可核对的完整条件。" for i in range(3000))
    evidence = build_teaching_evidence(chapter("原文"), [page(1, raw)])
    assert sum(len(source.quote) for source in evidence.sources.values()) > MAX_SOURCE_CHARACTERS
    covered = bytearray(len(raw))
    for source in evidence.sources.values():
        start = raw.index(source.quote)
        covered[start:start + len(source.quote)] = b'\1' * len(source.quote)
    assert all(covered)  # The bounded wire must not silently truncate the registry.
    anchors = next(iter(evidence.point_sources.values()))
    batch = bounded_teaching_sources(evidence, {"source_ids": anchors})
    assert set(anchors) <= batch.keys() <= evidence.sources.keys()
    assert sum(len(source['quote']) for source in batch.values()) <= MAX_SOURCE_CHARACTERS
    assert all(source == evidence.sources[key].model_dump() for key, source in batch.items())


def test_legacy_sources_have_explicit_context_warning_and_no_model_quotes() -> None:
    evidence = build_teaching_evidence(chapter("可核验的旧引文。"), [])
    assert any("缺少整章 OCR 上下文" in warning for warning in evidence.warnings)
    assert next(iter(evidence.sources.values())).quote == "可核验的旧引文。"
    with pytest.raises(ValueError, match="unknown reading source"):
        reading_sections(evidence, preferred_ids=["model-invented-source"])


def test_fingerprint_changes_with_context_even_when_point_anchor_unchanged() -> None:
    value = chapter("明确关系。")
    first = build_teaching_evidence(value, [page(1, "明确关系。还有例外。")])
    second = build_teaching_evidence(value, [page(1, "明确关系。没有该例外。")])
    assert first.fingerprint != second.fingerprint
    assert first.fingerprint == build_teaching_evidence(value, [page(1, "明确关系。还有例外。")]).fingerprint


def test_diagram_and_bad_recognition_warn_without_dropping_context() -> None:
    raw = "可靠事实\n甲\n乙\n丙\n丁\n戊\n己\n庚\nA=B\n仍保留的文字。"
    result = page(1, raw)
    result.unresolved_fragments = ["残缺记号"]
    evidence = build_teaching_evidence(chapter("可靠事实"), [result])
    assert len(evidence.warnings) == 2
    assert any(raw in source.quote for source in evidence.sources.values())


def test_reading_retains_one_context_for_every_point_without_daily_limit() -> None:
    value = chapter("知识点零的完整依据。", end=16)
    value.knowledge_points = [f"知识点{i}" for i in range(16)]
    value.knowledge_point_evidence = {
        label: [SourceQuote(page_number=i + 1, quote=f"{label}的完整依据。")]
        for i, label in enumerate(value.knowledge_points)
    }
    pages = [page(i + 1, f"本页背景。{label}的完整依据。还有本页条件。")
             for i, label in enumerate(value.knowledge_points)]
    evidence = build_teaching_evidence(value, pages)
    sections = reading_sections(evidence)
    assert len(sections) == 16
    assert {section.citations[0].page_number for section in sections} == set(range(1, 17))
    with pytest.raises(ValueError, match="truncation"):
        reading_sections(evidence, max_sections=8)


def test_missing_point_anchor_is_reported_and_not_claimed_covered() -> None:
    value = chapter("可靠事实。")
    value.knowledge_point_evidence = {}
    evidence = build_teaching_evidence(value, [page(1, "可靠事实。")])
    assert not evidence.point_sources
    assert any("缺少可核验原文锚点" in warning for warning in evidence.warnings)


def test_scope_navigation_prioritizes_related_conditions_without_changing_sources() -> None:
    from adaptive_learning.personalization.models import SourceCitation
    from adaptive_learning.personalization.teaching_evidence import (
        TeachingEvidence,
        scope_context_source_ids,
    )

    sources = {
        "anchor": SourceCitation(page_number=1, quote="转换器将输入信号转换成输出信号。"),
        "condition": SourceCitation(page_number=2, quote="但是，转换器在过载条件下会终止输出信号。"),
        "duplicate": SourceCitation(page_number=2, quote="转换器只有完成检查，才会继续提供输出信号。"),
        "unrelated": SourceCitation(page_number=3, quote="但是，海水在特定条件下也会结冰。"),
    }
    evidence = TeachingEvidence(sources, {"point": ["anchor"]}, [], "unchanged")
    before = {key: source.model_dump() for key, source in sources.items()}
    selected = scope_context_source_ids(evidence, sources["anchor"].quote, ["anchor"])
    assert selected and selected[0] in {"condition", "duplicate"}
    assert len(selected) == 1  # Repeated windows from one page do not crowd out other pages.
    assert "anchor" not in selected and "unrelated" not in selected
    assert before == {key: source.model_dump() for key, source in sources.items()}
    assert evidence.fingerprint == "unchanged"


def test_scope_navigation_does_not_link_a_distant_unrelated_marker_to_a_definition() -> None:
    from adaptive_learning.personalization.models import SourceCitation
    from adaptive_learning.personalization.teaching_evidence import (
        TeachingEvidence,
        scope_context_source_ids,
    )

    label = "转换器将输入信号转换成输出信号。"
    evidence = TeachingEvidence({
        "definition": SourceCitation(page_number=1, quote=label),
        "false_neighbor": SourceCitation(page_number=2, quote=label + "甲" * 140 + "但是，部分海水会结冰。"),
        "boundary": SourceCitation(page_number=3, quote="转换器的输入信号过强时，只有降低输入信号才会恢复输出信号。"),
    }, {}, [], "fixed")
    assert scope_context_source_ids(evidence, label, ["definition"]) == ["boundary"]
    assert scope_context_source_ids(evidence, label, [], limit=0) == []
