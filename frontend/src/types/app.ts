import type { ChapterEvidence } from "./api";

export type Screen =
  | "home"
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
  | "source"
  | "export"
  | "report"
  | "profile";

export type SheetState =
  | { type: "chat" }
  | { type: "source"; title: string; image: string; page: string }
  | {
      type: "note";
      concept: string;
      explanation?: string;
      sourceLabel?: string;
      source?: SourcePageTarget;
      image?: string;
      imageCaption?: string;
    }
  | { type: "editChapter"; chapterId: string; evidence?: ChapterEvidence }
  | { type: "bookSwitcher" }
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
  bookId: string;
  title: string;
  pageStart: number;
  pageEnd?: number | null;
  printedPageStart?: number | null;
  printedPageEnd?: number | null;
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
  go: (screen: Screen) => void;
  back: () => void;
  openSourcePage: (target: SourcePageTarget) => void;
  openSheet: (sheet: SheetState) => void;
  closeSheet: () => void;
  showToast: (text: string, tone?: ToastTone) => void;
  selectCourse: (bookId: string) => Promise<boolean>;
  updateStudyLocation: (bookId: string, location: Partial<StudyLocation>) => void;
};
