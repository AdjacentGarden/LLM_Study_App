import type { ApiChapter } from "../types/api";

function numberedLabel(title: string, unit: "章" | "节") {
  return title.match(new RegExp(`第\\s*\\d+\\s*${unit}`, "u"))?.[0].replace(/\s+/gu, " ") ?? null;
}

export function lessonHeaderSubtitle(chapters: ApiChapter[] | null, activeChapterId: string | null) {
  if (!chapters || !activeChapterId) return "当前章节";
  const chapterById = new Map(chapters.map((chapter) => [chapter.chapter_id, chapter]));
  const activeChapter = chapterById.get(activeChapterId);
  if (!activeChapter) return "当前章节";

  let rootChapter = activeChapter;
  const visited = new Set<string>();
  while (rootChapter.parent_id && !visited.has(rootChapter.chapter_id)) {
    visited.add(rootChapter.chapter_id);
    const parent = chapterById.get(rootChapter.parent_id);
    if (!parent) break;
    rootChapter = parent;
  }

  const chapterLabel = numberedLabel(rootChapter.source_title, "章");
  const sectionLabel = numberedLabel(activeChapter.source_title, "节");
  if (chapterLabel && sectionLabel) {
    return `${chapterLabel} ${sectionLabel.replace(/^第\s*/u, "")}`;
  }
  return sectionLabel ?? chapterLabel ?? activeChapter.source_title;
}
