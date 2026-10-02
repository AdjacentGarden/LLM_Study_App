import { describe, expect, it } from "vitest";
import type { ApiChapter } from "../types/api";
import { lessonHeaderSubtitle } from "./lessonHeader";

const chapters = [
  { chapter_id: "c1", level: 1, source_title: "第 1 章 遗传因子的发现", parent_id: null },
  { chapter_id: "c1s1", level: 2, source_title: "第 1 节 孟德尔的豌豆杂交实验（一）", parent_id: "c1" },
  { chapter_id: "c5", level: 1, source_title: "第 5 章 基因突变及其他变异", parent_id: null },
  { chapter_id: "c5s3", level: 2, source_title: "第 3 节 人类遗传病", parent_id: "c5" }
] as ApiChapter[];

describe("lessonHeaderSubtitle", () => {
  it("uses the active lesson's real chapter and section numbers", () => {
    expect(lessonHeaderSubtitle(chapters, "c1s1")).toBe("第 1 章 1 节");
    expect(lessonHeaderSubtitle(chapters, "c5s3")).toBe("第 5 章 3 节");
  });

  it("falls back safely when the active directory node is unavailable", () => {
    expect(lessonHeaderSubtitle(chapters, "missing")).toBe("当前章节");
    expect(lessonHeaderSubtitle(null, "c1s1")).toBe("当前章节");
  });
});
