import { describe, expect, it } from "vitest";
import type { ApiChapter } from "../types/api";
import {
  buildChapterTree,
  findChapterRangeConflicts,
  flattenChapterTree,
  getChapterDescendantIds,
  validateChapterDraft
} from "./chapterStructure";

function chapter(overrides: Partial<ApiChapter>): ApiChapter {
  return {
    chapter_id: "chapter",
    level: 1,
    source_title: "章节",
    ai_title: "章节",
    page_start: 1,
    page_end: 10,
    confidence: 90,
    status: "需检查",
    source: "toc",
    parent_id: null,
    ...overrides
  };
}

describe("chapter structure helpers", () => {
  it("builds a stable chapter tree from parent ids", () => {
    const chapters = [
      chapter({ chapter_id: "root", source_title: "第一章" }),
      chapter({ chapter_id: "child", level: 2, parent_id: "root", source_title: "第一节" }),
      chapter({ chapter_id: "standalone", source_title: "第二章" })
    ];

    const tree = buildChapterTree(chapters);
    expect(tree.map((node) => node.chapter.chapter_id)).toEqual(["root", "standalone"]);
    expect(tree[0].children[0].chapter.chapter_id).toBe("child");
    expect(flattenChapterTree(tree).map((node) => node.chapter.chapter_id)).toEqual(["root", "child", "standalone"]);
  });

  it("finds overlaps only between sibling chapters at the same level", () => {
    const chapters = [
      chapter({ chapter_id: "parent", page_end: 20 }),
      chapter({ chapter_id: "first", level: 2, parent_id: "parent", page_start: 2, page_end: 5 }),
      chapter({ chapter_id: "second", level: 2, parent_id: "parent", page_start: 5, page_end: 8 }),
      chapter({ chapter_id: "nested", level: 3, parent_id: "first", page_start: 3, page_end: 4 })
    ];

    expect(findChapterRangeConflicts(chapters)).toEqual([
      { chapterId: "first", otherChapterId: "second", overlapStart: 5, overlapEnd: 5 }
    ]);
  });

  it("collects all descendants for safe subtree removal", () => {
    const chapters = [
      chapter({ chapter_id: "root" }),
      chapter({ chapter_id: "child", parent_id: "root" }),
      chapter({ chapter_id: "grandchild", parent_id: "child" }),
      chapter({ chapter_id: "other" })
    ];

    expect(new Set(getChapterDescendantIds(chapters, "root"))).toEqual(new Set(["child", "grandchild"]));
  });

  it("rejects a draft that overlaps a sibling", () => {
    const first = chapter({ chapter_id: "first", level: 2, parent_id: "parent", page_start: 2, page_end: 5 });
    const second = chapter({ chapter_id: "second", level: 2, parent_id: "parent", page_start: 6, page_end: 8 });
    const parent = chapter({ chapter_id: "parent", page_end: 20 });

    expect(validateChapterDraft([parent, first, second], { ...first, page_end: 6 }, 20)).toMatchObject({
      code: "sibling-overlap",
      field: "page_start",
      message: expect.stringContaining("页码重叠")
    });
  });

  it("returns a stable field for a missing title", () => {
    expect(validateChapterDraft([], chapter({ source_title: "   " }))).toEqual({
      code: "source-title-required",
      field: "source_title",
      message: "章节名称不能为空"
    });
  });
});
