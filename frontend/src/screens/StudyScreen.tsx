import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent
} from "react";
import {
  Check,
  ChevronDown,
  ChevronRight,
  LibraryBig,
  Plus,
  Upload
} from "lucide-react";
import { ChapterToolCards } from "../components/study/ChapterToolCards";
import { Button, ProgressBar } from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { CollapsibleRegion, MotionIconSwap } from "../motion";
import type { ApiChapter, StudyTask } from "../types/api";
import type { StudyLocation } from "../types/app";
import { buildChapterTree, type ChapterTreeNode } from "../utils/chapterStructure";
import { liveBookTitle, sourcePageImageUrl, sourcePageLabel } from "./shared";
import { studyToolDefinitions, type StudyToolId } from "./studyTools";
import { calculateChapterProgress } from "./studyProgress";
import { hasCompleteLoadedCourseContext } from "./courseResourceIdentity";

function getDefaultLocation(chapters: ApiChapter[]): StudyLocation {
  const [firstRoot] = buildChapterTree(chapters);
  const firstSection = firstRoot ? getStudySections(firstRoot)[0]?.chapter ?? firstRoot.chapter : null;
  return {
    expandedChapterId: firstRoot?.chapter.chapter_id ?? null,
    expandedSectionId: firstSection?.chapter_id ?? null
  };
}

type StudySectionEntry = {
  chapter: ApiChapter;
  chapterIds: string[];
  depth: number;
};

function flattenStudySections(nodes: ChapterTreeNode[], depth = 0): StudySectionEntry[] {
  return nodes.flatMap((node) => [
    { chapter: node.chapter, chapterIds: getChapterNodeIds(node), depth },
    ...flattenStudySections(node.children, depth + 1)
  ]);
}

function getStudySections(node: ChapterTreeNode): StudySectionEntry[] {
  return node.children.length > 0
    ? flattenStudySections(node.children)
    : [{ chapter: node.chapter, chapterIds: [node.chapter.chapter_id], depth: 0 }];
}

function chapterPageLabel(chapter: ApiChapter): string {
  const pageStart = chapter.printed_page_start ?? chapter.page_start;
  const pageEnd = chapter.printed_page_end ?? chapter.page_end;
  return `教材${sourcePageLabel(pageStart, pageEnd)}`;
}

function countFormalSections(node: ChapterTreeNode): number {
  return node.children.filter((child) => /^第\s*\d+\s*节/.test(child.chapter.source_title)).length;
}

function getChapterNodeIds(node: ChapterTreeNode): string[] {
  return [node.chapter.chapter_id, ...node.children.flatMap((child) => getChapterNodeIds(child))];
}

function isSectionInChapter(node: ChapterTreeNode, sectionId: string | null): boolean {
  if (!sectionId) return false;
  return node.chapter.chapter_id === sectionId
    || node.children.some((child) => isSectionInChapter(child, sectionId));
}

function SectionLearningPanel({ chapter }: { chapter: ApiChapter }) {
  const { go, setActiveChapterId } = useAppContext();
  const primaryTool = studyToolDefinitions.find((tool) => tool.id === "source");

  function openTool(toolId: StudyToolId) {
    setActiveChapterId(chapter.chapter_id);
    if (toolId === "source") {
      go("lesson");
      return;
    }
    if (toolId === "assignment") {
      go("assignment");
      return;
    }
    go(toolId === "mistakes" ? "mistakes" : "flashcards");
  }

  return (
    <section className="study-tools-panel" aria-label={`${chapter.source_title}的学习方式`}>
      <div className="study-tools-heading">
        <div>
          <strong>选择下一步</strong>
          <small>{chapterPageLabel(chapter)}</small>
        </div>
        <button className="study-enter-button" type="button" onClick={() => openTool("source")}>
          {primaryTool?.title ?? "进入学习"}
          <ChevronRight size={17} aria-hidden="true" />
        </button>
      </div>
      <ChapterToolCards chapterTitle={chapter.source_title} onSelectTool={openTool} />
    </section>
  );
}

