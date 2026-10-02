import { StickerIcon } from "../components/icons/StickerIcon";
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState
} from "react";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Plus
} from "lucide-react";
import { ChapterToolCards } from "../components/study/ChapterToolCards";
import { Button, ProgressBar } from "../components/ui";
import { runtimeConfig } from "../config/runtime";
import { useAppContext } from "../context/AppContext";
import { CollapsibleRegion, MotionIconSwap } from "../motion";
import type { ApiChapter, StudyTask } from "../types/api";
import type { StudyLocation } from "../types/app";
import type { ChapterTreeNode } from "../utils/chapterStructure";
import { courseCoverImageUrl, liveBookTitle, sourcePageLabel } from "./shared";
import { studyToolDefinitions, type StudyToolId } from "./studyTools";
import { calculateChapterProgress } from "./studyProgress";
import { hasCompleteLoadedCourseContext } from "./sourceResourceIdentity";
import { buildStudyDirectory, normalizeStudyLocation } from "./studyDirectory";
import { currentStudyPlanTask, mergeFrontEndMockStudyTasks } from "./studyPlanPresentation";
import { preferredToolIds } from "../features/courses/recommendations";
import { bookIdFromResourceId } from "../features/courses/model";
import { courseLocationKey, courseSources, courseProgress } from "../features/courses/selectors";
import { useCoursePlans } from "../features/courses/useCoursePlans";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";

const curatedBiologyBookId = "book_biology_2";

function getDefaultLocation(chapters: ApiChapter[]): StudyLocation {
  return normalizeStudyLocation(buildStudyDirectory(chapters));
}

type StudySectionEntry = {
  chapter: ApiChapter;
  chapterIds: string[];
  depth: number;
};

function getStudySections(node: ChapterTreeNode): StudySectionEntry[] {
  return node.children.length > 0
    ? node.children.map((child) => ({
        chapter: child.chapter,
        chapterIds: getChapterNodeIds(child),
        depth: 0
      }))
    : [{ chapter: node.chapter, chapterIds: [node.chapter.chapter_id], depth: 0 }];
}

function chapterPageLabel(chapter: ApiChapter): string {
  const pageStart = chapter.printed_page_start ?? chapter.page_start;
  const pageEnd = chapter.printed_page_end ?? chapter.page_end;
  return `教材${sourcePageLabel(pageStart, pageEnd)}`;
}

function countFormalSections(node: ChapterTreeNode): number {
  return node.children.length;
}

function getChapterNodeIds(node: ChapterTreeNode): string[] {
  return [node.chapter.chapter_id, ...node.children.flatMap((child) => getChapterNodeIds(child))];
}

