import { AdditionalTools } from "../AdditionalTools";
import { AccountControls } from "../../components/AccountGate";
import { StickerIcon } from "../components/icons/StickerIcon";
import { useState } from "react";
import {
  ArrowRight,
  CalendarCheck2,
  Clock3,
  Coins,
  Plus
} from "lucide-react";
import {
  Button,
  Card
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useLocalMotionItem } from "../motion";
import type { StudyTask } from "../types/api";

import { courseSourceBookIds, dailyTimes, primaryGoals, type DailyTime, type PrimaryGoal } from "../features/courses/model";
import { useCoursePlans } from "../features/courses/useCoursePlans";
import { creditCosts, useCredits } from "../features/credits/creditStore";

function ProfilePortrait({ onClick }: { onClick: () => void }) {
  return (
    <button className="profile-portrait-button" type="button" aria-label="编辑学习偏好" onClick={onClick}>
      <svg className="profile-portrait-art" viewBox="0 0 280 156" aria-hidden="true">
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
      <span className="profile-portrait-hint"><Plus size={15} aria-hidden="true" />编辑偏好</span>
    </button>
  );
}

function TodayTaskRow({ task, onClick }: { task: StudyTask; onClick: () => void }) {
  return (
    <button className="profile-task-row" type="button" onClick={onClick}>
      <span className="profile-task-icon" aria-hidden="true">
        {task.status === "done" ? <StickerIcon name="Check" size={17} strokeWidth={2.8} /> : <StickerIcon name="BookOpen" size={17} />}
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
  const [toolsOpen,setToolsOpen]=useState(false);
  const credits = useCredits();
  const {
    selectCourse,
    currentStudyPlan,
    go,
    courses,
    showToast,
    uploadedFile
  } = useAppContext();
  const preferences = courses.state.preferences;
  const activeCourseProfile = courses.state.courses.find((item) => item.id === courses.state.activeCourseId);
  const { plans } = useCoursePlans();
  const plansForCourse = (course: typeof activeCourseProfile) => courseSourceBookIds(course, courses.state.resources)
    .flatMap((bookId) => plans.get(bookId) ?? []);
  const [editingPreferences, setEditingPreferences] = useState(false);
  const [nameDraft, setNameDraft] = useState(preferences?.displayName ?? "");
  const [goalDraft, setGoalDraft] = useState<PrimaryGoal | null>(preferences?.primaryGoal ?? null);
  const [timeDraft, setTimeDraft] = useState<DailyTime | null>(preferences?.dailyTime ?? null);

  function openPreferenceEditor() {
    setNameDraft(preferences?.displayName ?? "");
    setGoalDraft(preferences?.primaryGoal ?? null);
    setTimeDraft(preferences?.dailyTime ?? null);
    setEditingPreferences(true);
  }

  async function savePreferences() {
    try {
      await courses.updatePreferences({ displayName: nameDraft, primaryGoal: goalDraft, dailyTime: timeDraft });
      setEditingPreferences(false);
      showToast("学习偏好已保存");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "保存失败", "warning");
    }
  }

  async function switchCourse(courseId: string) {
    try {
      await selectCourse(courseId);
    } catch (error) {
      showToast(error instanceof Error ? error.message : "切换课程失败", "warning");
    }
  }
  const tasks = plansForCourse(activeCourseProfile).flatMap((plan) => plan.tasks);
  const pendingTasks = tasks.filter((task) => task.status !== "done");
  const completedTaskCount = tasks.length - pendingTasks.length;
  const planProgress = tasks.length > 0 ? Math.round((completedTaskCount / tasks.length) * 100) : null;
  const visibleTasks = (pendingTasks.length > 0 ? pendingTasks : tasks).slice(0, 2);
  const profileMotion = useLocalMotionItem(
    `profile:${uploadedFile?.bookId ?? "guest"}:${currentStudyPlan ? "loaded" : "baseline"}`
  );
  const hasPlan = tasks.length > 0;
  const planComplete = hasPlan && pendingTasks.length === 0;
  const planActionLabel = hasPlan ? "查看今日计划" : "选择课程";

  return (
    <div className="screen-stack profile-screen"><AccountControls /><Button variant="secondary" onClick={()=>setToolsOpen(true)}>账号、好友与学习工具</Button>{toolsOpen?<AdditionalTools onClose={()=>setToolsOpen(false)} />:null}
      <div className="profile-workspace">
        <Card {...profileMotion.attributes} className="profile-card profile-portrait-card">
          <div className="profile-portrait-heading">
            <div>
              <span className="profile-eyebrow">PROFILE</span>
            </div>
            <span className="profile-portrait-status">{preferences ? "已完善" : "待完善"}</span>
          </div>
          <ProfilePortrait onClick={openPreferenceEditor} />
          <div className="profile-learning-summary">
            {courses.state.courses.length > 0 ? <>
              <label htmlFor="profile-current-course">当前课程</label>
              <select id="profile-current-course" className="profile-course-space-picker" aria-label="切换课程" value={activeCourseProfile?.id ?? ""} onChange={(event) => { void switchCourse(event.target.value); }}>
                {!activeCourseProfile ? <option value="" disabled>选择课程</option> : null}
                {courses.state.courses.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </> : <Button variant="secondary" onClick={() => go("library")}>选择课程</Button>}
          </div>
          {editingPreferences ? <form className="profile-learning-form" onSubmit={(event) => { event.preventDefault(); savePreferences(); }}>
            <label htmlFor="profile-learning-name">称呼</label>
            <input id="profile-learning-name" maxLength={30} value={nameDraft} onChange={(event) => setNameDraft(event.target.value)} />
            <label htmlFor="profile-learning-goal">主要学习目标</label>
            <select id="profile-learning-goal" value={goalDraft ?? ""} onChange={(event) => setGoalDraft(event.target.value ? event.target.value as PrimaryGoal : null)}>
              <option value="">暂不设置（默认系统学习）</option>
              {primaryGoals.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <label htmlFor="profile-learning-time">每日学习时间</label>
            <select id="profile-learning-time" value={timeDraft ?? ""} onChange={(event) => setTimeDraft(event.target.value ? event.target.value as DailyTime : null)}>
              <option value="">暂不设置（默认 45 分钟）</option>
              {dailyTimes.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <div><button type="button" onClick={() => setEditingPreferences(false)}>取消</button><button type="submit">保存偏好</button></div>
          </form> : null}
        </Card>

        <div className="profile-dashboard-column">
          <section className="profile-credits-card" aria-labelledby="profile-credits-title">
            <div className="profile-credits-heading">
              <span className="profile-credits-icon" aria-hidden="true"><Coins size={24} strokeWidth={2.2} /></span>
              <div><span className="profile-eyebrow">CREDITS</span><h2 id="profile-credits-title">我的积分</h2></div>
              <strong aria-label={`当前剩余 ${credits.balance} 积分`}>{credits.balance}<small>积分</small></strong>
            </div>
            <p>AI 对话每次 {creditCosts.chat} 积分 · 视频生成每次 {creditCosts.video} 积分</p>
          </section>
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
                <span className="profile-plan-empty-icon" aria-hidden="true"><StickerIcon name="CalendarCheck2" size={21} /></span>
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

        </div>
      </div>
    </div>
  );
}
