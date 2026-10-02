import { StickerIcon } from "../components/icons/StickerIcon";
import { useEffect, useRef, useState } from "react";
import { Check, Upload } from "lucide-react";
import { runtimeConfig } from "../config/runtime";
import {
  Button,
  Card,
  Pill,
  ProgressBar,
  Section
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import { useReducedMotion } from "../motion";
import {
  hasFrontEndMockStudyPlan,
  isFrontEndMockStudyTask,
  mergeFrontEndMockStudyTasks,
  studyPlanCourseTitle,
  studyTaskStatusLabel
} from "./studyPlanPresentation";
import { useCoursePlans } from "../features/courses/useCoursePlans";
import { courseSources } from "../features/courses/selectors";

const defaultStudyPlanDays = 14;
const minimumStudyPlanDays = 1;
const maximumStudyPlanDays = 90;

function normalizeStudyPlanDays(value: unknown) {
  // Invalid runtime values use the backend default; finite values are safely truncated and bounded to 1–90.
  if (typeof value !== "number" || !Number.isFinite(value)) return defaultStudyPlanDays;
  const boundedValue = Math.min(maximumStudyPlanDays, Math.max(minimumStudyPlanDays, value));
  return Math.trunc(boundedValue);
}

function selectRenderablePlanDay(value: unknown, planDayCount: number) {
  if (typeof value !== "number" || !Number.isFinite(value)) return minimumStudyPlanDays;
  const day = Math.trunc(value);
  return day >= minimumStudyPlanDays && day <= planDayCount ? day : minimumStudyPlanDays;
}

export function StudyPlanScreen() {
  const bookcourseRepository = useBookCourseRepository();
  const { currentStudyPlan, courses, sourceSummaries, selectSource, setActiveChapterId, go, setCurrentStudyPlan, showToast, uploadedFile } = useAppContext();
  const activeCourse = courses.state.courses.find((course) => course.id === courses.state.activeCourseId);
  const sources = courseSources(activeCourse, courses.state.resources, sourceSummaries);
  const { plans, refresh: refreshPlans } = useCoursePlans(activeCourse?.id);
  const [taskOverrides, setTaskOverrides] = useState<Record<string, string>>({});
  const reducedMotion = useReducedMotion();
  const [selectedDay, setSelectedDay] = useState(1);
  const [selectedDateMotion, setSelectedDateMotion] = useState<{ day: number | null; state: "entering" | "idle" }>({ day: null, state: "idle" });
  const [completedTaskMotionIds, setCompletedTaskMotionIds] = useState<ReadonlySet<string>>(() => new Set());
  const [emptyStateMotion, setEmptyStateMotion] = useState<{ key: string; state: "entering" | "idle" }>({
    key: "study-plan-empty:initial",
    state: "idle"
  });
  const emptyStateSequenceRef = useRef(0);
  const usesFrontEndMockPlan = hasFrontEndMockStudyPlan(uploadedFile);
  const sourceTasks = mergeFrontEndMockStudyTasks(
    currentStudyPlan?.tasks ?? [],
    uploadedFile,
    currentStudyPlan?.user_id ?? runtimeConfig.defaultUserId
  );
  const scopedTasks = sources.length > 1 ? [...plans.values()].flatMap((plan) => plan.tasks) : sourceTasks;
  const liveTasks = scopedTasks.map((task) => taskOverrides[task.task_id] ? { ...task, status: taskOverrides[task.task_id] } : task);
  const sourceForTask = (taskId: string) => sources.find((source) => source.bookId && plans.get(source.bookId)?.tasks.some((task) => task.task_id === taskId));
  const planDayCount = normalizeStudyPlanDays(
    usesFrontEndMockPlan
      ? Math.max(defaultStudyPlanDays, currentStudyPlan?.days ?? 0)
      : sources.length > 1 ? Math.max(1, ...[...plans.values()].map((plan) => plan.days)) : currentStudyPlan?.days
  );
  const selectedPlanDay = selectRenderablePlanDay(selectedDay, planDayCount);
  const planDays = Array.from({ length: planDayCount }, (_, index) => index + 1);
  const selectedLiveTasks = liveTasks.filter((task) => task.day === selectedPlanDay);

  useEffect(() => {
    if (!reducedMotion) return;
    setSelectedDateMotion((current) => current.state === "idle" ? current : { ...current, state: "idle" });
    setCompletedTaskMotionIds((current) => current.size === 0 ? current : new Set());
    setEmptyStateMotion((current) => current.state === "idle" ? current : { ...current, state: "idle" });
  }, [reducedMotion]);

  useEffect(() => {
    if (!uploadedFile || currentStudyPlan) return;
    let active = true;
    bookcourseRepository
      .getStudyPlan(uploadedFile.bookId, runtimeConfig.defaultUserId)
      .then((plan) => {
        if (!active) return;
        setCurrentStudyPlan(plan);
        const normalizedPlanDays = normalizeStudyPlanDays(plan.days);
        const firstRenderableTask = plan.tasks.find((task) => selectRenderablePlanDay(task.day, normalizedPlanDays) === task.day);
        setSelectedDay(hasFrontEndMockStudyPlan(uploadedFile) ? minimumStudyPlanDays : firstRenderableTask?.day ?? minimumStudyPlanDays);
      })
      .catch((err) => {
        if (active) showToast(err instanceof Error ? err.message : "学习计划加载失败", "warning");
      });
    return () => {
      active = false;
    };
  }, [bookcourseRepository, currentStudyPlan, setCurrentStudyPlan, showToast, uploadedFile]);

  async function completeTask(taskId: string) {
    const previousTask = liveTasks.find((task) => task.task_id === taskId) ?? null;
    try {
      const updated = await bookcourseRepository.patchStudyTask(taskId, { status: "done" });
      setTaskOverrides((current) => ({ ...current, [taskId]: updated.status }));
      refreshPlans();
      setCurrentStudyPlan(currentStudyPlan ? {
        ...currentStudyPlan,
        tasks: currentStudyPlan.tasks.map((task) => (task.task_id === updated.task_id ? updated : task))
      } : currentStudyPlan);
      if (previousTask?.status !== "done" && updated.status === "done" && !reducedMotion) {
        setCompletedTaskMotionIds((current) => {
          const next = new Set(current);
          next.add(updated.task_id);
          return next;
        });
      }
      showToast("学习任务已同步到后端");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "任务状态同步失败", "warning");
    }
  }

  function selectPlanDay(day: number) {
    if (day === selectedPlanDay) return;

    const nextIsEmpty = liveTasks.every((task) => task.day !== day);
    if (!reducedMotion) {
      setSelectedDateMotion({ day, state: "entering" });
      if (nextIsEmpty) {
        emptyStateSequenceRef.current += 1;
        setEmptyStateMotion({
          key: `study-plan-empty:${day}:${emptyStateSequenceRef.current}`,
          state: "entering"
        });
      }
    }
    setSelectedDay(day);
  }

  if (!uploadedFile) {
    return (
      <div className="screen-stack study-plan-screen">
        <Card className="parse-empty-card">
          <StickerIcon name="CalendarDays" size={34} aria-hidden="true" />
          <h2>暂无真实学习计划</h2>
          <p>学习计划会在教材完成解析、章节确认和课程生成后由后端创建。</p>
          <Button icon={<Upload size={18} aria-hidden="true" />} onClick={() => go("upload")}>上传教材</Button>
          <Button variant="secondary" onClick={() => go("library")}>查看课程库</Button>
        </Card>
      </div>
    );
  }

  return (
    <div className="screen-stack study-plan-screen">
      <Card className="plan-hero-card">
        <span className="book-summary-icon">
          <StickerIcon name="CalendarDays" size={30} aria-hidden="true" />
        </span>
        <div>
          <h2>{activeCourse?.name ?? studyPlanCourseTitle(uploadedFile.name)}</h2>
          <p>{planDayCount} 天 · 每天 {currentStudyPlan?.daily_minutes ?? 30} 分钟</p>
          <p>按课程资料汇总学习任务，完成状态会保存到对应资料。</p>
          <ProgressBar value={Math.round((liveTasks.filter((task) => task.status === "done").length / Math.max(1, liveTasks.length)) * 100)} label={`已完成 ${liveTasks.filter((task) => task.status === "done").length} / ${liveTasks.length} 项`} />
        </div>
      </Card>
      <div className="study-plan-workspace">
        <div className="study-plan-calendar" aria-label="选择学习日期">
      <div className="plan-date-row">
        {planDays.map((day) => (
          <button
            className={day === selectedPlanDay ? "active" : ""}
            type="button"
            key={day}
            aria-label={`第 ${day} 天`}
            aria-pressed={day === selectedPlanDay}
            onClick={() => selectPlanDay(day)}
          >
            <span>{`第${day}天`}</span>
            {day === selectedPlanDay ? (
              <span
                className="plan-date-selection-check"
                data-motion-plan-date-key={`study-plan:day:${day}`}
                data-motion-plan-date-state={selectedDateMotion.day === day ? selectedDateMotion.state : "idle"}
                aria-hidden="true"
                onAnimationEnd={(event) => {
                  if (event.target !== event.currentTarget || event.animationName !== "motion-plan-date-check-in") return;
                  setSelectedDateMotion((current) => current.day === day && current.state === "entering" ? { ...current, state: "idle" } : current);
                }}
              >
                <Check size={13} strokeWidth={3} />
              </span>
            ) : null}
          </button>
        ))}
      </div>
        </div>
      <Section title={`学习任务 · 第 ${selectedPlanDay} 天`} className="study-plan-tasks">
      <div className="timeline">
        {selectedLiveTasks.length > 0 ? selectedLiveTasks.map((task) => (
          <button className={`timeline-item ${task.status === "done" ? "done" : ""}`} type="button" key={task.task_id} onClick={() => void completeTask(task.task_id)}>
            <span className="timeline-day-label">
              D{task.day}
              {task.status === "done" ? (
                <span
                  className="timeline-task-completion"
                  data-motion-plan-task-key={`study-plan:task:${task.task_id}:done`}
                  data-motion-plan-task-state={completedTaskMotionIds.has(task.task_id) ? "entering" : "idle"}
                  aria-hidden="true"
                  onAnimationEnd={(event) => {
                    if (event.target !== event.currentTarget || event.animationName !== "motion-stage-check-in") return;
                    setCompletedTaskMotionIds((current) => {
                      if (!current.has(task.task_id)) return current;
                      const next = new Set(current);
                      next.delete(task.task_id);
                      return next;
                    });
                  }}
                >
                  <Check size={16} strokeWidth={2.5} />
                </span>
              ) : null}
            </span>
            <div>
              <h3>{task.title}</h3>
              <p>{sourceForTask(task.task_id)?.name ?? uploadedFile.name} · {task.task_type} · {task.minutes} 分钟 · {studyTaskStatusLabel(task.status)}</p>
            </div>
          </button>
        )) : (
          <Card
            className="adjustment-card study-plan-empty-state"
            key={emptyStateMotion.key}
            data-motion-item="content"
            data-motion-item-key={emptyStateMotion.key}
            data-motion-item-state={emptyStateMotion.state}
            onAnimationEnd={(event) => {
              if (event.target !== event.currentTarget || event.animationName !== "motion-local-item-in") return;
              setEmptyStateMotion((current) => current.state === "entering" ? { ...current, state: "idle" } : current);
            }}
          >
            <Pill tone="orange">暂无任务</Pill>
            <h3>后端还没有返回第 {selectedPlanDay} 天任务</h3>
            <p>确认课程或重新生成学习计划后会出现在这里。</p>
          </Card>
        )}
      </div>
      </Section>
      </div>
      <Button icon={<StickerIcon name="CalendarDays" size={18} aria-hidden="true" />} onClick={() => {
        if(!liveTasks.length){showToast("当前没有可导出的学习任务", "warning");return;}
        const escape=(value:string)=>value.replace(/\\/g,"\\\\").replace(/\n/g,"\\n").replace(/,/g,"\\,").replace(/;/g,"\\;");
        const events=liveTasks.map((task)=>{const date=new Date();date.setDate(date.getDate()+Math.max(0,task.day-1));const stamp=date.toISOString().replace(/[-:]/g,"").slice(0,8);return `BEGIN:VEVENT\r\nUID:${task.task_id}@study\r\nDTSTART;VALUE=DATE:${stamp}\r\nSUMMARY:${escape(task.title)}\r\nDESCRIPTION:${task.minutes} 分钟\r\nEND:VEVENT`;});
        const blob=new Blob([`BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Study App//Learning Plan//ZH\r\n${events.join("\r\n")}\r\nEND:VCALENDAR\r\n`],{type:"text/calendar;charset=utf-8"});
        const url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download="学习计划.ics";link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);showToast("学习计划日历文件已下载");
      }}>加入日历</Button>
      <Button variant="secondary" onClick={() => {
        const task = selectedLiveTasks.find((item) => item.status !== "done") ?? selectedLiveTasks[0];
        const source = task ? sourceForTask(task.task_id) : null;
        if (source?.bookId) void selectSource(source.bookId, activeCourse?.id).then((opened) => { if (opened) { if (task?.chapter_id) setActiveChapterId(task.chapter_id); go("lesson"); } });
        else { if (task?.chapter_id) setActiveChapterId(task.chapter_id); go("lesson"); }
      }}>开始今天学习</Button>
    </div>
  );
}