function StudySection({
  chapter,
  depth,
  expanded,
  progress,
  onToggle
}: {
  chapter: ApiChapter;
  depth: number;
  expanded: boolean;
  progress: number;
  onToggle: () => void;
}) {
  const toggleRef = useRef<HTMLButtonElement | null>(null);
  const safeId = chapter.chapter_id.replace(/[^a-zA-Z0-9_-]/g, "-");
  const toggleId = `study-section-${safeId}-toggle`;
  const regionId = `study-section-${safeId}-content`;
  const complete = progress >= 100;

  return (
    <div className={`study-section ${expanded ? "is-expanded" : ""} ${depth > 0 ? "is-nested" : ""} ${complete ? "is-complete" : ""}`}>
      <button
        ref={toggleRef}
        id={toggleId}
        className="study-section-toggle"
        type="button"
        aria-label={`${chapter.source_title} ${chapterPageLabel(chapter)}${complete ? " 已完成" : ""}`}
        aria-expanded={expanded}
        aria-controls={regionId}
        onClick={onToggle}
      >
        <span className="study-path-node" aria-hidden="true">
          {expanded ? <span /> : null}
        </span>
        <span className="study-section-copy">
          <strong>{chapter.source_title}</strong>
          <small>{chapterPageLabel(chapter)}</small>
        </span>
        <span className="study-section-status" aria-hidden="true">{complete ? "已完成" : ""}</span>
        <MotionIconSwap
          state={expanded ? "expanded" : "collapsed"}
          firstState="collapsed"
          secondState="expanded"
          firstIcon={<ChevronRight size={19} />}
          secondIcon={<ChevronDown size={19} />}
        />
      </button>
      <CollapsibleRegion
        expanded={expanded}
        id={regionId}
        labelledBy={toggleId}
        focusFallbackRef={toggleRef}
        className="study-section-region"
      >
        <SectionLearningPanel chapter={chapter} />
      </CollapsibleRegion>
    </div>
  );
}

