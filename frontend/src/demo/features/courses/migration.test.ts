import { afterEach, describe, expect, it, vi } from "vitest";
import { courseStorageKey, legacyCourseStorageKey, migrateCourseState, defaultCourseDiagnosis, courseSourceBookIds } from "./model";
import { hydrateCourseState, loadCourseState } from "./repository";
import { courseLocationKey, courseProgress, courseRagScope, preferredCourseSource } from "./selectors";
import type { CourseSourceSummary, StudyPlan } from "../../types/api";

const legacy = {
  version: 1,
  preferences: { displayName: "小明", primaryGoal: "exam", dailyTime: "under30", completedAt: 5 },
  sets: [{ id: "original-id", name: "期末复习", resourceIds: ["course:biology", "local:file-1"], diagnosis: defaultCourseDiagnosis(), createdAt: 1, updatedAt: 2 }],
  resources: [{ id: "file-1", name: "笔记.pdf", contentType: "application/pdf", sizeBytes: 200, addedAt: 3, status: "pending" }],
  draft: { id: "draft-1", name: "未完成", resourceIds: ["course:math"], diagnosis: { urgency: "urgent" }, step: 2, editingSetId: null, parseJobId: "job-1" },
  activeSetId: "original-id"
};
afterEach(() => vi.unstubAllGlobals());

describe("course migration and source scope", () => {
  it("preserves identities, preferences, local file references and unfinished answers", () => {
    const state = migrateCourseState(legacy)!;
    expect(state.version).toBe(2);
    expect(state.courses[0]).toMatchObject({ id: "original-id", name: "期末复习", createdAt: 1, resourceIds: ["source:biology", "local:file-1"] });
    expect(state.resources).toEqual(legacy.resources);
    expect(state.preferences).toEqual(legacy.preferences);
    expect(state.draft).toMatchObject({ id: "draft-1", step: 2, resourceIds: ["source:math"], parseJobId: "job-1", diagnosis: { urgency: "urgent" } });
    expect(state.draft).not.toHaveProperty("editingSetId");
    expect(state.activeCourseId).toBe("original-id");
    expect(migrateCourseState(state)).toEqual(state);
  });
  it("does not adopt unscoped browser coursework into a real account",()=>{
    vi.stubGlobal("window",{localStorage:{getItem:()=>JSON.stringify(legacy),setItem:vi.fn()}});
    expect(loadCourseState().courses).toEqual([]);
  });
  it("loads server-owned state and retains backend processing status",async()=>{
    const state=migrateCourseState(legacy)!;state.resources[0].status="processing";
    vi.stubGlobal("fetch",vi.fn().mockResolvedValue(new Response(JSON.stringify(state))));
    await hydrateCourseState();expect(loadCourseState()).toEqual(state);
  });
  it("restores a local source and isolates positions when two courses share a book", () => {
    const state = migrateCourseState(legacy)!;
    state.resources[0].bookId = "local-source";
    state.resources[0].status = "ready";
    state.courses[0].activeResourceId = "local:file-1";
    const summaries = ["biology", "local-source"].map((book_id) => ({ book_id, status: "ready" } as CourseSourceSummary));
    expect(preferredCourseSource(state.courses[0], state.resources, summaries)?.bookId).toBe("local-source");
    expect(courseSourceBookIds(state.courses[0], state.resources)).toEqual(["biology", "local-source"]);
    expect(courseLocationKey("course-a", "biology")).not.toBe(courseLocationKey("course-b", "biology"));
    expect(courseRagScope(state, "biology")).toEqual({ course_id: "original-id", book_ids: ["biology", "local-source"] });
    expect(courseRagScope(state, "unrelated")).toEqual({});
  });
  it("weights progress by tasks across files", () => {
    const plans = [{ tasks: [{ status: "done" }] }, { tasks: [{ status: "done" }, { status: "pending" }, { status: "pending" }] }] as StudyPlan[];
    expect(courseProgress(plans)).toBe(50);
    expect(courseProgress([])).toBe(0);
  });
});
