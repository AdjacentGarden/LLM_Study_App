import { describe, expect, it } from "vitest";
import { demoRepository as bookcourseApi } from "../services/DemoRepository";

describe("demo repository boundary", () => {
  it("returns the fixed local course without a network request", async () => {
    const courses = await bookcourseApi.listCourses();

    expect(courses).toHaveLength(1);
    expect(courses[0]).toMatchObject({
      book_id: "book_biology_2",
      status: "ready",
      rag_index_provider: "local-fixture"
    });
  });

  it("runs the deterministic parse lifecycle", async () => {
    bookcourseApi.reset();
    const job = await bookcourseApi.startParse("book_biology_2");
    const first = await bookcourseApi.getJob(job.job_id);
    const second = await bookcourseApi.getJob(job.job_id);
    const third = await bookcourseApi.getJob(job.job_id);
    const final = await bookcourseApi.getJob(job.job_id);

    expect([first.progress, second.progress, third.progress, final.progress]).toEqual([18, 46, 74, 100]);
    expect(final.status).toBe("done");
  });

  it("starts retries with distinct job identities and independent progress", async () => {
    bookcourseApi.reset();
    const firstJob = await bookcourseApi.startParse("book_biology_2");
    await bookcourseApi.getJob(firstJob.job_id);
    const retryJob = await bookcourseApi.startParse("book_biology_2");
    const retryFirstPoll = await bookcourseApi.getJob(retryJob.job_id);

    expect(retryJob.job_id).not.toBe(firstJob.job_id);
    expect(retryFirstPoll).toMatchObject({
      job_id: retryJob.job_id,
      progress: 18,
      status: "processing"
    });
  });

  it("keeps mistake records grounded in an existing MinerU chunk", async () => {
    const chunks = await bookcourseApi.getChunks("book_biology_2");
    const mistakes = await bookcourseApi.getMistakes("local_user", "book_biology_2");
    const chunkIds = new Set(chunks.map((chunk) => chunk.chunk_id));

    expect(mistakes).not.toHaveLength(0);
    expect(mistakes.flatMap((mistake) => mistake.citation_ids).every((id) => chunkIds.has(id))).toBe(true);
  });
});
