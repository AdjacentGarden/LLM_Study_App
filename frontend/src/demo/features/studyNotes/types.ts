import type { Screen, SourcePageTarget } from "../../types/app";
import type { Citation } from "../../types/api";

export type NoteKind = "text" | "ink" | "voice";

export type NoteAnchor = {
  courseId?: string;
  bookId: string;
  bookTitle?: string;
  chapterId?: string;
  chapterTitle?: string;
  pageStart?: number;
  pageEnd?: number;
  printedPageStart?: number;
  printedPageEnd?: number;
  quote?: string;
  sourceText?: string;
};

export type NoteCaptureIntent = {
  kind: NoteKind;
  anchor?: NoteAnchor;
  source?: SourcePageTarget;
  existingNoteId?: string;
  from?: Screen;
};

export type NotePipelinePhase =
  | "idle"
  | "saving"
  | "recognizing"
  | "transcribing"
  | "checking"
  | "needs_confirmation"
  | "retrieving"
  | "organizing"
  | "reviewing"
  | "complete"
  | "error";

export type NoteEvidence = {
  label: string;
  excerpt: string;
  page: number;
};

export type InkTool = "pen" | "highlighter";

export type InkPoint = {
  x: number;
  y: number;
  pressure: number;
  t: number;
};

export type InkStroke = {
  id: string;
  tool: InkTool;
  color: string;
  width: number;
  opacity: number;
  points: InkPoint[];
};

export type OrganizedNoteVersion = {
  noteVersion: number;
  text: string;
  createdAt: number;
};

type StudyNoteBase = {
  id: string;
  kind: NoteKind;
  title: string;
  anchor?: NoteAnchor;
  createdAt: number;
  updatedAt: number;
  noteVersion: number;
  pipelinePhase: NotePipelinePhase;
  recognizedText?: string;
  uncertain?: string[];
  organizedText?: string;
  organizedFromVersion?: number;
  organizedVersions?: OrganizedNoteVersion[];
  evidence?: NoteEvidence[];
  fixtureId?: string;
  error?: string;
};

export type TextStudyNote = StudyNoteBase & {
  kind: "text";
  body: string;
  /** Relative to the original page, so the marker survives resizing. */
  position?: TextNotePosition;
  conversation?: TextNoteMessage[];
};

export type TextNotePosition = { x: number; y: number };

export type TextNoteMessage = {
  role: "user" | "assistant";
  text: string;
  citations?: Citation[];
  noReliableSource?: boolean;
};

export type InkStudyNote = StudyNoteBase & {
  kind: "ink";
  pages: Record<string, InkStroke[]>;
};

export type VoiceStudyNote = StudyNoteBase & {
  kind: "voice";
  audioId?: string;
  mimeType?: string;
  durationMs: number;
  sizeBytes: number;
  waveform: number[];
  transcript?: string;
  confirmedTranscript?: string;
};

export type StudyNote = TextStudyNote | InkStudyNote | VoiceStudyNote;

export function noteAnchorFromSource(source: SourcePageTarget, quote?: string): NoteAnchor {
  return {
    courseId: source.courseId,
    bookId: source.bookId,
    bookTitle: source.title,
    chapterTitle: source.title,
    pageStart: source.pageStart,
    pageEnd: source.pageEnd ?? source.pageStart,
    printedPageStart: source.printedPageStart ?? undefined,
    printedPageEnd: source.printedPageEnd ?? undefined,
    quote,
    sourceText: source.sourceText ?? undefined
  };
}

export function noteKindLabel(kind: NoteKind) {
  if (kind === "ink") return "手写";
  if (kind === "voice") return "语音";
  return "文字";
}
