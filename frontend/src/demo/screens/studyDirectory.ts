import type { ApiChapter } from "../types/api";
import type { StudyLocation } from "../types/app";
import { buildChapterTree, type ChapterTreeNode } from "../utils/chapterStructure";

const frontmatterChapterId = "frontmatter";

export function isFormalStudySection(chapter: ApiChapter): boolean {
  return /^(?:第\s*\d+\s*节|\d+\.\d+\*?\s*)/u.test(chapter.source_title);
}

/**
 * The source directory keeps every parsed heading for provenance and source
 * navigation. The learning directory intentionally exposes only the product
 * hierarchy: chapter -> formal section.
 */
export function buildStudyDirectory(chapters: ApiChapter[]): ChapterTreeNode[] {
  return buildChapterTree(chapters)
    .filter((node) => node.chapter.chapter_id !== frontmatterChapterId)
    .map((node) => ({
      ...node,
      children: node.children
        .filter((child) => isFormalStudySection(child.chapter))
        .map((child) => ({ ...child, children: [] }))
    }));
}

export function normalizeStudyLocation(
  directory: readonly ChapterTreeNode[],
  location?: StudyLocation | null
): StudyLocation {
  const hasSavedLocation = location !== null && location !== undefined;
  const chapterWasCollapsed = hasSavedLocation && location.expandedChapterId === null;
  const sectionWasCollapsed = hasSavedLocation && location.expandedSectionId === null;
  const chapter = chapterWasCollapsed
    ? null
    : directory.find((node) => node.chapter.chapter_id === location?.expandedChapterId)
      ?? directory[0]
      ?? null;
  const sections = chapter
    ? chapter.children.length > 0
      ? chapter.children
      : [chapter]
    : directory.flatMap((node) => node.children.length > 0 ? node.children : [node]);
  const section = sectionWasCollapsed
    ? null
    : sections.find((node) => node.chapter.chapter_id === location?.expandedSectionId)
      ?? (chapter ? sections[0] ?? null : null);
  return {
    expandedChapterId: chapterWasCollapsed ? null : chapter?.chapter.chapter_id ?? null,
    expandedSectionId: section?.chapter.chapter_id ?? null
  };
}
