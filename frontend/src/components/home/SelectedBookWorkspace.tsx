import {
  ArrowRight,
  BookOpenText,
  CircleAlert,
  Cloud,
  LibraryBig,
  ListChecks,
  Play,
  RefreshCw,
  RotateCcw,
  Upload
} from "lucide-react";
import { ChapterToolCards, type ChapterToolId } from "../study/ChapterToolCards";
import type { HomeBookListState, HomeBookModel } from "../../screens/homeBookModel";
import type { HomeNextStep } from "../../screens/homeNextStep";

type SelectedBookWorkspaceProps = Readonly<{
  book: HomeBookModel | null;
  canOpenOriginal: boolean;
  hasLocalUploadSession: boolean;
  listState: HomeBookListState;
  loadedBookId: string | null;
  pendingBookId: string | null;
  selectionError: string | null;
  nextStep: HomeNextStep | null;
  onContinue: () => void;
  onOpenOriginal: () => void;
  onOpenSource: () => void;
  onSelectTool: (toolId: ChapterToolId) => void;
  onRestart: () => void;
  onRetrySelection: () => void;
  onViewStatus: (book: HomeBookModel) => void;
  onUpload: () => void;
}>;

function workspaceHeading(book: HomeBookModel): string {
  switch (book.status) {
    case "catalog":
      return "这本示范教材还未导入";
    case "ready":
      return "学习内容已经准备好";
    case "processing":
      return "正在整理教材";
    case "needs_review":
      return "课程目录等待确认";
    case "error":
      return "教材整理没有完成";
    case "uploaded":
      return "教材已上传";
    case "unknown":
      return "教材状态暂未同步";
  }
}

function workspaceDescription(book: HomeBookModel): string {
  switch (book.status) {
    case "catalog":
      return "当前只展示书名与原始封面。导入教材后，才会生成章节、学习进度与本章工具。";
    case "ready":
      return book.chapterCount > 0
        ? `已整理 ${book.chapterCount} 个目录项，请到教材详情继续处理。`
        : "学习内容已经准备好，请到教材详情继续处理。";
    case "processing":
      return book.progress > 0
        ? `已完成 ${book.progress}%。可以先离开，教材会继续在后台整理。`
        : "整理任务已经提交。可以先离开，完成后会生成课程目录。";
    case "needs_review":
      return book.chapterCount > 0
        ? `系统识别出 ${book.chapterCount} 个目录项。确认章节边界后，学习内容和本章工具才会解锁。`
        : "请确认章节边界。确认完成后，学习内容和本章工具才会解锁。";
    case "error":
      return book.errorMessage ?? "教材整理遇到问题。请查看处理详情，再决定重试或重新上传。";
    case "uploaded":
      return "文件已经安全保存，但还没有开始整理。启动后可以在后台继续处理。";
    case "unknown":
      return "暂时无法确认这本教材的处理状态。查看教材详情或重新上传，章节工具不会提前解锁。";
  }
}

function statusActionLabel(book: HomeBookModel, hasLocalUploadSession: boolean): string {
  switch (book.status) {
    case "catalog":
      return "导入这本教材";
    case "processing":
      return "查看整理详情";
    case "needs_review":
      return "确认课程目录";
    case "error":
      return "查看处理详情";
    case "uploaded":
      return hasLocalUploadSession ? "开始整理教材" : "查看处理详情";
    case "unknown":
    case "ready":
      return "查看教材详情";
  }
}

function statusIcon(book: HomeBookModel) {
  switch (book.status) {
    case "catalog":
      return <BookOpenText size={16} aria-hidden="true" />;
    case "processing":
      return <Cloud size={16} aria-hidden="true" />;
    case "needs_review":
      return <ListChecks size={16} aria-hidden="true" />;
    case "error":
      return <CircleAlert size={16} aria-hidden="true" />;
    case "uploaded":
      return <Upload size={16} aria-hidden="true" />;
    case "unknown":
    case "ready":
      return <LibraryBig size={16} aria-hidden="true" />;
  }
}

