import { useState, type CSSProperties, type KeyboardEvent } from "react";
import type { Chapter, Profile } from "../types/api";
import { chapterEstimate, masteryLabel, chaptersToConsolidate } from "./learningPlan";
import { Icon } from "./Icon";

export function ChapterMap({chapters, profile, completed, busy, onCourse}: {
  chapters: Chapter[]; profile?: Profile; completed: boolean; busy: boolean;
  onCourse: (id:string, tab?:"guide"|"cards")=>void;
}) {
  const [focusOnly, setFocusOnly] = useState(false);
  const [expanded, setExpanded] = useState<string|null>(null);
  const priority = chaptersToConsolidate(chapters, profile);
  const shown = focusOnly ? priority.map(row=>row.chapter) : chapters;
  function select(value:boolean) { setFocusOnly(value); setExpanded(null); }
  function keyboard(event:KeyboardEvent<HTMLButtonElement>) {
    if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? false : event.key === "End" ? true : !focusOnly;
    select(next);
    (event.currentTarget.parentElement?.querySelectorAll("button")[Number(next)] as HTMLButtonElement)?.focus();
  }
  return <section className="learning-atlas" aria-label="章节学习地图">
    <div className="atlas-heading"><div><span>CHAPTER JOURNEY</span><h2>把每一步，学扎实。</h2></div><span className="atlas-total">{chapters.length}<small>章节</small></span></div>
    {completed && <div className="atlas-switch" role="tablist" aria-label="章节筛选" style={{"--selected-tab":Number(focusOnly)} as CSSProperties}>
      <i aria-hidden="true"/>
      <button id="atlas-all" role="tab" aria-selected={!focusOnly} aria-controls="atlas-panel" tabIndex={focusOnly?-1:0} onKeyDown={keyboard} onClick={()=>select(false)}><Icon name="book" size={16}/>全部章节<span>{chapters.length}</span></button>
      <button id="atlas-priority" role="tab" aria-selected={focusOnly} aria-controls="atlas-panel" tabIndex={focusOnly?0:-1} onKeyDown={keyboard} onClick={()=>select(true)}><Icon name="spark" size={16}/>优先巩固<span>{priority.length}</span></button>
    </div>}
    <p className="atlas-caption" role="status">{!completed?"先看看各章讲什么，完成诊断后解锁专属课程。":focusOnly?"按掌握估计排序，先从最需要巩固的地方开始。":"沿着书的脉络前进，轻触章节展开学习入口。"}</p>
    <div id="atlas-panel" role={completed?"tabpanel":undefined} aria-labelledby={completed?(focusOnly?"atlas-priority":"atlas-all"):undefined} className="atlas-paper">
      <div className="atlas-rows" key={String(focusOnly)}>{shown.map((chapter,index)=>{
        const mastery=chapterEstimate(profile,chapter.chapter_id);
        const open=expanded===chapter.chapter_id;
        const percent=mastery===null?null:Math.round(mastery*100);
        const tone=mastery===null?"unknown":mastery<.5?"foundation":mastery<.75?"practice":"ready";
        return <article key={chapter.chapter_id} className={`atlas-row ${tone} ${open?"is-open":""}`} style={{"--row-order":Math.min(index,6)} as CSSProperties}>
          <button className="atlas-trigger" aria-expanded={open} aria-controls={`chapter-preview-${chapter.chapter_id}`} onClick={()=>setExpanded(open?null:chapter.chapter_id)}>
            <span className="atlas-marker"><svg viewBox="0 0 44 44" aria-hidden="true"><circle cx="22" cy="22" r="19"/><circle cx="22" cy="22" r="19" pathLength="100" strokeDasharray={`${percent??0} 100`}/></svg><span>{String(chapter.order).padStart(2,"0")}</span></span>
            <span className="atlas-copy"><strong>{chapter.title}</strong><span>第 {chapter.start_page}–{chapter.end_page} 页 <i/> {masteryLabel(mastery)}</span></span>
            <span className="atlas-end">{percent!==null&&<small>{percent}<em>%</em></small>}<svg className="atlas-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></span>
          </button>
          <div className="atlas-expand" id={`chapter-preview-${chapter.chapter_id}`} aria-hidden={!open} inert={!open}><div><div className="atlas-preview">
            <span className="atlas-preview-label"><Icon name="book" size={14}/>这一章，你会遇见</span>
            <p className="atlas-preview-text">{chapter.summary||"展开这章的课程，查看讲解、知识点与学习闪卡。"}</p>
            <p className="atlas-evidence">{mastery===null?"尚无足够作答证据，暂不判断掌握程度。":`掌握估计 ${percent}% · 基于 ${profile?.chapter_mastery[chapter.chapter_id]?.evidence_count??0} 条学习证据`}</p>
            {completed?<div className="atlas-actions"><button disabled={busy} onClick={()=>onCourse(chapter.chapter_id,"guide")}>进入学习<Icon name="arrow" size={17}/></button><button disabled={busy} onClick={()=>onCourse(chapter.chapter_id,"cards")}><Icon name="cards" size={17}/>练习闪卡</button></div>:<small className="atlas-locked">完成上方选择题诊断后，为你定制这一章。</small>}
          </div></div></div>
        </article>;
      })}</div>
      {!shown.length&&<div className="atlas-empty"><Icon name="check" size={28}/><h3>{focusOnly?"这一站，先不急着补课":"章节正在准备"}</h3><p>{focusOnly?"目前没有掌握估计低于 75% 的章节。尚未诊断的章节不算作已掌握，可以回到全部章节继续探索。":"教材结构就绪后，会在这里展示。"}</p>{focusOnly&&<button onClick={()=>select(false)}>查看全部章节<Icon name="arrow" size={16}/></button>}</div>}
    </div>
    <p className="atlas-footnote"><span/>圆环表示掌握估计，不代表章节已完成</p>
  </section>;
}