function SectionLearningPanel({ chapter }: { chapter: ApiChapter }) {
  const { go, courses, setActiveChapterId, uploadedFile, sourceSummaries } = useAppContext();
  const activeCourseProfile = courses.state.courses.find((item) => item.id === courses.state.activeCourseId
    && uploadedFile && item.resourceIds.some((id) => bookIdFromResourceId(id, courses.state.resources) === uploadedFile.bookId));
  const toolOrder = activeCourseProfile ? preferredToolIds(activeCourseProfile.diagnosis) : undefined;
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
    if (toolId === "notes") {
      go("notes");
      return;
    }
    go(toolId === "mistakes" ? "mistakes" : "flashcards");
  }

  if (sourceSummaries.find((source) => source.book_id === uploadedFile?.bookId)?.content_mode === "directory") {
    return <p className="study-directory-preview-note">目录演示 · {chapterPageLabel(chapter)}</p>;
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
      {activeCourseProfile?.diagnosis.aids.includes("video") ? (
        <div className="study-personal-aids" aria-label="偏好的学习辅助">
          <button type="button" onClick={() => openTool("source")}>查看已有图示与动画 · 视频待提供</button>
        </div>
      ) : null}
      <ChapterToolCards chapterTitle={chapter.source_title} onSelectTool={openTool} orderedToolIds={toolOrder} />
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
        {expanded ? <SectionLearningPanel chapter={chapter} /> : null}
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
  onToggleSection,
  directoryOnly = false
}: {
  node: ChapterTreeNode;
  chapterIndex: number;
  progress: number;
  tasks: StudyTask[];
  location: StudyLocation;
  onToggleChapter: () => void;
  onToggleSection: (sectionId: string) => void;
  directoryOnly?: boolean;
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
        scroller.querySelectorAll<HTMLElement>(".study-book-bar, .study-plan-summary")
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
        aria-label={`${node.chapter.source_title} ${formalSectionCount} 个小节 ${chapterPageLabel(node.chapter)}${directoryOnly ? "" : ` 学习进度 ${progress}%${complete ? " 已完成" : ""}`}`}
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
              progress={node.children.length === 0 ? progress : calculateChapterProgress(tasks, chapterIds)}
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
        {kind === "empty" ? <StickerIcon name="Upload" size={26} /> : <StickerIcon name="LibraryBig" size={26} />}
      </span>
      <h2>{kind === "empty" ? "开始你的第一门课程" : "课程资料还在准备中"}</h2>
      <p>{kind === "empty" ? "添加教材后，这里会按原书目录整理章节和每个小节的学习入口。" : "你可以查看解析进度，或先选择另一门已经就绪的课程。"}</p>
      <Button onClick={() => go("library")}>
        {kind === "empty" ? "创建课程" : "查看课程资料"}
      </Button>
    </section>
  );
}

