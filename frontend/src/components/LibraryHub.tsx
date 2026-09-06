import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BookCatalogItem, LibraryResource } from "../types/api";
import { LibraryShelf } from "./LibraryShelf";
import { DetailSheet } from "./DetailSheet";
import { NoteEditor, ShareDialog, SharedContent } from "./CommunitySharing";
import { Icon } from "./Icon";

export function LibraryHub({books,activeBook,busy,onOpen,onRemove,onRestore,onCommunity}:{books:BookCatalogItem[];activeBook:BookCatalogItem|null;busy:boolean;onOpen:(id:string)=>void;onRemove:(id:string)=>Promise<void>;onRestore:(id:string)=>Promise<void>;onCommunity:()=>void}) {
  const [tab,setTab]=useState("books"),[resources,setResources]=useState<LibraryResource[]>([]);
  const [loading,setLoading]=useState(false),[error,setError]=useState(""),[working,setWorking]=useState(false);
  const [editor,setEditor]=useState<LibraryResource|"new"|null>(null),[preview,setPreview]=useState<LibraryResource|null>(null);
  const [sharing,setSharing]=useState<LibraryResource|"books"|null>(null),[manage,setManage]=useState(false),[removeId,setRemoveId]=useState("");
  const [revision,setRevision]=useState(0),[message,setMessage]=useState("");
  const [archivedId,setArchivedId]=useState<string|null>(null),[removedBook,setRemovedBook]=useState<string|null>(null);
  useEffect(()=>{if(tab!=="materials")return;let cancelled=false;setLoading(true);setError("");void api.resources().then(value=>{if(!cancelled)setResources(value);}).catch(e=>{if(!cancelled)setError(e.message);}).finally(()=>{if(!cancelled)setLoading(false);});return()=>{cancelled=true;};},[tab,revision]);
  async function remove(){setWorking(true);setError("");try{await onRemove(removeId);setRemovedBook(removeId);setRemoveId("");setMessage("已移出书架；云端原书和学习记录仍然保留。");}catch(e){setError((e as Error).message);}finally{setWorking(false);}}
  async function restore(){if(!removedBook)return;setWorking(true);try{await onRestore(removedBook);setRemovedBook(null);setMessage("教材已恢复。");}catch(e){setError((e as Error).message);}finally{setWorking(false);}}
  async function archive(id:string,undo=false){setWorking(true);setError("");try{await api.archiveResource(id,!undo);setPreview(null);setArchivedId(undo?null:id);setRevision(r=>r+1);setMessage(undo?"资料已恢复。":"资料已收起，可撤销；不会删除社区分享。");}catch(e){setError((e as Error).message);}finally{setWorking(false);}}
  return <div className="library-hub"><div className="community-tabs library-tabs" role="group" aria-label="书架内容"><button aria-pressed={tab==="books"} onClick={()=>setTab("books")}>我的教材 <span>{books.length}</span></button><button aria-pressed={tab==="materials"} onClick={()=>setTab("materials")}>闪卡与笔记</button></div>
    {message&&<p className="community-success" role="status">{message}</p>}
    {archivedId&&<button className="secondary" disabled={working} onClick={()=>void archive(archivedId,true)}>撤销收起资料</button>}
    {removedBook&&<button className="secondary" disabled={working} onClick={()=>void restore()}>撤销移出教材</button>}
    {error&&<p className="community-error" role="alert">{error}<button onClick={()=>setRevision(r=>r+1)}>重试</button></p>}
    {tab==="books"?<><LibraryShelf books={books} activeBook={activeBook} busy={busy} onOpen={onOpen}/><div className="library-actions"><button onClick={onCommunity}><Icon name="community" size={18}/>去社区找好书</button><button disabled={!books.length} onClick={()=>setSharing("books")}><Icon name="send" size={18}/>分享书架内容</button></div><button className="community-withdraw" onClick={()=>setManage(true)}>管理我的书架</button></>:<>
      <section className="materials-intro"><span className="kicker">把理解，留在这里</span><h2>你的知识收藏夹</h2><p>写下自己的理解，也收藏来自社区的灵感。</p><button className="primary" disabled={!books.length} onClick={()=>setEditor("new")}><Icon name="book" size={18}/>写一篇笔记</button></section>
      {loading&&<p role="status">正在打开你的资料…</p>}
      {!loading&&!resources.length&&!error&&<div className="community-empty"><Icon name="cards" size={36}/><h3>第一份收藏，从好奇开始</h3><p>私人笔记和社区领取的闪卡会在这里相遇。自己的课程闪卡仍在对应章节中。</p><button onClick={onCommunity}>到社区发现内容</button></div>}
      <div className="materials-list">{resources.map(r=><button key={r.id} onClick={()=>setPreview(r)}><span className="community-post-icon"><Icon name={r.kind==="note"?"book":"cards"}/></span><span><small>{r.kind==="note"?"学习笔记":`${r.content.cards?.length??0} 张闪卡`} · {r.source_post?"社区收藏":"私人记录"}</small><strong>{r.title}</strong><small>{books.find(b=>b.book_id===r.book_id)?.title??"社区学习资料"}</small></span><Icon name="arrow" size={16}/></button>)}</div>
    </>}
    {preview&&<DetailSheet title={preview.kind==="note"?"学习笔记":"知识闪卡"} onClose={()=>{if(!working)setPreview(null);}}><h3 className="preview-book-title">{preview.title}</h3><SharedContent content={preview.content}/><div className="library-actions">{preview.kind==="note"&&!preview.source_post&&books.some(b=>b.book_id===preview.book_id)&&<button disabled={working} onClick={()=>{setEditor(preview);setPreview(null);}}>编辑这篇笔记</button>}{preview.kind==="note"&&books.some(b=>b.book_id===preview.book_id)&&<button disabled={working} onClick={()=>{setSharing(preview);setPreview(null);}}>分享到社区</button>}</div><button className="community-withdraw" disabled={working} onClick={()=>void archive(preview.id)}>收起这份资料（可撤销）</button>{error&&<p className="community-error" role="alert">{error}</p>}</DetailSheet>}
    {editor&&<NoteEditor books={books} note={editor==="new"?undefined:editor} onClose={()=>setEditor(null)} onSaved={()=>{setEditor(null);setRevision(r=>r+1);setMessage("笔记已私人保存，不会自动公开。");}}/>}
    {sharing&&<ShareDialog books={books} initialBookId={activeBook?.book_id} resource={sharing==="books"?undefined:sharing} onClose={()=>setSharing(null)} onShared={duplicate=>{setSharing(null);setMessage(duplicate?"相同内容已在社区，没有重复发布。":"分享成功，可以到社区查看。");}}/>}
    {manage&&<DetailSheet title="管理我的书架" onClose={()=>{if(!working){setManage(false);setRemoveId("");}}}><p className="community-fineprint">移出只改变你的书架，不删除服务器原书，也不清空学习进度。</p>{books.map(b=><div className="manage-book" key={b.book_id}><span>{b.title}</span><button disabled={working} onClick={()=>setRemoveId(b.book_id)}>移出</button></div>)}{removeId&&<div className="remove-confirm"><p>确定移出《{books.find(b=>b.book_id===removeId)?.title}》？</p><div className="library-actions"><button disabled={working} onClick={()=>setRemoveId("")}>取消</button><button disabled={working} onClick={()=>void remove()}>{working?"正在处理…":"确认移出"}</button></div></div>}{error&&<p role="alert" className="community-error">{error}</p>}</DetailSheet>}
  </div>;
}
