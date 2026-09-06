import { describe, expect, it } from "vitest";
import type { CourseSummary, JobStatusResponse } from "../types/api";
import type { UploadedCourseFile } from "../types/app";
import {
  buildHomeBookModels,
  canOpenHomeBookOriginal,
  createCourseSelectionCoordinator,
  resolveHomeBookListState,
  resolveHomeBookSelection,
  resolveHomeBookStatusAction,
  stableBookCoverVariant
} from "./homeBookModel";

function course(bookId: string, status = "ready", overrides: Partial<CourseSummary> = {}): CourseSummary {
  return {
    book_id: bookId,
    title: `教材 ${bookId}`,
    filename: `${bookId}.pdf`,
    status,
    page_count: 100,
    chapter_count: 8,
    chunk_count: 20,
    asset_count: 2,
    average_confidence: 0.94,
    updated_at: 1,
    ...overrides
  };
}

const uploadedFile: UploadedCourseFile = {
  bookId: "book-b",
  name: "本地教材 B.pdf",
  sizeBytes: 1024,
  contentType: "application/pdf",
  uploadedAt: 2_000,
  origin: "local-upload"
};

function deferred<Value>() {
  let resolve!: (value: Value) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<Value>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function models(overrides: Partial<Parameters<typeof buildHomeBookModels>[0]> = {}) {
  return buildHomeBookModels({
    courses: [course("book-a"), course("book-b")],
    uploadedFile: null,
    parseJobId: null,
    parseJobStatus: null,
    loadedBookId: null,
    loadedChapterCount: 0,
    ...overrides
  });
}

describe("home book model", () => {
  it("deduplicates local and remote books without changing the remote order", () => {
    const result = models({
      courses: [course("book-a"), course("book-b"), course("book-a", "processing")],
      uploadedFile
    });

    expect(result.map((book) => book.bookId)).toEqual(["book-a", "book-b"]);
  });

  it("appends a local-only upload and lets its parse job override remote-safe state", () => {
    const localOnly = { ...uploadedFile, bookId: "book-local" };
    const parseJobStatus: JobStatusResponse = {
      job_id: "job-local",
      book_id: "book-local",
      status: "processing",
      stage: "ocr",
      progress: 41,
      message: "正在识别",
      error: null
    };
    const result = models({
      courses: [course("book-a")],
      uploadedFile: localOnly,
      parseJobId: "job-local",
      parseJobStatus
    });

    expect(result.map((book) => book.bookId)).toEqual(["book-a", "book-local"]);
    expect(result[1]).toMatchObject({ status: "processing", progress: 41, statusLabel: "整理中 41%" });
  });

  it("maps both remote and local failed jobs to the safe error state", () => {
    const remoteFailure = models({
      courses: [course("book-a", "ready", { parse_job_status: "failed" })]
    });
    const localFailure = models({
      uploadedFile,
      parseJobId: "job-b",
      parseJobStatus: {
        job_id: "job-b",
        book_id: "book-b",
        status: "failed",
        stage: "parse",
        progress: 27,
        message: "无法解析",
        error: "文件损坏"
      }
    });

    expect(remoteFailure[0].status).toBe("error");
    expect(localFailure[1]).toMatchObject({ status: "error", errorMessage: "文件损坏" });
  });

  it("keeps unknown server states explicit instead of treating them as ready", () => {
    expect(models({ courses: [course("book-a", "archived_elsewhere")] })[0]).toMatchObject({
      status: "unknown",
      statusLabel: "状态待同步"
    });
  });

  it("adds display-only shelf books while enriching a matching course with its original cover", () => {
    const result = models({
      courses: [course("book-a")],
      catalogBooks: [
        {
          bookId: "book-a",
          title: "课程里的标题优先",
          filename: "book-a-source.pdf",
          coverUrl: "/assets/book-a.webp"
        },
        {
          bookId: "catalog-book-b",
          title: "书架教材 B",
          filename: "book-b.pdf",
          coverUrl: "/assets/book-b.webp"
        }
      ]
    });

    expect(result).toHaveLength(2);
    expect(result[0]).toMatchObject({
      bookId: "book-a",
      status: "ready",
      coverUrl: "/assets/book-a.webp"
    });
    expect(result[1]).toMatchObject({
      bookId: "catalog-book-b",
      title: "书架教材 B",
      status: "catalog",
      statusLabel: "书架预览",
      pageCount: 0,
      chapterCount: 0,
      coverUrl: "/assets/book-b.webp"
    });
    expect(canOpenHomeBookOriginal(result[1], null)).toBe(false);
    expect(resolveHomeBookStatusAction({
      book: result[1],
      uploadedFile: null,
      parseJobId: null,
      parseJobStatus: null
    })).toBe("upload");
  });

  it("keeps catalog order while replacing a matching preview with its ready course", () => {
    const result = models({
      courses: [course("book-ready")],
      catalogBooks: [
        {
          bookId: "catalog-left",
          title: "左侧预览",
          filename: "left.pdf",
          coverUrl: "/left.webp"
        },
        {
          bookId: "book-ready",
          title: "课程标题",
          filename: "ready.pdf",
          coverUrl: "/ready.webp"
        },
        {
          bookId: "catalog-right",
          title: "右侧预览",
          filename: "right.pdf",
          coverUrl: "/right.webp"
        }
      ]
    });

    expect(result.map((book) => book.bookId)).toEqual([
      "catalog-left",
      "book-ready",
      "catalog-right"
    ]);
    expect(result[1]).toMatchObject({ status: "ready", coverUrl: "/ready.webp" });
  });

  it.each([
    ["processing", "processing", "整理中 0%"],
    ["needs_review", "needs_review", "目录待确认"],
    ["uploaded", "uploaded", "等待整理"],
    ["failed", "error", "需要处理"]
  ])("normalizes %s to one explicit workspace state", (sourceStatus, status, statusLabel) => {
    expect(models({ courses: [course("book-a", sourceStatus, { page_count: 0 })] })[0]).toMatchObject({
      status,
      statusLabel
    });
  });

  it("only exposes an original-book capability for readable pages or a true local upload", () => {
    const withoutPages = models({
      courses: [course("book-a", "error", { page_count: 0 })]
    })[0];
    const withPages = models({
      courses: [course("book-a", "error", { page_count: 6 })]
    })[0];
    const localUpload = { ...uploadedFile, bookId: "book-a" };
    const remoteGhost = { ...localUpload, origin: "remote-course" as const };

    expect(withoutPages.pageCount).toBe(0);
    expect(canOpenHomeBookOriginal(withoutPages, null)).toBe(false);
    expect(canOpenHomeBookOriginal(withoutPages, remoteGhost)).toBe(false);
    expect(canOpenHomeBookOriginal(withoutPages, localUpload)).toBe(true);
    expect(canOpenHomeBookOriginal(withPages, null)).toBe(true);
    expect(canOpenHomeBookOriginal(withoutPages, { ...localUpload, bookId: "book-b" })).toBe(false);
  });

  it.each([
    [{ bookCount: 0, loadState: "loading", readyKind: "empty" }, "loading"],
    [{ bookCount: 0, loadState: "error", readyKind: "empty" }, "error"],
    [{ bookCount: 0, loadState: "ready", readyKind: "empty" }, "empty"],
    [{ bookCount: 0, loadState: "ready", readyKind: "content" }, "loading"],
    [{ bookCount: 1, loadState: "loading", readyKind: "content" }, "content"],
    [{ bookCount: 1, loadState: "error", readyKind: "content" }, "content"]
  ] as const)("resolves the homepage list state %# without mixing empty and loading/error", (input, expected) => {
    expect(resolveHomeBookListState(input)).toBe(expected);
  });

  it("keeps processing and error routes bound to the selected book's parse job identity", () => {
    const processing = models({ courses: [course("book-a", "processing", { page_count: 0 })] })[0];
    const error = models({ courses: [course("book-a", "error", { page_count: 0 })] })[0];
    const remoteSession = { ...uploadedFile, bookId: "book-a", origin: "remote-course" as const };
    const statusFor = (bookId: string): JobStatusResponse => ({
      job_id: `job-${bookId}`,
      book_id: bookId,
      status: "processing",
      stage: "parse",
      progress: 30,
      message: null,
      error: null
    });

    expect(resolveHomeBookStatusAction({
      book: processing,
      uploadedFile: remoteSession,
      parseJobId: "job-a",
      parseJobStatus: null
    })).toBe("processing");
    expect(resolveHomeBookStatusAction({
      book: error,
      uploadedFile: remoteSession,
      parseJobId: "job-a",
      parseJobStatus: statusFor("book-a")
    })).toBe("processing");
    expect(resolveHomeBookStatusAction({
      book: processing,
      uploadedFile: remoteSession,
      parseJobId: "job-a",
      parseJobStatus: statusFor("book-b")
    })).toBe("library");
    expect(resolveHomeBookStatusAction({
      book: error,
      uploadedFile: { ...remoteSession, bookId: "book-b" },
      parseJobId: "job-a",
      parseJobStatus: null
    })).toBe("library");
    expect(resolveHomeBookStatusAction({
      book: error,
      uploadedFile: remoteSession,
      parseJobId: null,
      parseJobStatus: null
    })).toBe("library");
  });

  it("routes uploaded, review, and unknown states without promoting a remote upload to a local parse", () => {
    const uploaded = models({ courses: [course("book-a", "uploaded", { page_count: 0 })] })[0];
    const review = models({ courses: [course("book-a", "needs_review")] })[0];
    const unknown = models({ courses: [course("book-a", "moving_backend")] })[0];
    const trueLocal = { ...uploadedFile, bookId: "book-a" };
    const remoteSession = { ...trueLocal, origin: "remote-course" as const };
    const action = (book: typeof uploaded, session: UploadedCourseFile | null) => resolveHomeBookStatusAction({
      book,
      uploadedFile: session,
      parseJobId: null,
      parseJobStatus: null
    });

    expect(action(uploaded, trueLocal)).toBe("parseReady");
    expect(action(uploaded, remoteSession)).toBe("library");
    expect(action(uploaded, { ...trueLocal, bookId: "book-b" })).toBe("library");
    expect(action(review, null)).toBe("chapterConfirm");
    expect(action(unknown, trueLocal)).toBe("library");
  });

  it.each([
    {
      label: "needs_review with a done job and loaded chapters",
      status: "needs_review",
      parseJobStatus: {
        job_id: "job-b",
        book_id: "book-b",
        status: "done",
        stage: "complete",
        progress: 100,
        message: "目录待确认",
        error: null
      } satisfies JobStatusResponse
    },
    { label: "unknown with loaded chapters", status: "backend_migrating", parseJobStatus: null },
    { label: "uploaded with loaded chapters", status: "uploaded", parseJobStatus: null }
  ])("does not promote $label to ready", ({ status, parseJobStatus }) => {
    const result = models({
      courses: [course("book-b", status)],
      uploadedFile,
      parseJobId: parseJobStatus ? "job-b" : null,
      parseJobStatus,
      loadedBookId: "book-b",
      loadedChapterCount: 8
    });

    expect(result[0].status).toBe(status === "backend_migrating" ? "unknown" : status);
  });

  it("drops a deleted remote course instead of resurrecting its active session as a local book", () => {
    const remoteSession: UploadedCourseFile = { ...uploadedFile, origin: "remote-course" };
    const beforeDelete = models({
      courses: [course("book-a"), course("book-b")],
      uploadedFile: remoteSession,
      loadedBookId: "book-b",
      loadedChapterCount: 8
    });
    const selectedBeforeDelete = resolveHomeBookSelection(beforeDelete, "book-b", "book-b");
    const afterDelete = models({
      courses: [course("book-a")],
      uploadedFile: remoteSession,
      loadedBookId: "book-b",
      loadedChapterCount: 8
    });

    expect(selectedBeforeDelete).toBe("book-b");
    expect(afterDelete.map((book) => book.bookId)).toEqual(["book-a"]);
    expect(resolveHomeBookSelection(afterDelete, selectedBeforeDelete, "book-b")).toBe("book-a");
  });

  it("falls back from a removed selection to the loaded book, then to the stable first book", () => {
    const current = models({ courses: [course("book-a"), course("book-c")] });
    expect(resolveHomeBookSelection(current, "book-b", "book-c")).toBe("book-c");
    expect(resolveHomeBookSelection(current, "book-b", "book-b")).toBe("book-a");
    expect(resolveHomeBookSelection([], "book-b", "book-a")).toBeNull();
  });

  it("prefers a ready course over a leading catalog preview on initial selection", () => {
    const current = models({
      courses: [course("book-ready")],
      catalogBooks: [
        {
          bookId: "catalog-preview",
          title: "目录预览",
          filename: "preview.pdf",
          coverUrl: "/preview.webp"
        },
        {
          bookId: "book-ready",
          title: "真实课程",
          filename: "ready.pdf",
          coverUrl: "/ready.webp"
        }
      ]
    });

    expect(resolveHomeBookSelection(current, null, null)).toBe("book-ready");
  });

  it("creates stable differentiated cover variants from title and id", () => {
    const first = stableBookCoverVariant("book-a", "高中生物");
    expect(stableBookCoverVariant("book-a", "高中生物")).toBe(first);
    expect(new Set([
      stableBookCoverVariant("book-a", "高中生物"),
      stableBookCoverVariant("book-b", "高中数学"),
      stableBookCoverVariant("book-c", "高中物理")
    ]).size).toBeGreaterThan(1);
  });
});

describe("course selection coordinator", () => {
  it("commits only B when A and B succeed out of order", async () => {
    const coordinator = createCourseSelectionCoordinator();
    const requestA = deferred<string>();
    const requestB = deferred<string>();
    const commits: string[] = [];
    const pending: Array<string | null> = [];
    const errors: unknown[] = [];
    const runA = coordinator.run("book-a", {
      load: () => requestA.promise,
      commit: (payload) => commits.push(payload),
      onLatestError: (error) => errors.push(error),
      onPendingChange: (bookId) => pending.push(bookId)
    });
    const runB = coordinator.run("book-b", {
      load: () => requestB.promise,
      commit: (payload) => commits.push(payload),
      onLatestError: (error) => errors.push(error),
      onPendingChange: (bookId) => pending.push(bookId)
    });

    requestB.resolve("book-b");
    await expect(runB).resolves.toBe(true);
    requestA.resolve("book-a");
    await expect(runA).resolves.toBe(false);

    expect(commits).toEqual(["book-b"]);
    expect(errors).toEqual([]);
    expect(pending).toEqual(["book-a", "book-b", null]);
  });

  it("suppresses an old A failure after B succeeds", async () => {
    const coordinator = createCourseSelectionCoordinator();
    const requestA = deferred<string>();
    const requestB = deferred<string>();
    const commits: string[] = [];
    const errors: unknown[] = [];
    const onPendingChange = () => undefined;
    const runA = coordinator.run("book-a", {
      load: () => requestA.promise,
      commit: (payload) => commits.push(payload),
      onLatestError: (error) => errors.push(error),
      onPendingChange
    });
    const runB = coordinator.run("book-b", {
      load: () => requestB.promise,
      commit: (payload) => commits.push(payload),
      onLatestError: (error) => errors.push(error),
      onPendingChange
    });

    requestB.resolve("book-b");
    await expect(runB).resolves.toBe(true);
    requestA.reject(new Error("old A failed"));
    await expect(runA).resolves.toBe(false);

    expect(commits).toEqual(["book-b"]);
    expect(errors).toEqual([]);
  });

  it("commits the complete B payload through one atomic callback", async () => {
    type Payload = { scan: string; chapters: string[]; lessons: string[]; quizzes: string[] };
    const coordinator = createCourseSelectionCoordinator();
    const requestB = deferred<Payload>();
    const snapshots: Payload[] = [];
    const runB = coordinator.run("book-b", {
      load: () => requestB.promise,
      commit: (payload) => snapshots.push(payload),
      onLatestError: () => undefined,
      onPendingChange: () => undefined
    });
    const completePayload: Payload = {
      scan: "scan-b",
      chapters: ["chapter-b"],
      lessons: ["lesson-b"],
      quizzes: ["quiz-b"]
    };

    expect(snapshots).toEqual([]);
    requestB.resolve(completePayload);
    await expect(runB).resolves.toBe(true);
    expect(snapshots).toEqual([completePayload]);
  });

  it("does not let A's finally clear B's pending state", async () => {
    const coordinator = createCourseSelectionCoordinator();
    const requestA = deferred<string>();
    const requestB = deferred<string>();
    let pendingBookId: string | null = null;
    const options = (load: () => Promise<string>) => ({
      load,
      commit: () => undefined,
      onLatestError: () => undefined,
      onPendingChange: (bookId: string | null) => {
        pendingBookId = bookId;
      }
    });
    const runA = coordinator.run("book-a", options(() => requestA.promise));
    const runB = coordinator.run("book-b", options(() => requestB.promise));

    requestA.resolve("book-a");
    await expect(runA).resolves.toBe(false);
    expect(pendingBookId).toBe("book-b");

    requestB.resolve("book-b");
    await expect(runB).resolves.toBe(true);
    expect(pendingBookId).toBeNull();
  });
});