function StudyChapter({
  node,
  chapterIndex,
  progress,
  tasks,
  location,
  onToggleChapter,
  onToggleSection
}: {
  node: ChapterTreeNode;
  chapterIndex: number;
  progress: number;
  tasks: StudyTask[];
  location: StudyLocation;
  onToggleChapter: () => void;
  onToggleSection: (sectionId: string) => void;
}) {
  const expanded = location.expandedChapterId === node.chapter.chapter_id;
  const articleRef = useRef<HTMLElement | null>(null);
  const toggleRef = useRef<HTMLButtonElement | null>(null);
  const wasExpandedRef = useRef(expanded);
  const safeId = node.chapter.chapter_id.replace(/[^a-zA-Z0-9_-]/g, "-");
  const toggleId = `study-chapter-${safeId}-toggle`;
  const regionId = `study-chapter-${safeId}-content`;
  const sections = getStudySections(node);
  const formalSectionCount = countFormalSections(node);
  const complete = progress >= 100;

  useEffect(() => {
    const opened = expanded && !wasExpandedRef.current;
    wasExpandedRef.current = expanded;
    if (!opened) return;

    const article = articleRef.current;
    const region = article?.querySelector<HTMLElement>(".study-chapter-region");
    const scroller = article?.closest<HTMLElement>(".screen-content");
    if (!article || !region || !scroller) return;

    let cancelled = false;
    let positionFrame: number | null = null;
    let fallbackTimer: number | null = null;

    const removeTransitionListener = () => {
      region.removeEventListener("transitionend", handleTransitionEnd);
    };
    const positionExpandedChapter = () => {
      if (cancelled) return;
      const articleRect = article.getBoundingClientRect();
      const scrollerRect = scroller.getBoundingClientRect();
      const stickyBottom = Array.from(
        scroller.querySelectorAll<HTMLElement>(".study-sticky-stack, .study-book-bar, .study-plan-summary")
      ).reduce((bottom, element) => {
        const rect = element.getBoundingClientRect();
        const horizontallyOverlaps = rect.right > articleRect.left && rect.left < articleRect.right;
        return rect.height > 0 && horizontallyOverlaps ? Math.max(bottom, rect.bottom) : bottom;
      }, scrollerRect.top);
      const desiredTop = stickyBottom + 8;
      const nextScrollTop = Math.max(0, scroller.scrollTop + articleRect.top - desiredTop);
      if (Math.abs(articleRect.top - desiredTop) < 2) return;
      scroller.scrollTo({
        top: nextScrollTop,
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"
      });
    };
    const settleAndPosition = () => {
      if (cancelled) return;
      removeTransitionListener();
      if (fallbackTimer !== null) window.clearTimeout(fallbackTimer);
      positionFrame = window.requestAnimationFrame(positionExpandedChapter);
    };
    function handleTransitionEnd(event: TransitionEvent) {
      if (event.target === region && event.propertyName === "grid-template-rows") {
        settleAndPosition();
      }
    }
    const cancelForUserInput = () => {
      cancelled = true;
      removeTransitionListener();
      if (fallbackTimer !== null) window.clearTimeout(fallbackTimer);
      if (positionFrame !== null) window.cancelAnimationFrame(positionFrame);
      const currentScrollTop = scroller.scrollTop;
      const inlineScrollBehavior = scroller.style.scrollBehavior;
      scroller.style.scrollBehavior = "auto";
      scroller.scrollTop = currentScrollTop;
      scroller.style.scrollBehavior = inlineScrollBehavior;
    };

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      positionFrame = window.requestAnimationFrame(positionExpandedChapter);
    } else {
      region.addEventListener("transitionend", handleTransitionEnd);
      fallbackTimer = window.setTimeout(settleAndPosition, 320);
    }
    scroller.addEventListener("pointerdown", cancelForUserInput, { once: true, passive: true });
    scroller.addEventListener("touchstart", cancelForUserInput, { once: true, passive: true });
    scroller.addEventListener("wheel", cancelForUserInput, { once: true, passive: true });

    return () => {
      cancelled = true;
      removeTransitionListener();
      scroller.removeEventListener("pointerdown", cancelForUserInput);
      scroller.removeEventListener("touchstart", cancelForUserInput);
      scroller.removeEventListener("wheel", cancelForUserInput);
      if (fallbackTimer !== null) window.clearTimeout(fallbackTimer);
      if (positionFrame !== null) window.cancelAnimationFrame(positionFrame);
    };
  }, [expanded]);

  return (
    <article ref={articleRef} className={`study-chapter ${expanded ? "is-expanded" : ""}`}>
      <button
        ref={toggleRef}
        id={toggleId}
        className="study-chapter-toggle"
        type="button"
        aria-label={`${node.chapter.source_title} ${formalSectionCount} 个小节 ${chapterPageLabel(node.chapter)} 学习进度 ${progress}%${complete ? " 已完成" : ""}`}
        aria-expanded={expanded}
        aria-controls={regionId}
        onClick={onToggleChapter}
      >
        <span
          className={`study-chapter-progress ${complete ? "is-complete" : ""}`}
          data-progress={progress}
          aria-hidden="true"
        >
          <svg viewBox="0 0 44 44">
            <circle className="study-chapter-progress-track" cx="22" cy="22" r="18" />
            <circle
              className="study-chapter-progress-value"
              cx="22"
              cy="22"
              r="18"
              pathLength="100"
              style={{ strokeDashoffset: 100 - progress }}
            />
          </svg>
          <span>{complete ? <Check size={19} strokeWidth={3} /> : chapterIndex + 1}</span>
        </span>
        <span className="study-chapter-copy">
          <strong>{node.chapter.source_title}</strong>
          <small>{formalSectionCount} 个小节 · {chapterPageLabel(node.chapter)}</small>
        </span>
        <MotionIconSwap
          state={expanded ? "expanded" : "collapsed"}
          firstState="collapsed"
          secondState="expanded"
          firstIcon={<ChevronRight size={20} />}
          secondIcon={<ChevronDown size={20} />}
        />
      </button>
      <CollapsibleRegion
        expanded={expanded}
        id={regionId}
        labelledBy={toggleId}
        focusFallbackRef={toggleRef}
        className="study-chapter-region"
      >
        <div className="study-section-list">
          {sections.map(({ chapter, chapterIds, depth }) => (
            <StudySection
              chapter={chapter}
              depth={depth}
              expanded={location.expandedSectionId === chapter.chapter_id}
              key={chapter.chapter_id}
              progress={calculateChapterProgress(tasks, chapterIds)}
              onToggle={() => onToggleSection(chapter.chapter_id)}
            />
          ))}
        </div>
      </CollapsibleRegion>
    </article>
  );
}

