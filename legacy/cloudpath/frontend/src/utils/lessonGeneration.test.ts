import { describe, expect, it } from "vitest";
import type { LessonBuildJobResponse } from "../types/api";
import {
  isLessonBuildTerminal,
  lessonBuildSummary,
  successfulLessonChapterIds
} from "./lessonGeneration";

function job(overrides: Partial<LessonBuildJobResponse> = {}): LessonBuildJobResponse {
  return {
    job_id: "job_1",
    book_id: "book_1",
    status: "done_with_warnings",
    stage: "done_with_warnings",
    progress: 100,
    lessons: [],
    chapter_results: [],
    ...overrides
  };
}

describe("lesson generation helpers", () => {
  it("treats done_with_warnings as a terminal job status", () => {
    expect(isLessonBuildTerminal("done_with_warnings")).toBe(true);
    expect(isLessonBuildTerminal("processing")).toBe(false);
  });

  it("uses only successfully generated lesson chapter ids", () => {
    const result = job({
      lessons: [
        { chapter_id: "c1" },
        { chapter_id: "c1" },
        { chapter_id: "c2" }
      ] as LessonBuildJobResponse["lessons"]
    });
    expect(successfulLessonChapterIds(result)).toEqual(["c1", "c2"]);
  });

  it("separates normal directory containers from real warnings", () => {
    const result = job({
      lessons: [{ chapter_id: "c1" }] as LessonBuildJobResponse["lessons"],
      chapter_results: [
        { chapter_id: "parent", chapter_title: "Parent", status: "container" },
        { chapter_id: "c1", chapter_title: "Ready", status: "done" },
        { chapter_id: "empty", chapter_title: "Empty", status: "skipped" }
      ]
    });
    expect(lessonBuildSummary(result)).toEqual({
      lessonCount: 1,
      containerCount: 1,
      warningCount: 1,
      message: "已生成 1 节课程，1 个目录节点仅用于分组，1 个章节未生成"
    });
  });
});
