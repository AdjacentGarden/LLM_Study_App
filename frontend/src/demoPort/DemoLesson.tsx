import { useEffect, useMemo, useState } from "react";
import { AppProvider, type AppContextValue } from "../demo/context/AppContext";
import { LessonScreen } from "../demo/screens/LessonScreen";
import type { ApiAsset, ApiChapter, Lesson, LessonBlock, LessonCitation } from "../demo/types/api";
import { ApiError, request } from "../api/transport";
import type { BookCatalogItem, BookStructure, Course, CourseCitation, CourseMedia } from "../types/api";
import { StudioEntry } from "../components/LearningStudio";
import { cleanChapterTitle } from "../services/mappers/chapterTitles";

type TeachingLesson = {
  book_id: string; chapter_id: string; title: string; summary: string; objectives: string[];
  blocks: { title: string; content: string; citations: CourseCitation[]; media: CourseMedia[] }[];
  introduction_media: CourseMedia[];
};
type SupplementaryLesson = {
  book_id: string; chapter_id: string; title: string; summary: string;
  blocks: { title: string; content: string }[];
};

function toCitation(citation: CourseCitation): LessonCitation {
  return {
    chunk_id: "",
    page_start: citation.page_number,
    page_end: citation.page_number,
    quote: citation.quote || null,
  };
}

function toAsset(bookId: string, chapterId: string, media: CourseMedia): ApiAsset {
  const sourceType = media.source_kind === "textbook" ? "extracted" : "ai_generated";
  const common = {
    asset_id: media.asset_id, book_id: bookId, chapter_id: chapterId,
    type: media.kind, caption: media.caption, image_url: media.url,
    thumbnail_url: media.url, source_chunk_ids: [], concepts: [],
  };
  if (sourceType === "extracted") return {
    ...common, source_type: "extracted", page: media.page_number ?? 0,
    bbox: [], source_page_image_url: media.page_number ? `/api/books/${encodeURIComponent(bookId)}/pages/${media.page_number}/image` : "",
  };
  return { ...common, source_type: "ai_generated", page: null, generation_provider: "backend", review_status: "available" };
}

function makeBlock(index: number, title: string, content: string, citations: CourseCitation[], media: CourseMedia[], aiGenerated: boolean): LessonBlock {
  return {
    block_id: `lesson-block-${index}`, block_type: "reading", title, content,
    citations: citations.filter(item => item.page_number > 0).map(toCitation),
    source_chunk_ids: [], asset_ids: media.map(item => item.asset_id), ai_generated: aiGenerated,
  };
}

function mapLesson(book: BookCatalogItem, chapterId: string, course: Course | null, teaching: TeachingLesson | null, supplementary: SupplementaryLesson | null,
  pageStart: number, pageEnd: number): { lesson: Lesson | null; assets: ApiAsset[] } {
  if (!course && !teaching && !supplementary) return { lesson: null, assets: [] };
  const blocks: LessonBlock[] = [];
  const media: CourseMedia[] = [];
  const add = (title: string, content: string, citations: CourseCitation[] = [], attached: CourseMedia[] = [], generated = false) => {
    if (!content.trim()) return;
    blocks.push(makeBlock(blocks.length, title, content, citations, attached, generated));
    media.push(...attached);
  };
  if (teaching) {
    media.push(...teaching.introduction_media);
    for (const block of teaching.blocks) add(block.title, block.content, block.citations, block.media);
  }
  if (supplementary) for (const block of supplementary.blocks) add(block.title, block.content, [], [], true);
  if (course) {
    const existing = new Set(blocks.map(block => `${block.title}\u0000${block.content}`));
    for (const section of [...course.original_reading, ...course.worked_examples]) {
      if (!existing.has(`${section.title}\u0000${section.content}`)) add(section.title, section.content, section.citations, section.media ?? []);
    }
    for (const point of course.knowledge_points) add(point.title, point.explanation, point.citations);
    add("本节小结", course.summary, []);
  }
  const title = cleanChapterTitle(teaching?.title ?? course?.chapter_title ?? supplementary?.title ?? "章节学习");
  const lesson: Lesson = {
    book_id: book.book_id, lesson_id: course?.course_id ?? `supplement-${chapterId}`, chapter_id: chapterId,
    title, source_title: title, page_start: pageStart, page_end: pageEnd,
    lesson_kind: "lesson", status: "ready", confidence: 0,
    objectives: teaching?.objectives ?? [], key_concepts: course?.knowledge_points.map(item => item.title) ?? [],
    summary: teaching?.summary || course?.opening || supplementary?.summary || "",
    blocks, source_chunk_ids: [], asset_ids: teaching?.introduction_media.map(item => item.asset_id) ?? [],
    warnings: course?.unresolved_source_warnings ?? [],
  };
  const assets = [...new Map(media.map(item => [item.asset_id, toAsset(book.book_id, chapterId, item)])).values()];
  return { lesson, assets };
}

