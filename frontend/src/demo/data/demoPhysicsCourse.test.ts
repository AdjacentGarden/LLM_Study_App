import { describe, expect, it } from "vitest";
import { DemoRepository } from "../services/DemoRepository";
import { buildStudyDirectory } from "../screens/studyDirectory";
import { buildHomeBookModels } from "../screens/homeBookModel";
import { demoPhysicsBookId, demoPhysicsChapters, demoPhysicsSummary } from "./demoPhysicsCourse";

describe("physics directory demonstration", () => {
  it("preserves the original five chapters, 23 sections and backmatter with PDF page mapping", () => {
    const roots = buildStudyDirectory(demoPhysicsChapters);
    expect(roots.filter((node) => node.children.length).map((node) => node.chapter.source_title)).toEqual([
      "第 9 章 静电场及其应用", "第 10 章 静电场中的能量", "第 11 章 电路及其应用",
      "第 12 章 电能 能量守恒定律", "第 13 章 电磁感应与电磁波初步"
    ]);
    expect(roots.flatMap((node) => node.children)).toHaveLength(23);
    expect(roots.slice(-2).map((node) => node.chapter.source_title)).toEqual(["课题研究", "索引"]);
    for (const chapter of demoPhysicsChapters) {
      expect(chapter.page_start).toBe(chapter.printed_page_start! + 4);
      expect(chapter.page_end).toBe(chapter.printed_page_end! + 4);
      expect(chapter.page_end).toBeGreaterThanOrEqual(chapter.page_start);
      expect(chapter.page_end).toBeLessThanOrEqual(140);
      if (chapter.parent_id) {
        const parent = demoPhysicsChapters.find((item) => item.chapter_id === chapter.parent_id)!;
        expect(chapter.page_start).toBeGreaterThanOrEqual(parent.page_start);
        expect(chapter.page_end).toBeLessThanOrEqual(parent.page_end);
      }
    }
  });

  it("keeps a complete independent directory snapshot without generated biology content", async () => {
    const repository = new DemoRepository();
    const [scan, chapters, toc, plan, chunks, lessons, cards, quizzes, assets, mistakes] = await Promise.all([
      repository.getScanResult(demoPhysicsBookId), repository.getChapters(demoPhysicsBookId),
      repository.getTocAnalysis(demoPhysicsBookId), repository.getStudyPlan(demoPhysicsBookId, "preview-user"),
      repository.getChunks(demoPhysicsBookId), repository.getLessons(demoPhysicsBookId),
      repository.getFlashcards(demoPhysicsBookId), repository.getQuizzes(demoPhysicsBookId),
      repository.getAssets(demoPhysicsBookId), repository.getMistakes("preview-user", demoPhysicsBookId)
    ]);
    expect(scan).toMatchObject({ book_id: demoPhysicsBookId, page_count: 140 });
    expect(chapters).toEqual(demoPhysicsChapters);
    expect(toc.chapter_evidence.map((item) => item.chapter_id)).toEqual(chapters.map((item) => item.chapter_id));
    expect(plan).toMatchObject({ book_id: demoPhysicsBookId, user_id: "preview-user", tasks: [] });
    for (const content of [chunks, lessons, cards, quizzes, assets, mistakes]) expect(content).toEqual([]);
    chapters[0].source_title = "mutated copy";
    expect((await repository.getChapters(demoPhysicsBookId))[0].source_title).toBe(demoPhysicsChapters[0].source_title);
    await expect(repository.getLesson(demoPhysicsBookId, "lesson_meiosis")).rejects.toThrow("演示课程");
    await expect(repository.buildLessons(demoPhysicsBookId)).rejects.toThrow("演示课程");
  });

  it("carries the textbook cover and directory-only presentation into the home model", () => {
    const models = buildHomeBookModels({ courses: [demoPhysicsSummary], uploadedFile: null,
      loadedBookId: null, loadedChapterCount: 0, parseJobId: null, parseJobStatus: null });
    expect(models[0]).toMatchObject({ bookId: demoPhysicsBookId, directoryOnly: true,
      statusLabel: "演示课程", chapterCount: 5, coverUrl: "/assets/book-covers/physics-required-3.webp" });
  });
});
