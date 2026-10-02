import { StickerIcon } from "../../components/icons/StickerIcon";
import { useCoursePlans } from "./useCoursePlans";
import { useMemo, useState } from "react";
import { ArrowRight, FileDown, Pencil, Plus, Sparkles, X } from "lucide-react";
import { useAppContext } from "../../context/AppContext";
import { communityCatalog as communityBooks } from "../../services/CommunityRepository";
import { bookIdFromResourceId, diagnosisQuestions, localResourceId } from "./model";
import { getLearningResourceFile } from "./repository";
import { buildCourseRecommendations, preferredToolIds, type CourseRecommendation } from "./recommendations";

function RecommendationCard({ recommendation, bookTitle, onOpen }: { recommendation: CourseRecommendation; bookTitle: string; onOpen: () => void }) {
  return (
    <button className="learning-recommendation" type="button" onClick={onOpen}>
      <span className="learning-recommendation-icon"><StickerIcon name="CalendarDays" size={19} aria-hidden="true" /></span>
      <span className="learning-recommendation-body">
        <strong>{recommendation.title}</strong>
        <small>{bookTitle} · {recommendation.taskType} · 建议 {recommendation.minutes} 分钟{recommendation.originalMinutes > recommendation.minutes ? `（完整任务预计 ${recommendation.originalMinutes} 分钟）` : ""}</small>
        <em>{recommendation.reason}</em>
        {recommendation.segments.length > 1 ? <span className="learning-segments">{recommendation.segments.map((minutes, index) => <span key={index}>{minutes} 分钟</span>)}</span> : null}
      </span>
      <ArrowRight size={18} aria-hidden="true" />
    </button>
  );
}

