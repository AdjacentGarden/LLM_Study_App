import { useMemo } from "react";
import { AppProvider, type AppContextValue } from "../demo/context/AppContext";
import { FlashcardScreen } from "../demo/screens/FlashcardScreen";
import type { Flashcard as DemoFlashcard } from "../demo/types/api";
import type { BookCatalogItem, Course, CourseActivity } from "../types/api";

export function DemoFlashcards({ book, course, onSource, onReview, onUpdate, onBack, onNotice }: {
  book: BookCatalogItem; course: Course; onSource: (page: number) => void;
  onReview: (id: string, rating: "again" | "good", seconds: number) => Promise<CourseActivity | null>;
  onUpdate: () => Promise<boolean>; onBack: () => void; onNotice: (value: string) => void;
}) {
  const cards = useMemo<DemoFlashcard[]>(() => course.flashcards.map(card => {
    const citation = card.citations.find(item => item.page_number > 0);
    return {
      card_id: card.card_id, book_id: book.book_id, lesson_id: course.course_id, chapter_id: course.chapter_id,
      front: card.front, back: card.back, concept: course.knowledge_points.find(point => point.point_id === card.point_id)?.title ?? "本节知识点",
      source_chunk_ids: [], page_start: citation?.page_number ?? 0, page_end: citation?.page_number ?? 0,
      source_kind: citation ? "textbook" : "ai_supplement", source_quote: citation?.quote ?? null,
      due: "本节卡片", mastery: 0, reason: card.reason_for_user,
    };
  }), [book.book_id, course]);
  const bridge = {
    activeChapterId: course.chapter_id, generatedFlashcards: cards, generatedLessons: [],
    uploadedFile: { bookId: book.book_id, name: book.title, sizeBytes: 0, contentType: "application/pdf", uploadedAt: 0, origin: "remote-course" },
    demoPort: { updateCourse: onUpdate, reviewFlashcard: async (id: string, rating: "again" | "good", seconds: number) => Boolean(await onReview(id, rating, seconds)) },
    back: onBack, go: (target: string) => { if (target === "lesson") onBack(); },
    openSourcePage: (target: { pageStart: number }) => onSource(target.pageStart),
    showToast: onNotice,
  } as unknown as AppContextValue;
  return <AppProvider value={bridge}><FlashcardScreen /></AppProvider>;
}
