import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { HomeBookCarousel } from "../src/components/home/HomeBookCarousel";
import { SelectedBookWorkspace } from "../src/components/home/SelectedBookWorkspace";
import {
  buildHomeBookModels,
  canOpenHomeBookOriginal,
  resolveHomeBookStatusAction,
  type HomeBookModel,
  type HomeBookStatusAction
} from "../src/screens/homeBookModel";
import { resolveHomeNextStep } from "../src/screens/homeNextStep";
import {
  hasCompleteLoadedCourseContext,
  shouldClearCourseSessionForDeletedBook,
  shouldClearLoadedCourseAfterRefresh,
  shouldClearLoadedCourseForDeletedBook,
  shouldClearRemoteSessionAfterRefresh,
  type LoadedCourseContext
} from "../src/screens/courseResourceIdentity";
import type { ApiChapter, CourseSummary, JobStatusResponse, ScanResult, StudyPlan } from "../src/types/api";
import type { UploadedCourseFile } from "../src/types/app";
import "../src/styles/tokens.css";
import "../src/styles/base.css";
import "../src/styles/home.css";
import "../src/styles/chapter-tools.css";

type HarnessMode = "empty" | "single" | "two" | "many";
type HarnessAction = { destination: "lesson" | "source" | "assignment" | "flashcards"; bookId: string; chapterId: string };
type StatusAction = { bookId: string; target: HomeBookStatusAction | "upload" | "source" };

type HomeBookCarouselHarness = {
  applySuccessfulRefresh: (bookIds: string[]) => void;
  clearLoadedBook: () => void;
  deleteBook: (bookId: string) => void;
  failNextSelection: (bookId: string) => void;
  getLoadRequests: () => string[];
  getStatusActions: () => StatusAction[];
  getWorkspaceActions: () => HarnessAction[];
  setResourceIdentity: (loadedBookId: string | null, sessionBookId: string | null, resourceBookId: string | null) => void;
  setMode: (mode: HarnessMode) => void;
};

declare global {
  interface Window {
    __homeBookCarouselHarness?: HomeBookCarouselHarness;
  }
}

function summary(
  bookId: string,
  title: string,
  status: string,
  overrides: Partial<CourseSummary> = {}
): CourseSummary {
  return {
    book_id: bookId,
    title,
    filename: `${bookId}.pdf`,
    status,
    page_count: 0,
    chapter_count: 0,
    chunk_count: 0,
    asset_count: 0,
    average_confidence: .9,
    updated_at: 1,
    ...overrides
  };
}

const courseSummaries: CourseSummary[] = [
  summary("book-a", "高中生物 必修二 遗传与进化", "ready", { page_count: 128, chapter_count: 7 }),
  summary("book-b", "这是一本用于验证窄屏省略和稳定换行的超长高中数学教材标题", "ready", { page_count: 146, chapter_count: 12 }),
  summary("book-c", "高中化学 必修一", "processing", { parse_job_status: "processing", parse_job_progress: 58 }),
  summary("book-d", "世界历史纲要", "backend_migrating"),
  summary("book-e", "物理选修课程", "needs_review", { page_count: 88, chapter_count: 9 }),
  summary("book-f", "本地上传的地理教材", "uploaded"),
  summary("book-g", "远端解析失败的语文教材", "error", {
    parse_job_status: "failed",
    parse_job_error: "文件页面无法读取，请检查文件后重试。"
  }),
  summary("book-h", "有可读页但远端任务失败的超长历史教材标题，用于验证双次级操作不会挤出手机屏幕", "error", {
    page_count: 9,
    parse_job_status: "failed",
    parse_job_error: "解析服务返回了一段很长的错误说明：原文件仍然可以查看，但当前远端任务无法继续，请重新上传后再试。"
  }),
  summary("book-i", "本地解析失败的政治教材", "error", {
    parse_job_status: "failed",
    parse_job_error: "本地任务没有完成，可以重新整理。"
  }),
  summary("book-j", "仅存在远端摘要的已上传教材", "uploaded")
];

