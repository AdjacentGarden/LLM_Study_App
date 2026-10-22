export type CourseTabName = "guide" | "reading" | "points" | "cards" | "practice";
const tabs: Array<[CourseTabName,string]> = [["guide","导读"],["reading","原文"],["points","知识"],["cards","闪卡"],["practice","练习"]];

export function CourseTabs({ value, busy, onChange }: {value:CourseTabName;busy:boolean;onChange:(value:CourseTabName)=>void}) {
  return <div className="course-tabs" role="tablist" aria-label="章节学习方式">{tabs.map(([id,label],index)=><button key={id} type="button" role="tab" aria-selected={value===id} aria-controls="course-panel" id={`course-tab-${id}`} tabIndex={value===id?0:-1} disabled={busy} className={value===id?"active":""} onClick={()=>onChange(id)} onKeyDown={event=>{
    const next=event.key==="ArrowRight"?(index+1)%tabs.length:event.key==="ArrowLeft"?(index+tabs.length-1)%tabs.length:event.key==="Home"?0:event.key==="End"?tabs.length-1:null;
    if(next===null||busy)return;event.preventDefault();onChange(tabs[next][0]);event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('button')[next]?.focus();
  }}>{label}</button>)}</div>;
}
