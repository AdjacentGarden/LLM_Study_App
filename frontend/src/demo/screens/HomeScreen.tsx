import { useEffect, useMemo, useRef, useState } from "react";
import { StickerIcon } from "../components/icons/StickerIcon";
import { Upload } from "lucide-react";
import { HomeBookCarousel } from "../components/home/HomeBookCarousel";
import { SelectedBookWorkspace } from "../components/home/SelectedBookWorkspace";
import type { ChapterToolId } from "../components/study/ChapterToolCards";
import { useAppContext } from "../context/AppContext";
import { courseLocationKey, preferredCourseSource } from "../features/courses/selectors";
import { buildHomeBookModels, type HomeBookModel } from "./homeBookModel";
import { resolveHomeNextStep } from "./homeNextStep";
import { hasCompleteLoadedCourseContext } from "./sourceResourceIdentity";
import { courseCoverImageUrl } from "./shared";

export function HomeScreen() {
  const ctx = useAppContext();
  const { courses, sourceSummaries, loadedBookId, uploadedFile, parsedScanResult, parsedChapters, parsedChunks, parsedAssets, currentStudyPlan, generatedLessons, generatedFlashcards, generatedQuizzes, selectCourse, go, openSourcePage, setActiveChapterId, updateStudyLocation, studyLocations } = ctx;
  const [selectedId, setSelectedId] = useState<string | null>(courses.state.activeCourseId);
  const [openingId, setOpeningId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failedCourseId, setFailedCourseId] = useState<string | null>(null);
  const selectionEpoch = useRef(0);
  const models = useMemo(() => {
    const models = courses.state.courses.map((course) => {
      const source = preferredCourseSource(course, courses.state.resources, sourceSummaries);
      const base = buildHomeBookModels({ courses: source?.source ? [{ ...source.source, title: course.name, cover_url: source.source.cover_url ?? courseCoverImageUrl(source.bookId!) }] : [], uploadedFile: null, loadedBookId: null, parseJobId: null, parseJobStatus: null, loadedChapterCount: 0 })[0];
      const model: HomeBookModel = base ?? { bookId: course.id, title: course.name, status: "uploaded", filename: null, statusLabel: "待添加资料", coverUrl: null, coverVariant: 0, pageCount: 0, chapterCount: 0, progress: 0, errorMessage: null, updatedAt: course.updatedAt, nextTitle: "添加资料，开始学习" };
      return { course, source, model: { ...model, bookId: course.id, title: course.name } };
    });
    return models;
  }, [courses.state.courses, courses.state.resources, sourceSummaries]);
  const selected = models.find((item) => item.course.id === selectedId) ?? models.find((item) => item.course.id === courses.state.activeCourseId) ?? models[0];
  const selectedSourceId = selected?.source?.bookId ?? null;
  const loaded = Boolean(selected && selected.course.id === courses.state.activeCourseId && selectedSourceId && hasCompleteLoadedCourseContext({ loadedBookId, uploadedFile, parsedScanResult, parsedChapters, parsedChunks, parsedAssets, currentStudyPlan, generatedLessons, generatedFlashcards, generatedQuizzes }, selectedSourceId));
  const nextStep = loaded ? resolveHomeNextStep({ chapters: parsedChapters ?? [], location: studyLocations[courseLocationKey(selected?.course.id, loadedBookId!)] ?? studyLocations[loadedBookId!], plan: currentStudyPlan, lessons: generatedLessons }) : null;
  const workspaceBook = selected ? { ...selected.model, bookId: selectedSourceId ?? selected.course.id } : null;
  const listState = courses.state.courses.length ? "content" : ctx.sourceSummariesLoadState === "loading" ? "loading" : ctx.sourceSummariesLoadState === "error" ? "error" : "empty";

  useEffect(() => {
    if (!selectedId || !courses.state.courses.some((course) => course.id === selectedId)) setSelectedId(courses.state.activeCourseId);
  }, [selectedId, courses.state.courses, courses.state.activeCourseId]);
  useEffect(() => {
    if (failedCourseId && !courses.state.courses.some((course) => course.id === failedCourseId)) {
      setFailedCourseId(null);
      setError(null);
    }
  }, [failedCourseId, courses.state.courses]);
  useEffect(() => {
    if (loadedBookId || !courses.state.activeCourseId || courses.state.draft || ctx.sourceSummariesLoadState !== "ready") return;
    void selectCourse(courses.state.activeCourseId);
  }, [loadedBookId, courses.state.activeCourseId, courses.state.draft, ctx.sourceSummariesLoadState, selectCourse]);
  async function choose(courseId: string, enter = false) {
    const epoch = ++selectionEpoch.current;
    const previous = courses.state.activeCourseId;
    setSelectedId(courseId); setOpeningId(courseId); setError(null); setFailedCourseId(null);
    try {
      const opened = await selectCourse(courseId);
      if (epoch !== selectionEpoch.current) return;
      if (opened) { if (enter) go("study"); }
      else {
        setSelectedId(previous); setFailedCourseId(courseId);
        const name = courses.state.courses.find((course) => course.id === courseId)?.name ?? "课程";
        setError(`未能打开《${name}》，已回到上次选择的课程。`);
      }
    } catch (cause) {
      if (epoch !== selectionEpoch.current) return;
      setSelectedId(previous); setFailedCourseId(courseId);
      setError(cause instanceof Error ? cause.message : "课程暂时无法打开");
    } finally { if (epoch === selectionEpoch.current) setOpeningId(null); }
  }
  async function openStatus() {
    if (selected?.model.directoryOnly) go("study");
    else if (selected?.model.status === "needs_review") {
      if (await selectCourse(selected.course.id)) go("chapterConfirm");
    } else go("courseDetail");
  }
  function createCourse() { courses.startDraft(); go("courseSetup"); }
  function prepareNext() {
    if (!nextStep || !loadedBookId) return false;
    setActiveChapterId(nextStep.chapter.chapter_id);
    updateStudyLocation(loadedBookId, { expandedChapterId: nextStep.expandedChapterId, expandedSectionId: nextStep.chapter.chapter_id });
    return true;
  }
  function openSource(next = false) {
    if (!loadedBookId || !selected) return;
    const chapter = next ? nextStep?.chapter : null;
    if (next && !prepareNext()) return;
    openSourcePage({ courseId: selected.course.id, bookId: loadedBookId, title: chapter?.source_title ?? selected.source?.name ?? selected.course.name, pageStart: chapter?.page_start ?? 1, pageEnd: chapter?.page_end ?? 1, printedPageStart: chapter?.printed_page_start, printedPageEnd: chapter?.printed_page_end, from: "home" });
  }
  function openTool(tool: ChapterToolId) { if (prepareNext()) go(tool === "assignment" ? "assignment" : tool === "mistakes" ? "mistakes" : tool === "notes" ? "notes" : "flashcards"); }
  const globalActions = [
    ...(loaded && currentStudyPlan?.tasks.length ? [{ icon: <StickerIcon name="CalendarDays" size={20} />, title: "学习计划", helper: "查看课程安排", target: "plan" as const, id: "plan" }] : []),
    ...(loaded && !selected?.model.directoryOnly ? [{ icon: <StickerIcon name="CircleAlert" size={20} />, title: "错题复习", helper: "回顾当前资料的卡点", target: "mistakes" as const, id: "mistakes" }] : []),
    ...(selected ? [{ icon: <StickerIcon name="Upload" size={20} />, title: "课程资料", helper: "管理文件与教材", target: "courseDetail" as const, id: "upload" }] : [])
  ];
  return <div className="home-dashboard">
    <header className="home-topline"><div><h1>Hi，{courses.state.preferences?.displayName ?? "同学"}</h1><p>每天学一点，让进步慢慢发生。</p></div>{listState === "error" ? <button className="home-import-course-action" type="button" onClick={createCourse}><Upload size={16} /><span>创建课程</span></button> : null}</header>
    {ctx.sourceSummariesError ? <div className="home-course-error" role="alert"><span><strong>课程资料暂时无法更新</strong><small>{ctx.sourceSummariesError}</small></span><button type="button" onClick={() => void ctx.refreshSources()}>重新加载</button></div> : null}
    <HomeBookCarousel books={models.map((item) => item.model)} selectedBookId={selected?.course.id ?? null} listState={listState} onSelectBook={(id) => void choose(id)} onOpenBook={(id) => void choose(id, true)} onAddBook={createCourse} onOpenLibrary={() => go("library")} />
    <SelectedBookWorkspace book={workspaceBook} canOpenOriginal={loaded} hasLocalUploadSession={false} listState={listState} loadedBookId={loaded ? loadedBookId : null} pendingBookId={openingId ? selectedSourceId : null} selectionError={error} nextStep={nextStep} studyPreview={null} onContinue={() => { if (prepareNext()) go("lesson"); }} onOpenOriginal={() => openSource()} onOpenSource={() => openSource(true)} onRestart={() => go("courseDetail")} onRetrySelection={() => failedCourseId && void choose(failedCourseId)} onSelectTool={openTool} onViewStatus={() => void openStatus()} onUpload={() => { if (selected) ctx.openSheet({ type: "addMaterials", courseId: selected.course.id }); else createCourse(); }} onPreviewAction={() => {}} />
    {globalActions.length ? <section className="home-global-section" aria-labelledby="home-global-heading"><div className="home-section-heading"><div><h2 id="home-global-heading">学习安排</h2><p>计划、复习与课程资料</p></div></div><div className="home-global-action-list">
      {globalActions.map((action) => <button className={`home-global-action is-${action.id}`} data-home-global-action={action.id} key={action.id} type="button" onClick={() => go(action.target)}><span className="home-global-action-icon">{action.icon}</span><span className="home-global-action-copy"><strong>{action.title}</strong><small>{action.helper}</small></span></button>)}
    </div></section> : null}
  </div>;
}
