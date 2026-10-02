import { describe, expect, it } from "vitest";
import demoState from "./generated/demo-state.json";

const expectedChapterSectionIds = [
  "c1s1", "c1s2",
  "c2s1", "c2s2", "c2s3",
  "c3s1", "c3s2", "c3s3", "c3s4",
  "c4s1", "c4s2", "c4s3",
  "c5s1", "c5s2", "c5s3"
];

describe("expanded Chapter 1–5 lesson library", () => {
  it("contains one five-page lesson for every formal section", () => {
    const lessons = demoState.lessons.filter((lesson) => expectedChapterSectionIds.includes(lesson.chapter_id));
    expect(lessons).toHaveLength(expectedChapterSectionIds.length);
    expect(new Set(lessons.map((lesson) => lesson.chapter_id))).toEqual(new Set(expectedChapterSectionIds));
    for (const lesson of lessons) {
      expect(lesson.objectives).toHaveLength(3);
      expect(lesson.blocks).toHaveLength(4);
      expect(lesson.key_concepts.length).toBeGreaterThanOrEqual(4);
      expect(lesson.warnings.length).toBeGreaterThan(0);
      for (const block of lesson.blocks) {
        expect(block.content.split(/\n\s*\n/u).filter(Boolean)).toHaveLength(3);
      }
    }
  });

  it("keeps the existing meiosis lesson and discloses AI generation on new blocks", () => {
    const meiosis = demoState.lessons.find((lesson) => lesson.chapter_id === "c2s1");
    expect(meiosis?.lesson_id).toBe("lesson_meiosis");
    expect(meiosis?.blocks.every((block) => block.ai_generated === false)).toBe(true);
    expect(demoState.lessons
      .filter((lesson) => lesson.chapter_id !== "c2s1")
      .every((lesson) => lesson.blocks.every((block) => block.ai_generated === true)))
      .toBe(true);
  });

  it("does not fabricate textbook citations for the missing Chapter 1 body", () => {
    const chapterOneLessons = demoState.lessons.filter((lesson) => lesson.chapter_id.startsWith("c1"));
    expect(chapterOneLessons).toHaveLength(2);
    for (const lesson of chapterOneLessons) {
      expect(lesson.page_start).toBe(0);
      expect(lesson.page_end).toBe(0);
      expect(lesson.source_chunk_ids).toEqual([]);
      expect(lesson.blocks.every((block) => (
        block.source_chunk_ids.length === 0 && block.citations.length === 0
      ))).toBe(true);
      expect(lesson.warnings.join(" ")).toMatch(/源 PDF 缺少第 1 章正文/u);
    }
  });

  it("binds every new lesson to an approved ImageGen overview and a learning page", () => {
    const assetById = new Map(demoState.assets.map((asset) => [asset.asset_id, asset]));
    for (const lesson of demoState.lessons.filter((item) => item.chapter_id !== "c2s1")) {
      const overview = lesson.asset_ids
        .map((assetId) => assetById.get(assetId))
        .find((asset) => asset?.source_type === "ai_generated" && asset.metadata?.role === "lesson_overview");
      expect(overview).toMatchObject({
        generation_provider: "openai-imagegen",
        review_status: "approved"
      });
      expect(lesson.blocks.some((block) => block.asset_ids.includes(overview!.asset_id))).toBe(true);
    }
  });
});
