import { describe, expect, it, vi } from "vitest";

import { DemoRepository } from "../services/DemoRepository";

const fixtureRepository = new DemoRepository();

describe("explicit fixture repository (not production HTTP)", () => {
  it("excludes catalog entries without prepared sources from the course retrieval corpus", async () => {
    const repository = new DemoRepository();
    const readChunks = vi.spyOn(repository, "getChunks");
    const response = await repository.queryRag({
      course_id: "catalog:community_functions",
      book_id: "catalog_high_school_math_required_2",
      book_ids: ["catalog_high_school_math_required_2", "community_functions"],
      question: "减数分裂与受精作用有什么关系？"
    });
    expect(readChunks).not.toHaveBeenCalledWith("community_functions");
    expect(response.citations).toEqual([]);
  });

  it("returns the fixed local courses without a network request", async () => {
    const courses = await fixtureRepository.listSources();

    expect(courses).toHaveLength(6);
    expect(courses[0]).toMatchObject({
      book_id: "book_biology_2",
      status: "ready",
      rag_index_provider: "local-fixture"
    });
    expect(courses[1]).toMatchObject({
      book_id: "catalog_high_school_math_required_2",
      title: "数学 必修 第二册",
      status: "ready",
      chapter_count: 5,
      rag_index_provider: "toc-screenshot-fixture"
    });
    expect(courses[2]).toMatchObject({
      book_id: "catalog_physics_required_3",
      content_mode: "directory",
      title: "物理 必修 第三册",
      chapter_count: 5
    });
    expect(courses.slice(3).map((course) => course.title)).toEqual([
      "化学 必修 第二册", "英语 必修 第三册", "高等数学 上册（第七版）"
    ]);
    expect(courses.slice(2).every((course) => course.content_mode === "directory")).toBe(true);
  });

  it("builds the mathematics directory from the uploaded catalog screenshots", async () => {
    const chapters = await fixtureRepository.getChapters("catalog_high_school_math_required_2");

    expect(chapters.filter((chapter) => chapter.level === 1).map((chapter) => chapter.source_title)).toEqual([
      "第六章 平面向量及其应用",
      "第七章 复数",
      "第八章 立体几何初步",
      "第九章 统计",
      "第十章 概率"
    ]);
    expect(chapters.some((chapter) => chapter.source_title === "8.6 空间直线、平面的垂直")).toBe(true);
    expect(chapters.some((chapter) => chapter.source_title === "10.3 频率与概率")).toBe(true);
  });

  it("runs the deterministic parse lifecycle", async () => {
    fixtureRepository.reset();
    const job = await fixtureRepository.startParse("book_biology_2");
    const first = await fixtureRepository.getJob(job.job_id);
    const second = await fixtureRepository.getJob(job.job_id);
    const third = await fixtureRepository.getJob(job.job_id);
    const final = await fixtureRepository.getJob(job.job_id);

    expect([first.progress, second.progress, third.progress, final.progress]).toEqual([18, 46, 74, 100]);
    expect(final.status).toBe("done");
  });

  it("starts retries with distinct job identities and independent progress", async () => {
    fixtureRepository.reset();
    const firstJob = await fixtureRepository.startParse("book_biology_2");
    await fixtureRepository.getJob(firstJob.job_id);
    const retryJob = await fixtureRepository.startParse("book_biology_2");
    const retryFirstPoll = await fixtureRepository.getJob(retryJob.job_id);

    expect(retryJob.job_id).not.toBe(firstJob.job_id);
    expect(retryFirstPoll).toMatchObject({
      job_id: retryJob.job_id,
      progress: 18,
      status: "processing"
    });
  });

  it("keeps mistake records grounded in an existing MinerU chunk", async () => {
    const chunks = await fixtureRepository.getChunks("book_biology_2");
    const mistakes = await fixtureRepository.getMistakes("local_user", "book_biology_2");
    const chunkIds = new Set(chunks.map((chunk) => chunk.chunk_id));

    expect(mistakes).not.toHaveLength(0);
    expect(mistakes.flatMap((mistake) => mistake.citation_ids).every((id) => chunkIds.has(id))).toBe(true);
  });
});
