import type { LessonBuildJobResponse } from "../types/api";

export function isLessonBuildTerminal(status: LessonBuildJobResponse["status"]): boolean {
  return status === "done" || status === "done_with_warnings" || status === "failed";
}

export function successfulLessonChapterIds(job: LessonBuildJobResponse): string[] {
  return [...new Set(job.lessons.map((lesson) => lesson.chapter_id).filter(Boolean))];
}

export function lessonBuildSummary(job: LessonBuildJobResponse): {
  lessonCount: number;
  containerCount: number;
  warningCount: number;
  message: string;
} {
  const lessonCount = job.lessons.length;
  const containerCount = job.chapter_results.filter((item) => item.status === "container").length;
  const warningCount = job.chapter_results.filter((item) => item.status === "skipped" || item.status === "failed").length;
  const parts = [`已生成 ${lessonCount} 节课程`];
  if (containerCount > 0) parts.push(`${containerCount} 个目录节点仅用于分组`);
  if (warningCount > 0) parts.push(`${warningCount} 个章节未生成`);
  return { lessonCount, containerCount, warningCount, message: parts.join("，") };
}