const books: HomeBookModel[] = buildHomeBookModels({
  courses: courseSummaries,
  uploadedFile: null,
  parseJobId: null,
  parseJobStatus: null,
  loadedBookId: null,
  loadedChapterCount: 0
});

function chaptersFor(bookId: string): ApiChapter[] {
  const chapterId = `chapter-${bookId}`;
  return [
    {
      chapter_id: `root-${bookId}`,
      level: 1,
      source_title: "第 1 章 学习目录",
      ai_title: "学习目录",
      page_start: 10,
      page_end: 22,
      confidence: 1,
      status: "匹配良好",
      source: "harness",
      parent_id: null
    },
    {
      chapter_id: chapterId,
      level: 2,
      source_title: bookId === "book-a" ? "第 1 节 减数分裂和受精作用" : "第 1 节 函数的单调性",
      ai_title: "本章课程",
      page_start: 11,
      page_end: 17,
      printed_page_start: 16,
      printed_page_end: 22,
      confidence: 1,
      status: "匹配良好",
      source: "harness",
      parent_id: `root-${bookId}`
    }
  ];
}

function booksForMode(mode: HarnessMode): HomeBookModel[] {
  if (mode === "empty") return [];
  if (mode === "single") return books.slice(0, 1);
  if (mode === "two") return books.slice(0, 2);
  return books;
}

