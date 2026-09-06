import type { BookCatalogItem, BookStructure, Profile } from "../types/api";
import { masteryLabel, prioritizeChapters } from "./learningPlan";
import { Icon } from "./Icon";
import { ChapterMap } from "./ChapterMap";

interface Props {
  book: BookCatalogItem | null; structure: BookStructure | null; profile?: Profile;
  completed: boolean; diagnosed: number; busy: boolean;
  onStart: () => void; onContinue: () => void;
  onCourse: (chapterId: string, tab?: "guide" | "cards") => void;
}

export function LearningHome({ book, structure, profile, completed, diagnosed, busy, onStart, onContinue, onCourse }: Props) {
  const chapters = structure?.chapters ?? [];
  const ranked = prioritizeChapters(chapters, profile);
  const priorities = ranked.filter(({ mastery }) => mastery !== null).slice(0, 3);
  const first = priorities[0];
  return <div className="page-stack home-studio">
    <div className="home-welcome"><div><span className="kicker">{completed ? "从薄弱处出发，向理解走近" : "一本书，为你重新编排"}</span><h2>{completed ? "今天，读懂一点新东西。" : "你的起点，就是第一课。"}</h2></div></div>
    {book?.diagnostics_ready === false && !profile && <p className="connection-note" role="status">诊断题正在准备与校验。现在可以先查看下方全书主线，或前往「答疑」针对这本书提问。</p>}
    <section className="focus-session">
      <div className="session-label"><span><Icon name="spark" size={16}/>{completed ? "为你挑选的下一课" : "找到你的学习起点"}</span><span><Icon name="clock" size={14}/>{profile?.constraints.minutes_per_day ?? 30} 分钟/天</span></div>
      <div className="session-focus-heading"><h2>{first ? first.chapter.title : completed ? "每一章，都按你的起点学" : "先认识你，再定制课程。"}</h2><img src="/assets/brand/cloud-mascot-home.png" alt=""/></div>
      <p>{first ? "根据你的作答，这一章值得优先巩固。" : "选一选目标，做几道由浅入深的题，让学习从合适的难度开始。"}</p>
      {first && <div className="session-mastery"><div><span>当前掌握估计</span><b>{Math.round((first.mastery ?? 0) * 100)}<small>%</small></b></div><div className="mastery-track"><i style={{width:`${Math.round((first.mastery ?? 0) * 100)}%`}}/></div></div>}
      {first ? <div className="session-actions"><button className="primary" disabled={busy} onClick={()=>onCourse(first.chapter.chapter_id)}>开始这一课 <Icon name="arrow"/></button><button className="session-card-action" aria-label="练习推荐章节闪卡" disabled={busy} onClick={()=>onCourse(first.chapter.chapter_id,"cards")}><Icon name="cards"/><span>闪卡</span></button></div> : !completed && <button className="primary" disabled={busy || !book} onClick={profile ? onContinue : onStart}>{busy ? "正在准备…" : profile ? "继续我的诊断" : "开始选择题诊断"}<Icon name="arrow"/></button>}
      {!completed && <small className="session-note">{profile ? `已完成 ${diagnosed} 道题 · 进度已保存` : "无需写长答案，也不是一场排名考试"}</small>}
    </section>
    {book && <div className="book-strip"><span className="book-strip-icon"><Icon name="book" size={22}/></span><div><b>{book.title}</b><span>{book.page_count ?? "待处理"} 页 · {chapters.length || book.chapter_count} 章 · 云端教材</span></div><span className="cloud-ready" aria-label="教材已就绪"><Icon name="check" size={16}/></span></div>}
    {completed && priorities.length > 1 && <section className="next-up"><div className="section-title"><h2>接下来，循序渐进</h2><span>按你的掌握情况排序</span></div><div>{priorities.slice(1).map(({chapter,mastery},index)=><button key={chapter.chapter_id} disabled={busy} onClick={()=>onCourse(chapter.chapter_id)}><span className="next-number">0{index+2}</span><div><strong>{chapter.title}</strong><small>{masteryLabel(mastery)} · 掌握估计 {Math.round((mastery ?? 0)*100)}%</small></div><Icon name="arrow" size={17}/></button>)}</div></section>}
    <ChapterMap chapters={chapters} profile={profile} completed={completed} busy={busy} onCourse={onCourse}/>
    {structure?.summary && <details className="book-summary"><summary>读之前，看看全书主线</summary><p>{structure.summary}</p></details>}
  </div>;
}
