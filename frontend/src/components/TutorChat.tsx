import { useEffect, useRef, useState } from "react";
import type { QAResult } from "../types/api";
import { Icon } from "./Icon";

export function TutorChat({ question, askedQuestion, result, busy, error, bookTitle, suggestions, available, onQuestion, onAsk }: {
  question:string; askedQuestion:string; result:QAResult|null; busy:boolean;
  error:string; bookTitle:string; suggestions:string[]; available:boolean;
  onQuestion:(value:string)=>void; onAsk:(value?:string)=>void;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const [elapsed, setElapsed] = useState(0);
  const [showSuggestions, setShowSuggestions] = useState(false);
  useEffect(()=>{setElapsed(0); if(!busy)return; const timer=window.setInterval(()=>setElapsed(value=>value+1),1000);return()=>window.clearInterval(timer);},[busy]);
  useEffect(()=>{scroll.current?.scrollTo({top:0,behavior:"instant"});},[askedQuestion]);
  return <div className="qa-page tutor-chat"><div className="tutor-context"><Icon name="book" size={16}/><span>{bookTitle}</span></div><div className="qa-scroll" ref={scroll}>
    {!askedQuestion && <section className="tutor-welcome"><img src="/assets/brand/cloud-mascot-parsing.png" alt=""/><span className="kicker">你的教材小助手</span><h2>不懂的地方，<br/>一起想明白。</h2><p>把问题交给我。<br/>我们从教材里的依据开始。</p><div><Icon name="book" size={16}/><span>结合原文 · 附有页码</span></div></section>}
    {askedQuestion && <div className="question-bubble">{askedQuestion}</div>}
    {busy && <div className="thinking-panel" role="status"><div><Icon name="spark"/><b>{elapsed > 30 ? "仍在处理，请稍候…" : "正在查找与核验…"}</b><span className="loading-dots" aria-hidden="true"><i/><i/><i/></span></div><p>会先寻找原文，再检查结论与证据是否一致。</p><small aria-live="off">已等待 {elapsed} 秒</small><span className="skeleton-line"/><span className="skeleton-line"/><span className="skeleton-line short"/></div>}
    {error && !busy && <section className="qa-error" role="alert"><b>这次还没能回答</b><p>{error}</p><button disabled={!available} onClick={()=>onAsk(askedQuestion || question)}>重新提问 <Icon name="arrow" size={16}/></button></section>}
    {result && <section className="tutor-response"><div className="response-label"><Icon name="spark" size={18}/><b>一起看这个问题</b></div><div className="qa-answer">{result.status === "supported" ? result.claims.map((claim,index)=><p key={index} className="answer-paragraph" style={{"--paragraph-order":Math.min(index,4)} as React.CSSProperties}>{claim.text}</p>) : <><p>{result.answer}</p>{result.insufficiency_reason && <p className="insufficiency-reason">{result.insufficiency_reason}</p>}</>}</div><div className={`answer-status ${result.status==='supported'?'supported':''}`}><Icon name={result.status==='supported'?'check':'book'} size={16}/><span>{result.status==='supported'?(result.semantic_checked?'已检查结论与原文的一致性':'附有可核验的教材原文'):'证据不够时，不猜测答案'}</span></div>{result.claims.length>0 && <details className="source-drawer"><summary>查看教材依据 <span>{result.claims.length} 条</span></summary><div>{result.claims.map((claim,index)=><article key={index}><strong>{claim.text}</strong>{claim.citations.map((citation,i)=><blockquote key={i}><span>教材第 {citation.page_number} 页</span>{citation.quote}</blockquote>)}</article>)}</div></details>}</section>}
  </div><div className="qa-composer"><button className="suggestion-toggle" aria-expanded={showSuggestions} onClick={()=>setShowSuggestions(value=>!value)}><Icon name="spark" size={14}/>{showSuggestions ? "收起推荐问题" : "不知道怎么问？看看灵感"}<span>{showSuggestions ? "−" : "+"}</span></button>{showSuggestions && <div className="suggestions" aria-label="推荐问题">{suggestions.map((item)=><button disabled={busy || !available} onClick={()=>{setShowSuggestions(false);onAsk(item);}} key={item}>{item}<Icon name="arrow" size={14}/></button>)}</div>}<div className="composer-input"><textarea aria-label="向教材小助手提问" maxLength={2000} value={question} onChange={event=>onQuestion(event.target.value)} onKeyDown={event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.nativeEvent.isComposing){event.preventDefault();if(!busy&&available&&question.trim())onAsk();}}} placeholder="哪里没理解？问问我…" rows={2}/><button aria-label="发送问题" onClick={()=>{setShowSuggestions(false);onAsk();}} disabled={busy||!available||!question.trim()}>{busy?<span className="button-spinner"/>:<Icon name="send" size={22}/>}</button></div><small className="composer-note">回答仅供学习，重要结论请核对原文</small></div></div>;
}
