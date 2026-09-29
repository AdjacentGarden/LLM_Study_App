import {
  ArrowRight,
  BookOpen,
  CalendarCheck2,
  Check,
  Clock3,
  Plus
} from "lucide-react";
import {
  Button,
  Card
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useLocalMotionItem } from "../motion";
import type { CourseSummary, StudyTask } from "../types/api";
import type { UploadedCourseFile } from "../types/app";

type ProfileCourse = {
  id: string;
  title: string;
  meta: string;
  status: string;
  progress: number | null;
};

function courseStatusLabel(course: CourseSummary) {
  if (course.status === "ready") return "可继续学习";
  if (course.status === "needs_review") return "目录待确认";
  if (course.status === "processing" || course.parse_job_status === "processing") return "正在生成课程";
  if (course.status === "error" || course.parse_job_status === "failed") return "需要处理";
  return "等待开始";
}

function formatCourseTitle(filename: string) {
  return filename.replace(/\.[^.]+$/, "") || filename;
}

function buildProfileCourses(
  courseSummaries: CourseSummary[],
  uploadedFile: UploadedCourseFile | null,
  planBookId: string | null,
  planProgress: number | null
): ProfileCourse[] {
  const courses = courseSummaries.map((course) => ({
    id: course.book_id,
    title: course.title,
    meta: course.chapter_count > 0 ? `${course.chapter_count} 个章节` : `${course.page_count} 页教材`,
    status: courseStatusLabel(course),
    progress: course.book_id === planBookId ? planProgress : null
  }));

  if (uploadedFile && !courses.some((course) => course.id === uploadedFile.bookId)) {
    courses.unshift({
      id: uploadedFile.bookId,
      title: formatCourseTitle(uploadedFile.name),
      meta: uploadedFile.origin === "remote-course" ? "课程内容已同步" : "新导入教材",
      status: uploadedFile.origin === "remote-course" ? "可继续学习" : "等待生成课程",
      progress: uploadedFile.bookId === planBookId ? planProgress : null
    });
  }

  return courses.slice(0, 2);
}

function ProfilePortrait({ onClick }: { onClick: () => void }) {
  return (
    <button className="profile-portrait-button" type="button" aria-label="添加个人画像" onClick={onClick}>
      <svg className="profile-portrait-art" viewBox="0 0 280 156" role="img" aria-label="个人画像占位图">
        <path
          className="profile-portrait-outline"
          d="M78 149c5-27 22-47 47-58-16-7-27-23-27-42 0-27 18-43 42-43s42 16 42 43c0 19-11 35-27 42 25 11 42 31 47 58"
        />
        <circle className="profile-portrait-fill" cx="140" cy="50" r="34" />
        <path className="profile-portrait-fill" d="M92 149c5-31 24-51 48-51s43 20 48 51H92Z" />
        <g className="profile-portrait-plus" aria-hidden="true">
          <path d="M140 39v22" />
          <path d="M129 50h22" />
        </g>
      </svg>
      <span className="profile-portrait-hint"><Plus size={15} aria-hidden="true" />添加画像</span>
    </button>
  );
}

function TodayTaskRow({ task, onClick }: { task: StudyTask; onClick: () => void }) {
  return (
    <button className="profile-task-row" type="button" onClick={onClick}>
      <span className="profile-task-icon" aria-hidden="true">
        {task.status === "done" ? <Check size={17} strokeWidth={2.8} /> : <BookOpen size={17} />}
      </span>
      <span className="profile-task-copy">
        <strong>{task.title}</strong>
        <small><Clock3 size={13} aria-hidden="true" />{task.task_type} · {task.minutes} 分钟</small>
      </span>
      <ArrowRight className="profile-row-arrow" size={17} aria-hidden="true" />
    </button>
  );
}

