import { createContext, useContext, type ReactNode } from "react";
import type {
  ApiAsset,
  ApiChapter,
  ApiChunk,
  CourseSourceSummary,
  DiagnosisResponse,
  Flashcard,
  JobStatusResponse,
  Lesson,
  LessonBuildJobResponse,
  QuizQuestion,
  ScanResult,
  StudyPlan,
} from "../types/api";
import type { AppActions, SourcePageTarget, StudyLocation, UploadedCourseFile } from "../types/app";
import type { NoteCaptureIntent } from "../features/studyNotes/types";
import type { CourseController } from "../features/courses/repository";

export type SourceSummariesLoadState = "loading" | "ready" | "error";
export type SourceSummariesReadyKind = "content" | "empty";

export type AppContextValue = AppActions & {
  courses: CourseController;
  /** Enables local display-only shelf metadata in the bundled demo app. */
  demoShelfEnabled?: boolean;
  selectedUpload: boolean;
  setSelectedUpload: (value: boolean) => void;
  uploadedFile: UploadedCourseFile | null;
  setUploadedFile: (value: UploadedCourseFile | null) => void;
  parseJobId: string | null;
  setParseJobId: (value: string | null) => void;
  parseJobStatus: JobStatusResponse | null;
  setParseJobStatus: (value: JobStatusResponse | null) => void;
  sourceSummaries: CourseSourceSummary[];
  sourceSummariesLoadState: SourceSummariesLoadState;
  sourceSummariesReadyKind: SourceSummariesReadyKind;
  sourceSummariesError: string | null;
  sourceSummariesRefreshing: boolean;
  loadedBookId: string | null;
  clearLoadedCourse: (expectedBookId?: string) => boolean;
  clearCourseSession: (expectedBookId?: string) => boolean;
  pendingBookId: string | null;
  sourceSelectionLoadingId: string | null;
  selectedCommunityBookId: string;
  selectCommunityBook: (bookId: string) => void;
  cancelSourceSelection: () => void;
  refreshSources: () => Promise<void>;
  parsedScanResult: ScanResult | null;
  setParsedScanResult: (value: ScanResult | null) => void;
  parsedChapters: ApiChapter[] | null;
  setParsedChapters: (value: ApiChapter[] | null) => void;
  parsedChunks: ApiChunk[] | null;
  setParsedChunks: (value: ApiChunk[] | null) => void;
  parsedAssets: ApiAsset[] | null;
  setParsedAssets: (value: ApiAsset[] | null) => void;
  generatedLessons: Lesson[] | null;
  setGeneratedLessons: (value: Lesson[] | null) => void;
  lessonBuildJobId: string | null;
  setLessonBuildJobId: (value: string | null) => void;
  lessonBuildJobStatus: LessonBuildJobResponse | null;
  setLessonBuildJobStatus: (value: LessonBuildJobResponse | null) => void;
  generatedFlashcards: Flashcard[] | null;
  setGeneratedFlashcards: (value: Flashcard[] | null) => void;
  generatedQuizzes: QuizQuestion[] | null;
  setGeneratedQuizzes: (value: QuizQuestion[] | null) => void;
  activeChapterId: string | null;
  setActiveChapterId: (value: string | null) => void;
  currentStudyPlan: StudyPlan | null;
  setCurrentStudyPlan: (value: StudyPlan | null) => void;
  latestDiagnosis: DiagnosisResponse | null;
  setLatestDiagnosis: (value: DiagnosisResponse | null) => void;
  answer: string;
  setAnswer: (value: string) => void;
  savedNoteCount: number;
  setSavedNoteCount: (fn: (count: number) => number) => void;
  sourcePageTarget: SourcePageTarget | null;
  sourceReaderCurrentPage: number;
  setSourceReaderCurrentPage: (page: number) => void;
  noteCaptureIntent: NoteCaptureIntent | null;
  studyLocations: Record<string, StudyLocation>;
};

const AppContext = createContext<AppContextValue | null>(null);

export function AppProvider({ value, children }: { value: AppContextValue; children: ReactNode }) {
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useAppContext(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) {
    throw new Error("useAppContext must be used within an AppProvider");
  }
  return ctx;
}
