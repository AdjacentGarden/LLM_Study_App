import { useState } from "react";
import type { BookCatalogItem } from "../types/api";
import { DetailSheet } from "./DetailSheet";
import { Icon } from "./Icon";

function Cover({book, eager=false}:{book:BookCatalogItem;eager?:boolean}) {
  const [failed,setFailed]=useState(false);
  return book.cover_url && !failed ? <img src={book.cover_url} alt={`${book.title}的封面或首页`} loading={eager?"eager":"lazy"} onError={()=>setFailed(true)}/> : <div className="cover-fallback"><Icon name="book" size={36}/><span>封面暂不可用</span></div>;
}

export function LibraryShelf({books,activeBook,busy,onOpen}:{books:BookCatalogItem[];activeBook:BookCatalogItem|null;busy:boolean;onOpen:(id:string)=>void}) {
  const [search,setSearch]=useState("");
  const [currentOnly,setCurrentOnly]=useState(false);
  const [preview,setPreview]=useState<BookCatalogItem|null>(null);
  const shown=books.filter(book=>(!currentOnly||book.book_id===activeBook?.book_id)&&book.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  return <div className="shelf-page">
    <section className="shelf-intro"><span className="kicker">YOUR LITTLE LIBRARY</span><div><h2>把世界，收进书架。</h2><span>{books.length} 本</span></div><p>从一本书出发，找到适合你的学习路径。</p></section>
    <label className="shelf-search"><Icon name="book" size={18}/><input aria-label="搜索书架" placeholder="搜索书名，找到下一本…" value={search} onChange={event=>setSearch(event.target.value)}/>{search&&<button aria-label="清空搜索" onClick={()=>setSearch("")}>×</button>}</label>
    <div className="shelf-filters" aria-label="书架筛选"><button aria-pressed={!currentOnly} onClick={()=>setCurrentOnly(false)}>全部教材 <span>{books.length}</span></button><button aria-pressed={currentOnly} onClick={()=>setCurrentOnly(true)}>当前在读</button><small>云端保存</small></div>
    <div className="visual-shelf">{shown.map((book,index)=><article className={`shelf-volume shelf-tint-${index%4}`} key={book.book_id} style={{"--book-order":Math.min(index,5)} as React.CSSProperties}>
      <button className="shelf-cover" disabled={busy} aria-label={`预览《${book.title}》`} onClick={()=>setPreview(book)}><Cover book={book} eager={index<4}/><span className="cover-peek"><Icon name="spark" size={13}/>轻触预览</span>{book.book_id===activeBook?.book_id&&<span className="reading-ribbon">在读</span>}</button>
      <button className="shelf-caption" disabled={busy} title={book.title} onClick={()=>onOpen(book.book_id)}><h3>{book.title}</h3><p>{book.page_count} 页<span>·</span>{book.chapter_count} 章</p><span className="shelf-enter">进入学习 <Icon name="arrow" size={15}/></span></button>
    </article>)}</div>
    {!shown.length&&<div className="shelf-empty"><Icon name="book" size={42}/><h3>暂时没有找到这本书</h3><p>试试更短的书名，或查看全部教材。</p><button className="secondary" onClick={()=>{setSearch("");setCurrentOnly(false);}}>显示全部教材</button></div>}
    {preview&&<DetailSheet title="打开一本新世界" onClose={()=>setPreview(null)}><div className="book-preview-cover"><Cover book={preview} eager/></div><p className="preview-origin">来自原 PDF 的封面 / 首页</p><h3 className="preview-book-title">{preview.title}</h3><div className="preview-facts"><span>{preview.page_count} 页</span><span>{preview.chapter_count} 章</span><span>云端教材</span></div><p className="preview-summary">{preview.summary}</p>{preview.diagnostics_ready===false&&<p className="connection-note">诊断题正在校验，章节与教材答疑已可使用。</p>}<button className="primary sheet-primary" disabled={busy} onClick={()=>{setPreview(null);onOpen(preview.book_id);}}>进入这本书的学习空间 <Icon name="arrow"/></button></DetailSheet>}
  </div>;
}
