import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BookCatalogItem, CommunityKind, LibraryResource, SharedCard, ShareCandidate, ShareRequest } from "../types/api";
import { safeGet } from "./bookContext";
import { DetailSheet } from "./DetailSheet";
import { Icon } from "./Icon";

export function SharedContent({content}:{content:{body?:string;cards?:SharedCard[]}}) {
  const [index,setIndex]=useState(0);
  const [back,setBack]=useState(false);
  const cards=content.cards??[];
  const card=cards[index];
  return <>{content.body&&<div className="shared-note-text">{content.body}</div>}{card&&<div className="shared-deck"><p>共享闪卡 · {index+1} / {cards.length}</p><button className={`shared-flip ${back?"back":""}`} aria-label={back?"闪卡答案，点击看题目":"闪卡题目，点击看答案"} aria-pressed={back} onClick={()=>setBack(!back)}><small>{back?"参考答案":"先在心里回答"}</small><span>{back?card.back:card.front}</span><small>轻触翻面 ↻</small></button>{card.pages.length>0&&<small>原教材第 {card.pages.join("、")} 页</small>}<div className="shared-deck-nav"><button disabled={index===0} onClick={()=>{setIndex(index-1);setBack(false);}}>上一张</button><button disabled={index===cards.length-1} onClick={()=>{setIndex(index+1);setBack(false);}}>下一张</button></div><p className="community-fineprint">共享内容供学习参考，不会直接计入你的诊断结果。</p></div>}</>;
}

