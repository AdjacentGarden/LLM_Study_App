import type { BookCatalogItem, BookStructure, Course, CourseCitation } from "../../types/api";

export interface BookTile {
  id: string;
  title: string;
  cover: string | null;
  description: string | null;
  pages: number | null;
  chapters: number | null;
  status: string;
  diagnosticsReady: boolean | null;
}

export function bookTile(book: BookCatalogItem): BookTile {
  return {
    id: book.book_id,
    title: book.title,
    cover: book.cover_url || null,
    description: book.summary?.trim() || null,
    pages: book.page_count,
    chapters: Number.isFinite(book.chapter_count) ? book.chapter_count : null,
    status: book.status,
    diagnosticsReady: typeof book.diagnostics_ready === "boolean" ? book.diagnostics_ready : null,
  };
}

export function chapterRange(structure: BookStructure | null, chapterId: string) {
  const chapter = structure?.chapters.find(item => item.chapter_id === chapterId);
  return chapter ? { start: chapter.start_page, end: chapter.end_page } : null;
}

export function firstSourcePage(citations: readonly CourseCitation[]): number | null {
  return citations.find(item => Number.isInteger(item.page_number) && item.page_number > 0)?.page_number ?? null;
}

export function courseEvidenceCount(course: Course | null): number {
  if (!course) return 0;
  return course.original_reading.reduce((sum, section) => sum + section.citations.length, 0)
    + course.knowledge_points.reduce((sum, point) => sum + point.citations.length, 0);
}
