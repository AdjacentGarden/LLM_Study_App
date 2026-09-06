import { describe, expect, it } from "vitest";
import type {
  ApiChapter,
  CourseSummary,
  ScanResult,
  StudyPlan
} from "../types/api";
import type { UploadedCourseFile } from "../types/app";
import {
  hasCompleteLoadedCourseContext,
  resolveCourseSessionClear,
  resolveLatestCourseTitle,
  resolveProgressOpenMode,
  shouldClearCourseSessionForDeletedBook,
  shouldClearLoadedCourseAfterRefresh,
  shouldClearLoadedCourseForDeletedBook,
  shouldClearRemoteSessionAfterRefresh,
  type LoadedCourseContext
} from "./courseResourceIdentity";

function uploaded(bookId: string, origin: UploadedCourseFile["origin"] = "remote-course"): UploadedCourseFile {
  return {
    bookId,
    name: `${bookId}.pdf`,
    sizeBytes: 1,
    contentType: "application/pdf",
    uploadedAt: 1,
    origin
  };
}

function summary(bookId: string): CourseSummary {
  return {
    book_id: bookId,
    title: `教材 ${bookId}`,
    filename: `${bookId}.pdf`,
    status: "ready",
    page_count: 1,
    chapter_count: 1,
    chunk_count: 0,
    asset_count: 0,
    average_confidence: 1,
    updated_at: 1
  };
}

function loadedContext(bookId: string): LoadedCourseContext {
  return {
    loadedBookId: bookId,
    uploadedFile: uploaded(bookId),
    parsedScanResult: { book_id: bookId } as ScanResult,
    parsedChapters: [{ chapter_id: `chapter-${bookId}` }] as ApiChapter[],
    parsedChunks: [],
    parsedAssets: [],
    currentStudyPlan: { book_id: bookId } as StudyPlan,
    generatedLessons: [],
    generatedFlashcards: [],
    generatedQuizzes: []
  };
}

describe("loaded course resource identity", () => {
  it("accepts a complete same-book snapshot even when optional collections are empty", () => {
    expect(hasCompleteLoadedCourseContext(loadedContext("book-a"))).toBe(true);
  });

  it("blocks A resources after the active session moves to processing book B", () => {
    const mismatched = {
      ...loadedContext("book-a"),
      uploadedFile: uploaded("book-b")
    };

    expect(hasCompleteLoadedCourseContext(mismatched)).toBe(false);
    expect(hasCompleteLoadedCourseContext(mismatched, "book-a")).toBe(false);
  });

  it("clears a removed remote loaded course and its remote session after a successful refresh", () => {
    const remoteSession = uploaded("book-a");

    expect(shouldClearLoadedCourseAfterRefresh("book-a", remoteSession, [])).toBe(true);
    expect(shouldClearRemoteSessionAfterRefresh(remoteSession, [])).toBe(true);
    expect(resolveLatestCourseTitle(remoteSession, [summary("book-b")])).toBe("教材 book-b");
  });

  it("preserves a real local upload while it is still waiting to appear in summaries", () => {
    const localUpload = uploaded("book-local", "local-upload");

    expect(shouldClearLoadedCourseAfterRefresh("book-local", localUpload, [])).toBe(false);
    expect(shouldClearRemoteSessionAfterRefresh(localUpload, [])).toBe(false);
    expect(resolveLatestCourseTitle(localUpload, [])).toBe("book-local.pdf");
  });

  it("clears A payload when deleting A even if the current session belongs to B", () => {
    const processingSessionB = uploaded("book-b");

    expect(shouldClearLoadedCourseForDeletedBook("book-a", "book-a")).toBe(true);
    expect(shouldClearCourseSessionForDeletedBook(processingSessionB, "book-a")).toBe(false);
  });

  it("invalidates the old processing generation only when the deleted session still matches A", () => {
    expect(resolveCourseSessionClear(uploaded("book-a"), "book-a", 7)).toEqual({
      shouldClear: true,
      nextGeneration: 8
    });
    expect(resolveCourseSessionClear(uploaded("book-b"), "book-a", 7)).toEqual({
      shouldClear: false,
      nextGeneration: 7
    });
  });

  it("keeps loaded A intact for a progress target with neither a job nor the current session", () => {
    expect(resolveProgressOpenMode("book-missing", null, "book-a")).toBe("unavailable");
    expect(resolveProgressOpenMode("book-b", "job-b", "book-a")).toBe("job");
    expect(resolveProgressOpenMode("book-a", null, "book-a")).toBe("current-session");
  });
});
