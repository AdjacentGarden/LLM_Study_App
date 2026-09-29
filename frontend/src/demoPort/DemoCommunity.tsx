import { useEffect, useId, useState } from "react";
import { ArrowRight, BookOpenText, Check, ChevronDown, ChevronRight, FileText, Layers3, Search, SearchX, Share2, UsersRound, X } from "lucide-react";
import { api } from "../api/client";
import { ShareDialog, SharedContent } from "../components/CommunitySharing";
import { Button } from "./DemoPrimitives";
import type { BookCatalogItem, CommunityKind, CommunityPost } from "../types/api";

type FeedKind = "all" | CommunityKind;
type OwnershipCheck = Awaited<ReturnType<typeof api.checkShared>>;
const categories: { kind: FeedKind; label: string }[] = [
  { kind: "all", label: "推荐" }, { kind: "book", label: "教材" }, { kind: "note", label: "笔记" }, { kind: "flashcards", label: "闪卡" },
];
const kindLabel: Record<CommunityKind, string> = { book: "共享教材", note: "学习笔记", flashcards: "知识闪卡" };
const errorText = (value: unknown) => value instanceof Error ? value.message : "请求暂时未完成，请稍后重试。";
function PostCover({ post }: { post: CommunityPost }) {
  return post.kind === "book" && post.book?.cover_url ? <img src={post.book.cover_url} alt="" loading="lazy"/> :
    <span className={`demo-port-community-cover demo-port-community-cover-${post.kind}`} aria-hidden="true">
      {post.kind === "book" ? <BookOpenText size={36}/> : post.kind === "note" ? <FileText size={36}/> : <Layers3 size={36}/>}</span>;
}

