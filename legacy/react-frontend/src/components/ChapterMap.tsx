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
    <div className="atlas-heading"><div><h2>章节</h2></div><span className="atlas-total">{chapters.length}<small>章</small></span></div>
    {completed && <div className="atlas-switch" role="tablist" aria-label="章节筛选" style={{"--selected-tab":Number(focusOnly)} as CSSProperties}>
      <i aria-hidden="true"/>
      <button id="atlas-all" role="tab" aria-selected={!focusOnly} aria-controls="atlas-panel" tabIndex={focusOnly?-1:0} onKeyDown={keyboard} onClick={()=>select(false)}><Icon name="book" size={16}/>全部章节<span>{chapters.length}</span></button>
      <button id="atlas-priority" role="tab" aria-selected={focusOnly} aria-controls="atlas-panel" tabIndex={focusOnly?0:-1} onKeyDown={keyboard} onClick={()=>select(true)}><Icon name="spark" size={16}/>优先巩固<span>{priority.length}</span></button>
    </div>}

    <div id="atlas-panel" role={completed?"tabpanel":undefined} aria-labelledby={completed?(focusOnly?"atlas-priority":"atlas-all"):undefined} className="atlas-paper">
      <div className="atlas-rows" key={String(focusOnly)}>{shown.map((chapter,index)=>{
        const mastery=chapterEstimate(profile,chapter.chapter_id);
        const open=expanded===chapter.chapter_id;
        const percent=mastery===null?null:Math.round(mastery*100);
        const tone=mastery===null?"unknown":mastery<.5?"foundation":mastery<.75?"practice":"ready";
        return <article key={chapter.chapter_id} className={`atlas-row ${tone} ${open?"is-open":""}`} style={{"--row-order":Math.min(index,6)} as CSSProperties}>
          <button className="atlas-trigger" aria-expanded={open} aria-controls={`chapter-preview-${chapter.chapter_id}`} onClick={()=>setExpanded(open?null:chapter.chapter_id)}>
            <span className="atlas-marker"><span>{String(chapter.order).padStart(2,"0")}</span></span>
            <span className="atlas-copy"><strong>{chapter.title}</strong><span>第 {chapter.start_page}–{chapter.end_page} 页 {mastery !== null && <> · {masteryLabel(mastery)}</>}</span></span>
            <span className="atlas-end">{percent!==null&&<small>{percent}<em>%</em></small>}<svg className="atlas-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></span>
          </button>
          <div className="atlas-expand" id={`chapter-preview-${chapter.chapter_id}`} aria-hidden={!open} inert={!open}><div><div className="atlas-preview">
            <span className="atlas-preview-label"><Icon name="book" size={14}/>章节概览</span>
            <p className="atlas-preview-text">{chapter.summary||"展开这章的课程，查看讲解、知识点与学习闪卡。"}</p>
            <p className="atlas-evidence">{mastery===null?"尚无足够作答证据，暂不判断掌握程度。":`掌握估计 ${percent}% · 基于 ${profile?.chapter_mastery[chapter.chapter_id]?.evidence_count??0} 条学习证据`}</p>
            {completed?<div className="atlas-actions"><button disabled={busy} onClick={()=>onCourse(chapter.chapter_id,"guide")}>学习<Icon name="arrow" size={17}/></button><button disabled={busy} onClick={()=>onCourse(chapter.chapter_id,"cards")}><Icon name="cards" size={17}/>闪卡</button></div>:<small className="atlas-locked">完成诊断后解锁</small>}
          </div></div></div>
        </article>;
      })}</div>
      {!shown.length&&<div className="atlas-empty"><Icon name="check" size={28}/><h3>{focusOnly?"暂无优先巩固章节":"章节准备中"}</h3><p>{focusOnly?"当前没有需要优先巩固的章节。":"准备完成后显示章节。"}</p>{focusOnly&&<button onClick={()=>select(false)}>全部章节<Icon name="arrow" size={16}/></button>}</div>}
    </div>

  </section>;
}
