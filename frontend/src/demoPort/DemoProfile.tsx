import { useEffect, useState } from "react";
import { ArrowRight, BookOpen, CalendarCheck2, Check, Clock3, LibraryBig, Layers3, MessageCircle, Sparkles, UserRound, UsersRound } from "lucide-react";
import { Button, Card } from "./DemoPrimitives";
import { AccountControls } from "../components/AccountGate";
import { DEFAULT_AVATAR } from "../components/UserProfilePage";
import type { BookCatalogItem, InterviewResponse, LearningRecords, UserProfile } from "../types/api";
import type { StudyRepository } from "../services/contracts";
import type { StudyWorkspace } from "../types/studyWorkspace";
import { cleanChapterTitle } from "../services/mappers/chapterTitles";

export function DemoProfile({ profile, accountEmail, userId, book, books, session, workspace, repository, onAccount, onSocial, onReport, onMaterials,
  onDiagnosis, onPlan, onLibrary, onTask, onQa }: {
  profile: UserProfile | null; accountEmail: string | null; userId: string; book: BookCatalogItem | null; books: BookCatalogItem[];
  session: InterviewResponse | null; workspace: StudyWorkspace | null; repository: StudyRepository;
  onAccount: () => void; onSocial: () => void; onReport: () => void; onMaterials: () => void; onDiagnosis: () => void;
  onPlan: () => void; onLibrary: () => void; onTask: () => void; onQa: () => void;
}) {
  const [records, setRecords] = useState<LearningRecords | null>(null);
  useEffect(() => {
    if (!session) { setRecords(null); return; }
    let active = true;
    void repository.learning.records(session.session_id).then(value => { if (active) setRecords(value); }).catch(() => {});
    return () => { active = false; };
  }, [repository, session?.session_id]);
  const tasks = workspace?.plan.days.flatMap(day => day.tasks) ?? [];
  const pending = tasks.filter(task => task.status !== "done");
  const visible = (pending.length ? pending : tasks).slice(0, 2);
  const progress = workspace?.plan.progress.percent ?? null;
  const shortcuts = [
    { label: "好友与消息", detail: "查找同伴、私信与分享", icon: UsersRound, action: onSocial },
    { label: "闪卡与笔记", detail: "查看个人记录与收藏", icon: LibraryBig, action: onMaterials },
    { label: "学习报告", detail: `${records?.evidence.length ?? "—"} 条学习证据`, icon: Layers3, action: onReport },
    { label: "学习画像", detail: session?.phase === "complete" ? "查看或重新诊断" : "继续学习诊断", icon: Sparkles, action: onDiagnosis },
    { label: "教材答疑", detail: "针对当前教材提问", icon: MessageCircle, action: onQa },
    { label: "个人资料", detail: "昵称、头像与简介", icon: UserRound, action: onAccount },
  ];
  return <div className="screen-stack profile-screen"><div className="profile-workspace">
    <Card className="profile-card profile-portrait-card"><div className="profile-portrait-heading"><div><span className="profile-eyebrow">PROFILE</span>
      <h2>个人画像</h2></div><span className="profile-portrait-status">{session?.phase === "complete" ? "已建立" : "待完善"}</span></div>
      <button className="profile-portrait-button" type="button" aria-label="编辑个人资料" onClick={onAccount}>
        <span className="demo-port-profile-avatar"><img src={profile?.avatar_url || DEFAULT_AVATAR} alt="个人头像"/></span>
        <span className="profile-portrait-hint">{profile?.nickname || accountEmail || "学习者"} <ArrowRight size={15}/></span>
      </button><p className="demo-port-profile-bio">{profile?.bio || "每一步理解，都值得留下。"}</p>
      <small>{accountEmail || userId}</small>
    </Card>
    <div className="profile-dashboard-column"><section className="profile-today-card" aria-labelledby="profile-today-title">
      <div className="profile-section-heading profile-today-heading"><div><span className="profile-eyebrow">TODAY</span><h2 id="profile-today-title">今日计划</h2></div>
        <div className="profile-plan-ring" role="progressbar" aria-label={progress === null ? "尚未生成计划" : `学习计划完成 ${progress}%`}
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress ?? 0}>
          <svg viewBox="0 0 72 72" aria-hidden="true"><circle className="profile-plan-ring-track" cx="36" cy="36" r="29"/>
            <circle className="profile-plan-ring-value" cx="36" cy="36" r="29" pathLength="100" style={{ strokeDashoffset: 100 - (progress ?? 0) }}/></svg>
          <span><strong>{tasks.length ? pending.length : "—"}</strong><small>{tasks.length && !pending.length ? "已完成" : "项待完成"}</small></span></div></div>
      {visible.length ? <div className="profile-task-list">{visible.map(task => <button className="profile-task-row" type="button" key={task.task_id} onClick={onTask}>
        <span className="profile-task-icon" aria-hidden="true">{task.status === "done" ? <Check size={17}/> : <BookOpen size={17}/>}</span>
        <span className="profile-task-copy"><strong>{cleanChapterTitle(task.title)}</strong><small><Clock3 size={13}/>{task.kind} · {task.estimated_minutes} 分钟</small></span>
        <ArrowRight className="profile-row-arrow" size={17}/></button>)}</div> : <div className="profile-plan-empty"><span className="profile-plan-empty-icon"><CalendarCheck2 size={21}/></span>
          <div><strong>今天还没有学习计划</strong><p>先选择教材并完成学习诊断。</p></div></div>}
      <Button className="profile-today-action" icon={<CalendarCheck2 size={18}/>} onClick={tasks.length ? onPlan : onLibrary}>{tasks.length ? "查看今日计划" : "选择课程"}</Button>
    </section>
    <section className="profile-courses-card" aria-labelledby="profile-courses-title"><div className="profile-section-heading"><div><span className="profile-eyebrow">COURSES</span>
      <h2 id="profile-courses-title">我的课程</h2></div><button className="profile-section-link" type="button" onClick={onLibrary}>查看全部</button></div>
      {books.length ? <div className="profile-course-list">{books.slice(0, 2).map(item => <button className="profile-course-row" type="button" key={item.book_id} onClick={onLibrary}>
        <span className="profile-course-cover">{item.cover_url ? <img src={item.cover_url} alt=""/> : <BookOpen size={21}/>}</span>
        <span className="profile-course-copy"><strong>{item.title}</strong><small>{item.chapter_count} 个目录项 · {item.book_id === book?.book_id ? "正在学习" : "可选课程"}</small>
          {item.book_id === book?.book_id && progress !== null && <span className="profile-course-progress" aria-label={`计划完成 ${progress}%`}><span style={{ transform: `scaleX(${progress / 100})` }}/></span>}
        </span><ArrowRight className="profile-row-arrow" size={17}/></button>)}</div> : <div className="profile-courses-empty"><BookOpen size={20}/>
        <div><strong>还没有课程</strong><p>上传教材或从发现页领取课程。</p></div><button type="button" onClick={onLibrary}>去添加</button></div>}
    </section>
    <section className="profile-courses-card demo-port-profile-shortcuts"><div className="profile-section-heading"><div><span className="profile-eyebrow">MY SPACE</span><h2>我的空间</h2></div></div>
      <div className="profile-course-list">{shortcuts.map(item => <button className="profile-course-row" key={item.label} type="button" onClick={item.action}>
        <span className="profile-course-cover"><item.icon size={21}/></span><span className="profile-course-copy"><strong>{item.label}</strong><small>{item.detail}</small></span>
        <ArrowRight className="profile-row-arrow" size={17}/></button>)}</div></section>
    <AccountControls/>
    </div>
  </div></div>;
}
