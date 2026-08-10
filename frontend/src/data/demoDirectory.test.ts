import { describe, expect, it } from "vitest";
import generatedChapters from "./generated/chapters.json";
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

const expectedChapterTitles = [
  "封面、编者信息、目录与科学家访谈",
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

  it("matches the seven available top-level units and seventeen formal sections in the source PDF", () => {
    expect(curated.filter((entry) => entry.level === 1).map((entry) => entry.source_title))
      .toEqual(expectedChapterTitles);
    expect(curated.filter((entry) => /^第\s*\d+\s*节/.test(entry.source_title))).toHaveLength(17);
    expect(curatedContent.book.chapterCount).toBe(7);
    expect(curatedContent.book.sectionCount).toBe(17);
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
      page_start: 1,
      page_end: 9,
      printed_page_start: null,
      printed_page_end: null
    });
  });

  it("regenerates every curated directory entry", () => {
    expect(generated.map((entry) => entry.chapter_id)).toEqual(curated.map((entry) => entry.chapter_id));
  });
});