function StudyEmptyState({ kind }: { kind: "empty" | "unavailable" }) {
  const { go } = useAppContext();
  return (
    <section className="study-empty-state">
      <span className="study-empty-icon" aria-hidden="true">
        {kind === "empty" ? <Upload size={26} /> : <LibraryBig size={26} />}
      </span>
      <h2>{kind === "empty" ? "开始你的第一门课程" : "教材还在准备中"}</h2>
      <p>{kind === "empty" ? "添加教材后，这里会按原书目录整理章节和每个小节的学习入口。" : "你可以查看解析进度，或先选择另一门已经就绪的课程。"}</p>
      <Button onClick={() => go(kind === "empty" ? "upload" : "library")}>
        {kind === "empty" ? "添加教材" : "查看课程状态"}
      </Button>
    </section>
  );
}

export function StudyScreen() {
  const {
    courseSelectionLoadingId,
    courseSummaries,
    courseSummariesLoadState,
    currentStudyPlan,
    generatedFlashcards,
    generatedLessons,
    generatedQuizzes,
    loadedBookId,
    openSheet,
    parsedAssets,
    parsedChapters,
    parsedChunks,
    parsedScanResult,
    selectCourse,
    setActiveChapterId,
    studyLocations,
    updateStudyLocation,
    uploadedFile,
    go
  } = useAppContext();
  const [attemptedBookId, setAttemptedBookId] = useState<string | null>(null);
  const [planCompact, setPlanCompact] = useState(false);
  const [directoryDragging, setDirectoryDragging] = useState(false);
  const studyScreenRef = useRef<HTMLDivElement | null>(null);
  const directoryDragRef = useRef<{
    pointerId: number;
    startY: number;
    startScrollTop: number;
    moved: boolean;
    scroller: HTMLElement;
  } | null>(null);
  const suppressDirectoryClickRef = useRef(false);
  const hasLoadedCourse = hasCompleteLoadedCourseContext({
    loadedBookId,
    uploadedFile,
    parsedScanResult,
    parsedChapters,
    parsedChunks,
    parsedAssets,
    currentStudyPlan,
    generatedLessons,
    generatedFlashcards,
    generatedQuizzes
  });
  const activeChapters = hasLoadedCourse ? parsedChapters : null;
  const chapterTree = useMemo(() => buildChapterTree(activeChapters ?? []), [activeChapters]);
  const currentBookId = hasLoadedCourse ? loadedBookId : null;
  const defaultLocation = useMemo(() => getDefaultLocation(activeChapters ?? []), [activeChapters]);
  const location = currentBookId ? studyLocations[currentBookId] ?? defaultLocation : defaultLocation;

  useEffect(() => {
    if (currentBookId && activeChapters?.length && !studyLocations[currentBookId]) {
      updateStudyLocation(currentBookId, defaultLocation);
      setActiveChapterId(defaultLocation.expandedSectionId);
    }
  }, [activeChapters, currentBookId, defaultLocation, setActiveChapterId, studyLocations, updateStudyLocation]);

  useEffect(() => {
    if (uploadedFile) return;
    const readyCourse = courseSummaries.find((course) => course.status === "ready");
    if (!readyCourse || attemptedBookId === readyCourse.book_id || courseSelectionLoadingId) return;
    setAttemptedBookId(readyCourse.book_id);
    void selectCourse(readyCourse.book_id);
  }, [attemptedBookId, courseSelectionLoadingId, courseSummaries, parsedChapters, selectCourse, uploadedFile]);

  useEffect(() => {
    const scroller = studyScreenRef.current?.closest<HTMLElement>(".screen-content");
    if (!scroller) return;

    let updateFrame: number | null = null;
    const updatePlanState = () => {
      const scrollTop = scroller.scrollTop;
      const planHasFocus = studyScreenRef.current
        ?.querySelector<HTMLElement>(".study-plan-summary")
        ?.contains(document.activeElement) ?? false;
      setPlanCompact((current) => (
        current ? scrollTop > 16 : scrollTop > 48 && !planHasFocus
      ));
    };
    const schedulePlanStateUpdate = () => {
      if (updateFrame !== null) return;
      updateFrame = window.requestAnimationFrame(() => {
        updateFrame = null;
        updatePlanState();
      });
    };

    updatePlanState();
    scroller.addEventListener("scroll", schedulePlanStateUpdate, { passive: true });
    return () => {
      scroller.removeEventListener("scroll", schedulePlanStateUpdate);
      if (updateFrame !== null) window.cancelAnimationFrame(updateFrame);
    };
  }, [courseSelectionLoadingId, courseSummariesLoadState, currentBookId, parsedChapters?.length]);

  const totalTasks = currentStudyPlan?.tasks.length ?? 0;
  const completedTasks = currentStudyPlan?.tasks.filter((task) => task.status === "done").length ?? 0;
  const planProgress = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;
  const currentSection = activeChapters?.find((chapter) => chapter.chapter_id === location.expandedSectionId)
    ?? activeChapters?.[0]
    ?? null;
  const currentLesson = generatedLessons?.find((lesson) => lesson.chapter_id === currentSection?.chapter_id);
  const bookTitle = liveBookTitle(uploadedFile, parsedScanResult);
  const todayMinutes = currentStudyPlan?.daily_minutes ?? 25;

  function toggleChapter(node: ChapterTreeNode) {
    if (!currentBookId) return;
    if (location.expandedChapterId === node.chapter.chapter_id) {
      updateStudyLocation(currentBookId, { expandedChapterId: null });
      return;
    }
    const sections = getStudySections(node);
    const nextSectionId = isSectionInChapter(node, location.expandedSectionId)
      ? location.expandedSectionId
      : sections[0]?.chapter.chapter_id ?? node.chapter.chapter_id;
    updateStudyLocation(currentBookId, {
      expandedChapterId: node.chapter.chapter_id,
      expandedSectionId: nextSectionId
    });
    setActiveChapterId(nextSectionId);
  }

  function toggleSection(sectionId: string) {
    if (!currentBookId) return;
    const nextSectionId = location.expandedSectionId === sectionId ? null : sectionId;
    updateStudyLocation(currentBookId, { expandedSectionId: nextSectionId });
    if (nextSectionId) setActiveChapterId(nextSectionId);
  }

  function startDirectoryMouseDrag(event: ReactPointerEvent<HTMLElement>) {
    if (event.pointerType !== "mouse" || event.button !== 0) return;
    const scroller = event.currentTarget.closest<HTMLElement>(".screen-content");
    if (!scroller) return;
    directoryDragRef.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      startScrollTop: scroller.scrollTop,
      moved: false,
      scroller
    };
  }

  function moveDirectoryWithMouse(event: ReactPointerEvent<HTMLElement>) {
    const drag = directoryDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const distance = event.clientY - drag.startY;
    if (!drag.moved) {
      if (Math.abs(distance) < 5) return;
      drag.moved = true;
      event.currentTarget.setPointerCapture(event.pointerId);
      setDirectoryDragging(true);
    }
    event.preventDefault();
    drag.scroller.scrollTop = drag.startScrollTop - distance;
  }

  function finishDirectoryMouseDrag(event: ReactPointerEvent<HTMLElement>) {
    const drag = directoryDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (drag.moved) {
      suppressDirectoryClickRef.current = true;
      window.setTimeout(() => {
        suppressDirectoryClickRef.current = false;
      }, 0);
    }
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    directoryDragRef.current = null;
    setDirectoryDragging(false);
  }

  function suppressClickAfterDirectoryDrag(event: ReactMouseEvent<HTMLElement>) {
    if (!suppressDirectoryClickRef.current) return;
    event.preventDefault();
    event.stopPropagation();
    suppressDirectoryClickRef.current = false;
  }

  if (courseSummariesLoadState === "loading" || courseSelectionLoadingId) {
    return (
      <div className="study-screen book-course-screen" aria-busy="true">
        <div className="study-loading-bar" />
        <div className="study-loading-heading" />
        <div className="study-loading-card" />
        <div className="study-loading-list" />
        <span className="sr-only">正在打开学习页</span>
      </div>
    );
  }

  if (!hasLoadedCourse || !uploadedFile || !activeChapters?.length) {
    return (
      <div className="study-screen book-course-screen">
        <header className="study-book-bar study-book-bar-empty">
          <div>
            <span className="study-book-cover-placeholder"><LibraryBig size={19} aria-hidden="true" /></span>
            <span><small>当前教材</small><strong>尚未选择</strong></span>
          </div>
          <button type="button" className="study-add-button" onClick={() => go("upload")}>
            <Plus size={18} aria-hidden="true" />添加
          </button>
        </header>
        <StudyEmptyState kind={courseSummaries.length === 0 ? "empty" : "unavailable"} />
      </div>
    );
  }

  return (
    <div ref={studyScreenRef} className="study-screen book-course-screen">
      <div className={`study-sticky-stack ${planCompact ? "is-plan-compact" : ""}`}>
        <header className="study-book-bar">
          <button className="study-book-switch" type="button" onClick={() => openSheet({ type: "bookSwitcher" })}>
            <img src={sourcePageImageUrl(uploadedFile.bookId, 1)} alt="" />
            <span>
              <small>当前教材</small>
              <strong>{bookTitle}</strong>
            </span>
            <ChevronDown size={19} aria-hidden="true" />
          </button>
          <button type="button" className="study-add-button" onClick={() => go("upload")}>
            <Plus size={18} aria-hidden="true" />添加
          </button>
        </header>

        <section
          className={`study-plan-summary ${planCompact ? "is-compact" : ""}`}
          data-plan-state={planCompact ? "compact" : "expanded"}
          aria-label="学习计划"
        >
          {!planCompact ? (
            <>
              <div className="study-plan-heading">
                <h1>学习计划</h1>
                <button type="button" onClick={() => go("plan")}>
                  计划详情
                  <ChevronRight size={16} aria-hidden="true" />
                </button>
              </div>
              <div className="study-plan-copy">
                <span className="study-plan-icon" aria-hidden="true"><Check size={18} /></span>
                <div>
                  <small>今日建议 · {todayMinutes} 分钟</small>
                  <strong>{currentLesson?.title ?? currentSection?.source_title ?? "从第一节开始"}</strong>
                </div>
              </div>
            </>
          ) : null}
          <ProgressBar value={planProgress} label={`计划完成 ${planProgress}%`} />
        </section>
      </div>

      <section
        className={`study-directory ${directoryDragging ? "is-mouse-dragging" : ""}`}
        aria-labelledby="study-directory-title"
        onClickCapture={suppressClickAfterDirectoryDrag}
        onPointerCancel={finishDirectoryMouseDrag}
        onPointerDown={startDirectoryMouseDrag}
        onPointerMove={moveDirectoryWithMouse}
        onPointerUp={finishDirectoryMouseDrag}
      >
        <div className="study-directory-heading">
          <h2 id="study-directory-title">教材目录</h2>
          <span>{chapterTree.length} 章</span>
        </div>
        <div className="study-chapter-list">
          {chapterTree.map((node, index) => (
            <StudyChapter
              key={node.chapter.chapter_id}
              node={node}
              chapterIndex={index}
              progress={calculateChapterProgress(currentStudyPlan?.tasks ?? [], getChapterNodeIds(node))}
              tasks={currentStudyPlan?.tasks ?? []}
              location={location}
              onToggleChapter={() => toggleChapter(node)}
              onToggleSection={toggleSection}
            />
          ))}
        </div>
      </section>
    </div>
  );
}
