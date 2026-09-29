import { useEffect, useMemo, useState } from "react";
import { CalendarDays, Check, Download, RotateCcw, Upload } from "lucide-react";
import { Button, Card, Pill, ProgressBar } from "./DemoPrimitives";
import { downloadStudyCalendar } from "../services/mappers/studyCalendar";
import { cleanChapterTitle } from "../services/mappers/chapterTitles";
import type { BookCatalogItem } from "../types/api";
import type { StudyTask, StudyTaskStatus, StudyWorkspace } from "../types/studyWorkspace";

function localDate() {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
const kindLabel: Record<StudyTask["kind"], string> = { reading: "章节阅读", practice: "作业诊断", flashcards: "闪卡复习" };

export function DemoPlan({ book, workspace, loading, error, busy, onReload, onSetTask, onOpenTask, onUpload }: {
  book: BookCatalogItem | null; workspace: StudyWorkspace | null; loading: boolean; error: string | null; busy: boolean;
  onReload: () => void; onSetTask: (id: string, status: StudyTaskStatus) => Promise<boolean>;
  onOpenTask: (task: StudyTask) => void; onUpload: () => void;
}) {
  const plan = workspace && book && workspace.book_id === book.book_id ? workspace.plan : null;
  const days = useMemo(() => [...(plan?.days ?? [])].sort((left, right) => left.day_index - right.day_index), [plan]);
  const [dayIndex, setDayIndex] = useState(1);
  const [date, setDate] = useState(localDate);
  const [working, setWorking] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");
  const activeDay = days.find(day => day.day_index === dayIndex) ?? days[0] ?? null;
  useEffect(() => {
    if (days.length && !days.some(day => day.day_index === dayIndex)) setDayIndex(days.find(day => day.tasks.some(task => task.status === "todo"))?.day_index ?? days[0].day_index);
  }, [days, dayIndex]);
  useEffect(() => { setNotice(""); setActionError(""); }, [book?.book_id]);
  async function toggle(task: StudyTask) {
    if (working || busy || (task.status === "done" && task.completion_source === "activity")) return;
    const next: StudyTaskStatus = task.status === "done" ? "todo" : "done";
    setWorking(task.task_id); setActionError(""); setNotice("");
    try {
      if (!await onSetTask(task.task_id, next)) { setActionError("任务状态没有保存，请重试。"); return; }
      setNotice(next === "done" ? "任务完成状态已保存。" : "任务已重新放回待办。");
    } catch (cause) { setActionError(cause instanceof Error ? cause.message : "任务状态没有保存，请重试。"); }
    finally { setWorking(null); }
  }
  function exportCalendar() {
    if (!plan || !book) return;
    setActionError(""); setNotice("");
    try { downloadStudyCalendar(plan, book.title, date); setNotice("已下载 .ics 文件，可导入日历应用；完成状态仍以云径为准。"); }
    catch (cause) { setActionError(cause instanceof Error ? cause.message : "日历文件生成失败"); }
  }
  if (!book) return <div className="screen-stack study-plan-screen"><Card className="parse-empty-card"><CalendarDays size={34}/><h2>暂无真实学习计划</h2>
    <p>选择或上传 PDF 教材，完成学习诊断后安排任务。</p><Button icon={<Upload size={18}/>} onClick={onUpload}>上传教材</Button></Card></div>;
  return <div className="screen-stack study-plan-screen">
    <Card className="plan-hero-card"><span className="book-summary-icon"><CalendarDays size={30}/></span><div><h2>{book.title}</h2>
      <p>{plan ? `${days.length} 天 · 每天建议 ${plan.minutes_per_day} 分钟` : "按真实章节和学习记录安排任务"}</p>
      <p>{plan ? "计划由当前账号的教材、诊断与学习记录生成。" : "完成诊断后可查看个人计划。"}</p>
      {plan?.progress.total ? <ProgressBar value={plan.progress.percent} label={`已完成 ${plan.progress.done} / ${plan.progress.total} 项`}/> : null}</div></Card>
    {error && <Card className="adjustment-card" role="alert"><p>{error}</p><Button icon={<RotateCcw size={17}/>} onClick={onReload}>重新读取</Button></Card>}
    {actionError && <p className="field-error" role="alert">{actionError}</p>}
    {notice && <p className="demo-port-plan-notice" role="status">{notice}</p>}
    {!plan ? loading ? <Card className="adjustment-card" aria-busy="true"><p role="status">正在同步学习计划…</p></Card>
      : <Card className="adjustment-card"><Pill tone="orange">计划未就绪</Pill><h3>完成学习诊断后会生成计划</h3><Button variant="secondary" onClick={onReload}>重新读取</Button></Card> : <>
      <div className="study-plan-workspace"><div className="study-plan-calendar" aria-label="选择学习日期"><div className="plan-date-row">
        {days.map(day => <button className={activeDay?.day_index === day.day_index ? "active" : ""} key={day.day_index}
          type="button" aria-pressed={activeDay?.day_index === day.day_index} onClick={() => setDayIndex(day.day_index)}>
          <span>第{day.day_index}天</span>{day.tasks.length > 0 && day.tasks.every(task => task.status === "done") && <span className="plan-date-selection-check"><Check size={13}/></span>}
        </button>)}
      </div></div>
      <section className="study-plan-tasks"><h2>学习任务 · 第 {activeDay?.day_index ?? 1} 天</h2><div className="timeline">
        {activeDay?.tasks.length ? activeDay.tasks.map(task => <div className={`timeline-item ${task.status === "done" ? "done" : ""}`} key={task.task_id}>
          <span className="timeline-day-label">D{activeDay.day_index}{task.status === "done" && <span className="timeline-task-completion"><Check size={16}/></span>}</span>
          <div><h3>{cleanChapterTitle(task.title)}</h3><p>{kindLabel[task.kind]} · {task.estimated_minutes} 分钟 · {task.status === "done" ? "已完成" : "待学习"}</p>
            <div className="demo-port-plan-task-actions"><button type="button" onClick={() => onOpenTask(task)}>去学习</button>
              <button type="button" disabled={busy || !!working || (task.status === "done" && task.completion_source === "activity")}
                onClick={() => void toggle(task)}>{working === task.task_id ? "正在保存…" : task.status === "done" ? task.completion_source === "activity" ? "由学习记录完成" : "撤销完成" : "标记完成"}</button></div>
          </div></div>) : <Card className="adjustment-card study-plan-empty-state"><Pill tone="orange">暂无任务</Pill><h3>这一天暂无任务</h3>
            <p>选择其他日期，继续已安排的真实学习任务。</p></Card>}
      </div></section></div>
      <Card className="demo-port-calendar-export"><h2>加入自己的日历</h2><p>选择计划第一天的日期，下载日历文件，再导入常用日历应用。</p>
        <label>计划开始日期<input type="date" value={date} onChange={event => setDate(event.target.value)}/></label>
        <Button icon={<Download size={17}/>} disabled={!days.some(day => day.tasks.length)} onClick={exportCalendar}>下载日历文件</Button></Card>
    </>}
  </div>;
}
