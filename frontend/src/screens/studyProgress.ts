import type { StudyTask } from "../types/api";

function taskProgress(status: string): number {
  const normalized = status.trim().toLowerCase();
  if (normalized === "done" || normalized === "completed") return 1;
  if (normalized === "in_progress" || normalized === "processing") return 0.5;
  return 0;
}

export function calculateChapterProgress(tasks: StudyTask[], chapterIds: Iterable<string>): number {
  const chapterIdSet = new Set(chapterIds);
  const chapterTasks = tasks.filter((task) => task.chapter_id && chapterIdSet.has(task.chapter_id));
  if (chapterTasks.length === 0) return 0;

  const completedShare = chapterTasks.reduce((total, task) => total + taskProgress(task.status), 0);
  return Math.round((completedShare / chapterTasks.length) * 100);
}
