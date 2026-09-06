import { useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  FileText,
  FolderOpen,
  Settings,
  Upload
} from "lucide-react";
import {
  Button,
  Card,
  Pill,
  ProgressBar
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import { CourseCardMotion, SkeletonReveal } from "../motion";
import type { JobStatusResponse } from "../types/api";
import {
  CourseCover,
  CourseSummariesSkeleton,
  CourseSummaryLoadError,
  liveBookTitle
} from "./shared";
import {
  resolveProgressOpenMode
} from "./courseResourceIdentity";

type CourseCardModel = {
  bookId: string;
  title: string;
  filename?: string | null;
  progress: number;
  mastery: number;
  next: string;
  plan: string;
  mistakes: number;
  flashcardsDue: number;
  status: string;
  jobId?: string | null;
  job?: JobStatusResponse | null;
};

const processingStatuses = new Set(["pending", "processing"]);

export function LibraryScreen() {
  const bookcourseRepository = useBookCourseRepository();
  const {
    clearCourseSession,
    clearLoadedCourse,
    courseSummaries,
    courseSummariesError,
    courseSummariesLoadState,
    courseSummariesReadyKind,
    courseSummariesRefreshing,
    go,
    parsedChapters,
    parsedScanResult,
    parseJobId,
    parseJobStatus,
    refreshCourses,
    selectCourse,
    setParseJobId,
    setParseJobStatus,
    setUploadedFile,
    showToast,
    uploadedFile
  } = useAppContext();
  const [openingBookId, setOpeningBookId] = useState<string | null>(null);
  const [editingBookId, setEditingBookId] = useState<string | null>(null);
  const [confirmingDeleteBookId, setConfirmingDeleteBookId] = useState<string | null>(null);
  const [deletingBookId, setDeletingBookId] = useState<string | null>(null);

  const liveJobMatches = Boolean(
    uploadedFile
      && parseJobId
      && (!parseJobStatus || parseJobStatus.book_id === uploadedFile.bookId)
  );
  const liveStatus = liveJobMatches && processingStatuses.has(parseJobStatus?.status ?? "processing")
      ? "processing"
      : liveJobMatches && parseJobStatus?.status === "failed"
        ? "error"
        : liveJobMatches && parseJobStatus?.status === "done"
          ? "needs_review"
        : "uploaded";
  const liveCourse: CourseCardModel | null = uploadedFile
    && uploadedFile.origin !== "remote-course"
    && !courseSummaries.some((course) => course.book_id === uploadedFile.bookId)
    ? {
        bookId: uploadedFile.bookId,
        title: liveBookTitle(uploadedFile, parsedScanResult),
        filename: uploadedFile.name,
        progress: liveStatus === "processing" ? parseJobStatus?.progress ?? 1 : 0,
        mastery: parsedChapters?.length
          ? Math.round(parsedChapters.reduce((sum, chapter) => sum + chapter.confidence, 0) / parsedChapters.length)
          : 0,
        next: liveStatus === "processing"
          ? parseJobStatus?.message ?? "后台正在解析教材"
          : liveStatus === "error"
            ? parseJobStatus?.error ?? "解析遇到问题"
            : parsedChapters?.[0]?.ai_title ?? "等待启动解析任务",
        plan: liveStatus === "processing" ? "后台处理中" : liveStatus === "error" ? "解析异常" : liveStatus === "needs_review" ? "目录待确认" : "等待解析",
        mistakes: 0,
        flashcardsDue: parsedChapters?.length ?? 0,
        status: liveStatus,
        jobId: liveJobMatches ? parseJobId : null,
        job: liveJobMatches ? parseJobStatus : null
      }
    : null;

  const backendCourses: CourseCardModel[] = courseSummaries.map((course) => {
    const localJob = parseJobStatus?.book_id === course.book_id ? parseJobStatus : null;
    const localJobIsActive = Boolean(localJob && processingStatuses.has(localJob.status));
    const localJobFailed = localJob?.status === "failed";
    const status = localJobIsActive
        ? "processing"
        : localJobFailed
          ? "error"
          : course.status;
    const progress = status === "processing"
      ? localJob?.progress ?? course.parse_job_progress ?? 1
      : 0;
    const jobId = localJob ? parseJobId : course.parse_job_id;
    const jobStatus = localJob ?? (jobId && course.parse_job_status
      ? {
          job_id: jobId,
          book_id: course.book_id,
          status: course.parse_job_status,
          stage: course.parse_job_stage ?? "pending",
          progress: course.parse_job_progress ?? 0,
          message: course.parse_job_message,
          error: course.parse_job_error
        }
      : null);
    return {
      bookId: course.book_id,
      title: course.title,
      filename: course.filename,
      progress,
      mastery: course.average_confidence,
      next: status === "processing"
        ? localJob?.message ?? course.parse_job_message ?? "后台正在解析教材"
        : status === "error"
          ? localJob?.error ?? course.parse_job_error ?? "解析或索引构建遇到问题"
          : course.next_title ?? (status === "uploaded" ? "等待启动解析" : status === "needs_review" ? "确认课程目录" : "进入课程学习"),
      plan: status === "processing"
        ? "后台处理中"
        : status === "error"
          ? "需要处理"
          : status === "ready"
            ? "课程已生成"
            : status === "needs_review"
              ? "目录待确认"
              : "等待解析",
      mistakes: 0,
      flashcardsDue: course.chapter_count,
      status,
      jobId,
      job: jobStatus
    };
  });
  const courses = liveCourse ? [liveCourse, ...backendCourses] : backendCourses;

  function openProgress(course: CourseCardModel) {
    const mode = resolveProgressOpenMode(course.bookId, course.jobId, uploadedFile?.bookId);
    if (mode === "job") {
      clearLoadedCourse();
      clearCourseSession();
      setUploadedFile({
        bookId: course.bookId,
        name: course.filename || course.title,
        sizeBytes: 0,
        contentType: course.filename?.toLowerCase().endsWith(".pdf") ? "application/pdf" : "application/octet-stream",
        uploadedAt: Date.now(),
        origin: "remote-course"
      });
      setParseJobId(course.jobId ?? null);
      setParseJobStatus(course.job ?? null);
      go("processing");
      return;
    }
    if (mode === "current-session") {
      clearLoadedCourse();
      go("parseReady");
      return;
    }
    showToast("解析任务仍在后台运行，请刷新课程列表后重试", "warning");
  }

  async function openCourse(course: CourseCardModel, target: "study" | "chapterConfirm" = "study") {
    if (course.status === "processing" || course.status === "uploaded" || course.status === "error") {
      openProgress(course);
      return;
    }
    setOpeningBookId(course.bookId);
    try {
      const opened = await selectCourse(course.bookId);
      if (!opened) return;
      setEditingBookId(null);
      setConfirmingDeleteBookId(null);
      go(target);
    } finally {
      setOpeningBookId(null);
    }
  }

  async function deleteCourse(course: CourseCardModel) {
    setDeletingBookId(course.bookId);
    try {
      await bookcourseRepository.deleteCourse(course.bookId);
      clearLoadedCourse(course.bookId);
      clearCourseSession(course.bookId);
      setEditingBookId(null);
      setConfirmingDeleteBookId(null);
      await refreshCourses();
      showToast("课程已删除");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "课程删除失败", "warning");
    } finally {
      setDeletingBookId(null);
    }
  }

  return (
    <div className="screen-stack library-screen">
      {courseSummariesError && courseSummariesLoadState === "ready" ? (
        <CourseSummaryLoadError
          message={courseSummariesError}
          onRetry={() => void refreshCourses()}
          refreshing={courseSummariesRefreshing}
        />
      ) : null}
      <SkeletonReveal
        state={courseSummariesLoadState}
        readyKind={courseSummariesReadyKind}
        skeleton={<CourseSummariesSkeleton variant="grid" />}
        error={(
          <CourseSummaryLoadError
            message={courseSummariesError ?? "请稍后重试。"}
            onRetry={() => void refreshCourses()}
            refreshing={courseSummariesRefreshing}
          />
        )}
        minBlockSize="220px"
      >
        {courses.length === 0 ? (
          <Card className="parse-empty-card">
            <FolderOpen size={34} aria-hidden="true" />
            <h2>课程库暂无真实课程</h2>
            <p>上传教材并完成解析后，后端课程会显示在这里；前端不会再补充样例书籍。</p>
            <Button variant="secondary" icon={<Upload size={18} aria-hidden="true" />} onClick={() => go("upload")}>上传新教材</Button>
          </Card>
        ) : (
          <section className="library-course-grid" aria-label="课程列表">
        {courses.map((course, index) => {
        const isEditing = editingBookId === course.bookId;
        const isConfirmingDelete = confirmingDeleteBookId === course.bookId;
        const isProcessing = course.status === "processing";
        const hasError = course.status === "error";
        const needsReview = course.status === "needs_review";
        const statusMotion = isProcessing ? "processing" : hasError ? "error" : course.status === "ready" ? "complete" : "static";
        const actionLabel = isProcessing
          ? "查看解析进度"
          : hasError
            ? "查看解析问题"
            : course.status === "uploaded"
              ? "继续生成课程"
              : needsReview
                ? "确认课程目录"
                : "进入课程";
        return (
          <CourseCardMotion bookId={course.bookId} index={index} key={course.bookId}>
          {(motionAttributes) => (
          <Card {...motionAttributes} className={`course-space-card ${isEditing ? "is-editing" : ""}`}>
            <button
              className="course-card-edit"
              type="button"
              aria-label={`编辑 ${course.title}`}
              onClick={() => {
                setEditingBookId((current) => current === course.bookId ? null : course.bookId);
                setConfirmingDeleteBookId(null);
              }}
            >
              <Settings size={16} aria-hidden="true" />
            </button>
            <CourseCover course={course} />
            <div key={`library-status:${course.bookId}:${statusMotion}`} className={`library-status-content ${statusMotion === "static" ? "" : "library-status-feedback"}`}>
              <div className="library-status-heading" aria-live={statusMotion === "static" ? undefined : "polite"} role={statusMotion === "static" ? undefined : "status"}>
                <Pill tone={hasError ? "orange" : isProcessing ? "sky" : course.status === "ready" ? "mint" : "purple"}>{course.plan}</Pill>
                {statusMotion === "error" ? <CircleAlert size={16} aria-hidden="true" /> : null}
                {statusMotion === "complete" ? <span className="library-status-success-mark" aria-hidden="true"><CheckCircle2 size={15} /></span> : null}
              </div>
              <h2>{course.title}</h2>
              <p>下一步：{course.next}</p>
              <ProgressBar
                value={course.progress}
                label={isProcessing ? `解析进度 ${course.progress}%` : `学习进度 ${course.progress}% · 目录置信度 ${course.mastery}%`}
              />
              <div className="course-space-meta">
                <span>{isProcessing ? "后台任务运行中" : `${course.mistakes} 个错题卡点`}</span>
                <span>{course.flashcardsDue} 个目录项</span>
              </div>
            </div>
            <div className="button-row">
              <Button
                loading={openingBookId === course.bookId}
                onClick={() => void openCourse(course, needsReview ? "chapterConfirm" : "study")}
              >
                {actionLabel}
              </Button>
            </div>
            {isEditing ? (
              <div className="course-card-menu">
                {course.status === "ready" || needsReview ? (
                  <button type="button" onClick={() => void openCourse(course, "chapterConfirm")}>
                    <FileText size={16} aria-hidden="true" />
                    编辑内容
                  </button>
                ) : null}
                <button
                  className={`danger ${isConfirmingDelete ? "confirm" : ""}`}
                  type="button"
                  disabled={deletingBookId === course.bookId}
                  onClick={() => {
                    if (isConfirmingDelete) {
                      void deleteCourse(course);
                      return;
                    }
                    setConfirmingDeleteBookId(course.bookId);
                  }}
                >
                  <CircleAlert size={16} aria-hidden="true" />
                  {deletingBookId === course.bookId ? "删除中..." : isConfirmingDelete ? "确认删除" : "删除课程"}
                </button>
              </div>
            ) : null}
          </Card>
          )}
          </CourseCardMotion>
        );
        })}
          </section>
        )}
      </SkeletonReveal>
      <Button variant="secondary" icon={<Upload size={18} aria-hidden="true" />} onClick={() => go("upload")}>上传新教材</Button>
    </div>
  );
}
