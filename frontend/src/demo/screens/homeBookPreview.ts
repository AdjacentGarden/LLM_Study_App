import type { ChapterToolPreviewContent } from "./studyTools";

export type HomeBookStudyPreview = Readonly<{
  bookId: string;
  chapterId: string;
  chapterTitle: string;
  toolPreview: ChapterToolPreviewContent;
}>;
