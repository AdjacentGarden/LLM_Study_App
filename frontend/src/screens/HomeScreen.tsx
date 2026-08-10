import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, CalendarDays, CircleAlert, Upload } from "lucide-react";
import { HomeBookCarousel } from "../components/home/HomeBookCarousel";
import { SelectedBookWorkspace } from "../components/home/SelectedBookWorkspace";
import type { ChapterToolId } from "../components/study/ChapterToolCards";
import { useAppContext } from "../context/AppContext";
import { demoShelfBooks } from "../data/demoShelfBooks";
import { hasCompleteLoadedCourseContext } from "./courseResourceIdentity";
import {
  buildHomeBookModels,
  canOpenHomeBookOriginal,
  resolveHomeBookListState,
  resolveHomeBookSelection,
  resolveHomeBookStatusAction,
  type HomeBookModel
} from "./homeBookModel";
import { buildHomeGlobalActions, type HomeGlobalActionId } from "./homeGlobalActions";
import { resolveHomeNextStep } from "./homeNextStep";

function globalActionIcon(actionId: HomeGlobalActionId) {
  switch (actionId) {
    case "plan":
      return <CalendarDays size={20} aria-hidden="true" />;
    case "mistakes":
      return <CircleAlert size={20} aria-hidden="true" />;
    case "upload":
      return <Upload size={20} aria-hidden="true" />;
  }
}

