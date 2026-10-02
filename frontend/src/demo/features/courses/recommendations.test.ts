import { describe, expect, it } from "vitest";
import type { StudyPlan, StudyTask } from "../../types/api";
import {
  buildCourseRecommendations,
  preferredToolIds,
  reviewIntervalDays
} from "./recommendations";
import type { Course, LearnerPreferences } from "./model";

const baseSet: Course = {
  id: "set-1",
  name: "期末复习",
  resourceIds: ["course:book-a", "course:book-b", "local:file-c"],
  diagnosis: {
    urgency: "urgent",
    timePattern: "fragmented",
    goals: ["exam", "gaps"],
    contentFoci: ["exercises", "keyPoints"],
    aids: ["mistakes"],
    reviews: ["alongside", "periodic"]
  },
  createdAt: 1,
  updatedAt: 1
};

const preferences: LearnerPreferences = {
  displayName: "小明",
  primaryGoal: "exam",
  dailyTime: "under30",
  completedAt: 1
};

function task(id: string, title: string, type: string, minutes: number, status = "pending"): StudyTask {
  return { task_id: id, user_id: "local_user", day: 1, title, task_type: type, minutes, status, weak_points: [] };
}

function plan(bookId: string, tasks: StudyTask[]): StudyPlan {
  return { user_id: "local_user", book_id: bookId, days: 14, daily_minutes: 30, tasks };
}

describe("course recommendations", () => {
  it("selects existing pending tasks within the global budget without changing source plans", () => {
    const originalA = plan("book-a", [task("a-1", "核心概念", "课程", 25), task("a-2", "复习闪卡", "闪卡复习", 10, "done")]);
    const originalB = plan("book-b", [task("b-1", "诊断练习", "练习", 18)]);
    const result = buildCourseRecommendations(baseSet, preferences, new Map([["book-a", originalA], ["book-b", originalB]]));
    expect(result.budget).toBe(20);
    expect(result.recommendations[0].taskId).toBe("b-1");
    expect(result.recommendations.every((item) => item.taskId !== "a-2")).toBe(true);
    expect(result.recommendations.reduce((sum, item) => sum + item.minutes, 0)).toBeLessThanOrEqual(20);
    expect(result.recommendations.flatMap((item) => item.segments).every((minutes) => minutes >= 5 && minutes <= 10)).toBe(true);
    expect(originalA.tasks[0].minutes).toBe(25);
    expect(originalB.tasks[0].status).toBe("pending");
  });

  it("uses a forty-five minute default and respects free review", () => {
    const relaxed = {
      ...baseSet,
      diagnosis: { ...baseSet.diagnosis, urgency: "relaxed" as const, reviews: ["free" as const] }
    };
    const result = buildCourseRecommendations(relaxed, { ...preferences, dailyTime: null }, new Map());
    expect(result.budget).toBe(45);
    expect(result.reviewDays).toBeNull();
    expect(reviewIntervalDays(baseSet.diagnosis)).toBe(1);
    expect(preferredToolIds(baseSet.diagnosis).slice(0, 2)).toEqual(["assignment", "mistakes"]);
  });

  it("preserves book order for equal priorities and applies each time and review setting", () => {
    const first = plan("book-a", [task("a-1", "概念导读", "课程", 12)]);
    const second = plan("book-b", [task("b-1", "概念导读", "课程", 12)]);
    const plans = new Map([["book-a", first], ["book-b", second]]);
    for (const [dailyTime, budget] of [["under30", 20], ["30to60", 45], ["1to2h", 90], ["over2h", 120]] as const) {
      const result = buildCourseRecommendations(baseSet, { ...preferences, dailyTime }, plans);
      expect(result.budget).toBe(budget);
      expect(result.recommendations[0].bookId).toBe("book-a");
    }
    expect(reviewIntervalDays({ ...baseSet.diagnosis, urgency: "steady" })).toBe(3);
    expect(reviewIntervalDays({ ...baseSet.diagnosis, urgency: "relaxed" })).toBe(7);
  });
});
