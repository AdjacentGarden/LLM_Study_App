import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BookCatalogItem, CommunityPost } from "../types/api";
import { DetailSheet } from "./DetailSheet";
import { SharedContent, ShareDialog } from "./CommunitySharing";
import { Icon } from "./Icon";
import { GeneratedText } from "./GeneratedText";

const labels={book:"书籍",flashcards:"知识闪卡",note:"学习笔记"};
export function CommunityPage({books,onLibraryChanged,onOpenLibrary,onSocial}:{onSocial:()=>void;books:BookCatalogItem[];onLibraryChanged:()=>Promise<void>;onOpenLibrary:()=>void}) {
  const [kind,setKind]=useState("book"),[search,setSearch]=useState("");
  const [items,setItems]=useState<CommunityPost[]>([]),[hasMore,setHasMore]=useState(false),[page,setPage]=useState(0);
  const [preview,setPreview]=useState<CommunityPost|null>(null),[sharing,setSharing]=useState(false);
  const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState("");
  const [revision,setRevision]=useState(0),[check,setCheck]=useState<{already_owned:boolean;similar_titles:string[];method:string}|null>(null);
  useEffect(()=>{let cancelled=false;setLoading(true);setError("");const timer=setTimeout(()=>{void api.community(kind,search,page).then(value=>{if(cancelled)return;setItems(previous=>page?[...previous,...value.items]:value.items);setHasMore(value.has_more);}).catch(e=>{if(!cancelled)setError(e.message);}).finally(()=>{if(!cancelled)setLoading(false);});},200);return()=>{cancelled=true;clearTimeout(timer);};},[kind,search,page,revision]);
  useEffect(()=>{let cancelled=false;setCheck(null);if(preview)void api.checkShared(preview.id).then(value=>{if(!cancelled)setCheck(value);}).catch(e=>{if(!cancelled)setError(e.message);});return()=>{cancelled=true;};},[preview]);
  function refresh(){setPage(0);setRevision(r=>r+1);}
  async function acquire(){if(!preview||!check)return;setBusy(true);setError("");try{const result=await api.acquire(preview.id);setMessage(result.status==="already_owned"?"这项内容已在书架中。":result.kind==="book"?"已加入书架。":"已保存到「闪卡与笔记」。");setPreview(null);await onLibraryChanged();refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function withdraw(){if(!preview)return;setBusy(true);setError("");try{await api.withdraw(preview.id);setPreview(null);setMessage("已撤回社区展示，已领取者的副本不受影响。");refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <div className="community-page"><section className="community-hero"><div><h2>分享内容</h2><p>书籍或笔记</p></div><button onClick={()=>setSharing(true)} aria-label="分享书籍或笔记"><Icon name="send" size={17}/><span>分享</span></button></section>
    <button className="social-entry" onClick={onSocial}><span className="social-entry-icon"><Icon name="community" size={24}/></span><span><strong>好友与消息</strong><small>联系人与会话</small></span><Icon name="arrow" size={18}/></button>
    <label className="shelf-search"><Icon name="book" size={17}/><input aria-label="搜索社区" placeholder="搜索书籍或笔记" value={search} onChange={e=>{setSearch(e.target.value);setPage(0);setItems([]);}}/>{search&&<button aria-label="清空社区搜索" onClick={()=>{setSearch("");setPage(0);setItems([]);}}>×</button>}</label>
    <div className="community-tabs" role="group" aria-label="社区内容筛选">{[['book','书籍'],['note','笔记']].map(([id,label])=><button key={id} aria-pressed={kind===id} onClick={()=>{if(id!==kind){setKind(id);setPage(0);setItems([]);}}}>{label}</button>)}</div>
    <div className="community-section-title"><div><small>{kind==="book"?"公开书籍":"公开笔记"}</small><h3>{kind==="book"?"书籍":"笔记"}</h3></div><span>{items.length} 份</span></div>
    {message&&<div role="status" className="community-success"><p>{message}</p><button onClick={onOpenLibrary}>去书架看看<Icon name="arrow" size={16}/></button></div>}
    {error&&!preview&&!sharing&&<div role="alert" className="community-error">{error}<button onClick={refresh}>重试</button></div>}
    {loading&&<p role="status" className="community-loading">正在加载…</p>}
    {!loading&&!items.length&&!error&&<div className="community-empty"><Icon name="community" size={38}/><h3>{search?"没有匹配结果":"暂无内容"}</h3><p>{search?"请调整搜索词。":"可以分享书籍或笔记。"}</p><button onClick={()=>setSharing(true)}>分享内容</button></div>}
    <div className="community-feed">{items.map(item=><button className={`community-post ${item.kind}`} key={item.id} onClick={()=>{setError("");setPreview(item);}}>{item.kind==="book"?<img src={item.book.cover_url??""} alt={`${item.title}封面`} loading="lazy"/>:<span className="community-post-icon"><Icon name={item.kind==="flashcards"?"cards":"book"} size={27}/></span>}<span className="community-post-copy"><small>{labels[item.kind]} · 免费</small><strong>{item.title}</strong><span>{item.kind==="book"?`${item.book.page_count} 页 · ${item.book.chapter_count} 章`:item.kind==="flashcards"?`${item.content.cards?.length??0} 张闪卡`:item.description||"点开阅读笔记"}</span><span className="community-post-meta">{item.author}<i>{item.in_library?"已在书架":`${item.downloads} 人领取`}</i></span></span><Icon name="arrow" size={15}/></button>)}</div>
    {hasMore&&<button className="secondary" disabled={loading} onClick={()=>setPage(p=>p+1)}>加载更多</button>}
    <p className="community-fineprint">请仅分享有权传播的内容。</p>
    {preview&&<DetailSheet title={labels[preview.kind]} onClose={()=>{if(!busy)setPreview(null);}}>{preview.kind==="book"&&<div className="book-preview-cover"><img src={preview.book.cover_url??""} alt={preview.title}/></div>}<h3 className="preview-book-title">{preview.title}</h3><p className="community-fineprint">{preview.author} · 免费分享 · {preview.downloads} 人领取</p>{preview.kind==="book"?<><GeneratedText value={preview.book.summary} className="preview-summary"/><div className="share-book-info"><Icon name="check"/><p>加入后可按自己的进度学习。</p></div></>:<SharedContent content={preview.content}/>}{preview.description&&<GeneratedText value={preview.description} className="preview-summary"/>}
      <p className="community-check" role="status">{check?check.already_owned?"已在书架中。":check.similar_titles.length?"书架中可能已有其他版本，请确认后加入。":"可以加入书架。":"正在检查…"}</p>
      {error&&<p role="alert" className="community-error">{error}</p>}
      <button className="primary sheet-primary" disabled={busy||!check} onClick={()=>{if(check?.already_owned){setPreview(null);onOpenLibrary();}else void acquire();}}>{busy?"正在保存…":check?.already_owned?"已在书架 · 去查看":preview.kind==="book"?"免费加入我的书架":"免费保存到闪卡与笔记"}</button>
      {preview.mine&&<button className="community-withdraw" disabled={busy} onClick={()=>void withdraw()}>撤回我的分享</button>}
    </DetailSheet>}
    {sharing&&<ShareDialog books={books} onClose={()=>setSharing(false)} onShared={duplicate=>{setSharing(false);setMessage(duplicate?"相同内容已在社区，没有重复发布。":"分享已发布，其他用户现在可以免费领取。");refresh();}}/>}
  </div>;
}