export function DemoLesson({ book, structure, chapterId, course, onSource, onAsk, onUpdate, onComplete, onUpload, onLibrary, onNotice }: {
  book: BookCatalogItem; structure: BookStructure | null; chapterId: string; course: Course | null;
  onSource: (page: number) => void; onAsk: () => void; onUpdate: () => Promise<boolean>; onComplete: () => void;
  onUpload: () => void; onLibrary: () => void; onNotice: (value: string) => void;
}) {
  const [teaching, setTeaching] = useState<TeachingLesson | null>(null);
  const [supplementary, setSupplementary] = useState<SupplementaryLesson | null>(null);
  const [loadError, setLoadError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setTeaching(null); setSupplementary(null); setLoadError("");
    const base = `/api/books/${encodeURIComponent(book.book_id)}`;
    void request<TeachingLesson>(`${base}/teaching-lessons/${encodeURIComponent(chapterId)}`, { signal: controller.signal }, 20_000)
      .then(value => { if (!controller.signal.aborted && value.book_id === book.book_id && value.chapter_id === chapterId) setTeaching(value); })
      .catch(error => { if (!controller.signal.aborted && (!(error instanceof ApiError) || error.status !== 404)) setLoadError((error as Error).message); });
    if (!course) void request<SupplementaryLesson>(`${base}/supplementary-lessons/${encodeURIComponent(chapterId)}`, { signal: controller.signal }, 20_000)
      .then(value => { if (!controller.signal.aborted && value.book_id === book.book_id && value.chapter_id === chapterId) setSupplementary(value); })
      .catch(error => { if (!controller.signal.aborted) setLoadError((error as Error).message); });
    return () => controller.abort();
  }, [book.book_id, chapterId, course, revision]);
  const chapter = structure?.chapters.find(item => item.chapter_id === chapterId);
  const pageStart = chapter?.start_page ?? 1;
  const pageEnd = chapter?.end_page ?? pageStart;
  const adaptedChapter: ApiChapter = {
    chapter_id: chapterId, level: chapter?.level ?? 1, source_title: cleanChapterTitle(chapter?.title ?? course?.chapter_title ?? "章节学习"),
    ai_title: cleanChapterTitle(course?.chapter_title ?? chapter?.title ?? "章节学习"), page_start: pageStart, page_end: pageEnd,
    confidence: 0, status: "ready", source: "backend", parent_id: chapter?.parent_id,
  };
  const mapped = useMemo(() => mapLesson(book, chapterId, course, teaching, supplementary, pageStart, pageEnd),
    [book, chapterId, course, teaching, supplementary, pageStart, pageEnd]);
  const bridge = {
    activeChapterId: chapterId, generatedLessons: mapped.lesson ? [mapped.lesson] : [], parsedChapters: [adaptedChapter],
    parsedChunks: [], parsedAssets: mapped.assets, lessonBuildJobStatus: null,
    uploadedFile: { bookId: book.book_id, name: book.title, sizeBytes: 0, contentType: "application/pdf", uploadedAt: 0, origin: "remote-course" },
    demoPort: { updateCourse: onUpdate, openAsk: onAsk }, showToast: onNotice,
    openSheet: (sheet: { type: string; source?: { pageStart?: number } }) => { if (sheet.type === "source" && sheet.source?.pageStart) onSource(sheet.source.pageStart); },
    go: (destination: string) => { if (destination === "study") onComplete(); else if (destination === "upload") onUpload(); else if (destination === "library") onLibrary(); },
  } as unknown as AppContextValue;
  if (!course && !mapped.lesson && !loadError) return <div className="parse-empty-card" role="status">正在读取章节讲解…</div>;
  if (loadError && !mapped.lesson) return <div className="parse-empty-card" role="alert"><p>{loadError}</p><button className="button button-secondary" onClick={() => setRevision(value => value + 1)}>重新读取</button></div>;
  const sourcePages = [...new Set((course?.original_reading ?? []).flatMap(section => section.citations.map(item => item.page_number)).filter(page => page > 0))];
  return <AppProvider value={bridge}><LessonScreen />
    <section className="demo-port-more-tools" aria-label="本节创作工具"><details><summary>本节笔记与讲解工具</summary>
      <div className="demo-port-more-tool-content"><StudioEntry anchor={{ book_id: book.book_id,
        excerpt: course?.opening || teaching?.summary || supplementary?.summary || book.summary || book.title, pages: sourcePages }}/></div>
    </details></section>
  </AppProvider>;
}
