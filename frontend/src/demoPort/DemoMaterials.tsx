import { useEffect, useMemo, useState } from "react";
import { BookOpen, FilePlus2, Layers3, NotebookPen, Search, Share2, Undo2 } from "lucide-react";
import { api } from "../api/client";
import { NoteEditor, ShareDialog, SharedContent } from "../components/CommunitySharing";
import { Button, Card } from "./DemoPrimitives";
import type { BookCatalogItem, LibraryResource } from "../types/api";

const errorText = (value: unknown) => value instanceof Error ? value.message : "请求暂时未完成，请稍后重试。";
export function DemoMaterials({ books, activeBook, onDiscover }: { books: BookCatalogItem[]; activeBook: BookCatalogItem | null; onDiscover: () => void }) {
  const [resources, setResources] = useState<LibraryResource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [operationError, setOperationError] = useState("");
  const [revision, setRevision] = useState(0);
  const [kind, setKind] = useState<"all" | "note" | "flashcards">("all");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editor, setEditor] = useState<LibraryResource | "new" | null>(null);
  const [sharing, setSharing] = useState<LibraryResource | null>(null);
  const [busy, setBusy] = useState(false);
  const [undoResource, setUndoResource] = useState<LibraryResource | null>(null);
  const [notice, setNotice] = useState("");
  const key = books.map(book => book.book_id).sort().join("|");
  const noteBooks = useMemo(() => activeBook ? [activeBook, ...books.filter(book => book.book_id !== activeBook.book_id)] : books,
    [activeBook, books]);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(""); setResources([]);
    void api.resources().then(value => { if (active) setResources(value); })
      .catch(cause => { if (active) setError(errorText(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [revision, key]);
  const filtered = resources.filter(item => {
    if (kind !== "all" && item.kind !== kind) return false;
    const text = `${item.title} ${books.find(book => book.book_id === item.book_id)?.title || ""}`.toLocaleLowerCase();
    return text.includes(query.trim().toLocaleLowerCase());
  });
  const selected = filtered.find(item => item.id === selectedId) ?? filtered[0] ?? null;
  async function archive(item: LibraryResource) {
    setBusy(true); setOperationError("");
    try { await api.archiveResource(item.id, true); setResources(old => old.filter(value => value.id !== item.id)); setUndoResource(item);
      setNotice("资料已收起，可以撤销；原始学习记录仍保留。"); setSelectedId(null); }
    catch (cause) { setOperationError(errorText(cause)); }
    finally { setBusy(false); }
  }
  async function undo() {
    if (!undoResource) return;
    setBusy(true); setError("");
    try { await api.archiveResource(undoResource.id, false); setUndoResource(null); setNotice("资料已恢复。"); setRevision(value => value + 1); }
    catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }
  return <div className="screen-stack notes-screen demo-port-materials">
    <Card className="book-summary demo-port-materials-hero"><span className="book-summary-icon"><NotebookPen size={30}/></span>
      <div><h2>闪卡与笔记</h2><p>私人笔记和社区收藏按当前账号保存；课程闪卡可在对应章节复习。</p>
        <div className="notes-actions"><Button icon={<FilePlus2 size={18}/>} disabled={!books.length} onClick={() => setEditor("new")}>写一篇笔记</Button>
          <Button variant="secondary" onClick={onDiscover}>发现社区内容</Button></div></div></Card>
    {notice && <p className="demo-port-plan-notice" role="status">{notice} {undoResource && <button type="button" disabled={busy} onClick={() => void undo()}><Undo2 size={15}/>撤销收起</button>}</p>}
    {error && <Card className="adjustment-card" role="alert"><p>{error}</p><Button variant="secondary" onClick={() => setRevision(value => value + 1)}>重试读取</Button></Card>}
    <div className="demo-port-materials-filter"><label className="mistake-search"><Search size={18}/><span className="sr-only">搜索我的资料</span>
      <input type="search" value={query} placeholder="搜索标题或关联教材" onChange={event => setQuery(event.target.value)}/></label>
      <div className="mistake-filter-group" role="group" aria-label="资料类型">{([["all", "全部"], ["note", "笔记"], ["flashcards", "闪卡"]] as const).map(([id, label]) => <button
        key={id} type="button" aria-pressed={kind === id} onClick={() => setKind(id)}>{label}</button>)}</div></div>
    {loading && <p role="status">正在读取你的资料…</p>}
    <div className="notes-workspace"><div className="notes-list" aria-label="资料列表">
      {filtered.map(item => <button className="card note-card note-list-item" key={item.id} type="button" data-selected={selected?.id === item.id}
        aria-pressed={selected?.id === item.id} onClick={() => setSelectedId(item.id)}>
        {item.kind === "note" ? <NotebookPen size={18}/> : <Layers3 size={18}/>}<div><h3>{item.title}</h3>
          <p>{books.find(book => book.book_id === item.book_id)?.title || "关联教材未在当前书架"} · {item.source_post ? "社区收藏" : "私人记录"}</p></div></button>)}
      {!loading && !error && !filtered.length && <Card className="note-card"><NotebookPen size={18}/><div><h3>{resources.length ? "没有匹配的资料" : "还没有资料"}</h3>
        <p>写下自己的理解，或从社区收藏真实分享。</p></div></Card>}
    </div>
    <Card className="book-summary notes-detail-panel"><span className="book-summary-icon">{selected?.kind === "flashcards" ? <Layers3 size={30}/> : <NotebookPen size={30}/>}</span>
      <div>{selected ? <><h2>{selected.title}</h2><p>{books.find(book => book.book_id === selected.book_id)?.title || "关联教材未在当前书架"}</p>
        <SharedContent content={selected.content}/><div className="notes-actions">
          {selected.kind === "note" && !selected.source_post && books.some(book => book.book_id === selected.book_id) && <Button variant="secondary" disabled={busy} onClick={() => setEditor(selected)}>编辑笔记</Button>}
          {books.some(book => book.book_id === selected.book_id) && <Button variant="secondary" icon={<Share2 size={16}/>} disabled={busy} onClick={() => setSharing(selected)}>分享到社区</Button>}
          <Button variant="danger" disabled={busy} onClick={() => void archive(selected)}>收起这份资料</Button></div>
        {operationError && <p className="field-error" role="alert">{operationError}</p>}</> : <><h2>选择一份资料</h2><p>左侧会显示当前账号保存的笔记与闪卡。</p></>}</div>
    </Card></div>
    {editor && <NoteEditor books={noteBooks} note={editor === "new" ? undefined : editor} onClose={() => setEditor(null)}
      onSaved={() => { setEditor(null); setNotice("笔记已保存到当前账号，不会自动公开。"); setRevision(value => value + 1); }}/>}
    {sharing && <ShareDialog books={books} initialBookId={activeBook?.book_id} resource={sharing} onClose={() => setSharing(null)}
      onShared={duplicate => { setSharing(null); setNotice(duplicate ? "相同资料已在社区，没有重复发布。" : "资料已分享到社区。"); }}/>}
  </div>;
}