function HarnessView() {
  const [mode, setModeState] = useState<HarnessMode>("many");
  const [selectedBookId, setSelectedBookId] = useState<string | null>("book-a");
  const [loadedBookId, setLoadedBookId] = useState<string | null>("book-a");
  const [sessionBookId, setSessionBookId] = useState<string | null>("book-a");
  const [resourceBookId, setResourceBookId] = useState<string | null>("book-a");
  const [pendingBookId, setPendingBookId] = useState<string | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const failureBookIdRef = useRef<string | null>(null);
  const lastFailedSelectionRef = useRef<string | null>(null);
  const requestGenerationRef = useRef(0);
  const loadRequestsRef = useRef<string[]>([]);
  const statusActionsRef = useRef<StatusAction[]>([]);
  const workspaceActionsRef = useRef<HarnessAction[]>([]);
  const visibleBooks = booksForMode(mode);
  const selectedBook = visibleBooks.find((book) => book.bookId === selectedBookId) ?? null;
  const resourceChapters = resourceBookId ? chaptersFor(resourceBookId) : null;
  const loadedContext: LoadedCourseContext = {
    loadedBookId,
    uploadedFile: sessionBookId ? {
      bookId: sessionBookId,
      name: `${sessionBookId}.pdf`,
      sizeBytes: 1,
      contentType: "application/pdf",
      uploadedAt: 1,
      origin: "remote-course"
    } : null,
    parsedScanResult: resourceBookId ? { book_id: resourceBookId } as ScanResult : null,
    parsedChapters: resourceChapters,
    parsedChunks: resourceBookId ? [] : null,
    parsedAssets: resourceBookId ? [] : null,
    currentStudyPlan: resourceBookId ? { book_id: resourceBookId } as StudyPlan : null,
    generatedLessons: resourceBookId ? [] : null,
    generatedFlashcards: resourceBookId ? [] : null,
    generatedQuizzes: resourceBookId ? [] : null
  };
  const selectedHasLocalSession = selectedBook?.bookId === "book-f" || selectedBook?.bookId === "book-i";
  const selectedHasRemoteJobSession = selectedBook?.bookId === "book-c";
  const selectedUploadSession: UploadedCourseFile | null = selectedHasLocalSession || selectedHasRemoteJobSession
    ? {
        bookId: selectedBook?.bookId ?? "",
        name: `${selectedBook?.bookId}.pdf`,
        sizeBytes: 1,
        contentType: "application/pdf",
        uploadedAt: 1,
        origin: selectedHasLocalSession ? "local-upload" : "remote-course"
      }
    : loadedContext.uploadedFile;
  const selectedParseJobId = selectedBook?.bookId === "book-c" || selectedBook?.bookId === "book-i"
    ? `job-${selectedBook.bookId}`
    : null;
  const selectedParseJobStatus: JobStatusResponse | null = selectedBook?.bookId === "book-i"
    ? {
        job_id: "job-book-i",
        book_id: "book-i",
        status: "failed",
        stage: "parse",
        progress: 31,
        message: "本地解析失败",
        error: "本地任务没有完成，可以重新整理。"
      }
    : null;
  const hasLocalUploadSession = Boolean(
    selectedBook
    && selectedUploadSession?.bookId === selectedBook.bookId
    && selectedUploadSession.origin !== "remote-course"
  );
  const canOpenOriginal = Boolean(
    selectedBook && canOpenHomeBookOriginal(selectedBook, selectedUploadSession)
  );
  const canRenderLoadedResources = hasCompleteLoadedCourseContext(loadedContext);
  const nextStep = canRenderLoadedResources && selectedBook?.bookId === loadedBookId && loadedBookId
    ? resolveHomeNextStep({
        chapters: resourceChapters ?? [],
        location: {
          expandedChapterId: `root-${loadedBookId}`,
          expandedSectionId: `chapter-${loadedBookId}`
        },
        plan: loadedContext.currentStudyPlan,
        lessons: loadedContext.generatedLessons
      })
    : null;

  function recordWorkspaceAction(destination: HarnessAction["destination"]) {
    if (!loadedBookId || !nextStep) return;
    workspaceActionsRef.current.push({
      destination,
      bookId: loadedBookId,
      chapterId: nextStep.chapter.chapter_id
    });
  }

  function recordStatusAction(book: HomeBookModel) {
    statusActionsRef.current.push({
      bookId: book.bookId,
      target: resolveHomeBookStatusAction({
        book,
        uploadedFile: selectedUploadSession,
        parseJobId: selectedParseJobId,
        parseJobStatus: selectedParseJobStatus
      })
    });
  }

  function setMode(nextMode: HarnessMode) {
    requestGenerationRef.current += 1;
    const nextBooks = booksForMode(nextMode);
    setModeState(nextMode);
    setSelectedBookId(nextBooks[0]?.bookId ?? null);
    setLoadedBookId(nextBooks[0]?.status === "ready" ? nextBooks[0].bookId : null);
    setSessionBookId(nextBooks[0]?.status === "ready" ? nextBooks[0].bookId : null);
    setResourceBookId(nextBooks[0]?.status === "ready" ? nextBooks[0].bookId : null);
    setPendingBookId(null);
    setSelectionError(null);
    lastFailedSelectionRef.current = null;
  }

  function selectBook(bookId: string) {
    const nextBook = visibleBooks.find((book) => book.bookId === bookId);
    if (!nextBook) return;
    setSelectedBookId(bookId);
    setSelectionError(null);
    if (nextBook.status !== "ready" || bookId === loadedBookId) {
      requestGenerationRef.current += 1;
      setPendingBookId(null);
      return;
    }

    const generation = ++requestGenerationRef.current;
    const previousLoadedBookId = loadedBookId;
    loadRequestsRef.current.push(bookId);
    setPendingBookId(bookId);
    window.setTimeout(() => {
      if (generation !== requestGenerationRef.current) return;
      setPendingBookId(null);
      if (failureBookIdRef.current === bookId) {
        failureBookIdRef.current = null;
        lastFailedSelectionRef.current = bookId;
        if (previousLoadedBookId) {
          setSelectedBookId(previousLoadedBookId);
          setSelectionError(`未能打开《${nextBook.title}》，已回到上一本教材。`);
        } else {
          setSelectionError(`未能打开《${nextBook.title}》。请检查连接后重试。`);
        }
        return;
      }
      lastFailedSelectionRef.current = null;
      setLoadedBookId(bookId);
      setSessionBookId(bookId);
      setResourceBookId(bookId);
    }, 280);
  }

  window.__homeBookCarouselHarness = {
    applySuccessfulRefresh(bookIds) {
      const summaries = bookIds.map((bookId) => ({ book_id: bookId })) as CourseSummary[];
      const activeSession = sessionBookId ? loadedContext.uploadedFile : null;
      if (shouldClearLoadedCourseAfterRefresh(loadedBookId, activeSession, summaries)) {
        setLoadedBookId(null);
        setResourceBookId(null);
      }
      if (shouldClearRemoteSessionAfterRefresh(activeSession, summaries)) setSessionBookId(null);
    },
    clearLoadedBook() {
      requestGenerationRef.current += 1;
      setLoadedBookId(null);
      setPendingBookId(null);
      setSelectionError(null);
    },
    deleteBook(bookId) {
      if (shouldClearLoadedCourseForDeletedBook(loadedBookId, bookId)) {
        setLoadedBookId(null);
        setResourceBookId(null);
      }
      if (shouldClearCourseSessionForDeletedBook(loadedContext.uploadedFile, bookId)) {
        setSessionBookId(null);
      }
    },
    failNextSelection(bookId) {
      failureBookIdRef.current = bookId;
    },
    getLoadRequests() {
      return [...loadRequestsRef.current];
    },
    getStatusActions() {
      return [...statusActionsRef.current];
    },
    getWorkspaceActions() {
      return [...workspaceActionsRef.current];
    },
    setResourceIdentity(nextLoadedBookId, nextSessionBookId, nextResourceBookId) {
      requestGenerationRef.current += 1;
      setLoadedBookId(nextLoadedBookId);
      setSessionBookId(nextSessionBookId);
      setResourceBookId(nextResourceBookId);
      setPendingBookId(null);
    },
    setMode
  };

  return (
    <main id="home-book-carousel-harness">
      <HomeBookCarousel
        books={visibleBooks}
        selectedBookId={selectedBookId}
        listState={visibleBooks.length > 0 ? "content" : "empty"}
        onSelectBook={selectBook}
        onAddBook={() => undefined}
        onOpenLibrary={() => undefined}
      />
      <SelectedBookWorkspace
        book={selectedBook}
        canOpenOriginal={canOpenOriginal}
        hasLocalUploadSession={hasLocalUploadSession}
        listState={visibleBooks.length > 0 ? "content" : "empty"}
        loadedBookId={loadedBookId}
        pendingBookId={pendingBookId}
        selectionError={selectionError}
        nextStep={nextStep}
        onContinue={() => recordWorkspaceAction("lesson")}
        onOpenOriginal={() => {
          if (selectedBook) statusActionsRef.current.push({ bookId: selectedBook.bookId, target: "source" });
        }}
        onOpenSource={() => recordWorkspaceAction("source")}
        onRestart={() => {
          if (selectedBook) statusActionsRef.current.push({ bookId: selectedBook.bookId, target: "parseReady" });
        }}
        onRetrySelection={() => {
          const failedBookId = lastFailedSelectionRef.current;
          if (failedBookId) selectBook(failedBookId);
        }}
        onSelectTool={recordWorkspaceAction}
        onViewStatus={recordStatusAction}
        onUpload={() => {
          if (selectedBook) statusActionsRef.current.push({ bookId: selectedBook.bookId, target: "upload" });
        }}
      />
      <section
        aria-label="教材资源身份门禁"
        data-loaded-book-id={loadedBookId ?? ""}
        data-session-book-id={sessionBookId ?? ""}
        data-resource-book-id={resourceBookId ?? ""}
        data-switcher-selected-book-id={canRenderLoadedResources ? loadedBookId ?? "" : ""}
        id="course-resource-identity-harness"
      >
        {canRenderLoadedResources ? (
          <div className="study-directory" data-book-id={loadedBookId}>教材目录 · {loadedBookId}</div>
        ) : (
          <div className="study-empty-state">教材还在准备中</div>
        )}
      </section>
    </main>
  );
}

const rootElement = document.getElementById("home-book-carousel-root");
if (!rootElement) throw new Error("Home book carousel harness root is missing.");
createRoot(rootElement).render(<HarnessView />);