export function DemoCommunity({ books, onLibraryChanged, onOpenLibrary, onSocial }: {
  books: BookCatalogItem[]; onLibraryChanged: (finalBookId?: string) => Promise<void>; onOpenLibrary: () => void; onSocial: () => void;
}) {
  const searchId = useId();
  const [kind, setKind] = useState<FeedKind>("all");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [items, setItems] = useState<CommunityPost[]>([]);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [feedError, setFeedError] = useState("");
  const [revision, setRevision] = useState(0);
  const [preview, setPreview] = useState<CommunityPost | null>(null);
  const [sharing, setSharing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [operationError, setOperationError] = useState("");
  const [notice, setNotice] = useState("");
  const [check, setCheck] = useState<OwnershipCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkRevision, setCheckRevision] = useState(0);
  const [similarConfirmed, setSimilarConfirmed] = useState(false);
  const [categoryOpen, setCategoryOpen] = useState(false);
  const libraryKey = books.map(book => book.book_id).sort().join("|");

  useEffect(() => {
    let active = true;
    setLoading(true); setFeedError("");
    const timer = window.setTimeout(() => {
      void api.community(kind, query.trim(), page).then(result => {
        if (!active) return;
        setItems(current => {
          if (!page) return result.items;
          const known = new Set(current.map(item => item.id));
          return [...current, ...result.items.filter(item => !known.has(item.id))];
        });
        setMore(result.has_more);
      }).catch(cause => { if (active) setFeedError(errorText(cause)); })
        .finally(() => { if (active) setLoading(false); });
    }, query.trim() ? 220 : 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [kind, query, page, revision, libraryKey]);
  useEffect(() => {
    if (!preview) { setCheck(null); return; }
    let active = true;
    setCheck(null); setChecking(true); setOperationError(""); setSimilarConfirmed(false);
    void api.checkShared(preview.id).then(value => { if (active) setCheck(value); })
      .catch(cause => { if (active) setOperationError(errorText(cause)); })
      .finally(() => { if (active) setChecking(false); });
    return () => { active = false; };
  }, [preview?.id, checkRevision]);

  function reset() { setPage(0); setItems([]); setMore(false); setRevision(value => value + 1); }
  function choose(next: FeedKind) { setKind(next); setPage(0); setItems([]); setMore(false); setCategoryOpen(false); }
  function search(next: string) { setQuery(next); setPage(0); setItems([]); setMore(false); }
  async function openOwned() {
    if (!preview || !check) return;
    setBusy(true); setOperationError("");
    try { if (preview.kind === "book") await onLibraryChanged(check.book_id); setPreview(null); onOpenLibrary(); }
    catch (cause) { setOperationError(errorText(cause)); }
    finally { setBusy(false); }
  }
  async function acquire() {
    if (!preview || !check || check.already_owned || (check.similar_titles.length && !similarConfirmed)) return;
    setBusy(true); setOperationError("");
    try {
      const value = await api.acquire(preview.id);
      await onLibraryChanged(value.kind === "book" ? value.book_id : undefined);
      setPreview(null); setNotice(value.status === "already_owned" ? "这份内容已在你的书架，没有重复保存。" : value.kind === "book" ? "教材已加入书架，可以开始个人学习。" : "资料已保存到闪卡与笔记。");
      reset();
    } catch (cause) { setOperationError(errorText(cause)); }
    finally { setBusy(false); }
  }
  async function withdraw() {
    if (!preview?.mine) return;
    setBusy(true); setOperationError("");
    try { await api.withdraw(preview.id); setPreview(null); setNotice("分享已撤回，已领取的副本仍属于领取者。"); reset(); }
    catch (cause) { setOperationError(errorText(cause)); }
    finally { setBusy(false); }
  }

  return <div className="screen-stack community-screen">
    <div className="community-discovery-controls"><div className="community-search-panel" role="search">
      <label className="community-search-label" htmlFor={searchId}>搜索课程</label>
      <div className="community-search-field"><Search size={19}/><input id={searchId} type="search" inputMode="search" enterKeyHint="search"
        value={query} placeholder="搜索教材与学习资料" autoComplete="off" onChange={event => search(event.target.value)}/>
        {query && <button type="button" aria-label="清除搜索内容" onClick={() => search("")}><X size={17}/></button>}</div></div>
      <section className="community-catalog-section community-category-section" aria-label="内容分类"><div className="community-category-rail">
        <div className="community-category-list" role="group" aria-label="按内容筛选">{categories.map(item => <button className="community-category-button"
          key={item.kind} type="button" aria-pressed={kind === item.kind} onClick={() => choose(item.kind)}><span>{item.label}</span></button>)}</div>
        <button className="community-category-more" type="button" aria-label="查看更多分类" aria-expanded={categoryOpen} onClick={() => setCategoryOpen(value => !value)}>
          <ChevronDown size={21}/></button></div>
        {categoryOpen && <div className="community-category-menu" role="group" aria-label="全部内容分类">{categories.map(item => <button
          className="community-category-menu-button" key={item.kind} type="button" aria-pressed={kind === item.kind} onClick={() => choose(item.kind)}>{item.label}</button>)}</div>}
      </section></div>
    <div className="demo-port-community-actions"><button type="button" onClick={onSocial}><UsersRound size={20}/>好友与消息 <ChevronRight size={16}/></button>
      <Button icon={<Share2 size={17}/>} disabled={!books.length} onClick={() => setSharing(true)}>分享我的内容</Button></div>
    {notice && <div className="demo-port-community-notice" role="status"><Check size={18}/>{notice}<button onClick={onOpenLibrary}>打开书架 <ArrowRight size={15}/></button></div>}
    {feedError && <div className="community-empty-state" role="alert"><SearchX size={24}/><div><h3>社区内容加载失败</h3><p>{feedError}</p></div><button onClick={reset}>重试</button></div>}
    <section className="community-catalog-section community-popular-section" aria-label="社区课程">
      <p className="community-result-summary" aria-live="polite">{query.trim() ? `“${query.trim()}”找到 ${items.length} 份分享` : `已显示 ${items.length} 份真实分享`}</p>
      {loading && <p role="status">正在读取社区分享…</p>}
      {!loading && !feedError && !items.length && <div className="community-empty-state"><SearchX size={24}/><div role="status"><h3>没有找到匹配内容</h3>
        <p>{query ? "换一个关键词再试。" : "社区还没有这个分类的真实用户分享。"}</p></div><button onClick={() => { choose("all"); search(""); }}>查看推荐</button></div>}
      <div className="community-grid">{items.map(post => <button className="community-book-card" key={post.id} type="button"
        aria-label={`查看分享：${post.title}`} onClick={() => { setOperationError(""); setPreview(post); }}>
        <span className="community-book-visual"><PostCover post={post}/></span><span className="community-book-copy"><strong>{post.title}</strong>
          <span className="community-book-bottom"><span className="community-book-meta"><small>{kindLabel[post.kind]} · {post.mine ? "我的分享" : post.author}</small>
            <span>{post.in_library ? "已在书架" : `${post.downloads} 人领取`}</span></span><span className="community-book-enter">进入</span></span></span>
      </button>)}</div>
      {more && <Button variant="secondary" disabled={loading} onClick={() => setPage(value => value + 1)}>{loading ? "正在加载…" : "加载更多"}</Button>}
      <p className="demo-port-community-rights">分享教材与笔记时请确认拥有传播权；个人学习记录不会随教材公开。</p>
    </section>
    {preview && <div className="demo-port-community-preview-layer" role="presentation" onPointerDown={event => { if (!busy && event.target === event.currentTarget) setPreview(null); }}>
      <section className="demo-port-community-preview community-detail-screen" role="dialog" aria-modal="true" aria-label={kindLabel[preview.kind]}>
        <header><button type="button" aria-label="关闭详情" disabled={busy} onClick={() => setPreview(null)}><X size={20}/></button><strong>{kindLabel[preview.kind]}</strong></header>
        <div className="community-detail-workspace"><article className="community-detail-card"><div className="community-detail-cover"><PostCover post={preview}/></div>
          <div className="community-detail-content"><h2>{preview.title}</h2><p>{preview.author} · {preview.downloads} 人领取</p>
            {preview.kind === "book" ? <p>{preview.book?.summary || "这本书暂时没有简介。"}</p> : <SharedContent content={preview.content}/>}
            {preview.description && <p>{preview.description}</p>}
            <div className="demo-port-community-check" role="status">{checking ? "正在检查是否已领取…" : check ? check.already_owned ? "已在你的书架或收藏中。" : check.similar_titles.length ? "发现同名但内容不同的教材，请核对版本。" : "未发现重复内容，可以保存。" : "重复检查尚未完成。"}
              {check && check.similar_titles.length > 0 && !check.already_owned && <><ul>{check.similar_titles.map((title, index) => <li key={index}>{title}</li>)}</ul>
                <label><input type="checkbox" checked={similarConfirmed} onChange={event => setSimilarConfirmed(event.target.checked)}/>我已核对版本，仍要领取</label></>}</div>
            {operationError && <p className="field-error" role="alert">{operationError}{!check && <button onClick={() => setCheckRevision(value => value + 1)}>重试检查</button>}</p>}
          </div></article></div>
        <div className="community-detail-actions"><Button disabled={busy || !check || checking || (!check.already_owned && !!check.similar_titles.length && !similarConfirmed)}
          onClick={() => void (check?.already_owned ? openOwned() : acquire())}>{busy ? "正在处理…" : check?.already_owned ? "去我的书架" : preview.kind === "book" ? "加入我的书架" : "保存到闪卡与笔记"}</Button>
          {preview.mine && <Button variant="danger" disabled={busy} onClick={() => void withdraw()}>撤回我的分享</Button>}</div>
      </section></div>}
    {sharing && <ShareDialog books={books} onClose={() => setSharing(false)} onShared={duplicate => { setSharing(false); setNotice(duplicate ? "相同内容已在社区，没有重复发布。" : "分享已发布。现可被其他用户领取。"); reset(); }}/>}
  </div>;
}
