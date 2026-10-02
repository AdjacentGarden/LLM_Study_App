import { describe, expect, it } from "vitest";
import { resolveSourceReaderHeading } from "./sourceReaderHeading";
import type { ApiChapter, ScanResult } from "../../types/api";

const chapters = [
  { chapter_id: "c2", level: 1, source_title: "第 2 章 基因和染色体的关系", page_start: 10, page_end: 35, printed_page_start: 15 },
  { chapter_id: "c2s1", level: 2, source_title: "第 1 节 减数分裂和受精作用", page_start: 11, page_end: 21, printed_page_start: 16 },
  { chapter_id: "c2s1a", level: 3, source_title: "一 减数分裂", page_start: 11, page_end: 17, printed_page_start: 16 }
] as ApiChapter[];

describe("source reader heading", () => {
  it("uses the section name and printed page while retaining the PDF index internally", () => {
    const scan: ScanResult = {
      book_id: "book",
      filename: "教材.pdf",
      file_type: "pdf",
      page_count: 125,
      has_text_layer: true,
      needs_ocr: false,
      source_unit: "page",
      source_locations: [{ index: 11, printed_page: 16 }],
      quality_warnings: []
    };
    const heading = resolveSourceReaderHeading({ bookId: "book", title: "本节导读", pageStart: 11, pageEnd: 11 }, 11, chapters, scan);
    expect(heading.title).toBe("第 1 节 减数分裂和受精作用");
    expect(heading.pageLabel).toBe("第 16 页");
    expect(heading.chapter?.chapter_id).toBe("c2s1");
  });

  it("falls back to the PDF index when a printed-page mapping is unavailable", () => {
    const heading = resolveSourceReaderHeading({ bookId: "book", title: "教材前言", pageStart: 3, pageEnd: 3 }, 3, null, null);
    expect(heading.title).toBe("教材前言");
    expect(heading.pageLabel).toBe("第 3 页");
  });
});
