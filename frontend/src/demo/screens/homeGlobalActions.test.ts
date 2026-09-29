import { describe, expect, it } from "vitest";
import type { StudyPlan, StudyTask } from "../types/api";
import { buildHomeGlobalActions } from "./homeGlobalActions";

function task(taskId: string, status: string): StudyTask {
  return {
    task_id: taskId,
    user_id: "user",
    day: 1,
    title: `任务 ${taskId}`,
    task_type: "lesson",
    minutes: 20,
    status,
    weak_points: []
  };
}

function plan(bookId: string, tasks: StudyTask[]): StudyPlan {
  return {
    user_id: "user",
    book_id: bookId,
    days: 7,
    daily_minutes: 20,
    tasks
  };
}

describe("home global actions", () => {
  it.each(["loading", "error", "empty"] as const)(
    "hides the lower global section for the %s list state",
    (listState) => {
      expect(buildHomeGlobalActions({
        listState,
        selectedBookId: null,
        selectedLoadedReady: false,
        plan: null
      })).toEqual([]);
    }
  );

  it("shows only the clear upload value when the selected course is not fully loaded", () => {
    expect(buildHomeGlobalActions({
      listState: "content",
      selectedBookId: "book-a",
      selectedLoadedReady: false,
      plan: plan("book-a", [task("a", "pending")])
    })).toEqual([
      {
        id: "upload",
        title: "上传新书",
        helper: "添加另一份教材",
        target: "upload"
      }
    ]);
  });

  it("uses only the matching plan's real tasks and keeps routes out of the bottom navigation", () => {
    const actions = buildHomeGlobalActions({
      listState: "content",
      selectedBookId: "book-a",
      selectedLoadedReady: true,
      plan: plan("book-a", [
        task("done", "done"),
        task("completed", " COMPLETED "),
        task("pending", "pending")
      ])
    });

    expect(actions).toEqual([
      {
        id: "plan",
        title: "学习计划",
        helper: "1 项待完成",
        target: "plan"
      },
      {
        id: "mistakes",
        title: "错题复习",
        helper: "按当前教材回看诊断记录",
        target: "mistakes"
      },
      {
        id: "upload",
        title: "上传新书",
        helper: "添加另一份教材",
        target: "upload"
      }
    ]);
    expect(actions.map((action) => action.target)).not.toEqual(expect.arrayContaining(["home", "library", "study"]));
  });

  it("reports a fully completed real plan without inventing progress", () => {
    const actions = buildHomeGlobalActions({
      listState: "content",
      selectedBookId: "book-a",
      selectedLoadedReady: true,
      plan: plan("book-a", [task("a", "done"), task("b", "completed")])
    });

    expect(actions[0]).toMatchObject({ id: "plan", helper: "2 项任务已完成" });
  });

  it("omits empty and cross-book plans while preserving the ready-course review value", () => {
    for (const candidatePlan of [plan("book-a", []), plan("book-b", [task("b", "pending")])]) {
      const actions = buildHomeGlobalActions({
        listState: "content",
        selectedBookId: "book-a",
        selectedLoadedReady: true,
        plan: candidatePlan
      });

      expect(actions.map((action) => action.id)).toEqual(["mistakes", "upload"]);
    }
  });
});
