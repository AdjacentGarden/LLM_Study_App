import { createContext, useContext, type ReactNode } from "react";
import type {
  ApiAsset,
  ApiChapter,
  ApiChunk,
  CourseSummary,
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

export type CourseSummariesLoadState = "loading" | "ready" | "error";
export type CourseSummariesReadyKind = "content" | "empty";

export type AppContextValue = AppActions & {
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
  courseSummaries: CourseSummary[];
  courseSummariesLoadState: CourseSummariesLoadState;
  courseSummariesReadyKind: CourseSummariesReadyKind;
  courseSummariesError: string | null;
  courseSummariesRefreshing: boolean;
  loadedBookId: string | null;
  clearLoadedCourse: (expectedBookId?: string) => boolean;
  clearCourseSession: (expectedBookId?: string) => boolean;
  pendingBookId: string | null;
  courseSelectionLoadingId: string | null;
  selectedCommunityBookId: string;
  selectCommunityBook: (bookId: string) => void;
  cancelCourseSelection: () => void;
  refreshCourses: () => Promise<void>;
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
