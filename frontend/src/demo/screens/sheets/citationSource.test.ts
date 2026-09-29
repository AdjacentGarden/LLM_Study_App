import { describe, expect, it } from "vitest";
import type { ApiAsset } from "../../types/api";
import {
  getCitationSourceText,
  getExtractedCitationSourcePageImage,
  isPublishedCitationSourcePageImage,
  selectExtractedCitationSourcePageImage
} from "./citationSource";

const generatedDiagram: ApiAsset = {
  asset_id: "asset-generated-diagram",
  book_id: "book-1",
  chapter_id: "chapter-1",
  source_type: "ai_generated",
  type: "diagram",
  caption: "AI 生成示意图",
  image_url: "/assets/generated-diagram.webp",
  thumbnail_url: "/assets/generated-diagram-thumb.webp",
  source_page_image_url: null,
  source_chunk_ids: ["chunk-citation"],
  concepts: ["DNA"],
  generation_provider: "fixture",
  review_status: "ready"
};

const extractedCrop: ApiAsset = {
  asset_id: "asset-extracted-crop",
  book_id: "book-1",
  chapter_id: "chapter-1",
  source_type: "extracted",
  page: 15,
  type: "figure",
  caption: "教材页内裁剪图",
  bbox: [10, 10, 120, 120],
  image_url: "/assets/page-15-crop.webp",
  thumbnail_url: "/assets/page-15-crop-thumb.webp",
  source_page_image_url: "/assets/source-page-15.webp",
  source_chunk_ids: ["chunk-citation"],
  concepts: ["DNA"]
};

describe("citation source page image", () => {
  it("rejects generated assets and unregistered extracted page URLs", () => {
    const generatedAssetWithPretendPage = {
      ...generatedDiagram,
      source_page_image_url: "/assets/not-a-textbook-page.webp"
    } as unknown as ApiAsset;

    expect(getExtractedCitationSourcePageImage("chunk-citation", [
      generatedAssetWithPretendPage,
      extractedCrop
    ])).toBeUndefined();
    expect(isPublishedCitationSourcePageImage("/assets/source-page-15.webp")).toBe(false);
  });

  it("does not treat a generated diagram or a cropped image URL as a textbook original page", () => {
    expect(getExtractedCitationSourcePageImage("chunk-citation", [generatedDiagram])).toBeUndefined();

    const extractedWithoutSourcePage: ApiAsset = {
      ...extractedCrop,
      asset_id: "asset-crop-without-page",
      source_page_image_url: "   "
    };
    expect(getExtractedCitationSourcePageImage("chunk-citation", [
      extractedWithoutSourcePage
    ])).toBeUndefined();
  });

  it("requires an exact citation chunk association", () => {
    expect(getExtractedCitationSourcePageImage("other-chunk", [extractedCrop])).toBeUndefined();
  });

  it("would adopt only the exact extracted source_page_image_url after release validation", () => {
    const publishedOnlyThisPage = (value: unknown): value is string => value === "/assets/source-page-15.webp";
    const generatedAssetWithForgedPublishedUrl = {
      ...generatedDiagram,
      source_page_image_url: "/assets/source-page-15.webp"
    } as unknown as ApiAsset;
    expect(selectExtractedCitationSourcePageImage("chunk-citation", [
      generatedAssetWithForgedPublishedUrl,
      extractedCrop
    ], publishedOnlyThisPage)).toBe("/assets/source-page-15.webp");
  });

  it("uses only controlled local chunk text for the no-image source reader", () => {
    expect(getCitationSourceText({
      chapter_id: "chapter-1",
      chapter_title: "第一章",
      page: 15,
      chunk_id: "chunk-citation",
      quote: "受控摘录",
      score: 1,
      retrieval_method: "on-device-hybrid-rag",
      source_type: "textbook",
      source_metadata: { retrieved_chunk_text: "受控的完整本地教材片段。" }
    })).toBe("受控的完整本地教材片段。");
  });
});
