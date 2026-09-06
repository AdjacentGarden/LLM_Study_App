import type { ApiChapter } from "../types/api";

export type ChapterRangeConflict = {
  chapterId: string;
  otherChapterId: string;
  overlapStart: number;
  overlapEnd: number;
};

export type ChapterTreeNode = {
  chapter: ApiChapter;
  children: ChapterTreeNode[];
};

export type ChapterValidationField = "source_title" | "ai_title" | "page_start" | "page_end" | "parent_id" | "form";

export type ChapterValidationIssue = Readonly<{
  code: string;
  field: ChapterValidationField;
  message: string;
}>;

export function buildChapterTree(chapters: ApiChapter[]): ChapterTreeNode[] {
  const nodesById = new Map<string, ChapterTreeNode>(
    chapters.map((chapter) => [chapter.chapter_id, { chapter, children: [] }])
  );
  const roots: ChapterTreeNode[] = [];

  chapters.forEach((chapter) => {
    const node = nodesById.get(chapter.chapter_id);
    if (!node) return;
    const parent = chapter.parent_id ? nodesById.get(chapter.parent_id) : null;
    if (!parent || parent === node) {
      roots.push(node);
      return;
    }
    parent.children.push(node);
  });

  return roots;
}

export function flattenChapterTree(nodes: ChapterTreeNode[]): ChapterTreeNode[] {
  return nodes.flatMap((node) => [node, ...flattenChapterTree(node.children)]);
}

export function findChapterRangeConflicts(chapters: ApiChapter[]): ChapterRangeConflict[] {
  const conflicts: ChapterRangeConflict[] = [];

  chapters.forEach((chapter, index) => {
    chapters.slice(index + 1).forEach((other) => {
      if (chapter.level !== other.level || (chapter.parent_id ?? null) !== (other.parent_id ?? null)) {
        return;
      }

      const overlapStart = Math.max(chapter.page_start, other.page_start);
      const overlapEnd = Math.min(chapter.page_end, other.page_end);
      if (overlapStart <= overlapEnd) {
        conflicts.push({
          chapterId: chapter.chapter_id,
          otherChapterId: other.chapter_id,
          overlapStart,
          overlapEnd
        });
      }
    });
  });

  return conflicts;
}

export function getChapterDescendantIds(chapters: ApiChapter[], rootId: string): string[] {
  const descendantIds = new Set<string>();
  let foundNewDescendant = true;

  while (foundNewDescendant) {
    foundNewDescendant = false;
    chapters.forEach((chapter) => {
      if (!chapter.parent_id || descendantIds.has(chapter.chapter_id)) return;
      if (chapter.parent_id === rootId || descendantIds.has(chapter.parent_id)) {
        descendantIds.add(chapter.chapter_id);
        foundNewDescendant = true;
      }
    });
  }

  return [...descendantIds];
}

export function validateChapterDraft(
  chapters: ApiChapter[],
  candidate: ApiChapter,
  pageCount?: number
): ChapterValidationIssue | null {
  if (!candidate.source_title.trim()) {
    return { code: "source-title-required", field: "source_title", message: "章节名称不能为空" };
  }
  if (!candidate.ai_title.trim()) {
    return { code: "ai-title-required", field: "ai_title", message: "课程章节名称不能为空" };
  }
  if (!Number.isInteger(candidate.page_start) || !Number.isInteger(candidate.page_end)) {
    return {
      code: "page-integer",
      field: Number.isInteger(candidate.page_start) ? "page_end" : "page_start",
      message: "页码必须是整数"
    };
  }
  if (candidate.page_start < 1) {
    return { code: "page-start-minimum", field: "page_start", message: "起始页必须大于等于 1" };
  }
  if (candidate.page_end < candidate.page_start) {
    return { code: "page-order", field: "page_end", message: "结束页不能小于起始页" };
  }
  if (pageCount && candidate.page_end > pageCount) {
    return { code: "page-count", field: "page_end", message: `页码不能超过文档总页数 ${pageCount}` };
  }

  const parent = candidate.parent_id
    ? chapters.find((chapter) => chapter.chapter_id === candidate.parent_id)
    : null;
  if (candidate.parent_id && !parent) {
    return { code: "parent-missing", field: "parent_id", message: "所选上级章节不存在" };
  }
  if (parent && candidate.page_start < parent.page_start) {
    return {
      code: "parent-range-start",
      field: "page_start",
      message: `页码必须位于上级章节的 ${parent.page_start}–${parent.page_end} 页内`
    };
  }
  if (parent && candidate.page_end > parent.page_end) {
    return {
      code: "parent-range-end",
      field: "page_end",
      message: `页码必须位于上级章节的 ${parent.page_start}–${parent.page_end} 页内`
    };
  }

  const childOutsideRange = chapters.find(
    (chapter) => chapter.parent_id === candidate.chapter_id
      && (chapter.page_start < candidate.page_start || chapter.page_end > candidate.page_end)
  );
  if (childOutsideRange) {
    return {
      code: "child-range",
      field: childOutsideRange.page_start < candidate.page_start ? "page_start" : "page_end",
      message: `页码范围必须包含子章节“${childOutsideRange.source_title}”`
    };
  }

  const conflict = chapters.find((chapter) => {
    if (chapter.chapter_id === candidate.chapter_id) return false;
    if (chapter.level !== candidate.level || (chapter.parent_id ?? null) !== (candidate.parent_id ?? null)) return false;
    return candidate.page_start <= chapter.page_end && chapter.page_start <= candidate.page_end;
  });
  if (conflict) {
    return {
      code: "sibling-overlap",
      field: "page_start",
      message: `与同级章节“${conflict.source_title}”的页码重叠`
    };
  }

  return null;
}
