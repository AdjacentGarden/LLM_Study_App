import { useState } from "react";
import { BookOpenCheck, Check, ChevronRight, LibraryBig, Plus } from "lucide-react";
import { Button } from "../../components/ui";
import { useAppContext } from "../../context/AppContext";
import { hasCompleteLoadedCourseContext } from "../courseResourceIdentity";
import { sourcePageImageUrl } from "../shared";

function statusLabel(status: string) {
  if (status === "ready") return "可以学习";
  if (status === "needs_review") return "待确认目录";
  if (status === "processing") return "正在生成";
  if (status === "error") return "需要处理";
  return "等待解析";
}

export function BookSwitcherSheetContent() {
  const {
    closeSheet,
    courseSelectionLoadingId,
    courseSummaries,
    currentStudyPlan,
    generatedFlashcards,
    generatedLessons,
    generatedQuizzes,
    go,
    loadedBookId,
    parsedAssets,
    parsedChapters,
    parsedChunks,
    parsedScanResult,
    selectCourse,
    showToast,
    uploadedFile
  } = useAppContext();
  const [openingId, setOpeningId] = useState<string | null>(null);
  const hasLoadedContext = hasCompleteLoadedCourseContext({
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
  });

  async function chooseCourse(bookId: string, status: string) {
    if (hasLoadedContext && bookId === loadedBookId) {
      closeSheet();
      return;
    }
    if (status !== "ready" && status !== "needs_review") {
      closeSheet();
      go("library");
      showToast("这本教材还在准备中，可在课程库查看进度", "info");
      return;
    }
    setOpeningId(bookId);
    const selected = await selectCourse(bookId);
    setOpeningId(null);
    if (!selected) return;
    closeSheet();
    go(status === "needs_review" ? "chapterConfirm" : "study");
  }

  return (
    <div className="book-switcher-sheet">
      <p className="book-switcher-helper">切换后会回到这本教材上次展开的小节。</p>
      <div className="book-switcher-list">
        {courseSummaries.map((course) => {
          const selected = hasLoadedContext && course.book_id === loadedBookId;
          const loading = openingId === course.book_id || courseSelectionLoadingId === course.book_id;
          return (
            <button
              className={`book-switcher-row ${selected ? "is-selected" : ""}`}
              type="button"
              key={course.book_id}
              disabled={Boolean(courseSelectionLoadingId)}
              onClick={() => void chooseCourse(course.book_id, course.status)}
            >
              <img src={sourcePageImageUrl(course.book_id, 1)} alt="" />
              <span>
                <strong>{course.title}</strong>
                <small>{loading ? "正在打开…" : statusLabel(course.status)}</small>
              </span>
              {selected ? <Check size={19} aria-label="当前教材" /> : <ChevronRight size={19} aria-hidden="true" />}
            </button>
          );
        })}
        {courseSummaries.length === 0 ? (
          <div className="book-switcher-empty">
            <BookOpenCheck size={28} aria-hidden="true" />
            <span><strong>还没有教材</strong><small>添加一本教材后即可开始学习</small></span>
          </div>
        ) : null}
      </div>
      <div className="book-switcher-actions">
        <Button icon={<Plus size={18} aria-hidden="true" />} onClick={() => { closeSheet(); go("upload"); }}>
          添加新教材
        </Button>
        <Button variant="secondary" icon={<LibraryBig size={18} aria-hidden="true" />} onClick={() => { closeSheet(); go("library"); }}>
          管理全部教材
        </Button>
      </div>
    </div>
  );
}