export function HomeScreen() {
  const {
    cancelCourseSelection,
    courseSummaries,
    courseSummariesError,
    courseSummariesLoadState,
    courseSummariesReadyKind,
    courseSummariesRefreshing,
    currentStudyPlan,
    demoShelfEnabled,
    generatedFlashcards,
    generatedLessons,
    generatedQuizzes,
    go,
    loadedBookId,
    openSourcePage,
    parseJobId,
    parseJobStatus,
    parsedAssets,
    parsedChapters,
    parsedChunks,
    parsedScanResult,
    pendingBookId,
    refreshCourses,
    selectCourse,
    setActiveChapterId,
    studyLocations,
    updateStudyLocation,
    uploadedFile
  } = useAppContext();
  const books = useMemo(() => buildHomeBookModels({
    courses: courseSummaries,
    uploadedFile,
    parseJobId,
    parseJobStatus,
    loadedBookId,
    loadedChapterCount: loadedBookId === uploadedFile?.bookId ? parsedChapters?.length ?? 0 : 0,
    catalogBooks: demoShelfEnabled && courseSummariesLoadState === "ready"
      ? demoShelfBooks
      : undefined
  }), [
    courseSummaries,
    courseSummariesLoadState,
    demoShelfEnabled,
    loadedBookId,
    parseJobId,
    parseJobStatus,
    parsedChapters,
    uploadedFile
  ]);
  const [selectedBookId, setSelectedBookId] = useState<string | null>(null);
  const hasUserSelectedBookRef = useRef(false);
  const booksRef = useRef(books);
  const selectCourseRef = useRef(selectCourse);
  booksRef.current = books;
  selectCourseRef.current = selectCourse;
  const [failedSelectionBookId, setFailedSelectionBookId] = useState<string | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const listState = resolveHomeBookListState({
    bookCount: books.length,
    loadState: courseSummariesLoadState,
    readyKind: courseSummariesReadyKind
  });
  const selectedBook = books.find((book) => book.bookId === selectedBookId) ?? null;
  const hasLocalSelectedUpload = Boolean(
    selectedBook
    && uploadedFile?.bookId === selectedBook.bookId
    && uploadedFile.origin !== "remote-course"
  );
  const canOpenSelectedOriginal = Boolean(
    selectedBook && canOpenHomeBookOriginal(selectedBook, uploadedFile)
  );
  const selectedLoadedReady = Boolean(
    selectedBook?.status === "ready"
    && hasCompleteLoadedCourseContext({
      loadedBookId,
      uploadedFile,
      parsedScanResult,
      parsedChapters,
      parsedChunks,
      parsedAssets,
      currentStudyPlan,
      generatedLessons,
      generatedFlashcards,
      generatedQuizzes
    }, selectedBook.bookId)
  );
  const nextStep = useMemo(() => (
    selectedLoadedReady && loadedBookId
      ? resolveHomeNextStep({
          chapters: parsedChapters ?? [],
          location: studyLocations[loadedBookId],
          plan: currentStudyPlan,
          lessons: generatedLessons
        })
      : null
  ), [
    currentStudyPlan,
    generatedLessons,
    loadedBookId,
    parsedChapters,
    selectedLoadedReady,
    studyLocations
  ]);

  useEffect(() => {
    setSelectedBookId((current) => {
      if (
        !hasUserSelectedBookRef.current
        && loadedBookId
        && books.some((book) => book.bookId === loadedBookId)
      ) {
        return loadedBookId;
      }
      return resolveHomeBookSelection(books, current, loadedBookId);
    });
  }, [books, loadedBookId]);

  useEffect(() => {
    if (pendingBookId && !books.some((book) => book.bookId === pendingBookId)) {
      cancelCourseSelection();
    }
  }, [books, cancelCourseSelection, pendingBookId]);

  useEffect(() => {
    if (failedSelectionBookId && !books.some((book) => book.bookId === failedSelectionBookId)) {
      setFailedSelectionBookId(null);
      setSelectionError(null);
    }
  }, [books, failedSelectionBookId]);

  useEffect(() => {
    if (
      !selectedBook
      || selectedBook.status !== "ready"
      || selectedBook.bookId === loadedBookId
      || selectedBook.bookId === failedSelectionBookId
    ) return;

    let active = true;
    const candidateBookId = selectedBook.bookId;
    const previousLoadedBookId = loadedBookId;
    void selectCourseRef.current(candidateBookId).then((opened) => {
      if (!active || opened) return;
      setFailedSelectionBookId(candidateBookId);
      const previousBook = previousLoadedBookId
        ? booksRef.current.find((book) => book.bookId === previousLoadedBookId) ?? null
        : null;
      if (previousBook) {
        setSelectedBookId(previousBook.bookId);
        setSelectionError(`未能打开《${selectedBook.title}》，已回到《${previousBook.title}》。`);
      } else {
        setSelectionError(`未能打开《${selectedBook.title}》。请检查连接后重试。`);
      }
    });

    return () => {
      active = false;
    };
  }, [failedSelectionBookId, loadedBookId, selectedBook?.bookId, selectedBook?.status, selectedBook?.title]);

  function handleSelectBook(bookId: string) {
    const nextBook = books.find((book) => book.bookId === bookId);
    if (!nextBook) return;
    if (bookId === selectedBookId) return;
    hasUserSelectedBookRef.current = true;
    if (pendingBookId && pendingBookId !== bookId) cancelCourseSelection();
    setFailedSelectionBookId(null);
    setSelectionError(null);
    setSelectedBookId(bookId);
  }

  function retrySelectedBook() {
    setSelectionError(null);
    setFailedSelectionBookId(null);
    if (failedSelectionBookId && books.some((book) => book.bookId === failedSelectionBookId)) {
      setSelectedBookId(failedSelectionBookId);
    }
  }

  async function openBookStatus(book: HomeBookModel) {
    const action = resolveHomeBookStatusAction({ book, uploadedFile, parseJobId, parseJobStatus });
    if (action === "chapterConfirm") {
      if (await selectCourse(book.bookId)) go("chapterConfirm");
    } else {
      go(action);
    }
  }

  function restartSelectedBook() {
    go(hasLocalSelectedUpload ? "parseReady" : "upload");
  }

  function openSelectedOriginal() {
    if (!selectedBook || !canOpenSelectedOriginal) return;
    openSourcePage({
      bookId: selectedBook.bookId,
      title: selectedBook.title,
      pageStart: 1,
      pageEnd: 1,
      from: "home"
    });
  }

  function setNextStepContext() {
    if (!selectedLoadedReady || !loadedBookId || !nextStep) return false;
    setActiveChapterId(nextStep.chapter.chapter_id);
    updateStudyLocation(loadedBookId, {
      expandedChapterId: nextStep.expandedChapterId,
      expandedSectionId: nextStep.chapter.chapter_id
    });
    return true;
  }

  function continueNextStep() {
    if (!setNextStepContext()) return;
    go("lesson");
  }

  function openNextStepSource() {
    if (!setNextStepContext() || !loadedBookId || !nextStep) return;
    openSourcePage({
      bookId: loadedBookId,
      title: nextStep.chapter.source_title,
      pageStart: nextStep.chapter.page_start,
      pageEnd: nextStep.chapter.page_end,
      printedPageStart: nextStep.chapter.printed_page_start,
      printedPageEnd: nextStep.chapter.printed_page_end,
      from: "home"
    });
  }

  function openNextStepTool(toolId: ChapterToolId) {
    if (!setNextStepContext()) return;
    go(toolId === "assignment" ? "assignment" : "flashcards");
  }

  const globalActions = buildHomeGlobalActions({
    listState,
    selectedBookId: selectedBook?.bookId ?? null,
    selectedLoadedReady,
    plan: currentStudyPlan
  });

  const courseErrorMessage = courseSummariesError?.includes("Failed to fetch")
    ? "暂时无法连接课程服务"
    : courseSummariesError ?? "请稍后重试";

  return (
    <div className="home-dashboard">
      <header className="home-topline">
        <div>
          <h1>Hi，小明同学</h1>
          <p>今天，沿着原书继续前进</p>
        </div>
        <button
          className="home-import-course-action"
          type="button"
          aria-label="导入课程"
          onClick={() => go("upload")}
        >
          <Upload size={16} aria-hidden="true" />
          <span>导入课程</span>
        </button>
      </header>

      {listState === "error" || Boolean(courseSummariesError && listState === "content") ? (
        <div className="home-course-error" role="alert">
          <span>
            <strong>教材列表暂时无法更新</strong>
            <small>{courseErrorMessage}</small>
          </span>
          <button type="button" disabled={courseSummariesRefreshing} onClick={() => void refreshCourses()}>
            {courseSummariesRefreshing ? "重试中…" : "重新加载"}
          </button>
        </div>
      ) : null}

      {listState !== "error" ? (
        <>
          <HomeBookCarousel
            books={books}
            selectedBookId={selectedBookId}
            listState={listState}
            onSelectBook={handleSelectBook}
            onAddBook={() => go("upload")}
            onOpenLibrary={() => go("library")}
          />

          <SelectedBookWorkspace
            book={selectedBook}
            canOpenOriginal={canOpenSelectedOriginal}
            hasLocalUploadSession={hasLocalSelectedUpload}
            listState={listState}
            loadedBookId={loadedBookId}
            pendingBookId={pendingBookId}
            selectionError={selectionError}
            nextStep={nextStep}
            onContinue={continueNextStep}
            onOpenOriginal={openSelectedOriginal}
            onOpenSource={openNextStepSource}
            onRestart={restartSelectedBook}
            onRetrySelection={retrySelectedBook}
            onSelectTool={openNextStepTool}
            onViewStatus={(book) => void openBookStatus(book)}
            onUpload={() => go("upload")}
          />
        </>
      ) : null}

      {globalActions.length > 0 ? (
        <section className="home-global-section" aria-labelledby="home-global-heading">
          <div className="home-section-heading">
            <div>
              <h2 id="home-global-heading">学习安排</h2>
              <p>计划、复习与新教材</p>
            </div>
          </div>
          <div className="home-global-action-list">
            {globalActions.map((action) => (
              <button
                className={`home-global-action is-${action.id}`}
                data-home-global-action={action.id}
                key={action.id}
                type="button"
                aria-label={`${action.title}，${action.helper}`}
                onClick={() => go(action.target)}
              >
                <span className="home-global-action-icon">{globalActionIcon(action.id)}</span>
                <span className="home-global-action-copy">
                  <strong>{action.title}</strong>
                  <small>{action.helper}</small>
                </span>
                <ArrowRight className="home-global-action-arrow" size={18} aria-hidden="true" />
              </button>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
