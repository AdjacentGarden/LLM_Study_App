import type { ApiChapter, Lesson, StudyPlan } from "../types/api";
import type { StudyLocation } from "../types/app";

export type HomeNextStepSource = "location" | "plan" | "lesson" | "directory";

export type HomeNextStep = Readonly<{
  chapter: ApiChapter;
  expandedChapterId: string;
  source: HomeNextStepSource;
  sourceHint: string;
  pageLabel: string;
}>;

export type ResolveHomeNextStepInput = Readonly<{
  chapters: readonly ApiChapter[];
  location?: StudyLocation | null;
  plan?: StudyPlan | null;
  lessons?: readonly Lesson[] | null;
}>;

const sourceHints: Record<HomeNextStepSource, string> = {
  location: "从上次展开的小节继续",
  plan: "来自当前学习计划的首个待完成任务",
  lesson: "来自这本书已生成的课程内容",
  directory: "从教材目录的第一可学习小节开始"
};

function pageRange(start: number, end?: number | null) {
  return end && end !== start ? `${start}-${end}` : `${start}`;
}

export function homeChapterPageLabel(chapter: ApiChapter) {
  const pdfPages = pageRange(chapter.page_start, chapter.page_end);
  if (chapter.printed_page_start == null) return `原书第 ${pdfPages} 页`;
  const printedPages = pageRange(chapter.printed_page_start, chapter.printed_page_end);
  return `原书第 ${printedPages} 页 · PDF 第 ${pdfPages} 页`;
}

type NavigableChapter = Readonly<{
  chapter: ApiChapter;
  expandedChapterId: string;
}>;

type ChapterNavigationGraph = Readonly<{
  chaptersById: ReadonlyMap<string, ApiChapter>;
  childrenById: ReadonlyMap<string, readonly string[]>;
}>;

function createChapterNavigationGraph(chapters: readonly ApiChapter[]): ChapterNavigationGraph {
  const chaptersById = new Map<string, ApiChapter>();
  const childrenById = new Map<string, string[]>();
  chapters.forEach((chapter) => {
    if (!chaptersById.has(chapter.chapter_id)) chaptersById.set(chapter.chapter_id, chapter);
    if (!childrenById.has(chapter.chapter_id)) childrenById.set(chapter.chapter_id, []);
  });
  chapters.forEach((chapter) => {
    if (!chapter.parent_id || chapter.parent_id === chapter.chapter_id || !chaptersById.has(chapter.parent_id)) return;
    childrenById.get(chapter.parent_id)?.push(chapter.chapter_id);
  });
  return { chaptersById, childrenById };
}

function navigableChapter(
  chapterId: string | null | undefined,
  graph: ChapterNavigationGraph
): NavigableChapter | null {
  if (!chapterId) return null;
  const chapter = graph.chaptersById.get(chapterId);
  if (!chapter) return null;

  let current = chapter;
  const visited = new Set<string>();
  while (true) {
    if (visited.has(current.chapter_id)) return null;
    visited.add(current.chapter_id);
    if (!current.parent_id) break;
    const parent = graph.chaptersById.get(current.parent_id);
    if (!parent) break;
    current = parent;
  }

  const root = current;
  const rootIsDirectoryContainer = root.chapter_id === chapter.chapter_id
    && (graph.childrenById.get(root.chapter_id)?.length ?? 0) > 0;
  if (rootIsDirectoryContainer) return null;
  return { chapter, expandedChapterId: root.chapter_id };
}

function chapterFromExactReviewTarget(
  reviewTarget: string | null | undefined,
  graph: ChapterNavigationGraph
) {
  if (!reviewTarget) return null;
  const byId = navigableChapter(reviewTarget, graph);
  if (byId) return byId;
  const exactTitleMatches = [...graph.chaptersById.values()].filter(
    (chapter) => chapter.source_title === reviewTarget || chapter.ai_title === reviewTarget
  );
  if (exactTitleMatches.length !== 1) return null;
  return navigableChapter(exactTitleMatches[0].chapter_id, graph);
}

function buildResult(candidate: NavigableChapter, source: HomeNextStepSource): HomeNextStep {
  return {
    chapter: candidate.chapter,
    expandedChapterId: candidate.expandedChapterId,
    source,
    sourceHint: sourceHints[source],
    pageLabel: homeChapterPageLabel(candidate.chapter)
  };
}

export function resolveHomeNextStep({
  chapters,
  location,
  plan,
  lessons
}: ResolveHomeNextStepInput): HomeNextStep | null {
  if (chapters.length === 0) return null;
  const graph = createChapterNavigationGraph(chapters);

  const locationChapter = navigableChapter(location?.expandedSectionId, graph);
  if (locationChapter) return buildResult(locationChapter, "location");

  for (const task of plan?.tasks ?? []) {
    if (task.status === "done") continue;
    const taskChapter = navigableChapter(task.chapter_id, graph)
      ?? chapterFromExactReviewTarget(task.review_target, graph)
      ?? navigableChapter(
        lessons?.find((lesson) => lesson.lesson_id === task.lesson_id)?.chapter_id,
        graph
      );
    if (taskChapter) return buildResult(taskChapter, "plan");
  }

  for (const lesson of lessons ?? []) {
    if (lesson.lesson_kind === "module_intro") continue;
    const lessonChapter = navigableChapter(lesson.chapter_id, graph);
    if (lessonChapter) return buildResult(lessonChapter, "lesson");
  }

  for (const chapter of chapters) {
    const directoryChapter = navigableChapter(chapter.chapter_id, graph);
    if (directoryChapter) return buildResult(directoryChapter, "directory");
  }

  return null;
}
