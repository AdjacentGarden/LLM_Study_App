import { describe, expect, it } from "vitest";
import generatedChapters from "./generated/chapters.json";
import generatedBook from "./generated/book.json";
import curatedContent from "./seed/curated-content.json";

type DirectoryEntry = {
  chapter_id: string;
  level: number;
  source_title: string;
  page_start: number;
  page_end: number;
  printed_page_start?: number | null;
  printed_page_end?: number | null;
  parent_id?: string | null;
};

const expectedDirectoryRootTitles = [
  "教材封面、前言与目录",
  "第 1 章 遗传因子的发现",
  "第 2 章 基因和染色体的关系",
  "第 3 章 基因的本质",
  "第 4 章 基因的表达",
  "第 5 章 基因突变及其他变异",
  "第 6 章 从杂交育种到基因工程",
  "第 7 章 现代生物进化理论"
];

describe("demo textbook directory", () => {
  const curated = curatedContent.chapters as DirectoryEntry[];
  const generated = generatedChapters as DirectoryEntry[];

  it("keeps the six source-backed chapters in the curated directory seed", () => {
    expect(curated.filter((entry) => entry.level === 1).map((entry) => entry.source_title))
      .toEqual(expectedDirectoryRootTitles.filter((title) => title !== "第 1 章 遗传因子的发现"));
    expect(curated.filter((entry) => entry.level === 1 && entry.chapter_id !== "frontmatter")).toHaveLength(6);
    expect(curated.filter((entry) => /^第\s*\d+\s*节/.test(entry.source_title))).toHaveLength(17);
    expect(curatedContent.book.chapterCount).toBe(6);
    expect(curatedContent.book.sectionCount).toBe(17);
  });

  it("adds the source-missing Chapter 1 as an explicit supplement in generated fixtures", () => {
    expect(generated.filter((entry) => entry.level === 1).map((entry) => entry.source_title))
      .toEqual(expectedDirectoryRootTitles);
    expect(generated.filter((entry) => entry.level === 1 && entry.chapter_id !== "frontmatter")).toHaveLength(7);
    expect(generated.filter((entry) => /^第\s*\d+\s*节/.test(entry.source_title))).toHaveLength(19);
    expect(generatedBook.chapterCount).toBe(7);
    expect(generatedBook.sectionCount).toBe(19);
    expect(generated.find((entry) => entry.chapter_id === "c1s1")).toMatchObject({
      page_start: 0,
      page_end: 0,
      printed_page_start: 2,
      printed_page_end: 8,
      status: "AI 补充·待原文核验"
    });
  });

  it("preserves nested subtopics and their parent relationships", () => {
    expect(curated.filter((entry) => entry.parent_id === "c2s1").map((entry) => entry.source_title)).toEqual([
      "一 减数分裂",
      "二 受精作用"
    ]);
    expect(curated.filter((entry) => entry.parent_id === "c7s2").map((entry) => entry.source_title)).toEqual([
      "一 种群基因频率的改变与生物进化",
      "二 隔离与物种的形成",
      "与生物学有关的职业 化石标本的制作",
      "三 共同进化与生物多样性的形成"
    ]);
    expect(curated.find((entry) => entry.chapter_id === "c7sts1")?.parent_id).toBe("c7");
  });

  it("keeps printed textbook pages separate from PDF source pages", () => {
    expect(curated.find((entry) => entry.chapter_id === "c2s1")).toMatchObject({
      page_start: 11,
      page_end: 21,
      printed_page_start: 16,
      printed_page_end: 26
    });
    expect(curated.find((entry) => entry.chapter_id === "c7s2")).toMatchObject({
      page_start: 109,
      page_end: 121,
      printed_page_start: 114,
      printed_page_end: 126
    });
    expect(curated.find((entry) => entry.chapter_id === "frontmatter")).toMatchObject({
      source_title: "教材封面、前言与目录",
      page_start: 1,
      page_end: 9,
      printed_page_start: null,
      printed_page_end: null
    });
  });

  it("preserves every curated entry while keeping frontmatter distinct from Chapter 1", () => {
    expect(generated.filter((entry) => !entry.chapter_id.startsWith("c1")).map((entry) => entry.chapter_id))
      .toEqual(curated.map((entry) => entry.chapter_id));
    expect(generated.find((entry) => entry.chapter_id === "frontmatter")).toEqual(
      curated.find((entry) => entry.chapter_id === "frontmatter")
    );
    expect(generated.find((entry) => entry.chapter_id === "frontmatter")?.source_title)
      .not.toMatch(/第\s*1\s*章|遗传因子的发现/u);
  });
});