export function ProfileScreen() {
  const {
    courseSummaries,
    currentStudyPlan,
    go,
    showToast,
    uploadedFile
  } = useAppContext();
  const tasks = currentStudyPlan?.tasks ?? [];
  const pendingTasks = tasks.filter((task) => task.status !== "done");
  const completedTaskCount = tasks.length - pendingTasks.length;
  const planProgress = tasks.length > 0 ? Math.round((completedTaskCount / tasks.length) * 100) : null;
  const visibleTasks = (pendingTasks.length > 0 ? pendingTasks : tasks).slice(0, 2);
  const profileCourses = buildProfileCourses(
    courseSummaries,
    uploadedFile,
    currentStudyPlan?.book_id ?? null,
    planProgress
  );
  const profileMotion = useLocalMotionItem(
    `profile:${uploadedFile?.bookId ?? "guest"}:${currentStudyPlan ? "loaded" : "baseline"}`
  );
  const hasPlan = tasks.length > 0;
  const planComplete = hasPlan && pendingTasks.length === 0;
  const planActionLabel = hasPlan ? "查看今日计划" : "选择课程";

  return (
    <div className="screen-stack profile-screen">
      <div className="profile-workspace">
        <Card {...profileMotion.attributes} className="profile-card profile-portrait-card">
          <div className="profile-portrait-heading">
            <div>
              <span className="profile-eyebrow">PROFILE</span>
              <h2>个人画像</h2>
            </div>
            <span className="profile-portrait-status">待完善</span>
          </div>
          <ProfilePortrait onClick={() => showToast("个人画像功能正在完善")} />
        </Card>

        <div className="profile-dashboard-column">
          <section className="profile-today-card" aria-labelledby="profile-today-title">
            <div className="profile-section-heading profile-today-heading">
              <div>
                <span className="profile-eyebrow">TODAY</span>
                <h2 id="profile-today-title">今日计划</h2>
              </div>
              <div
                className="profile-plan-ring"
                role="progressbar"
                aria-label={hasPlan ? `学习计划完成 ${planProgress}%` : "尚未创建学习计划"}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={planProgress ?? 0}
              >
                <svg viewBox="0 0 72 72" aria-hidden="true">
                  <circle className="profile-plan-ring-track" cx="36" cy="36" r="29" />
                  <circle
                    className="profile-plan-ring-value"
                    cx="36"
                    cy="36"
                    r="29"
                    pathLength="100"
                    style={{ strokeDashoffset: 100 - (planProgress ?? 0) }}
                  />
                </svg>
                <span>
                  <strong>{hasPlan ? pendingTasks.length : "—"}</strong>
                  <small>{planComplete ? "已完成" : "项待完成"}</small>
                </span>
              </div>
            </div>

            {visibleTasks.length > 0 ? (
              <div className="profile-task-list">
                {visibleTasks.map((task) => (
                  <TodayTaskRow key={task.task_id} task={task} onClick={() => go("plan")} />
                ))}
              </div>
            ) : (
              <div className="profile-plan-empty">
                <span className="profile-plan-empty-icon" aria-hidden="true"><CalendarCheck2 size={21} /></span>
                <div>
                  <strong>今天还没有学习计划</strong>
                  <p>先选择一门课程，再安排今天要完成的内容。</p>
                </div>
              </div>
            )}

            <Button
              className="profile-today-action"
              icon={<CalendarCheck2 size={18} aria-hidden="true" />}
              onClick={() => go(hasPlan ? "plan" : "library")}
            >
              {planActionLabel}
            </Button>
          </section>

          <section className="profile-courses-card" aria-labelledby="profile-courses-title">
            <div className="profile-section-heading">
              <div>
                <span className="profile-eyebrow">COURSES</span>
                <h2 id="profile-courses-title">我的课程</h2>
              </div>
              <button className="profile-section-link" type="button" onClick={() => go("library")}>查看全部</button>
            </div>

            {profileCourses.length > 0 ? (
              <div className="profile-course-list">
                {profileCourses.map((course) => (
                  <button className="profile-course-row" type="button" key={course.id} onClick={() => go("library")}>
                    <span className="profile-course-cover" aria-hidden="true"><BookOpen size={21} /></span>
                    <span className="profile-course-copy">
                      <strong>{course.title}</strong>
                      <small>{course.meta} · {course.status}</small>
                      {course.progress === null ? null : (
                        <span className="profile-course-progress" aria-label={`计划完成 ${course.progress}%`}>
                          <span style={{ transform: `scaleX(${course.progress / 100})` }} />
                        </span>
                      )}
                    </span>
                    <ArrowRight className="profile-row-arrow" size={17} aria-hidden="true" />
                  </button>
                ))}
              </div>
            ) : (
              <div className="profile-courses-empty">
                <span aria-hidden="true"><BookOpen size={20} /></span>
                <div>
                  <strong>还没有课程</strong>
                  <p>上传教材或从发现页导入课程后，会展示在这里。</p>
                </div>
                <button type="button" onClick={() => go("library")}>去添加</button>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
