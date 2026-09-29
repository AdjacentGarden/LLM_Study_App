import { ArrowRight, CalendarDays, CircleAlert, Upload } from "lucide-react";
import { useMemo } from "react";
import type { BookCatalogItem, BookStructure, InterviewResponse, UserProfile } from "../types/api";
import { HomeBookCarousel } from "../demo/components/home/HomeBookCarousel";
import { SelectedBookWorkspace } from "../demo/components/home/SelectedBookWorkspace";
import type { ChapterToolId } from "../demo/components/study/ChapterToolCards";
import { buildHomeBookModels } from "../demo/screens/homeBookModel";
import type { HomeNextStep } from "../demo/screens/homeNextStep";
import { cleanChapterTitle } from "../services/mappers/chapterTitles";

function selectNextChapter(structure: BookStructure | null): HomeNextStep | null {
  if (!structure?.chapters.length) return null;
  const children = new Set(structure.chapters.map(chapter => chapter.parent_id).filter(Boolean));
  const chapter = structure.chapters.find(item => item.knowledge_points.length > 0 && !children.has(item.chapter_id))
    ?? structure.chapters.find(item => !children.has(item.chapter_id))
    ?? structure.chapters[0];
  const ancestor = structure.chapters.find(item => item.chapter_id === chapter.parent_id) ?? chapter;
  return { chapter: {
    chapter_id: chapter.chapter_id, parent_id: chapter.parent_id, level: chapter.level ?? 1,
    source_title: cleanChapterTitle(chapter.title), ai_title: cleanChapterTitle(chapter.title), page_start: chapter.start_page,
    page_end: chapter.end_page, confidence: 0, status: "已识别", source: "backend"
  }, expandedChapterId: ancestor.chapter_id, source: "directory", sourceHint: "从教材目录继续",
    pageLabel: chapter.start_page > 0 ? `原书第 ${chapter.start_page} 页` : "章节内容" };
}

export function DemoHome({ books, activeBook, structure, session, profile, loading, busy, error,
  onReload, onSelectBook, onOpenBook, onLibrary, onUpload, onSource, onChapter, onPlan, onMistakes }: {
  books: BookCatalogItem[];
  activeBook: BookCatalogItem | null;
  structure: BookStructure | null;
  session: InterviewResponse | null;
  profile: UserProfile | null;
  loading: boolean;
  busy: boolean;
  error: string;
  onReload: () => void;
  onSelectBook: (id: string) => void;
  onOpenBook: (id: string) => void;
  onLibrary: () => void;
  onUpload: () => void;
  onSource: (page: number) => void;
  onChapter: (id: string, tool: "reading" | ChapterToolId) => void;
  onPlan: () => void;
  onMistakes: () => void;
}) {
  const models = useMemo(() => buildHomeBookModels({
    courses: books.map(book => ({
      book_id: book.book_id, title: book.title, filename: null,
      status: book.status === "ready" || book.chapter_count > 0 ? "ready" : book.status,
      page_count: book.page_count ?? 0, chapter_count: book.chapter_count,
      chunk_count: 0, asset_count: 0, average_confidence: 0, updated_at: 0,
      next_title: null,
    })),
    uploadedFile: null, parseJobId: null, parseJobStatus: null,
    loadedBookId: activeBook?.book_id ?? null, loadedChapterCount: structure?.chapters.length ?? 0,
    catalogBooks: books.map(book => ({ bookId: book.book_id, title: book.title, filename: null, coverUrl: book.cover_url ?? "" }))
  }), [books, activeBook?.book_id, structure?.chapters.length]);
  const selectedBook = models.find(item => item.bookId === activeBook?.book_id) ?? null;
  const nextStep = selectNextChapter(structure);
  const listState = loading ? "loading" : error && books.length === 0 ? "error" : books.length === 0 ? "empty" : "content";
  const onNext = (tool: "reading" | ChapterToolId) => { if (nextStep) onChapter(nextStep.chapter.chapter_id, tool); };
  return <div className="home-dashboard">
    <header className="home-topline"><div><h1>{profile?.nickname ? `Hi，${profile.nickname}` : "Hi，今天想读什么？"}</h1><p>今天，沿着原书继续前进</p></div>
      <button className="home-import-course-action" type="button" aria-label="导入课程" onClick={onUpload}><Upload size={16} aria-hidden="true" /><span>导入课程</span></button></header>
    {error && <div className="home-course-error" role="alert"><span><strong>教材列表暂时无法更新</strong><small>{error}</small></span><button type="button" onClick={onReload}>重新加载</button></div>}
    {listState !== "error" && <><HomeBookCarousel books={models} selectedBookId={activeBook?.book_id ?? null} listState={listState}
      onSelectBook={onSelectBook} onOpenBook={onOpenBook} onAddBook={onUpload} onOpenLibrary={onLibrary} />
      <SelectedBookWorkspace book={selectedBook} canOpenOriginal={!!activeBook?.page_count} hasLocalUploadSession={false}
        listState={listState} loadedBookId={activeBook?.book_id ?? null} pendingBookId={busy ? activeBook?.book_id ?? null : null}
        selectionError={null} nextStep={nextStep} onContinue={() => onNext("reading")}
        onOpenOriginal={() => onSource(1)} onOpenSource={() => onSource(Math.max(1, nextStep?.chapter.page_start || 1))}
        onSelectTool={onNext} onRestart={onUpload} onRetrySelection={onReload} onViewStatus={item => onOpenBook(item.bookId)}
        onUpload={onUpload} /></>}
    <section className="home-global-section" aria-labelledby="home-global-heading"><div className="home-section-heading"><div>
      <h2 id="home-global-heading">学习安排</h2><p>计划、复习与新教材</p></div></div>
      <div className="home-global-action-list">
        {[{ id: "plan", title: "学习计划", helper: session?.phase === "complete" ? "继续今天的任务" : "完成学习诊断后查看", icon: CalendarDays, click: onPlan },
          { id: "mistakes", title: "错题回访", helper: "复习真实作答记录", icon: CircleAlert, click: onMistakes },
          { id: "upload", title: "添加教材", helper: "从 PDF 开始", icon: Upload, click: onUpload }].map(action => <button key={action.id} className={`home-global-action is-${action.id}`}
          data-home-global-action={action.id} type="button" aria-label={`${action.title}，${action.helper}`} onClick={action.click}>
          <span className="home-global-action-icon"><action.icon size={20} aria-hidden="true" /></span><span className="home-global-action-copy"><strong>{action.title}</strong><small>{action.helper}</small></span><ArrowRight className="home-global-action-arrow" size={18} aria-hidden="true" /></button>)}
      </div></section>
  </div>;
}