export function CourseDetailScreen() {
  const {
    sourceSummaries,
    go,
    courses,
    selectSource,
    selectCourse,
    importCourseFile,
    openSheet,
    setActiveChapterId,
    showToast
  } = useAppContext();
  const { courses: courseList, activeCourseId, resources, preferences } = courses.state;
  const course = courseList.find((item) => item.id === activeCourseId) ?? courseList[0] ?? null;
  const [openingBookId, setOpeningBookId] = useState<string | null>(null);
  const courseById = useMemo(() => new Map(sourceSummaries.map((course) => [course.book_id, course])), [sourceSummaries]);
  const localById = useMemo(() => new Map(resources.map((resource) => [localResourceId(resource.id), resource])), [resources]);
  const { plans, errors: planErrors, loading: loadingPlans } = useCoursePlans(course?.id);

  if (!course) {
    return <div className="course-space-detail"><h1>暂无课程</h1><button className="learning-home-primary" type="button" onClick={() => go("home")}>返回首页</button></div>;
  }

  const recommendationState = buildCourseRecommendations(course, preferences, plans, resources);
  const preferredTools = preferredToolIds(course.diagnosis);

  async function openBook(bookId: string, target: "chapterConfirm" | "study" | "lesson" | "assignment" | "mistakes" | "flashcards" | "notes" = "study", chapterId?: string | null) {
    setOpeningBookId(bookId);
    try {
      const opened = await selectSource(bookId, course?.id);
      if (!opened) return;
      if (chapterId) setActiveChapterId(chapterId);
      go(target);
    } finally {
      setOpeningBookId(null);
    }
  }

  function openRecommendation(recommendation: CourseRecommendation) {
    const target = recommendation.kind === "practice" ? "assignment"
      : recommendation.kind === "mistakes" ? "mistakes"
        : recommendation.kind === "flashcards" ? "flashcards"
          : recommendation.kind === "notes" ? "notes" : "lesson";
    void openBook(recommendation.bookId, target, recommendation.chapterId);
  }

  async function downloadLocal(resourceId: string) {
    const resource = localById.get(resourceId);
    if (!resource) return;
    try {
      const response = resource.bookId ? await fetch(`/api/demo/bookcourse/file/${encodeURIComponent(resource.bookId)}`) : null;
      if(response && !response.ok)throw new Error("原始文件读取失败");
      const file = response ? await response.blob() : await getLearningResourceFile(resource.id);
      if (!file) throw new Error("原始文件不在当前设备上，请重新添加");
      const url = URL.createObjectURL(file);
      const link = document.createElement("a");
      link.href = url;
      link.download = resource.name;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (error) {
      showToast(error instanceof Error ? error.message : "资料读取失败", "warning");
    }
  }

  async function removeResource(resourceId: string) {
    try {
      const wasActive = course?.activeResourceId === resourceId;
      await courses.removeResource(course.id, resourceId);
      if (wasActive) void selectCourse(course.id);
      showToast("已从课程移出，原资料仍保留");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "移出失败", "warning");
    }
  }

  function edit() {
    try {
      courses.editCourse(course.id);
      go("courseSetup");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "无法编辑课程", "warning");
    }
  }

  return (
    <div className="course-space-detail">
      <header className="course-space-detail-hero">
        <span className="learning-home-eyebrow"><Sparkles size={16} aria-hidden="true" />当前课程</span>
        <h1>{course.name}</h1>
        <p>{course.resourceIds.length} 份资料 · {course.diagnosis.timePattern === "fragmented" ? "碎片时间学习" : "整块时间学习"}</p>
        <div className="course-space-detail-actions">
          <button type="button" onClick={edit}><Pencil size={16} aria-hidden="true" />调整画像与名称</button>
          <button type="button" onClick={() => openSheet({ type: "addMaterials", courseId: course.id })}><Plus size={16} aria-hidden="true" />添加资料</button>
        </div>
      </header>

      <section className="course-space-section" aria-labelledby="course-space-today-title">
        <div className="course-space-section-header"><h2 id="course-space-today-title">今日建议</h2><span>{recommendationState.budget} 分钟预算</span></div>
        {recommendationState.reviewDays === null ? <p className="course-space-review-note">按你的自由复习偏好，不安排固定复盘提醒。</p> : <p className="course-space-review-note">建议每 {recommendationState.reviewDays} 天回看一次重点与错题。</p>}
        {loadingPlans ? <p className="course-space-state">正在整理课程资料的待做任务…</p> : null}
        {planErrors.length > 0 ? <p className="course-space-state warning" role="alert">{planErrors.join("、")} 的任务暂时无法加载，其余资料仍可继续学习。</p> : null}
        {!loadingPlans && recommendationState.recommendations.length === 0 ? <p className="course-space-state">当前没有可推荐的待做任务。选择已就绪的教材继续学习，或添加更多资料。</p> : null}
        <div className="learning-recommendations">
          {recommendationState.recommendations.map((recommendation) => (
            <RecommendationCard
              key={`${recommendation.bookId}:${recommendation.taskId}`}
              recommendation={recommendation}
              bookTitle={courseById.get(recommendation.bookId)?.title ?? "教材"}
              onOpen={() => openRecommendation(recommendation)}
            />
          ))}
        </div>
      </section>

      <section className="course-space-section" aria-labelledby="course-space-books-title">
        <div className="course-space-section-header"><h2 id="course-space-books-title">课程资料</h2><span>{course.resourceIds.length} 份</span></div>
        <div className="course-space-books">
          {course.resourceIds.map((resourceId) => {
            const bookId = bookIdFromResourceId(resourceId, resources);
            const course = bookId ? courseById.get(bookId) : null;
            const communityBook = bookId ? communityBooks.find((item) => item.id === bookId) : null;
            const resource = localById.get(resourceId);
            const ready = course?.status === "ready";
            const needsReview=course?.status==="needs_review";
            return (
              <article className="course-space-book" key={resourceId}>
                <span className="course-space-book-icon">{bookId ? <StickerIcon name="BookOpen" size={21} aria-hidden="true" /> : <StickerIcon name="FilePlus2" size={21} aria-hidden="true" />}</span>
                <div><strong>{course?.title ?? communityBook?.title ?? resource?.name ?? "资料暂时不可用"}</strong><small>{needsReview ? "解析完成 · 请核对目录" : course?.content_mode === "directory" ? "演示课程 · 目录预览" : ready ? "可开始学习" : resource?.status === "processing" ? `正在整理 ${resource.progress ?? 0}%` : resource?.error ?? "待整理 · 原文件已保留"}</small></div>
                {resource && (resource.status === "error" || resource.status === "pending") ? <button type="button" onClick={() => void getLearningResourceFile(resource.id).then((blob) => { if (!blob) throw new Error("原文件不在当前设备上"); return importCourseFile(new File([blob], resource.name, { type: resource.contentType }), courseList.find((item) => item.id === activeCourseId)?.id); }).catch((error: unknown) => showToast(error instanceof Error ? error.message : "整理失败", "warning"))}>重新整理</button> : null}
                {needsReview && bookId ? <button type="button" disabled={openingBookId===bookId} onClick={()=>void openBook(bookId,"chapterConfirm")}>核对目录<ArrowRight size={16} aria-hidden="true" /></button> : null}
                {ready && bookId ? <button type="button" disabled={openingBookId === bookId} onClick={() => void openBook(bookId)}>{openingBookId === bookId ? "打开中…" : "进入"}<ArrowRight size={16} aria-hidden="true" /></button> : null}
                {resource ? <button type="button" onClick={() => void downloadLocal(resourceId)} aria-label={`下载 ${resource.name}`}><FileDown size={18} aria-hidden="true" /></button> : null}
                <button className="course-space-remove" type="button" onClick={() => removeResource(resourceId)} aria-label={`从课程移出 ${course?.title ?? communityBook?.title ?? resource?.name ?? "资料"}`}><X size={18} aria-hidden="true" /></button>
              </article>
            );
          })}
        </div>
        {course.resourceIds.length === 0 ? <p className="course-space-state">这门课程暂时没有资料，点击“添加资料”继续。</p> : null}
      </section>

      <section className="course-space-section" aria-label="学习画像">
        <div className="course-space-section-header"><h2>学习画像</h2><button className="course-space-edit-link" type="button" onClick={edit}>修改</button></div>
        <div className="course-space-portrait-grid">
          {diagnosisQuestions.map((question) => {
            const answer = course.diagnosis[question.key];
            const values: readonly string[] = Array.isArray(answer) ? answer : [answer];
            return <div key={question.key}><strong>{question.title}</strong><span>{values.map((value) => question.options.find((option) => option.value === value)?.label ?? value).join("、")}</span></div>;
          })}
        </div>
      </section>

      <section className="course-space-section" aria-label="学习方式">
        <div className="course-space-section-header"><h2>为你优先展示</h2></div>
        <div className="course-space-preference-chips">
          {preferredTools.slice(0, 3).map((tool) => <span key={tool}>{tool === "assignment" ? "章节练习" : tool === "mistakes" ? "错题复盘" : tool === "flashcards" ? "闪卡回忆" : "学习笔记"}</span>)}
          {course.diagnosis.aids.includes("chat") ? <span>AI 问答</span> : null}
          {course.diagnosis.aids.includes("video") ? <span>优先图示与动画 · 视频素材待提供</span> : null}
        </div>
      </section>
    </div>
  );
}
