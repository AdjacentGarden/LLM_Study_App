import { useState } from "react";
import type { LearningRecords, Profile, UserProfile } from "../types/api";
import { DEFAULT_AVATAR } from "./UserProfilePage";
import { api } from "../api/client";
import { DetailSheet } from "./DetailSheet";
import { Icon, type IconName } from "./Icon";

type Section="knowledge"|"evidence"|"flashcards";
const names:Record<Section,string>={knowledge:"已验证知识",evidence:"学习证据",flashcards:"已复习闪卡"};
const ratings:Record<string,string>={again:"需要再学",hard:"有些困难",good:"基本理解",easy:"很熟悉"};
const when=(value:string|null)=>value?new Date(value).toLocaleString("zh-CN",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}):"暂无时间记录";
function Pages({pages}:{pages:number[]}){return pages.length?<small className="record-pages">教材第 {pages.join("、")} 页</small>:null;}

export function ProfileDashboard({profile,userProfile,onAccount,sessionId,bookTitle,completed,reminder,fontScale,onReminder,onFont,onDiagnose}:{profile?:Profile;userProfile:UserProfile|null;onAccount:()=>void;sessionId?:string;bookTitle:string;completed:boolean;reminder:string;fontScale:number;onReminder:(s:string)=>void;onFont:(n:number)=>void;onDiagnose:()=>void}) {
  const [section,setSection]=useState<Section|null>(null);
  const [records,setRecords]=useState<LearningRecords|null>(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState("");
  async function open(value:Section) {
    setSection(value);setError("");
    if(!sessionId){setRecords({book_id:profile?.book_id??"",knowledge:[],evidence:[],flashcards:[]});return;}
    setLoading(true);
    try {setRecords(await api.learningRecords(sessionId));} catch(value){setError((value as Error).message);} finally{setLoading(false);}
  }
  const known=Object.values(profile?.knowledge_mastery??{}).filter(item=>item.evidence_count>0).length;
  const counts={knowledge:known,evidence:profile?.diagnostic_observations.length??0,flashcards:Object.keys(profile?.flashcard_reviews??{}).length};
  const icons:Record<Section,IconName>={knowledge:"spark",evidence:"clock",flashcards:"cards"};
  return <div className="profile-page profile-dashboard"><section className="identity-card"><button className="profile-avatar-entry" aria-label="打开个人资料" onClick={onAccount}><img src={userProfile?.avatar_url??DEFAULT_AVATAR} alt="学习头像"/></button><div><span className="kicker">{userProfile?.nickname??"每一步理解，都值得留下"}</span><h2>我的学习档案</h2><p>{profile?.goal||"从第一道题，开始了解自己"}</p></div></section><p className="profile-book-context"><Icon name="book" size={15}/>{bookTitle}</p>
    <section className="record-shortcuts" aria-label="学习记录">{(["knowledge","evidence","flashcards"] as Section[]).map(key=><button key={key} className={`record-shortcut ${key}`} onClick={()=>void open(key)}><span className="record-icon"><Icon name={icons[key]}/></span><strong>{counts[key]}</strong><span>{names[key]}</span><small>查看记录 <Icon name="arrow" size={12}/></small></button>)}</section>
    <div className="profile-insight"><Icon name="spark"/><p>数字背后，是你走过的学习轨迹。<br/><span>点开卡片，看看掌握了什么、哪里值得再学。</span></p></div>
    <section className="settings-card"><h2>学习设置</h2><label><span><b>学习提醒</b><small>提醒时间保存在本机</small></span><input aria-label="学习提醒时间" type="time" value={reminder} onInput={event=>onReminder(event.currentTarget.value)}/></label><label><span><b>字体大小</b><small>当前 {Math.round(fontScale*100)}%</small></span><input aria-label="字体大小" type="range" min=".9" max="1.2" step=".05" value={fontScale} onInput={event=>onFont(Number(event.currentTarget.value))}/></label></section>
    <button className="secondary" onClick={onDiagnose}>{completed?"返回个性化课程":"查看或继续学习画像"}</button><p className="privacy-note">仅展示当前书籍、当前学习档案的记录；掌握程度是学习估计，不代表已完全掌握。</p>
    {section&&<DetailSheet title={names[section]} onClose={()=>setSection(null)}><p className="record-context">{bookTitle}</p><div className="record-tabs" aria-label="记录分类">{(Object.keys(names) as Section[]).map(key=><button key={key} aria-pressed={key===section} onClick={()=>setSection(key)}>{names[key]}</button>)}</div>
      {loading?<p role="status" className="record-loading">正在取回真实学习记录…</p>:error?<div role="alert" className="record-empty"><p>{error}</p><button className="secondary" onClick={()=>void open(section)}>重新加载</button></div>:<>
        {records?.[section].length===0&&<div className="record-empty"><Icon name={icons[section]} size={42}/><h3>{section==="knowledge"?"还没有验证过的知识点":section==="evidence"?"第一条学习证据，等你留下":"还没有复习过的闪卡"}</h3><p>{section==="flashcards"?"在课程中翻看闪卡并提交熟悉度后，这里会保留卡片内容和复习安排。":"完成选择题或章节练习后，这里会展示具体内容，而不只是数量。"}</p><button className="secondary" onClick={()=>{setSection(null);onDiagnose();}}>去学习</button></div>}
        {section==="knowledge"&&<div className="record-list">{records?.knowledge.map(item=><article className="knowledge-record" key={item.id}><div className="record-eyebrow"><span>{item.chapter}</span><b>{Math.round(item.mastery*100)}%</b></div><h3>{item.title}</h3><div className="record-meter"><i style={{width:`${item.mastery*100}%`}}/></div><p>掌握估计 · 基于 {item.evidence_count} 条学习证据</p><Pages pages={item.pages}/></article>)}</div>}
        {section==="evidence"&&<div className="record-list timeline-records">{records?.evidence.map(item=><details className="evidence-record" key={item.id}><summary><span className="record-eyebrow">{item.kind} · {when(item.at)}</span><h3>{item.title}</h3><span className={`score-pill ${item.score>=.8?"positive":""}`}>{item.kind==="闪卡复习"?"熟悉度反馈":"本次得分"} {Math.round(item.score*100)}% · 查看详情</span></summary><div className="evidence-body"><p>{item.chapter} · 用时 {Math.round(item.seconds)} 秒</p>{item.answer&&<p>你的作答：{ratings[item.answer]??item.answer}</p>}<p>评估置信度 {Math.round(item.confidence*100)}%，这是一次学习记录，不等于最终掌握结论。</p><Pages pages={item.pages}/></div></details>)}</div>}
        {section==="flashcards"&&<div className="record-list">{records?.flashcards.map(item=><details className="reviewed-card" key={item.id}><summary><span className="record-eyebrow">{item.chapter}</span><h3>{item.front}</h3><span className="score-pill">{ratings[item.rating??""]??"已复习"} · 点击回看答案</span></summary><div className="reviewed-back">{item.back??"历史卡片正文暂不可用，原复习记录仍保留。"}</div><div className="review-dates"><span>上次复习 {when(item.reviewed_at)}</span><span>下次复习 {when(item.due_at)}</span></div><Pages pages={item.pages}/></details>)}</div>}
      </>}
    </DetailSheet>}
  </div>;
}
