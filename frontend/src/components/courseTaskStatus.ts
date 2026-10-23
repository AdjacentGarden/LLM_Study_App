import type { CourseJob, CourseTaskAPI } from "./learningReliability.ts";

/** A known task status read cannot allocate or submit a generation operation. */
export async function readCourseTask(
  api: Pick<CourseTaskAPI, "courseJob">, sessionId: string, chapterId: string, jobId: string,
): Promise<CourseJob> {
  const job = await api.courseJob(sessionId, jobId);
  if (job.job_id !== jobId || job.session_id !== sessionId || job.chapter_id !== chapterId) {
    throw new Error("任务状态与当前章节不一致，请保留原任务并检查服务连接。");
  }
  if (!["queued", "running", "succeeded", "failed"].includes(job.status)) {
    throw new Error("任务状态暂时无法确认，原任务已保留；请检查服务连接。");
  }
  return job;
}
