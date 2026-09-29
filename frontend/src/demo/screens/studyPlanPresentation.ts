import { demoBook, studyPlan as biologyMockStudyPlan } from "../data/mockBook";
import type { StudyTask } from "../types/api";
import type { UploadedCourseFile } from "../types/app";

const frontEndMockTaskPrefix = "front-end-mock-plan";
const biologyChapterIds = [
  "c1s1",
  "c1s1",
  "c1s2",
  "c1s2",
  "c2s1",
  "c2s1",
  "c2s3",
  "c3s2",
  "c3s3",
  "c4s1",
  null,
  null,
  null,
  null
] as const;

export function studyPlanCourseTitle(filename: string) {
  return filename
    .replace(/\s*[（(][\s\S]*$/, "")
    .replace(/\.[^.]+$/, "")
    .trim();
}

export function studyTaskStatusLabel(status: string) {
  const normalizedStatus = status.trim().toLowerCase();
  if (normalizedStatus === "done" || normalizedStatus === "completed") return "已完成";
  if (normalizedStatus === "in_progress" || normalizedStatus === "processing") return "进行中";
  return "待完成";
}

export function currentStudyPlanTask(tasks: readonly StudyTask[]): StudyTask | null {
  const inProgressTask = tasks.find((task) => {
    const status = task.status.trim().toLowerCase();
    return status === "in_progress" || status === "processing";
  });
  if (inProgressTask) return inProgressTask;
  return tasks.find((task) => {
    const status = task.status.trim().toLowerCase();
    return status !== "done" && status !== "completed";
  }) ?? null;
}

export function hasFrontEndMockStudyPlan(uploadedFile: UploadedCourseFile | null) {
  if (!uploadedFile) return false;
  return uploadedFile.bookId === demoBook.id || studyPlanCourseTitle(uploadedFile.name).includes("遗传与进化");
}

function createBiologyMockTasks(userId: string): StudyTask[] {
  return biologyMockStudyPlan.map(([, title, taskType, duration], index) => {
    const day = index + 1;
    return {
      task_id: `${frontEndMockTaskPrefix}:${demoBook.id}:day-${day}`,
      user_id: userId,
      day,
      title,
      task_type: taskType,
      minutes: Number.parseInt(duration, 10),
      chapter_id: biologyChapterIds[index],
      lesson_id: day === 5 || day === 6 ? "lesson_meiosis" : null,
      review_target: day === 4
        ? "遗传图解"
        : day === 6
          ? "同源染色体、减数第一次分裂"
          : null,
      status: day < 5 ? "done" : day === 5 ? "in_progress" : "pending",
      weak_points: []
    };
  });
}

export function mergeFrontEndMockStudyTasks(
  existingTasks: StudyTask[],
  uploadedFile: UploadedCourseFile | null,
  userId: string
) {
  if (!hasFrontEndMockStudyPlan(uploadedFile)) return existingTasks;

  const existingDays = new Set(existingTasks.map((task) => task.day));
  const missingDayTasks = createBiologyMockTasks(userId).filter((task) => !existingDays.has(task.day));
  return [...existingTasks, ...missingDayTasks].sort((left, right) => left.day - right.day);
}

export function isFrontEndMockStudyTask(taskId: string) {
  return taskId.startsWith(`${frontEndMockTaskPrefix}:`);
}