export function StudyScreen() {
  const {
    sourceSelectionLoadingId,
    sourceSummaries,
    sourceSummariesLoadState,
    currentStudyPlan,
    generatedFlashcards,
    generatedLessons,
    generatedQuizzes,
    loadedBookId,
    courses,
    openSheet,
    parsedAssets,
    parsedChapters,
    parsedChunks,
    parsedScanResult,
    selectSource,
    selectCourse,
    setActiveChapterId,
    studyLocations,
    updateStudyLocation,
    uploadedFile,
    go
  } = useAppContext();
  const [attemptedBookId, setAttemptedBookId] = useState<string | null>(null);
  const activeCourse = courses.state.courses.find((course) => course.id === courses.state.activeCourseId);
  const sources = courseSources(activeCourse, courses.state.resources, sourceSummaries);
  const sourceKey = sources.map((source) => `${source.bookId}:${source.status}`).join("|");
  const repository = useBookCourseRepository();
  const [directories, setDirectories] = useState<Map<string, ApiChapter[]>>(new Map());
  const { plans } = useCoursePlans(activeCourse?.id);
  useEffect(() => {
    let active = true;
    void Promise.all(sources.filter((source) => source.bookId && source.status === "ready").map(async (source) => [source.bookId!, await repository.getChapters(source.bookId!).catch(() => [])] as const)).then((results) => { if (active) setDirectories(new Map(results)); });
    return () => { active = false; };
  }, [sourceKey, repository]);
  const [planCompact, setPlanCompact] = useState(false);
  const planCompactRef = useRef(false);
  const studyScreenRef = useRef<HTMLDivElement | null>(null);
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
  const chapterTree = useMemo(() => buildStudyDirectory(activeChapters ?? []), [activeChapters]);
  const currentBookId = hasLoadedCourse ? loadedBookId : null;
  const currentSource = sourceSummaries.find((source) => source.book_id === currentBookId);
  const directoryOnly = currentSource?.content_mode === "directory";
  const defaultLocation = useMemo(() => getDefaultLocation(activeChapters ?? []), [activeChapters]);
  const locationKey = currentBookId ? courseLocationKey(activeCourse?.id, currentBookId) : "";
  const location = currentBookId ? studyLocations[locationKey] ?? studyLocations[currentBookId] ?? defaultLocation : defaultLocation;

  useEffect(() => {
    if (!currentBookId || !activeChapters?.length) return;
    const savedLocation = studyLocations[locationKey] ?? studyLocations[currentBookId];
    const normalizedLocation = normalizeStudyLocation(chapterTree, savedLocation);
    if (
      savedLocation?.expandedChapterId === normalizedLocation.expandedChapterId
      && savedLocation.expandedSectionId === normalizedLocation.expandedSectionId
    ) return;
    updateStudyLocation(currentBookId, normalizedLocation);
    setActiveChapterId(normalizedLocation.expandedSectionId);
  }, [activeChapters, chapterTree, currentBookId, setActiveChapterId, studyLocations, updateStudyLocation]);

  useEffect(() => {
    if (!activeCourse || sourceSelectionLoadingId || attemptedBookId === activeCourse.id) return;
    if (uploadedFile && sources.some((source) => source.bookId === uploadedFile.bookId)) return;
    setAttemptedBookId(activeCourse.id);
    void selectCourse(activeCourse.id);
  }, [attemptedBookId, sourceSelectionLoadingId, sourceSummaries, courses.state.activeCourseId, courses.state.courses, parsedChapters, selectSource, uploadedFile]);

  useLayoutEffect(() => {
    const screen = studyScreenRef.current;
    const scroller = screen?.closest<HTMLElement>(".screen-content");
    const stickyStack = screen?.querySelector<HTMLElement>(".study-sticky-stack");
    if (!screen || !scroller || !stickyStack) return;

    const reserveExpandedHeight = () => {
      if (getComputedStyle(stickyStack).display === "contents") {
        stickyStack.style.removeProperty("--study-sticky-reserved-height");
        return;
      }
      // Measure the expanded layout only when content, width or fonts change.
      // Keeping its space prevents compaction from pulling the directory during a swipe.
      const expandedStack = stickyStack.cloneNode(true) as HTMLElement;
      expandedStack.classList.remove("is-plan-compact");
      expandedStack.querySelector(".study-plan-summary")?.classList.remove("is-compact");
      expandedStack.setAttribute("aria-hidden", "true");
      expandedStack.inert = true;
      Object.assign(expandedStack.style, {
        position: "absolute",
        visibility: "hidden",
        pointerEvents: "none",
        width: `${stickyStack.clientWidth}px`,
        height: "auto",
        minHeight: "0",
        inset: "0 auto auto 0"
      });
      screen.append(expandedStack);
      const expandedHeight = expandedStack.offsetHeight;
      expandedStack.remove();
      stickyStack.style.setProperty("--study-sticky-reserved-height", `${expandedHeight}px`);
    };
    let measuredWidth = screen.clientWidth;
    const resizeObserver = new ResizeObserver(() => {
      if (screen.clientWidth === measuredWidth) return;
      measuredWidth = screen.clientWidth;
      reserveExpandedHeight();
    });
    reserveExpandedHeight();
    resizeObserver.observe(screen);
    document.fonts.addEventListener("loadingdone", reserveExpandedHeight);

    let updateFrame: number | null = null;
    const updatePlanState = () => {
      const scrollTop = scroller.scrollTop;
      const planHasFocus = studyScreenRef.current
        ?.querySelector<HTMLElement>(".study-plan-summary")
        ?.contains(document.activeElement) ?? false;
      const nextCompact = planCompactRef.current
        ? scrollTop > 16
        : scrollTop > 48 && !planHasFocus;
      if (nextCompact === planCompactRef.current) return;
      planCompactRef.current = nextCompact;
      setPlanCompact(nextCompact);
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
      resizeObserver.disconnect();
      document.fonts.removeEventListener("loadingdone", reserveExpandedHeight);
      scroller.removeEventListener("scroll", schedulePlanStateUpdate);
      if (updateFrame !== null) window.cancelAnimationFrame(updateFrame);
    };
  }, [sourceSelectionLoadingId, sourceSummariesLoadState, currentBookId, parsedChapters?.length, currentStudyPlan, generatedLessons]);

  const studyTasks = useMemo(() => mergeFrontEndMockStudyTasks(
    currentStudyPlan?.tasks ?? [],
    uploadedFile,
    currentStudyPlan?.user_id ?? runtimeConfig.defaultUserId
  ), [currentStudyPlan, uploadedFile]);
  const totalTasks = studyTasks.length;
  const completedTasks = studyTasks.filter((task) => task.status === "done").length;
  const usesCuratedBiologyProgress = false;
  const chapterProgresses = chapterTree.map((node) => (
    calculateChapterProgress(studyTasks, getChapterNodeIds(node))
  ));
  const planProgress = sources.length > 1 ? courseProgress([...plans.values()]) : usesCuratedBiologyProgress && chapterProgresses.length > 0
    ? Math.round(chapterProgresses.reduce((sum, progress) => sum + progress, 0) / chapterProgresses.length)
    : totalTasks > 0
      ? Math.round((completedTasks / totalTasks) * 100)
      : 0;
  const visibleSectionIds = useMemo(
    () => new Set(chapterTree.flatMap((node) => getStudySections(node).map((section) => section.chapter.chapter_id))),
    [chapterTree]
  );
  const activePlanTask = currentStudyPlanTask(studyTasks);
  const activePlanSection = activeChapters?.find((chapter) => (
    chapter.chapter_id === activePlanTask?.chapter_id && visibleSectionIds.has(chapter.chapter_id)
  )) ?? null;
  const activePlanLesson = generatedLessons?.find((lesson) => lesson.chapter_id === activePlanSection?.chapter_id);
  const activePlanTitle = activePlanLesson?.title
    ?? activePlanSection?.source_title
    ?? activePlanTask?.title
    ?? chapterTree[0]?.chapter.source_title
    ?? "从第一节开始";
  const bookTitle = liveBookTitle(uploadedFile, parsedScanResult);
  const todayMinutes = activePlanTask?.minutes ?? currentStudyPlan?.daily_minutes ?? 25;

  function toggleChapter(node: ChapterTreeNode) {
    if (!currentBookId) return;
    if (location.expandedChapterId === node.chapter.chapter_id) {
      updateStudyLocation(currentBookId, { expandedChapterId: null });
      return;
    }
    const sections = getStudySections(node);
    const nextSectionId = sections.some((section) => section.chapter.chapter_id === location.expandedSectionId)
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

  if (sourceSummariesLoadState === "loading" || sourceSelectionLoadingId) {
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
          <button className="study-book-switch" type="button" onClick={() => openSheet({ type: "bookSwitcher" })}>
            <span className="study-book-cover-placeholder"><StickerIcon name="LibraryBig" size={19} aria-hidden="true" /></span>
            <span><small>当前课程</small><strong>{activeCourse?.name ?? "尚未选择"}</strong></span>
            <ChevronDown size={19} aria-hidden="true" />
          </button>
          <button type="button" className="study-add-button" onClick={() => { if (activeCourse) openSheet({ type: "addMaterials", courseId: activeCourse.id }); else { courses.startDraft(); go("courseSetup"); } }}>
            <Plus size={18} aria-hidden="true" />{activeCourse ? "添加资料" : "创建课程"}
          </button>
        </header>
        <StudyEmptyState kind={sourceSummaries.length === 0 ? "empty" : "unavailable"} />
      </div>
    );
  }

  return (
    <div ref={studyScreenRef} className="study-screen book-course-screen" data-directory-only={directoryOnly ? "true" : undefined}>
      <div className={`study-sticky-stack ${planCompact ? "is-plan-compact" : ""}`}>
        <header className="study-book-bar">
          <button className="study-book-switch" type="button" onClick={() => openSheet({ type: "bookSwitcher" })}>
            <img src={courseCoverImageUrl(uploadedFile.bookId)} alt="" />
            <span>
              <small>当前课程</small>
              <strong>{activeCourse?.name ?? bookTitle}</strong>
            </span>
            <ChevronDown size={19} aria-hidden="true" />
          </button>
          <button type="button" className="study-add-button" onClick={() => activeCourse && openSheet({ type: "addMaterials", courseId: activeCourse.id })}>
            <Plus size={18} aria-hidden="true" />添加资料
          </button>
        </header>

        {directoryOnly ? <p className="study-directory-preview-note">演示课程 · 仅展示教材封面、标题和原书目录</p> : <section
          className={`study-plan-summary ${planCompact ? "is-compact" : ""}`}
          data-plan-state={planCompact ? "compact" : "expanded"}
          aria-label="学习计划"
        >
          <div className="study-plan-details" aria-hidden={planCompact}>
            <div className="study-plan-heading">
              <h1>学习计划</h1>
              <button type="button" tabIndex={planCompact ? -1 : undefined} onClick={() => go("plan")}>
                计划详情
                <ChevronRight size={16} aria-hidden="true" />
              </button>
            </div>
            <div className="study-plan-copy">
              <span className="study-plan-icon" aria-hidden="true"><StickerIcon name="Check" size={18} /></span>
              <div>
                <small>
                  今日建议 · {todayMinutes} 分钟
                </small>
                <strong>{activePlanTitle}</strong>
              </div>
            </div>
          </div>
          <ProgressBar value={planProgress} label={`计划完成 ${planProgress}%`} />
        </section>}
      </div>

      <section className="study-directory" aria-labelledby="study-directory-title">
        <div className="study-directory-heading">
          <h2 id="study-directory-title">课程目录</h2>
          <span>{directoryOnly ? chapterTree.filter((node) => node.children.length > 0).length : chapterTree.length} {currentSource?.directory_unit_label ?? "章"} · {chapterTree.reduce((sum, node) => sum + countFormalSections(node), 0)} {currentSource?.directory_unit_label ? "个目录项" : "节"}</span>
        </div>
        <div className="course-source-heading"><strong>{bookTitle}</strong><button type="button" onClick={() => go("courseDetail")}>管理资料 <ChevronRight size={16} /></button></div>
        <div className="study-chapter-list">
          {chapterTree.map((node, index) => directoryOnly && node.children.length === 0 ? (
            <article className="study-directory-reference" key={node.chapter.chapter_id}>
              <strong>{node.chapter.source_title}</strong><small>{chapterPageLabel(node.chapter)}</small>
            </article>
          ) : (
            <StudyChapter
              key={node.chapter.chapter_id}
              node={node}
              chapterIndex={index}
              progress={chapterProgresses[index] ?? 0}
              tasks={studyTasks}
              location={location}
              onToggleChapter={() => toggleChapter(node)}
              onToggleSection={toggleSection}
              directoryOnly={directoryOnly}
            />
          ))}
        </div>
        {sources.filter((source) => source.bookId !== currentBookId).map((source) => <section className="course-source-group" key={source.id} aria-label={source.name}>
          <div className="course-source-heading"><strong>{source.name}</strong><small>{source.status === "ready" ? "课程资料" : source.local?.error ?? "正在准备"}</small></div>
          {(directories.get(source.bookId ?? "") ?? []).filter((chapter) => chapter.level === 1).map((chapter) => <button className="course-source-chapter" type="button" key={chapter.chapter_id} onClick={() => { if (source.bookId) void selectSource(source.bookId, activeCourse?.id).then((opened) => { if (opened) { updateStudyLocation(source.bookId!, { expandedChapterId: chapter.chapter_id, expandedSectionId: null }); setActiveChapterId(chapter.chapter_id); } }); }}><span>{chapter.source_title}</span><small>{sourcePageLabel(chapter.page_start, chapter.page_end)}</small><ChevronRight size={18} /></button>)}
        </section>)}
      </section>
    </div>
  );
}
