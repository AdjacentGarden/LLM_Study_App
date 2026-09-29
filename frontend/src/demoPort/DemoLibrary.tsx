import { useState } from "react";
import { CheckCircle2, CircleAlert, FileText, FolderOpen, Settings, Upload } from "lucide-react";
import { CourseCardMotion } from "../demo/motion";
import { Button, Card, Pill, ProgressBar } from "./DemoPrimitives";
import type { BookCatalogItem } from "../types/api";
import type { StudyWorkspace } from "../types/studyWorkspace";

export function DemoLibrary({ books, activeId, workspace, busy, onSelect, onRemove, onUpload, onDiscover, onMaterials }: {
  books: BookCatalogItem[]; activeId: string | null; workspace: StudyWorkspace | null; busy: boolean;
  onSelect: (id: string) => void; onRemove: (id: string) => Promise<void>;
  onUpload: () => void; onDiscover: () => void; onMaterials: () => void;
}) {
  const [editId, setEditId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [error, setError] = useState("");
  async function remove(id: string) {
    setRemovingId(id); setError("");
    try { await onRemove(id); setEditId(null); setConfirmId(null); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "移出书架失败"); }
    finally { setRemovingId(null); }
  }
  return <div className="screen-stack library-screen">
    {error && <p className="field-error" role="alert">{error}</p>}
    {!books.length ? <Card className="parse-empty-card"><FolderOpen size={34}/><h2>我的课程库还是空的</h2>
      <p>上传 PDF 教材，或从发现页领取一本共享教材。</p><Button icon={<Upload size={18}/>} onClick={onUpload}>上传新教材</Button>
      <Button variant="secondary" onClick={onDiscover}>发现共享教材</Button></Card>
      : <section className="library-course-grid" aria-label="课程列表">{books.map((book, index) => {
        const ready = book.status === "ready" || book.chapter_count > 0;
        const selected = book.book_id === activeId;
        const progress = selected && workspace?.book_id === book.book_id ? workspace.plan.progress.percent : null;
        return <CourseCardMotion bookId={book.book_id} index={index} key={book.book_id}>{attributes => <Card {...attributes}
          className={`course-space-card ${editId === book.book_id ? "is-editing" : ""}`}>
          <button className="course-card-edit" type="button" aria-label={`管理 ${book.title}`} onClick={() => { setEditId(value => value === book.book_id ? null : book.book_id); setConfirmId(null); }}><Settings size={16}/></button>
          {book.cover_url ? <img className="course-cover-image" src={book.cover_url} alt={`${book.title}封面`} loading="lazy"/> :
            <span className="book-summary-icon course-cover-fallback" role="img" aria-label={`${book.title}暂无封面`}><FileText size={30}/></span>}
          <div className="library-status-content"><div className="library-status-heading"><Pill tone={selected ? "purple" : ready ? "mint" : "orange"}>
            {selected ? "正在学习" : ready ? "已加入课程库" : "正在准备"}</Pill>{ready && <span className="library-status-success-mark"><CheckCircle2 size={15}/></span>}</div>
            <h2>{book.title}</h2><p>下一步：{ready ? "打开教材目录，继续学习" : "等待教材处理完成"}</p>
            {progress !== null ? <ProgressBar value={progress} label={`学习计划进度 ${progress}%`}/> : <p className="demo-port-library-progress-note">打开教材后查看学习进度</p>}
            <div className="course-space-meta">{book.page_count !== null && <span>{book.page_count} 页</span>}<span>{book.chapter_count} 个目录项</span></div>
          </div>
          <div className="button-row"><Button disabled={busy} onClick={() => onSelect(book.book_id)}>进入课程</Button></div>
          {editId === book.book_id && <div className="course-card-menu"><button type="button" onClick={onMaterials}><FileText size={16}/>查看闪卡与笔记</button>
            <button className={`danger ${confirmId === book.book_id ? "confirm" : ""}`} type="button" disabled={removingId === book.book_id}
              onClick={() => { if (confirmId === book.book_id) void remove(book.book_id); else setConfirmId(book.book_id); }}>
              <CircleAlert size={16}/>{removingId === book.book_id ? "移出中…" : confirmId === book.book_id ? "确认移出书架" : "移出书架"}</button>
            {confirmId === book.book_id && <small>学习记录仍保留在你的账号中。</small>}</div>}
        </Card>}</CourseCardMotion>;
      })}</section>}
    <Button variant="secondary" icon={<Upload size={18}/>} onClick={onUpload}>上传新教材</Button>
  </div>;
}
