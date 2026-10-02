import type { ChapterEvidence } from "./api";
import type { NoteCaptureIntent } from "../features/studyNotes/types";

export type Screen =
  | "home"
  | "onboarding"
  | "courseSetup"
  | "courseDetail"
  | "upload"
  | "parseReady"
  | "processing"
  | "chapterConfirm"
  | "courseReady"
  | "library"
  | "community"
  | "communityBook"
  | "communityImport"
  | "study"
  | "book"
  | "plan"
  | "flashcards"
  | "lesson"
  | "assignment"
  | "diagnosis"
  | "mistakes"
  | "notes"
  | "voiceNote"
  | "source"
  | "export"
  | "report"
  | "profile"
  | "settings";

export type SheetState =
  | { type: "chat" }
  | {
      type: "note";
      concept: string;
      kind?: "concept" | "selection";
      quote?: string;
      explanation?: string;
      sourceLabel?: string;
      source?: SourcePageTarget;
      image?: string;
      imageCaption?: string;
    }
  | {
      type: "noteType";
      intent: Omit<NoteCaptureIntent, "kind">;
      contextLabel?: string;
      inkAvailable?: boolean;
      pageOptions?: Array<{ page: number; label: string }>;
    }
  | { type: "editChapter"; chapterId: string; evidence?: ChapterEvidence }
  | { type: "bookSwitcher" }
  | { type: "addMaterials"; courseId: string }
  | null;

export type ToastTone = "success" | "info" | "warning";

export type ToastMessage = {
  id: number;
  text: string;
  tone: ToastTone;
};

export type UploadedCourseFile = {
  bookId: string;
  name: string;
  sizeBytes: number;
  contentType: string;
  uploadedAt: number;
  /**
   * Missing values are treated as local uploads for compatibility with
   * persisted sessions created before this discriminator was introduced.
   */
  origin?: "local-upload" | "remote-course";
};

export type SourcePageTarget = {
  courseId?: string;
  bookId: string;
  title: string;
  pageStart: number;
  pageEnd?: number | null;
  printedPageStart?: number | null;
  printedPageEnd?: number | null;
  /**
   * Locally bundled citation-chunk text. A verified published page image is the
   * primary source-reader view; this controlled text remains the offline
   * fallback if that image is unavailable.
   */
  sourceText?: string | null;
  /** A supplied citation preview when no published textbook page is available. */
  previewImageUrl?: string;
  from?: Screen | null;
};

export type StudyLocation = {
  expandedChapterId: string | null;
  expandedSectionId: string | null;
};

export type ChapterStatus = "匹配良好" | "需检查";

export type Chapter = {
  id: string;
  sourceTitle: string;
  aiTitle: string;
  pages: string;
  confidence: number;
  status: ChapterStatus;
  progress: number;
  duration: string;
  concepts: string[];
};

export type AppActions = {
  logout: () => Promise<void>;
  go: (screen: Screen) => void;
  replaceScreen: (screen: Screen) => void;
  back: () => void;
  openSourcePage: (target: SourcePageTarget) => void;
  startNote: (intent: NoteCaptureIntent) => void;
  finishNoteCapture: () => void;
  openSheet: (sheet: SheetState) => void;
  closeSheet: () => void;
  showToast: (text: string, tone?: ToastTone) => void;
  selectSource: (bookId: string, courseId?: string) => Promise<boolean>;
  selectCourse: (courseId: string) => Promise<boolean>;
  importCourseFile: (file: File, courseId?: string) => Promise<void>;
  updateStudyLocation: (bookId: string, location: Partial<StudyLocation>) => void;
};
