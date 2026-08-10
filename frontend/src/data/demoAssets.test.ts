import { describe, expect, it } from "vitest";
import assets from "./generated/assets.json";
import demoState from "./generated/demo-state.json";

describe("formal offline textbook assets", () => {
  it("syncs every formal MinerU asset into the demo fixture", () => {
    expect(assets).toHaveLength(267);
    expect(demoState.assets.filter((asset) => asset.source_type === "extracted")).toEqual(assets);
    expect(demoState.assets.filter((asset) => asset.source_type === "ai_generated")).toHaveLength(2);
    expect(assets.every((asset) => asset.source_type === "extracted")).toBe(true);
    expect(assets.every((asset) => asset.image_url.startsWith("/assets/textbook/figures/"))).toBe(true);
    expect(assets.every((asset) => asset.thumbnail_url.startsWith("/assets/textbook/figures/"))).toBe(true);
  });

  it("maps assets to available demo chapters and preserves formal provenance", () => {
    const chapterIds = new Set(demoState.chapters.map((chapter) => chapter.chapter_id));
    expect(assets.every((asset) => asset.chapter_id && chapterIds.has(asset.chapter_id))).toBe(true);
    expect(assets.every((asset) => typeof asset.metadata.formal_chapter_id === "string")).toBe(true);
    expect(assets.every((asset) => asset.metadata.formal_source_type === "mineru")).toBe(true);
  });

  it("binds the meiosis lesson and its cited blocks to synchronized assets", () => {
    const lesson = demoState.lessons.find((item) => item.lesson_id === "lesson_meiosis");
    expect(lesson).toBeDefined();
    expect(lesson?.asset_ids.length).toBeGreaterThan(10);
    expect(lesson?.blocks.some((block) => block.asset_ids.length > 0)).toBe(true);
    const assetIds = new Set(assets.map((asset) => asset.asset_id));
    expect(lesson?.asset_ids.every((assetId) => assetIds.has(assetId))).toBe(true);
  });
});
