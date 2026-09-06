import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BookCatalogItem, CommunityPost } from "../types/api";
import { DetailSheet } from "./DetailSheet";
import { SharedContent, ShareDialog } from "./CommunitySharing";
import { Icon } from "./Icon";

const labels={book:"已解析书籍",flashcards:"知识闪卡",note:"学习笔记"};
export function CommunityPage({books,onLibraryChanged,onOpenLibrary,onSocial}:{onSocial:()=>void;books:BookCatalogItem[];onLibraryChanged:()=>Promise<void>;onOpenLibrary:()=>void}) {
  const [kind,setKind]=useState("all"),[search,setSearch]=useState("");
  const [items,setItems]=useState<CommunityPost[]>([]),[hasMore,setHasMore]=useState(false),[page,setPage]=useState(0);
  const [preview,setPreview]=useState<CommunityPost|null>(null),[sharing,setSharing]=useState(false);
  const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState("");
  const [revision,setRevision]=useState(0),[check,setCheck]=useState<{already_owned:boolean;similar_titles:string[];method:string}|null>(null);
  useEffect(()=>{let cancelled=false;setLoading(true);setError("");const timer=setTimeout(()=>{void api.community(kind,search,page).then(value=>{if(cancelled)return;setItems(previous=>page?[...previous,...value.items]:value.items);setHasMore(value.has_more);}).catch(e=>{if(!cancelled)setError(e.message);}).finally(()=>{if(!cancelled)setLoading(false);});},200);return()=>{cancelled=true;clearTimeout(timer);};},[kind,search,page,revision]);
  useEffect(()=>{let cancelled=false;setCheck(null);if(preview)void api.checkShared(preview.id).then(value=>{if(!cancelled)setCheck(value);}).catch(e=>{if(!cancelled)setError(e.message);});return()=>{cancelled=true;};},[preview]);
  function refresh(){setPage(0);setRevision(r=>r+1);}
  async function acquire(){if(!preview||!check)return;setBusy(true);setError("");try{const result=await api.acquire(preview.id);setMessage(result.status==="already_owned"?"已在你的书架中，没有重复加入。":result.kind==="book"?"已加入书架，复用解析结果；你的学习进度独立保存。":"已保存到书架的「闪卡与笔记」。");setPreview(null);await onLibraryChanged();refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function withdraw(){if(!preview)return;setBusy(true);setError("");try{await api.withdraw(preview.id);setPreview(null);setMessage("已撤回社区展示，已领取者的副本不受影响。");refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <div className="community-page"><section className="community-hero"><span className="kicker">一起读，走得更远</span><h2>让好内容，遇见同路人。</h2><p>分享一本好书、一组闪卡，<br/>或一个刚刚想明白的瞬间。</p><button onClick={()=>setSharing(true)}><Icon name="send" size={17}/>分享我的内容</button><Icon name="community" size={62}/></section>
    <button className="social-entry" onClick={onSocial}><span className="social-entry-icon"><Icon name="community" size={24}/></span><span><strong>好友与消息</strong><small>找同路人 · 交换学习灵感</small></span><Icon name="arrow" size={18}/></button>
    <label className="shelf-search"><Icon name="book" size={17}/><input aria-label="搜索社区" placeholder="搜索书籍、闪卡或笔记" value={search} onChange={e=>{setSearch(e.target.value);setPage(0);}}/>{search&&<button aria-label="清空社区搜索" onClick={()=>{setSearch("");setPage(0);}}>×</button>}</label>
    <div className="community-tabs" role="group" aria-label="社区内容筛选">{[['all','全部'],['book','书籍'],['flashcards','闪卡'],['note','笔记']].map(([id,label])=><button key={id} aria-pressed={kind===id} onClick={()=>{setKind(id);setPage(0);}}>{label}</button>)}</div>
    {message&&<div role="status" className="community-success"><p>{message}</p><button onClick={onOpenLibrary}>去书架看看<Icon name="arrow" size={16}/></button></div>}
    {error&&!preview&&!sharing&&<div role="alert" className="community-error">{error}<button onClick={refresh}>重试</button></div>}
    {loading&&<p role="status" className="community-loading">正在发现好内容…</p>}
    {!loading&&!items.length&&!error&&<div className="community-empty"><Icon name="community" size={38}/><h3>{search?"还没找到匹配内容":"这里，等你的第一份分享"}</h3><p>真实分享才会出现在这里，不放虚构的帖子。</p><button onClick={()=>setSharing(true)}>去分享</button></div>}
    <div className="community-feed">{items.map(item=><button className={`community-post ${item.kind}`} key={item.id} onClick={()=>{setError("");setPreview(item);}}>{item.kind==="book"?<img src={item.book.cover_url??""} alt={`${item.title}封面`} loading="lazy"/>:<span className="community-post-icon"><Icon name={item.kind==="flashcards"?"cards":"book"} size={27}/></span>}<span className="community-post-copy"><small>{labels[item.kind]} · 免费</small><strong>{item.title}</strong><span>{item.kind==="book"?`${item.book.page_count} 页 · ${item.book.chapter_count} 章`:item.kind==="flashcards"?`${item.content.cards?.length??0} 张闪卡`:item.description||"点开阅读笔记"}</span><span className="community-post-meta">{item.author}<i>{item.in_library?"已在书架":`${item.downloads} 人领取`}</i></span></span><Icon name="arrow" size={15}/></button>)}</div>
    {hasMore&&<button className="secondary" disabled={loading} onClick={()=>setPage(p=>p+1)}>加载更多</button>}
    <p className="community-fineprint">书籍保存在服务器，加入书架无需重复 OCR。当前以浏览器访客身份保存；仅分享有权传播的内容。</p>
    {preview&&<DetailSheet title={labels[preview.kind]} onClose={()=>{if(!busy)setPreview(null);}}>{preview.kind==="book"&&<div className="book-preview-cover"><img src={preview.book.cover_url??""} alt={preview.title}/></div>}<h3 className="preview-book-title">{preview.title}</h3><p className="community-fineprint">{preview.author} · 免费分享 · {preview.downloads} 人领取</p>{preview.kind==="book"?<><p className="preview-summary">{preview.book.summary}</p><div className="share-book-info"><Icon name="check"/><p>复用现成的章节、解析文本和检索索引。学习诊断、课程与进度由你自己建立。</p></div></>:<SharedContent content={preview.content}/>}{preview.description&&<p className="preview-summary">{preview.description}</p>}
      <p className="community-check" role="status">{check?check.already_owned?"✓ 检查完成：这份内容已在你的书架，不会重复添加。":check.similar_titles.length?"发现同名但内容指纹不同的教材，请核对版本后再加入。":"✓ 检查完成：未发现相同内容，可以加入。":"正在检查是否重复…"}</p>
      {error&&<p role="alert" className="community-error">{error}</p>}
      <button className="primary sheet-primary" disabled={busy||!check} onClick={()=>{if(check?.already_owned){setPreview(null);onOpenLibrary();}else void acquire();}}>{busy?"正在保存…":check?.already_owned?"已在书架 · 去查看":preview.kind==="book"?"免费加入我的书架":"免费保存到闪卡与笔记"}</button>
      {preview.mine&&<button className="community-withdraw" disabled={busy} onClick={()=>void withdraw()}>撤回我的分享</button>}
    </DetailSheet>}
    {sharing&&<ShareDialog books={books} onClose={()=>setSharing(false)} onShared={duplicate=>{setSharing(false);setMessage(duplicate?"相同内容已在社区，没有重复发布。":"分享已发布，其他用户现在可以免费领取。");refresh();}}/>}
  </div>;
}
