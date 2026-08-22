import { useEffect, useRef, useState } from "react";
import {
  CalendarDays,
  Check,
  Upload
} from "lucide-react";
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
  const { currentStudyPlan, go, setCurrentStudyPlan, showToast, uploadedFile } = useAppContext();
  const reducedMotion = useReducedMotion();
  const [selectedDay, setSelectedDay] = useState(1);
  const [planLoading, setPlanLoading] = useState(false);
  const [selectedDateMotion, setSelectedDateMotion] = useState<{ day: number | null; state: "entering" | "idle" }>({ day: null, state: "idle" });
  const [completedTaskMotionIds, setCompletedTaskMotionIds] = useState<ReadonlySet<string>>(() => new Set());
  const [emptyStateMotion, setEmptyStateMotion] = useState<{ key: string; state: "entering" | "idle" }>({
    key: "study-plan-empty:initial",
    state: "idle"
  });
  const emptyStateSequenceRef = useRef(0);
  const liveTasks = currentStudyPlan?.tasks ?? [];
  const planDayCount = normalizeStudyPlanDays(currentStudyPlan?.days);
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
    setPlanLoading(true);
    bookcourseRepository
      .getStudyPlan(uploadedFile.bookId, runtimeConfig.defaultUserId)
      .then((plan) => {
        if (!active) return;
        setCurrentStudyPlan(plan);
        const normalizedPlanDays = normalizeStudyPlanDays(plan.days);
        const firstRenderableTask = plan.tasks.find((task) => selectRenderablePlanDay(task.day, normalizedPlanDays) === task.day);
        setSelectedDay(firstRenderableTask?.day ?? minimumStudyPlanDays);
      })
      .catch((err) => {
        if (active) showToast(err instanceof Error ? err.message : "学习计划加载失败", "warning");
      })
      .finally(() => {
        if (active) setPlanLoading(false);
      });
    return () => {
      active = false;
    };
  }, [bookcourseRepository, currentStudyPlan, setCurrentStudyPlan, showToast, uploadedFile]);

  async function completeTask(taskId: string) {
    const previousTask = currentStudyPlan?.tasks.find((task) => task.task_id === taskId) ?? null;
    try {
      const updated = await bookcourseRepository.patchStudyTask(taskId, { status: "done", score: 88, weak_points: [] });
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
          <CalendarDays size={34} aria-hidden="true" />
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
          <CalendarDays size={30} aria-hidden="true" />
        </span>
        <div>
          <h2>{uploadedFile.name}</h2>
          <p>{planDayCount} 天 · 每天 {currentStudyPlan?.daily_minutes ?? 30} 分钟</p>
          <p>计划由后端根据解析章节、错题记录和学习状态自动生成，可同步完成状态。</p>
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
              <p>{task.task_type} · {task.minutes} 分钟 · {task.status}</p>
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
      <Section title="计划调整记录">
        <div className="adjustment-list">
          {liveTasks.filter((task) => task.adjustment_reason).map((task) => (
            <Card className="adjustment-card" key={task.task_id}>
              <Pill tone="orange">后端调整</Pill>
              <h3>{task.title}</h3>
              <p>{task.adjustment_reason}</p>
              <Button variant="secondary" onClick={() => go("flashcards")}>查看相关闪卡</Button>
            </Card>
          ))}
          {!planLoading && liveTasks.filter((task) => task.adjustment_reason).length === 0 ? (
            <Card className="adjustment-card">
              <Pill tone="mint">暂无调整</Pill>
              <h3>计划稳定执行中</h3>
              <p>完成作业诊断后，低分卡点会自动追加复习任务。</p>
            </Card>
          ) : null}
        </div>
      </Section>
      <Button icon={<CalendarDays size={18} aria-hidden="true" />} onClick={() => showToast("已加入学习日历")}>加入日历</Button>
      <Button variant="secondary" onClick={() => go("lesson")}>开始今天学习</Button>
    </div>
  );
}
