import { describe, expect, it } from "vitest";
import type { StudyTask } from "../types/api";
import { calculateChapterProgress } from "./studyProgress";

function task(chapterId: string, status: string): StudyTask {
  return {
    task_id: `${chapterId}-${status}`,
    user_id: "learner",
    day: 1,
    title: "学习任务",
    task_type: "课程",
    minutes: 20,
    chapter_id: chapterId,
    status,
    weak_points: []
  };
}

describe("chapter study progress", () => {
  it("returns a complete ring when every chapter task is done", () => {
    expect(calculateChapterProgress([task("c1s1", "done"), task("c1s2", "done")], ["c1", "c1s1", "c1s2"]))
      .toBe(100);
  });

  it("uses half credit for an in-progress task", () => {
    expect(calculateChapterProgress([
      task("c2s1", "in_progress"),
      task("c2s1", "pending"),
      task("c2s3", "pending")
    ], ["c2", "c2s1", "c2s3"])).toBe(17);
  });

  it("returns zero when a chapter has no assigned tasks", () => {
    expect(calculateChapterProgress([task("c1s1", "done")], ["c7", "c7s1", "c7s2"])).toBe(0);
  });
});
