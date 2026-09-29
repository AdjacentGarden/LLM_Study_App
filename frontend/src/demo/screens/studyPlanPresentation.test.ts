import { describe, expect, it } from "vitest";
import type { StudyTask } from "../types/api";
import type { UploadedCourseFile } from "../types/app";
import {
  currentStudyPlanTask,
  hasFrontEndMockStudyPlan,
  isFrontEndMockStudyTask,
  mergeFrontEndMockStudyTasks,
  studyPlanCourseTitle,
  studyTaskStatusLabel
} from "./studyPlanPresentation";

const biologyFile: UploadedCourseFile = {
  bookId: "book_biology_2",
  name: "人教版高中生物必修2遗传与进化 (人民教育出版社, 课程教材研究所).pdf",
  sizeBytes: 0,
  contentType: "application/pdf",
  uploadedAt: 0,
  origin: "remote-course"
};

function task(day: number): StudyTask {
  return {
    task_id: `backend-day-${day}`,
    user_id: "learner",
    day,
    title: `后端第 ${day} 天任务`,
    task_type: "课程",
    minutes: 30,
    status: "pending",
    weak_points: []
  };
}

describe("study plan presentation", () => {
  it("removes parenthetical publisher metadata and the file extension from the displayed title", () => {
    expect(studyPlanCourseTitle(biologyFile.name)).toBe("人教版高中生物必修2遗传与进化");
    expect(studyPlanCourseTitle("高等数学 上册（第七版）.pdf")).toBe("高等数学 上册");
    expect(studyPlanCourseTitle("高中数学必修第二册.pdf")).toBe("高中数学必修第二册");
  });

  it("presents backend task states in Chinese", () => {
    expect(studyTaskStatusLabel("pending")).toBe("待完成");
    expect(studyTaskStatusLabel("in_progress")).toBe("进行中");
    expect(studyTaskStatusLabel("done")).toBe("已完成");
  });

  it("uses the in-progress task as the current learning-plan position", () => {
    const tasks = [
      task(1),
      { ...task(5), chapter_id: "c2s1", status: "in_progress" },
      task(6)
    ];
    expect(currentStudyPlanTask(tasks)).toMatchObject({
      day: 5,
      chapter_id: "c2s1",
      status: "in_progress"
    });
  });

  it("falls back to the first unfinished task when nothing is in progress", () => {
    const tasks = [
      { ...task(1), status: "done" },
      { ...task(2), chapter_id: "c1s2" }
    ];
    expect(currentStudyPlanTask(tasks)).toMatchObject({ day: 2, chapter_id: "c1s2" });
  });

  it("fills missing mock-course days without replacing backend tasks", () => {
    const backendTasks = Array.from({ length: 10 }, (_, index) => task(index + 5));
    const merged = mergeFrontEndMockStudyTasks(backendTasks, biologyFile, "learner");

    expect(merged).toHaveLength(14);
    expect(merged.map((item) => item.day)).toEqual(Array.from({ length: 14 }, (_, index) => index + 1));
    expect(merged.find((item) => item.day === 5)?.task_id).toBe("backend-day-5");
    expect(merged.find((item) => item.day === 1)?.title).toBe("孟德尔实验导读");
    expect(merged.filter((item) => item.day <= 4).every((item) => item.status === "done")).toBe(true);
    expect(isFrontEndMockStudyTask(merged.find((item) => item.day === 1)?.task_id ?? "")).toBe(true);
  });

  it("positions a fully mocked biology plan after chapter one", () => {
    const merged = mergeFrontEndMockStudyTasks([], biologyFile, "learner");
    expect(merged.filter((item) => item.day <= 4).map((item) => item.status)).toEqual([
      "done",
      "done",
      "done",
      "done"
    ]);
    expect(merged.find((item) => item.day === 5)).toMatchObject({
      chapter_id: "c2s1",
      status: "in_progress"
    });
  });

  it("leaves non-mock courses unchanged", () => {
    const otherFile = { ...biologyFile, bookId: "another-book", name: "普通课程.pdf" };
    const backendTasks = [task(3)];
    expect(hasFrontEndMockStudyPlan(otherFile)).toBe(false);
    expect(mergeFrontEndMockStudyTasks(backendTasks, otherFile, "learner")).toBe(backendTasks);
  });
});
