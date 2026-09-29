import type {
  ApiAsset,
  ApiChapter,
  ApiChunk,
  CourseSummary,
  Flashcard,
  Lesson,
  QuizQuestion,
  ScanResult,
  StudyPlan
} from "../types/api";
import type { UploadedCourseFile } from "../types/app";

export type LoadedCourseContext = {
  loadedBookId: string | null;
  uploadedFile: UploadedCourseFile | null;
  parsedScanResult: ScanResult | null;
  parsedChapters: ApiChapter[] | null;
  parsedChunks: ApiChunk[] | null;
  parsedAssets: ApiAsset[] | null;
  currentStudyPlan: StudyPlan | null;
  generatedLessons: Lesson[] | null;
  generatedFlashcards: Flashcard[] | null;
  generatedQuizzes: QuizQuestion[] | null;
};

function everyResourceBelongsToBook(
  resources: ReadonlyArray<{ book_id: string }>,
  bookId: string
) {
  return resources.every((resource) => resource.book_id === bookId);
}

/**
 * A loaded id is usable only when the complete resource snapshot is present
 * and every resource that carries identity belongs to the same book. Empty
 * optional collections are valid; a parsed directory is the minimum structure.
 */
export function hasCompleteLoadedCourseContext(
  context: LoadedCourseContext,
  expectedBookId: string | null = context.loadedBookId
) {
  if (
    !expectedBookId
    || context.loadedBookId !== expectedBookId
    || context.uploadedFile?.bookId !== expectedBookId
    || context.parsedScanResult?.book_id !== expectedBookId
    || !context.parsedChapters?.length
    || context.parsedChunks === null
    || context.parsedAssets === null
    || context.currentStudyPlan?.book_id !== expectedBookId
    || context.generatedLessons === null
    || context.generatedFlashcards === null
    || context.generatedQuizzes === null
  ) return false;

  return everyResourceBelongsToBook(context.parsedChunks, expectedBookId)
    && everyResourceBelongsToBook(context.parsedAssets, expectedBookId)
    && everyResourceBelongsToBook(context.generatedLessons, expectedBookId)
    && everyResourceBelongsToBook(context.generatedFlashcards, expectedBookId)
    && everyResourceBelongsToBook(context.generatedQuizzes, expectedBookId);
}

function isUnsyncedLocalUpload(uploadedFile: UploadedCourseFile | null, bookId: string) {
  return uploadedFile?.bookId === bookId && uploadedFile.origin !== "remote-course";
}

export function shouldClearLoadedCourseAfterRefresh(
  loadedBookId: string | null,
  uploadedFile: UploadedCourseFile | null,
  summaries: CourseSummary[]
) {
  if (!loadedBookId || summaries.some((course) => course.book_id === loadedBookId)) return false;
  return !isUnsyncedLocalUpload(uploadedFile, loadedBookId);
}

export function shouldClearRemoteSessionAfterRefresh(
  uploadedFile: UploadedCourseFile | null,
  summaries: CourseSummary[]
) {
  return Boolean(
    uploadedFile
    && uploadedFile.origin === "remote-course"
    && !summaries.some((course) => course.book_id === uploadedFile.bookId)
  );
}

export function shouldClearLoadedCourseForDeletedBook(
  loadedBookId: string | null,
  deletedBookId: string
) {
  return loadedBookId === deletedBookId;
}

export function shouldClearCourseSessionForDeletedBook(
  uploadedFile: UploadedCourseFile | null,
  deletedBookId: string
) {
  return uploadedFile?.bookId === deletedBookId;
}

export function resolveCourseSessionClear(
  uploadedFile: UploadedCourseFile | null,
  expectedBookId: string | undefined,
  currentGeneration: number
) {
  const shouldClear = expectedBookId === undefined
    || shouldClearCourseSessionForDeletedBook(uploadedFile, expectedBookId);
  return {
    shouldClear,
    nextGeneration: shouldClear ? currentGeneration + 1 : currentGeneration
  };
}

export type ProgressOpenMode = "job" | "current-session" | "unavailable";

export function resolveProgressOpenMode(
  targetBookId: string,
  jobId: string | null | undefined,
  currentSessionBookId: string | null | undefined
): ProgressOpenMode {
  if (jobId) return "job";
  if (currentSessionBookId === targetBookId) return "current-session";
  return "unavailable";
}

export function resolveLatestCourseTitle(
  uploadedFile: UploadedCourseFile | null,
  summaries: CourseSummary[]
) {
  const uploadedIsCurrent = Boolean(
    uploadedFile
    && (
      uploadedFile.origin !== "remote-course"
      || summaries.some((course) => course.book_id === uploadedFile.bookId)
    )
  );
  return uploadedIsCurrent ? uploadedFile?.name : summaries[0]?.title;
}
