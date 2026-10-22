import { useState } from "react";
import type { BookCatalogItem } from "../types/api";
import { DetailSheet } from "./DetailSheet";
import { Icon } from "./Icon";
import { GeneratedText } from "./GeneratedText";

export function BookCover({book, eager=false}:{book:BookCatalogItem;eager?:boolean}) {
  const [failedUrl,setFailedUrl]=useState<string|null>(null);
  return book.cover_url && failedUrl !== book.cover_url ? <img src={book.cover_url} alt={`${book.title}的封面或首页`} loading={eager?"eager":"lazy"} decoding="async" onError={()=>setFailedUrl(book.cover_url!)}/> : <div className="cover-fallback"><Icon name="book" size={36}/><span>封面暂不可用</span></div>;
}

export function LibraryShelf({books,activeBook,busy,onOpen}:{books:BookCatalogItem[];activeBook:BookCatalogItem|null;busy:boolean;onOpen:(id:string)=>void}) {
  const [search,setSearch]=useState("");
  const [currentOnly,setCurrentOnly]=useState(false);
  const [preview,setPreview]=useState<BookCatalogItem|null>(null);
  const shown=books.filter(book=>(!currentOnly||book.book_id===activeBook?.book_id)&&book.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  return <div className="shelf-page">

    <label className="shelf-search"><Icon name="book" size={18}/><input aria-label="搜索书架" placeholder="搜索书名" value={search} onChange={event=>setSearch(event.target.value)}/>{search&&<button aria-label="清空搜索" onClick={()=>setSearch("")}>×</button>}</label>
    <div className="shelf-filters" aria-label="书架筛选"><button aria-pressed={!currentOnly} onClick={()=>setCurrentOnly(false)}>全部 <span>{books.length}</span></button><button aria-pressed={currentOnly} onClick={()=>setCurrentOnly(true)}>正在读</button></div>
    <div className="visual-shelf">{shown.map((book,index)=><article className="shelf-volume" key={book.book_id} style={{"--book-order":Math.min(index,5)} as React.CSSProperties}>
      <button className="shelf-cover" disabled={busy} aria-label={`预览《${book.title}》`} onClick={()=>setPreview(book)}><BookCover book={book} eager={index<4}/></button>
      <button className="shelf-caption" disabled={busy} title={book.title} onClick={()=>onOpen(book.book_id)}><h3>{book.title}</h3><p>{book.page_count} 页<span>·</span>{book.chapter_count} 章</p>{book.book_id===activeBook?.book_id&&<span className="reading-ribbon">正在阅读</span>}</button>
    </article>)}</div>
    {!shown.length&&<div className="shelf-empty"><Icon name="book" size={42}/><h3>暂时没有找到这本书</h3><button className="secondary" onClick={()=>{setSearch("");setCurrentOnly(false);}}>显示全部教材</button></div>}
    {preview&&<DetailSheet title="教材详情" onClose={()=>setPreview(null)}><div className="book-preview-cover"><BookCover book={preview} eager/></div><h3 className="preview-book-title">{preview.title}</h3><div className="preview-facts"><span>{preview.page_count} 页</span><span>{preview.chapter_count} 章</span></div><GeneratedText value={preview.summary} className="preview-summary"/>{preview.diagnostics_ready===false&&<p className="connection-note">部分学习内容正在准备，章节与答疑已可使用。</p>}<button className="primary sheet-primary" disabled={busy} onClick={()=>{setPreview(null);onOpen(preview.book_id);}}>打开教材 <Icon name="arrow"/></button></DetailSheet>}
  </div>;
}
