import type { CourseSummary, JobStatusResponse } from "../types/api";
import type { UploadedCourseFile } from "../types/app";
import type { CourseSummariesLoadState, CourseSummariesReadyKind } from "../context/AppContext";

export type HomeBookStatus =
  | "catalog"
  | "uploaded"
  | "processing"
  | "needs_review"
  | "ready"
  | "error"
  | "unknown";

export type HomeBookListState = "content" | "loading" | "error" | "empty";
export type HomeBookStatusAction = "processing" | "parseReady" | "library" | "chapterConfirm" | "upload";

export type HomeBookCatalogItem = Readonly<{
  bookId: string;
  title: string;
  filename: string | null;
  coverUrl: string;
}>;

export type HomeBookModel = Readonly<{
  bookId: string;
  title: string;
  filename: string | null;
  status: HomeBookStatus;
  statusLabel: string;
  pageCount: number;
  chapterCount: number;
  progress: number;
  nextTitle: string | null;
  errorMessage: string | null;
  updatedAt: number;
  coverVariant: number;
  coverUrl: string | null;
}>;

export type BuildHomeBookModelsInput = Readonly<{
  courses: readonly CourseSummary[];
  uploadedFile: UploadedCourseFile | null;
  parseJobId: string | null;
  parseJobStatus: JobStatusResponse | null;
  loadedBookId: string | null;
  loadedChapterCount: number;
  catalogBooks?: readonly HomeBookCatalogItem[];
}>;

const coverVariantCount = 6;

function clampProgress(progress: number | null | undefined): number {
  if (!Number.isFinite(progress)) return 0;
  return Math.max(0, Math.min(100, Math.round(progress ?? 0)));
}

function normalizeRemoteStatus(course: CourseSummary): HomeBookStatus {
  if (course.parse_job_status === "failed") return "error";
  if (course.parse_job_status === "pending" || course.parse_job_status === "processing") {
    return "processing";
  }

  switch (course.status.toLowerCase()) {
    case "ready":
      return "ready";
    case "needs_review":
      return "needs_review";
    case "pending":
    case "processing":
    case "parsing":
      return "processing";
    case "failed":
    case "error":
      return "error";
    case "uploaded":
      return "uploaded";
    default:
      return "unknown";
  }
}

export function homeBookStatusLabel(status: HomeBookStatus, progress = 0): string {
  switch (status) {
    case "catalog":
      return "书架预览";
    case "ready":
      return "可以学习";
    case "processing":
      return `整理中 ${clampProgress(progress)}%`;
    case "needs_review":
      return "目录待确认";
    case "error":
      return "需要处理";
    case "uploaded":
      return "等待整理";
    case "unknown":
      return "状态待同步";
  }
}

export function stableBookCoverVariant(bookId: string, title: string): number {
  const source = `${bookId}\u0000${title}`;
  let hash = 2166136261;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) % coverVariantCount;
}

export function canOpenHomeBookOriginal(
  book: HomeBookModel,
  uploadedFile: UploadedCourseFile | null
): boolean {
  const hasReadablePages = book.pageCount > 0;
  const hasTrueLocalSession = Boolean(
    uploadedFile
    && uploadedFile.bookId === book.bookId
    && uploadedFile.origin !== "remote-course"
  );
  return hasReadablePages || hasTrueLocalSession;
}

export function resolveHomeBookListState(input: Readonly<{
  bookCount: number;
  loadState: CourseSummariesLoadState;
  readyKind: CourseSummariesReadyKind;
}>): HomeBookListState {
  if (input.bookCount > 0) return "content";
  if (input.loadState === "loading") return "loading";
  if (input.loadState === "error") return "error";
  return input.readyKind === "empty" ? "empty" : "loading";
}

export function resolveHomeBookStatusAction(input: Readonly<{
  book: HomeBookModel;
  uploadedFile: UploadedCourseFile | null;
  parseJobId: string | null;
  parseJobStatus: JobStatusResponse | null;
}>): HomeBookStatusAction {
  if (input.book.status === "catalog") return "upload";
  if (input.book.status === "needs_review") return "chapterConfirm";

  const sessionMatchesBook = input.uploadedFile?.bookId === input.book.bookId;
  const jobIdentityMatchesBook = Boolean(
    sessionMatchesBook
    && input.parseJobId
    && (!input.parseJobStatus || input.parseJobStatus.book_id === input.book.bookId)
  );
  if ((input.book.status === "processing" || input.book.status === "error") && jobIdentityMatchesBook) {
    return "processing";
  }

  const hasTrueLocalUpload = Boolean(
    sessionMatchesBook && input.uploadedFile?.origin !== "remote-course"
  );
  if (input.book.status === "uploaded" && hasTrueLocalUpload) return "parseReady";
  return "library";
}

