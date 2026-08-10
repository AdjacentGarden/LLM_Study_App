import { describe, expect, it } from "vitest";
import type { ApiChapter, Lesson, StudyPlan, StudyTask } from "../types/api";
import { resolveHomeNextStep } from "./homeNextStep";

function chapter(
  chapterId: string,
  level: number,
  parentId: string | null = null,
  overrides: Partial<ApiChapter> = {}
): ApiChapter {
  return {
    chapter_id: chapterId,
    level,
    source_title: `章节 ${chapterId}`,
    ai_title: `课程 ${chapterId}`,
    page_start: 1,
    page_end: 2,
    confidence: 1,
    status: "匹配良好",
    source: "test",
    parent_id: parentId,
    ...overrides
  };
}

function task(
  chapterId: string | null,
  status = "pending",
  overrides: Partial<StudyTask> = {}
): StudyTask {
  return {
    task_id: `task-${chapterId ?? "none"}`,
    user_id: "user",
    day: 1,
    title: "学习任务",
    task_type: "lesson",
    minutes: 20,
    chapter_id: chapterId,
    status,
    weak_points: [],
    ...overrides
  };
}

function plan(tasks: StudyTask[]): StudyPlan {
  return { user_id: "user", book_id: "book-a", days: 7, daily_minutes: 20, tasks };
}

function lesson(
  chapterId: string,
  lessonKind: string = "lesson",
  lessonId = `lesson-${chapterId}`
): Lesson {
  return {
    book_id: "book-a",
    chapter_id: chapterId,
    lesson_id: lessonId,
    lesson_kind: lessonKind
  } as Lesson;
}

const chapters = [
  chapter("root-a", 1),
  chapter("section-a1", 2, "root-a", {
    printed_page_start: 16,
    printed_page_end: 22,
    page_start: 11,
    page_end: 17
  }),
  chapter("section-a2", 2, "root-a"),
  chapter("root-b", 1)
];

describe("home next-step resolver", () => {
  it("uses a valid stored section before plan and lesson candidates", () => {
    const result = resolveHomeNextStep({
      chapters,
      location: { expandedChapterId: "root-a", expandedSectionId: "section-a2" },
      plan: plan([task("section-a1")]),
      lessons: [lesson("section-a1")]
    });

    expect(result).toMatchObject({
      chapter: { chapter_id: "section-a2" },
      expandedChapterId: "root-a",
      source: "location"
    });
  });

  it("ignores invalid ids and completed tasks, then uses the first valid pending plan chapter", () => {
    const result = resolveHomeNextStep({
      chapters,
      location: { expandedChapterId: "missing", expandedSectionId: "missing" },
      plan: plan([task("missing"), task("section-a1", "done"), task("section-a2")]),
      lessons: [lesson("section-a1")]
    });

    expect(result).toMatchObject({ chapter: { chapter_id: "section-a2" }, source: "plan" });
  });

  it("does not let a stored root container hide a valid plan section", () => {
    const result = resolveHomeNextStep({
      chapters,
      location: { expandedChapterId: "root-a", expandedSectionId: "root-a" },
      plan: plan([task("section-a2")]),
      lessons: [lesson("section-a1")]
    });

    expect(result).toMatchObject({ chapter: { chapter_id: "section-a2" }, source: "plan" });
  });

  it("does not let a plan root container override a valid lesson section", () => {
    const result = resolveHomeNextStep({
      chapters,
      plan: plan([task("root-a")]),
      lessons: [lesson("section-a2")]
    });

    expect(result).toMatchObject({ chapter: { chapter_id: "section-a2" }, source: "lesson" });
  });

  it("resolves a pending task from review_target when chapter_id is absent", () => {
    const result = resolveHomeNextStep({
      chapters,
      plan: plan([task(null, "pending", { review_target: "section-a2" })]),
      lessons: []
    });

    expect(result).toMatchObject({ chapter: { chapter_id: "section-a2" }, source: "plan" });
  });

  it("resolves a pending task from lesson_id when other task references are absent", () => {
    const result = resolveHomeNextStep({
      chapters,
      plan: plan([task(null, "pending", { lesson_id: "lesson-target" })]),
      lessons: [lesson("section-a1", "lesson", "lesson-target")]
    });

    expect(result).toMatchObject({ chapter: { chapter_id: "section-a1" }, source: "plan" });
  });

  it("uses the first valid generated lesson for an empty plan", () => {
    const result = resolveHomeNextStep({
      chapters,
      plan: plan([]),
      lessons: [lesson("missing"), lesson("section-a1")]
    });

    expect(result).toMatchObject({ chapter: { chapter_id: "section-a1" }, source: "lesson" });
    expect(result?.pageLabel).toBe("原书第 16-22 页 · PDF 第 11-17 页");
  });

  it("excludes a module intro for a root container from the lesson source", () => {
    const result = resolveHomeNextStep({
      chapters,
      plan: plan([]),
      lessons: [lesson("root-a", "module_intro"), lesson("section-a2")]
    });

    expect(result).toMatchObject({ chapter: { chapter_id: "section-a2" }, source: "lesson" });
  });

  it("skips a leading container for the stable directory fallback", () => {
    const result = resolveHomeNextStep({ chapters, plan: plan([]), lessons: [] });

    expect(result).toMatchObject({
      chapter: { chapter_id: "section-a1" },
      expandedChapterId: "root-a",
      source: "directory"
    });
  });

  it("treats a childless level-one root as a learnable chapter", () => {
    const rootLeaves = [chapter("root-first", 1), chapter("root-second", 1)];
    expect(resolveHomeNextStep({ chapters: rootLeaves, plan: null, lessons: null })).toMatchObject({
      chapter: { chapter_id: "root-first" },
      expandedChapterId: "root-first",
      source: "directory"
    });
  });

  it("rejects parent-cycle candidates and falls back to the first reachable leaf root", () => {
    const cyclicChapters = [
      chapter("cycle-a", 2, "cycle-b"),
      chapter("cycle-b", 2, "cycle-a"),
      chapter("root-leaf", 1)
    ];
    const result = resolveHomeNextStep({
      chapters: cyclicChapters,
      location: { expandedChapterId: "cycle-a", expandedSectionId: "cycle-a" },
      plan: plan([task("cycle-b")]),
      lessons: [lesson("cycle-a")]
    });

    expect(result).toMatchObject({
      chapter: { chapter_id: "root-leaf" },
      expandedChapterId: "root-leaf",
      source: "directory"
    });
  });

  it("returns null when every chapter is trapped in a parent cycle", () => {
    const cyclicOnly = [chapter("cycle-a", 2, "cycle-b"), chapter("cycle-b", 2, "cycle-a")];
    expect(resolveHomeNextStep({ chapters: cyclicOnly, plan: null, lessons: null })).toBeNull();
  });
});