type StatusActionsProps = Readonly<{
  book: HomeBookModel;
  canOpenOriginal: boolean;
  hasLocalUploadSession: boolean;
  onOpenOriginal: () => void;
  onRestart: () => void;
  onUpload: () => void;
  onViewStatus: (book: HomeBookModel) => void;
}>;

function StatusActions({
  book,
  canOpenOriginal,
  hasLocalUploadSession,
  onOpenOriginal,
  onRestart,
  onUpload,
  onViewStatus
}: StatusActionsProps) {
  const showOriginal = canOpenOriginal && (book.status === "needs_review" || book.status === "error");
  const showRecovery = book.status === "error" || book.status === "unknown"
    || (book.status === "uploaded" && !hasLocalUploadSession);

  return (
    <div className="home-status-actions" aria-label={`${book.title}的状态操作`}>
      <button
        className="home-primary-action"
        type="button"
        onClick={() => book.status === "catalog" ? onUpload() : onViewStatus(book)}
      >
        {book.status === "processing"
          ? <RefreshCw size={17} aria-hidden="true" />
          : book.status === "catalog"
            ? <Upload size={17} aria-hidden="true" />
            : <LibraryBig size={17} aria-hidden="true" />}
        <span>{statusActionLabel(book, hasLocalUploadSession)}</span>
        <ArrowRight size={16} aria-hidden="true" />
      </button>
      {showOriginal || showRecovery ? (
        <div className="home-status-secondary-actions">
          {showOriginal ? (
            <button className="home-source-action" type="button" onClick={onOpenOriginal}>
              <BookOpenText size={17} aria-hidden="true" />
              <span>打开原书</span>
            </button>
          ) : null}
          {showRecovery ? (
            <button
              className="home-status-secondary-action"
              type="button"
              onClick={book.status === "error" && hasLocalUploadSession ? onRestart : onUpload}
            >
              {book.status === "error" && hasLocalUploadSession
                ? <RotateCcw size={17} aria-hidden="true" />
                : <Upload size={17} aria-hidden="true" />}
              <span>{book.status === "error" && hasLocalUploadSession ? "重新整理" : "重新上传"}</span>
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function LoadingBookWorkspace({ book }: Readonly<{ book: HomeBookModel | null }>) {
  const announcement = book
    ? `正在准备《${book.title}》，学习操作暂不可用。`
    : "正在加载教材列表，上传和学习操作暂不可用。";
  return (
    <section
      className="home-focus-panel home-book-workspace is-loading"
      aria-label={book ? `正在切换到${book.title}` : "正在加载教材工作区"}
      aria-busy="true"
      data-book-id={book?.bookId}
      data-loaded="false"
    >
      <p className="home-visually-hidden" role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </p>
      <div className="home-workspace-loading-copy" aria-hidden="true">
        <span className="home-workspace-loading-line is-short" />
        <span className="home-workspace-loading-line is-course" />
        <span className="home-workspace-loading-line is-title" />
      </div>
      <div className="home-workspace-loading-actions" aria-hidden="true">
        <span />
        <span />
      </div>
      <div className="home-workspace-loading-tools" aria-hidden="true">
        <span className="home-workspace-loading-line is-tools-heading" />
        <div>
          <span /><span /><span />
        </div>
      </div>
    </section>
  );
}

export function SelectedBookWorkspace({
  book,
  canOpenOriginal,
  hasLocalUploadSession,
  listState,
  loadedBookId,
  pendingBookId,
  selectionError,
  nextStep,
  onContinue,
  onOpenOriginal,
  onOpenSource,
  onSelectTool,
  onRestart,
  onRetrySelection,
  onViewStatus,
  onUpload
}: SelectedBookWorkspaceProps) {
  if (listState === "loading") return <LoadingBookWorkspace book={null} />;
  if (listState === "error") return null;

  if (!book) {
    return (
      <section
        className="home-focus-panel home-book-workspace is-empty"
        aria-labelledby="home-workspace-title"
        data-loaded="false"
      >
        <div className="home-workspace-copy">
          <span className="home-workspace-label"><Upload size={16} aria-hidden="true" />从一本教材开始</span>
          <h2 id="home-workspace-title">把原书变成可继续的学习路径</h2>
          <p>上传 PDF、图片或 Office 文档，教材整理进度和下一步会在这里清楚显示。</p>
        </div>
      </section>
    );
  }

  const waitingForReadyBook = book.status === "ready" && book.bookId !== loadedBookId;
  const pending = !selectionError && (pendingBookId === book.bookId || waitingForReadyBook);

  if (pending) return <LoadingBookWorkspace book={book} />;

  const loadedReady = book.status === "ready" && book.bookId === loadedBookId && Boolean(nextStep);
  const readyLoadFailed = Boolean(selectionError && book.status === "ready" && !loadedReady);

  return (
    <section
      className={`home-focus-panel home-book-workspace is-${book.status}`}
      aria-labelledby="home-workspace-title"
      data-book-id={book.bookId}
      data-chapter-id={loadedReady ? nextStep?.chapter.chapter_id : undefined}
      data-loaded={loadedReady ? "true" : "false"}
    >
      {selectionError ? (
        <div
          className="home-workspace-selection-error"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          <CircleAlert size={17} aria-hidden="true" />
          <span>{selectionError}</span>
          <button type="button" onClick={onRetrySelection}>重试切换</button>
        </div>
      ) : null}

      <div className="home-workspace-copy">
        <span className="home-workspace-label">
          {statusIcon(book)}
          {loadedReady ? "这本书的下一步" : book.statusLabel}
        </span>
        <p className="home-workspace-course" title={book.title}>{book.title}</p>
        <h2 id="home-workspace-title">
          {loadedReady
            ? nextStep?.chapter.source_title
            : readyLoadFailed
              ? "暂时无法打开这本教材"
              : workspaceHeading(book)}
        </h2>
        {!loadedReady ? (
          <p>
            {readyLoadFailed
              ? "本次学习资源加载没有完成。可以重试切换，或先查看教材详情。"
              : workspaceDescription(book)}
          </p>
        ) : null}
        {book.status === "processing" ? (
          <div
            className="home-workspace-progress"
            role="progressbar"
            aria-label="教材整理进度"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={book.progress}
          >
            <span style={{ transform: `scaleX(${book.progress / 100})` }} />
          </div>
        ) : null}
      </div>

      {loadedReady && nextStep ? (
        <>
          <div className="home-workspace-actions" aria-label="本章主要操作">
            <button className="home-primary-action" type="button" onClick={onContinue}>
              <Play size={17} aria-hidden="true" />
              <span>继续学习</span>
              <ArrowRight size={16} aria-hidden="true" />
            </button>
            <button className="home-source-action" type="button" onClick={onOpenSource}>
              <BookOpenText size={17} aria-hidden="true" />
              <span>回到原书</span>
            </button>
          </div>
          <div className="home-chapter-tools">
            <div className="home-chapter-tools-heading">
              <strong>本章工具</strong>
              <span>围绕当前章节继续练习与复习</span>
            </div>
            <ChapterToolCards
              ariaLabel={`${nextStep.chapter.source_title}的本章工具`}
              chapterTitle={nextStep.chapter.source_title}
              onSelectTool={onSelectTool}
            />
          </div>
        </>
      ) : (
        <StatusActions
          book={book}
          canOpenOriginal={canOpenOriginal}
          hasLocalUploadSession={hasLocalUploadSession}
          onOpenOriginal={onOpenOriginal}
          onRestart={onRestart}
          onUpload={onUpload}
          onViewStatus={onViewStatus}
        />
      )}
    </section>
  );
}
