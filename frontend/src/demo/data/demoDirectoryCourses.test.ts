import { describe, expect, it } from "vitest";
import { DemoRepository } from "../services/DemoRepository";
import { buildStudyDirectory } from "../screens/studyDirectory";
import { hasCompleteLoadedCourseContext } from "../screens/sourceResourceIdentity";
import { demoChemistryCourse, demoEnglishCourse, demoCalculusCourse, demoDirectoryCourses } from "./demoDirectoryCourses";

describe("additional demonstration directories", () => {
  it("adds exactly three distinct subjects alongside the physics demonstration", () => {
    expect(demoDirectoryCourses).toHaveLength(4);
    expect(new Set(demoDirectoryCourses.map((course) => course.summary.book_id)).size).toBe(4);
    expect([demoChemistryCourse, demoEnglishCourse, demoCalculusCourse].map((course) => course.summary.chapter_count)).toEqual([4, 5, 7]);
    expect([demoChemistryCourse, demoEnglishCourse, demoCalculusCourse].map((course) => buildStudyDirectory(course.chapters).flatMap((node) => node.children).length)).toEqual([22, 40, 46]);
    expect(buildStudyDirectory(demoEnglishCourse.chapters)[4].children.map((node) => node.chapter.source_title)).toContain("Viewing and Talking");
    expect(buildStudyDirectory(demoChemistryCourse.chapters)[0].children.some((node) => node.chapter.source_title.includes("实验活动 4"))).toBe(true);
  });

  it.each(demoDirectoryCourses)("isolates $summary.title from other course content", async (fixture) => {
    const repository = new DemoRepository();
    const id = fixture.summary.book_id;
    const [scan, chapters, toc, plan, chunks, lessons, cards, quizzes, assets] = await Promise.all([
      repository.getScanResult(id), repository.getChapters(id), repository.getTocAnalysis(id), repository.getStudyPlan(id, "test-user"),
      repository.getChunks(id), repository.getLessons(id), repository.getFlashcards(id), repository.getQuizzes(id), repository.getAssets(id)
    ]);
    expect(hasCompleteLoadedCourseContext({ loadedBookId: id,
      uploadedFile: { bookId: id, name: scan.filename, sizeBytes: 0, contentType: "application/pdf", uploadedAt: 1, origin: "remote-course" },
      parsedScanResult: scan, parsedChapters: chapters, parsedChunks: chunks, parsedAssets: assets, currentStudyPlan: plan,
      generatedLessons: lessons, generatedFlashcards: cards, generatedQuizzes: quizzes })).toBe(true);
    for (const items of [chunks, lessons, cards, quizzes, assets, plan.tasks]) expect(items).toEqual([]);
    expect(toc.toc_pages.map((page) => page.page)).toEqual(fixture.tocPages);
    expect(toc.chapter_evidence.map((entry) => entry.chapter_id)).toEqual(chapters.map((chapter) => chapter.chapter_id));
    await expect(repository.buildLessons(id)).rejects.toThrow("演示课程");
    expect((await repository.getMistakes("test-user", id))).toEqual([]);
    const response = await repository.queryRag({ book_id: id, question: "减数分裂过程是什么？" });
    expect(response.citations).toEqual([]);
    const ids = new Set(chapters.map((chapter) => chapter.chapter_id));
    expect(ids.size).toBe(chapters.length);
    for (const chapter of chapters) {
      expect(chapter.page_start).toBeGreaterThan(0);
      expect(chapter.page_end).toBeGreaterThanOrEqual(chapter.page_start);
      expect(chapter.page_end).toBeLessThanOrEqual(scan.page_count);
      if (chapter.parent_id) expect(ids.has(chapter.parent_id)).toBe(true);
    }
    chapters[0].source_title = "changed copy";
    expect((await repository.getChapters(id))[0].source_title).toBe(fixture.chapters[0].source_title);
  });
});
