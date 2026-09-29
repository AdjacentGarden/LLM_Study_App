import type { StudyTask } from "../demo/types/api";

export function currentStudyPlanTask(tasks: readonly StudyTask[]): StudyTask | null {
  const active = tasks.find(task => {
    const status = task.status.trim().toLowerCase();
    return status === "in_progress" || status === "processing";
  });
  if (active) return active;
  return tasks.find(task => {
    const status = task.status.trim().toLowerCase();
    return status !== "done" && status !== "completed";
  }) ?? null;
}