export function NoteEditor({books,note,onClose,onSaved}:{books:BookCatalogItem[];note?:LibraryResource;onClose:()=>void;onSaved:()=>void}) {
  const [bookId,setBookId]=useState(note?.book_id??books[0]?.book_id??"");
  const [title,setTitle]=useState(note?.title??"");
  const [body,setBody]=useState(note?.content.body??"");
  const [busy,setBusy]=useState(false),[error,setError]=useState("");
  async function save(){setBusy(true);setError("");try{await api.saveNote({book_id:bookId,title:title.trim(),body:body.trim(),resource_id:note?.id});onSaved();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <DetailSheet title={note?"编辑学习笔记":"写一篇学习笔记"} onClose={()=>{if(!busy)onClose();}}><form className="community-form" onSubmit={e=>{e.preventDefault();void save();}}><label>关联教材<select aria-label="笔记关联教材" disabled={!!note} value={bookId} onChange={e=>setBookId(e.target.value)}>{books.map(b=><option key={b.book_id} value={b.book_id}>{b.title}</option>)}</select></label><label>笔记标题<input required maxLength={120} value={title} onChange={e=>setTitle(e.target.value)} placeholder="记录这次理解的突破…"/></label><label>笔记内容<textarea required maxLength={20000} rows={8} value={body} onChange={e=>setBody(e.target.value)} placeholder="用自己的话整理知识、问题或心得。"/></label><p className="community-fineprint">先保存到你的书架，不会自动公开。之后可以单独选择分享到社区。</p>{error&&<p role="alert" className="community-error">{error}</p>}<button className="primary" disabled={busy||!bookId||!title.trim()||!body.trim()}>{busy?"正在保存…":"保存为私人笔记"}</button></form></DetailSheet>;
}

export function ShareDialog({books,initialBookId,resource,onClose,onShared}:{books:BookCatalogItem[];initialBookId?:string;resource?:LibraryResource;onClose:()=>void;onShared:(duplicate:boolean)=>void}) {
  const [kind,setKind]=useState<CommunityKind>(resource?.kind??"book");
  const [bookId,setBookId]=useState(resource?.book_id??initialBookId??books[0]?.book_id??"");
  const [title,setTitle]=useState(resource?.title??"");
  const [description,setDescription]=useState("");
  const [notes,setNotes]=useState<LibraryResource[]>([]);
  const [noteId,setNoteId]=useState(resource?.kind==="note"?resource.id:"");
  const [candidates,setCandidates]=useState<ShareCandidate[]>([]);
  const [courseId,setCourseId]=useState("");
  const [cardIds,setCardIds]=useState<string[]>([]);
  const [rights,setRights]=useState(false),[busy,setBusy]=useState(false),[loading,setLoading]=useState(false),[error,setError]=useState("");
  const sessionId=safeGet(`zhiwo.active-session:${bookId}`);
  useEffect(()=>{let cancelled=false;setError("");setCandidates([]);setCourseId("");setCardIds([]);setLoading(true);
    const task=kind==="flashcards"&&!resource&&sessionId?api.shareCandidates(sessionId).then(value=>{if(cancelled)return;setCandidates(value);setCourseId(value[0]?.course_id??"");setCardIds(value[0]?.cards.map(c=>c.id!)??[]);}):kind==="note"?api.resources().then(value=>{if(cancelled)return;const own=value.filter(r=>r.kind==="note"&&r.book_id===bookId);setNotes(own);setNoteId(resource?.id??own[0]?.id??"");}):Promise.resolve();
    void task.catch(e=>{if(!cancelled)setError(e.message);}).finally(()=>{if(!cancelled)setLoading(false);});return()=>{cancelled=true;};
  },[kind,bookId,sessionId,resource]);
  const selectedCourse=candidates.find(c=>c.course_id===courseId);
  const selectedNote=notes.find(n=>n.id===noteId);
  const available=kind==="book"||!!resource||(kind==="note"?!!selectedNote:cardIds.length>0);
  async function submit(){setBusy(true);setError("");try{const payload:ShareRequest={kind,book_id:bookId,title:title.trim(),description:description.trim(),rights_confirmed:true};
    if(resource) payload.resource_id=resource.id;
    else if(kind==="note")payload.resource_id=noteId;
    else if(kind==="flashcards"){payload.session_id=sessionId!;payload.course_id=courseId;payload.card_ids=cardIds;}
    const result=await api.share(payload);onShared(result.status==="already_shared");
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <DetailSheet title="把好内容分享出去" onClose={()=>{if(!busy)onClose();}}><form className="community-form" onSubmit={e=>{e.preventDefault();void submit();}}>
    {!resource&&<div className="community-tabs" role="group" aria-label="分享类型">{([['book','书籍'],['flashcards','闪卡'],['note','笔记']] as const).map(([id,label])=><button type="button" aria-pressed={kind===id} key={id} onClick={()=>{setKind(id);setRights(false);}}>{label}</button>)}</div>}
    <label>来源教材<select aria-label="分享来源教材" disabled={!!resource} value={bookId} onChange={e=>{setBookId(e.target.value);setRights(false);}}>{books.map(b=><option key={b.book_id} value={b.book_id}>{b.title}</option>)}</select></label>
    {kind==="book"&&<div className="share-book-info"><Icon name="book"/><p>分享已解析教材。领取者会建立自己的学习画像，不会收到你的作答、笔记或学习进度。</p></div>}
    {kind!=="book"&&<label>分享标题（选填）<input maxLength={120} value={title} onChange={e=>setTitle(e.target.value)} placeholder="默认使用课程或笔记标题"/></label>}
    {loading&&<p role="status">正在读取可分享内容…</p>}
    {kind==="flashcards"&&!resource&&!loading&&<>{!candidates.length?<p className="community-fineprint">还没有生成过课程闪卡。请先进入这本书的一章生成课程，再来分享。</p>:<><label>选择章节<select aria-label="分享闪卡章节" value={courseId} onChange={e=>{setCourseId(e.target.value);setCardIds(candidates.find(c=>c.course_id===e.target.value)?.cards.map(c=>c.id!)??[]);}}>{candidates.map(c=><option key={c.course_id} value={c.course_id}>{c.title}</option>)}</select></label><p className="community-fineprint">选择要分享的闪卡（{cardIds.length} 张），只分享题面、答案和教材页码。</p><div className="share-card-options">{selectedCourse?.cards.map(c=><label key={c.id}><input type="checkbox" checked={cardIds.includes(c.id!)} onChange={e=>setCardIds(e.target.checked?[...cardIds,c.id!]:cardIds.filter(id=>id!==c.id))}/><span>{c.front}<small>{c.back}</small></span></label>)}</div></>}</>}
    {kind==="note"&&!resource&&!loading&&<>{notes.length?<><label>选择笔记<select aria-label="选择分享笔记" value={noteId} onChange={e=>setNoteId(e.target.value)}>{notes.map(n=><option key={n.id} value={n.id}>{n.title}</option>)}</select></label><div className="share-note-preview">{selectedNote?.content.body}</div></>:<p className="community-fineprint">这本书还没有笔记。先到「书架 → 闪卡与笔记」写一篇，保存后再分享。</p>}</>}
    {resource&&<div className="share-note-preview">{resource.content.body??`${resource.content.cards?.length??0} 张闪卡，仅分享卡片正文。`}</div>}
    <label>推荐语（选填）<textarea rows={2} maxLength={500} value={description} onChange={e=>setDescription(e.target.value)} placeholder="它适合谁？有什么值得学习的地方？"/></label>
    <label className="rights-confirm"><input type="checkbox" checked={rights} onChange={e=>setRights(e.target.checked)}/><span>我确认有权分享这些内容，且不包含个人隐私信息。</span></label>
    <p className="community-fineprint">其他用户可免费保存分享快照。你可撤回社区展示，但已被领取的副本会保留。</p>
    {error&&<p className="community-error" role="alert">{error}</p>}
    <button className="primary" disabled={busy||loading||!rights||!available||!bookId}>{busy?"正在发布…":"确认分享到社区"}</button>
  </form></DetailSheet>;
}
