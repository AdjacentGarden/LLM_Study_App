import type { StudyTask } from "../types/api";
import type { UploadedCourseFile } from "../types/app";

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

export function hasFrontEndMockStudyPlan(_uploadedFile: UploadedCourseFile | null) {return false;}
export function mergeFrontEndMockStudyTasks(existingTasks:StudyTask[],_uploadedFile:UploadedCourseFile|null,_userId:string){return existingTasks;}
export function isFrontEndMockStudyTask(_taskId:string){return false;}
