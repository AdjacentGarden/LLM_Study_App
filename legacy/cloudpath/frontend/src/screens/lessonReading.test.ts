import { describe, expect, it } from "vitest";
import type { ApiAsset, LessonBlock } from "../types/api";
import {
  buildLessonConceptDetail,
  buildLessonReadingSections,
  isWholePageScan
} from "./lessonReading";

const blocks: LessonBlock[] = [
  {
    block_id: "structure",
    block_type: "explanation",
    title: "先抓住总结构",
    content: "减数分裂中染色体只复制一次，细胞连续分裂两次。",
    citations: [{
      chunk_id: "chunk-1",
      page_start: 11,
      page_end: 11,
      printed_page_start: 16,
      printed_page_end: 16
    }],
    source_chunk_ids: ["chunk-1"],
    asset_ids: ["extracted-1", "ai-1"],
    ai_generated: false
  },
  {
    block_id: "homologous",
    block_type: "diagram",
    title: "同源染色体先分离",
    content: "减数第一次分裂中分离的是同源染色体。",
    citations: [{ chunk_id: "chunk-2", page_start: 13, page_end: 13 }],
    source_chunk_ids: ["chunk-2"],
    asset_ids: ["extracted-1", "ai-1"],
    ai_generated: false
  }
];

const assets: ApiAsset[] = [
  {
    asset_id: "ai-1",
    book_id: "book",
    chapter_id: "chapter",
    source_type: "ai_generated",
    page: null,
    type: "diagram",
    caption: "AI 示意图",
    image_url: "/ai.webp",
    thumbnail_url: "/ai-thumb.webp",
    source_page_image_url: null,
    source_chunk_ids: ["chunk-2"],
    concepts: ["同源染色体"],
    generation_provider: "test",
    review_status: "approved"
  },
  {
    asset_id: "extracted-1",
    book_id: "book",
    chapter_id: "chapter",
    source_type: "extracted",
    page: 11,
    type: "figure",
    caption: "教材原图",
    bbox: [0, 0, 100, 100],
    image_url: "/source.webp",
    thumbnail_url: "/source-thumb.webp",
    source_page_image_url: "/page.webp",
    source_chunk_ids: ["chunk-1"],
    concepts: ["减数分裂"]
  }
];

describe("lesson reading content", () => {
  it("prefers extracted textbook art and never repeats an image across reading sections", () => {
    const sections = buildLessonReadingSections(blocks, assets);

    expect(sections[0]?.asset?.asset_id).toBe("extracted-1");
    expect(sections[1]?.asset?.asset_id).toBe("ai-1");
    expect(sections[0]?.assets.map((asset) => asset.asset_id)).toEqual(["extracted-1"]);
    expect(sections[1]?.assets.map((asset) => asset.asset_id)).toEqual(["ai-1"]);
    expect(new Set(sections.map((section) => section.asset?.asset_id).filter(Boolean)).size).toBe(2);
  });

  it("rejects a normalized full-page scan and falls back to the linked AI illustration", () => {
    const fullPageScan: ApiAsset = {
      ...assets[1],
      asset_id: "full-page-scan",
      bbox: [0, 0, 1, 1],
      image_url: "/whole-page.webp",
      source_page_image_url: "/whole-page.webp"
    } as ApiAsset;
    const singleBlock: LessonBlock = {
      ...blocks[0],
      asset_ids: ["full-page-scan", "ai-1"]
    };

    expect(isWholePageScan(fullPageScan)).toBe(true);
    expect(buildLessonReadingSections([singleBlock], [fullPageScan, assets[0]])[0]?.asset?.asset_id).toBe("ai-1");
  });

  it("does not let an earlier keyword match steal an illustration linked to a later source chunk", () => {
    const earlierBlock: LessonBlock = {
      ...blocks[0],
      content: "先概览同源染色体与减数分裂的关系。",
      asset_ids: []
    };
    const laterBlock: LessonBlock = {
      ...blocks[1],
      asset_ids: []
    };
    const sections = buildLessonReadingSections([earlierBlock, laterBlock], [assets[0]]);

    expect(sections[0]?.asset).toBeNull();
    expect(sections[1]?.asset?.asset_id).toBe("ai-1");
  });

  it("builds a concept explanation from the matching lesson block and preserves its citation", () => {
    const sections = buildLessonReadingSections(blocks, assets);
    const detail = buildLessonConceptDetail("同源染色体", "课程摘要", sections);

    expect(detail.explanation).toContain("同源染色体");
    expect(detail.citation?.page_start).toBe(13);
    expect(detail.asset?.asset_id).toBe("ai-1");
  });

  it("falls back to the lesson summary when no block contains the concept", () => {
    const sections = buildLessonReadingSections(blocks, assets);
    const detail = buildLessonConceptDetail("受精作用", "受精作用使染色体数目恢复。", sections);

    expect(detail.explanation).toBe("受精作用使染色体数目恢复。");
    expect(detail.citation).toBeNull();
  });
});