export function buildHomeBookModels({
  courses,
  uploadedFile,
  parseJobId,
  parseJobStatus,
  catalogBooks = []
}: BuildHomeBookModelsInput): HomeBookModel[] {
  const uniqueCourses: CourseSummary[] = [];
  const seenBookIds = new Set<string>();

  for (const course of courses) {
    if (!course.book_id || seenBookIds.has(course.book_id)) continue;
    seenBookIds.add(course.book_id);
    uniqueCourses.push(course);
  }

  const hasTrueLocalUpload = Boolean(uploadedFile && uploadedFile.origin !== "remote-course");
  if (uploadedFile && hasTrueLocalUpload && !seenBookIds.has(uploadedFile.bookId)) {
    uniqueCourses.push({
      book_id: uploadedFile.bookId,
      title: uploadedFile.name,
      filename: uploadedFile.name,
      status: "uploaded",
      page_count: 0,
      chapter_count: 0,
      chunk_count: 0,
      asset_count: 0,
      average_confidence: 0,
      updated_at: uploadedFile.uploadedAt / 1000
    });
  }

  const catalogByBookId = new Map(catalogBooks.map((book) => [book.bookId, book]));
  const courseModels = uniqueCourses.map((course) => {
    const isSessionBook = uploadedFile?.bookId === course.book_id;
    const localJobMatches = Boolean(
      isSessionBook
      && parseJobId
      && (!parseJobStatus || parseJobStatus.book_id === course.book_id)
    );
    let status = normalizeRemoteStatus(course);
    let progress = clampProgress(course.parse_job_progress);
    let errorMessage = course.parse_job_error ?? null;

    if (localJobMatches) {
      progress = clampProgress(parseJobStatus?.progress ?? course.parse_job_progress ?? 1);
      if (!parseJobStatus || parseJobStatus.status === "pending" || parseJobStatus.status === "processing") {
        status = "processing";
      } else if (parseJobStatus.status === "failed") {
        status = "error";
        errorMessage = parseJobStatus.error ?? parseJobStatus.message ?? errorMessage;
      }
    }

    const title = course.title || uploadedFile?.name || "未命名教材";
    return {
      bookId: course.book_id,
      title,
      filename: course.filename ?? (isSessionBook ? uploadedFile?.name ?? null : null),
      status,
      statusLabel: homeBookStatusLabel(status, progress),
      pageCount: Math.max(0, course.page_count),
      chapterCount: Math.max(0, course.chapter_count),
      progress,
      nextTitle: course.next_title ?? null,
      errorMessage,
      updatedAt: course.updated_at,
      coverVariant: stableBookCoverVariant(course.book_id, title),
      coverUrl: catalogByBookId.get(course.book_id)?.coverUrl ?? null
    };
  });

  const catalogModels = catalogBooks
    .filter((book) => !seenBookIds.has(book.bookId))
    .map((book): HomeBookModel => ({
      bookId: book.bookId,
      title: book.title,
      filename: book.filename,
      status: "catalog",
      statusLabel: homeBookStatusLabel("catalog"),
      pageCount: 0,
      chapterCount: 0,
      progress: 0,
      nextTitle: null,
      errorMessage: null,
      updatedAt: 0,
      coverVariant: stableBookCoverVariant(book.bookId, book.title),
      coverUrl: book.coverUrl
    }));

  if (catalogBooks.length === 0) return courseModels;

  const courseModelById = new Map(courseModels.map((book) => [book.bookId, book]));
  const catalogModelById = new Map(catalogModels.map((book) => [book.bookId, book]));
  const catalogIds = new Set(catalogBooks.map((book) => book.bookId));
  const shelfOrderedModels = catalogBooks.flatMap((book) => {
    const model = courseModelById.get(book.bookId) ?? catalogModelById.get(book.bookId);
    return model ? [model] : [];
  });
  const uncatalogedCourseModels = courseModels.filter((book) => !catalogIds.has(book.bookId));

  return [...shelfOrderedModels, ...uncatalogedCourseModels];
}

export function resolveHomeBookSelection(
  books: readonly HomeBookModel[],
  selectedBookId: string | null,
  loadedBookId: string | null
): string | null {
  if (selectedBookId && books.some((book) => book.bookId === selectedBookId)) {
    return selectedBookId;
  }
  if (loadedBookId && books.some((book) => book.bookId === loadedBookId)) {
    return loadedBookId;
  }
  return books.find((book) => book.status === "ready")?.bookId ?? books[0]?.bookId ?? null;
}

export type CourseSelectionRunOptions<Payload> = Readonly<{
  load: () => Promise<Payload>;
  commit: (payload: Payload) => void;
  onLatestError: (error: unknown) => void;
  onPendingChange: (bookId: string | null) => void;
}>;

export type CourseSelectionCoordinator = Readonly<{
  run: <Payload>(bookId: string, options: CourseSelectionRunOptions<Payload>) => Promise<boolean>;
  invalidate: () => void;
}>;

export function createCourseSelectionCoordinator(): CourseSelectionCoordinator {
  let latestGeneration = 0;
  return {
    async run(bookId, options) {
      const generation = ++latestGeneration;
      options.onPendingChange(bookId);
      try {
        const payload = await options.load();
        if (generation !== latestGeneration) return false;
        options.commit(payload);
        return true;
      } catch (error) {
        if (generation !== latestGeneration) return false;
        options.onLatestError(error);
        return false;
      } finally {
        if (generation === latestGeneration) options.onPendingChange(null);
      }
    },
    invalidate() {
      latestGeneration += 1;
    }
  };
}
