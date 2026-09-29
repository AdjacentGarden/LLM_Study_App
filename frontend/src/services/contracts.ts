import type { BookCatalogItem, BookStructure, BookStatus, Course, CourseActivity, InterviewResponse, LibraryResource, QAResult, UploadResponse, LearningRecords, UserProfile } from "../types/api";
import type { MistakeReviewStatus, StudyMistake, StudyTask, StudyTaskStatus, StudyWorkspace } from "../types/studyWorkspace";

export interface SourcePage {
  book_id: string;
  page_number: number;
  page_count: number;
  text: string | null;
  image_url: string | null;
  printed_page_number: string | null;
}

/** The new UI knows only this app's authenticated backend contract. */
export interface StudyRepository {
  library: {
    list(): Promise<BookCatalogItem[]>;
    structure(bookId: string): Promise<BookStructure>;
    remove(bookId: string): Promise<void>;
    restore(bookId: string): Promise<void>;
    upload(file: File): Promise<UploadResponse>;
    bind(bookId: string): Promise<void>;
    process(bookId: string, retry?: boolean): Promise<BookStatus>;
    status(bookId: string): Promise<BookStatus>;
    buildStructure(bookId: string): Promise<BookStructure>;
    claim(bookId: string): Promise<BookCatalogItem>;
    diagnostics(bookId: string): Promise<boolean>;
    page(bookId: string, page: number): Promise<SourcePage>;
    resources(): Promise<LibraryResource[]>;
    saveNote(data: { book_id: string; title: string; body: string; resource_id?: string }): Promise<LibraryResource>;
  };
  learning: {
    start(bookId: string, userId: string): Promise<InterviewResponse>;
    resume(sessionId: string): Promise<InterviewResponse>;
    profileAnswer(sessionId: string, answer: string, selected: string[]): Promise<InterviewResponse>;
    next(sessionId: string): Promise<InterviewResponse>;
    respond(sessionId: string, body: { item_id: string; answer: string; selected_option_ids: string[]; confidence: number; response_seconds: number; hints_used: number; revisions: number }): ReturnType<typeof import("../api/client").api.respond>;
    confirm(sessionId: string, confirmed: boolean): Promise<InterviewResponse>;
    course(sessionId: string, chapterId: string): Promise<Course>;
    getCourse(sessionId: string, chapterId: string): Promise<Course>;
    practice(sessionId: string, courseId: string, itemId: string, body: object): Promise<CourseActivity>;
    review(sessionId: string, courseId: string, cardId: string, body: object): Promise<CourseActivity>;
    records(sessionId: string): Promise<LearningRecords>;
    workspace(sessionId: string): Promise<StudyWorkspace>;
    setTask(sessionId: string, taskId: string, status: StudyTaskStatus): Promise<StudyTask>;
    setMistake(sessionId: string, mistakeId: string, status: MistakeReviewStatus, reason?: string | null): Promise<StudyMistake>;
  };
  qa: { ask(bookId: string, question: string): Promise<QAResult> };
  profile: { get(): Promise<UserProfile> };
}
