import { useMemo, useState } from "react";
import { ChevronRight, CircleHelp, X } from "lucide-react";
import { AppProvider, type AppContextValue } from "../demo/context/AppContext";
import { StudyScreen } from "../demo/screens/StudyScreen";
import type { ApiChapter, CourseSummary, ScanResult, StudyPlan } from "../demo/types/api";
import type { StudyLocation } from "../demo/types/app";
import type { BookCatalogItem, BookStructure, InterviewResponse } from "../types/api";
import type { StudyWorkspace } from "../types/studyWorkspace";
import { DoubtReturn, MemoryReturn } from "../components/LearningReturn";
import { StudioEntry } from "../components/LearningStudio";
import type { Doubt } from "../api/retention";
import type { CourseActivity } from "../types/api";
import { cleanChapterTitle } from "../services/mappers/chapterTitles";

export function DemoStudy({ books, book, structure, session, workspace, loading, userId, onSelectBook,
  onChapter, onPlan, onMistakes, onUpload, onLibrary, onSource, onAsk, onDoubt, onReviewed, returnRevision }: {
  books: BookCatalogItem[];
  book: BookCatalogItem | null;
  structure: BookStructure | null;
  session: InterviewResponse | null;
  workspace: StudyWorkspace | null;
  loading: boolean;
  userId: string;
  onSelectBook: (id: string) => Promise<boolean>;
  onChapter: (id: string, mode: "reading" | "practice" | "cards") => void;
  onPlan: () => void;
  onMistakes: (id?: string) => void;
  onUpload: () => void;
  onLibrary: () => void;
  onSource: (page: number) => void;
  onAsk: () => void;
  onDoubt: (doubt: Doubt) => void;
  onReviewed: (activity: CourseActivity) => void;
  returnRevision: number;
}) {
  const [activeChapterId, setActiveChapterId] = useState<string | null>(null);
  const [locations, setLocations] = useState<Record<string, StudyLocation>>({});
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const summaries = useMemo<CourseSummary[]>(() => books.map(item => ({
    book_id: item.book_id, title: item.title, filename: null,
    status: item.status === "ready" || item.chapter_count > 0 ? "ready" : item.status,
    page_count: item.page_count ?? 0, chapter_count: item.chapter_count, chunk_count: 0,
    asset_count: 0, average_confidence: 0, updated_at: 0,
  })), [books]);
  const chapters = useMemo<ApiChapter[] | null>(() => structure?.chapters.map(item => ({
    chapter_id: item.chapter_id, parent_id: item.parent_id, level: item.level ?? 1,
    source_title: cleanChapterTitle(item.title), ai_title: cleanChapterTitle(item.title), page_start: item.start_page,
    page_end: item.end_page, confidence: 0, status: "已识别", source: "backend",
  })) ?? null, [structure]);
  const scan = useMemo<ScanResult | null>(() => book && structure ? {
    book_id: book.book_id, filename: book.title, file_type: "pdf", page_count: book.page_count ?? structure.source_page_count,
    has_text_layer: false, needs_ocr: false, source_unit: "page", source_locations: [], quality_warnings: [],
  } : null, [book, structure]);
  const plan = useMemo<StudyPlan | null>(() => book && workspace ? ({
    user_id: userId, book_id: book.book_id, days: workspace.plan.days.length,
    daily_minutes: workspace.plan.minutes_per_day,
    tasks: workspace.plan.days.flatMap(day => day.tasks.map(task => ({
      task_id: task.task_id, user_id: userId, day: day.day_index, title: cleanChapterTitle(task.title),
      task_type: task.kind, minutes: task.estimated_minutes, chapter_id: task.section_id ?? task.chapter_id,
      status: task.status, weak_points: [],
    }))),
  }) : null, [book, workspace, userId]);
  const selectedChapterId = activeChapterId ?? chapters?.find(item => item.level > 1)?.chapter_id ?? chapters?.[0]?.chapter_id;
  function go(destination: string) {
    if (destination === "lesson" && selectedChapterId) onChapter(selectedChapterId, "reading");
    else if (destination === "assignment" && selectedChapterId) onChapter(selectedChapterId, "practice");
    else if (destination === "flashcards" && selectedChapterId) onChapter(selectedChapterId, "cards");
    else if (destination === "mistakes") onMistakes(selectedChapterId);
    else if (destination === "plan") onPlan();
    else if (destination === "upload") onUpload();
    else if (destination === "library") onLibrary();
    else if (destination === "source") onSource(chapters?.find(item => item.chapter_id === selectedChapterId)?.page_start ?? 1);
  }
  const bridge = {
    courseSelectionLoadingId: loading ? book?.book_id ?? null : null,
    courseSummaries: summaries, courseSummariesLoadState: loading ? "loading" : "ready",
    currentStudyPlan: plan, generatedFlashcards: [], generatedLessons: [], generatedQuizzes: [],
    loadedBookId: book?.book_id ?? null,
    openSheet: (sheet: { type: string }) => { if (sheet.type === "bookSwitcher") setSwitcherOpen(true); },
    parsedAssets: [], parsedChapters: chapters, parsedChunks: [], parsedScanResult: scan,
    selectCourse: onSelectBook, setActiveChapterId, studyLocations: locations,
    updateStudyLocation: (bookId: string, location: Partial<StudyLocation>) => setLocations(current => ({
      ...current, [bookId]: { expandedChapterId: current[bookId]?.expandedChapterId ?? null,
        expandedSectionId: current[bookId]?.expandedSectionId ?? null, ...location }
    })),
    uploadedFile: book ? { bookId: book.book_id, name: book.title, coverUrl: book.cover_url, sizeBytes: 0, contentType: "application/pdf", uploadedAt: 0, origin: "remote-course" } : null,
    go,
  } as unknown as AppContextValue;
  return <AppProvider value={bridge}>
    <StudyScreen />
    {book && <section className="demo-port-more-tools" aria-label="复习与学习工具"><details><summary>更多学习工具 <ChevronRight size={17}/></summary>
      <div className="demo-port-more-tool-content"><button className="demo-port-more-ask" type="button" onClick={onAsk}>
        <CircleHelp size={19}/> 向这本教材提问 <ChevronRight size={17}/></button>
        <StudioEntry anchor={{ book_id: book.book_id, excerpt: structure?.summary || book.summary || book.title, pages: [] }}/>
        {session && <MemoryReturn sessionId={session.session_id} revision={returnRevision} onReviewed={onReviewed}/>}
        <DoubtReturn bookId={book.book_id} revision={returnRevision} onAsk={onDoubt}/></div>
    </details></section>}
    {switcherOpen && <div className="demo-port-switcher-layer" role="presentation" onPointerDown={event => { if (event.target === event.currentTarget) setSwitcherOpen(false); }}>
      <section className="demo-port-switcher" role="dialog" aria-modal="true" aria-label="切换教材"><header><h2>切换教材</h2><button type="button" aria-label="关闭" onClick={() => setSwitcherOpen(false)}><X size={20} /></button></header>
        <div>{books.map(item => <button type="button" key={item.book_id} aria-current={item.book_id === book?.book_id ? "true" : undefined}
          onClick={() => { void onSelectBook(item.book_id).then(ok => { if (ok) setSwitcherOpen(false); }); }}>
          <span>{item.cover_url ? <img src={item.cover_url} alt="" /> : item.title.slice(0, 1)}</span><strong>{item.title}</strong><ChevronRight size={18} /></button>)}</div>
        <button className="button button-secondary" type="button" onClick={() => { setSwitcherOpen(false); onUpload(); }}>添加教材</button>
      </section>
    </div>}
  </AppProvider>;
}
