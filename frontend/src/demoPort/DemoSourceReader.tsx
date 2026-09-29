import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, FileText } from "lucide-react";
import { Button, Card, Pill } from "./DemoPrimitives";
import { SourcePageInk } from "../screens/SourcePageInk";
import type { SourcePage, StudyRepository } from "../services/contracts";

export function DemoSourceReader({ bookId, pageNumber, title, repository, onPage, onBack, onRegisterSave }: {
  bookId: string; pageNumber: number; title: string; repository: StudyRepository;
  onPage: (page: number) => void; onBack: () => void;
  onRegisterSave: (save: (() => Promise<boolean>) | null) => void;
}) {
  const [page, setPage] = useState<SourcePage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [imageFailed, setImageFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const [revision, setRevision] = useState(0);
  const saveInk = useRef<(() => Promise<boolean>) | null>(null);
  const registerSave = useCallback((save: (() => Promise<boolean>) | null) => { saveInk.current = save; onRegisterSave(save); }, [onRegisterSave]);
  useEffect(() => {
    let active = true;
    setPage(null); setLoading(true); setError(""); setImageFailed(false); setRetry(0);
    void repository.library.page(bookId, pageNumber).then(value => {
      if (active && value.book_id === bookId && value.page_number === pageNumber) setPage(value);
    }).catch(cause => { if (active) setError((cause as Error).message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [bookId, pageNumber, repository, revision]);
  async function turn(next: number) {
    if (saveInk.current && !await saveInk.current()) return;
    onPage(next);
  }
  return <div className="screen-stack source-reader-screen">
    <section className="source-reader-summary"><Pill tone="mint">{page ? "已定位原文页" : "正在读取原文"}</Pill>
      <h2>{title}</h2><p>PDF 第 {pageNumber} 页{page?.printed_page_number ? ` · 印刷页码 ${page.printed_page_number}` : ""}</p></section>
    <div className="source-reader-workspace">
      <aside className="source-reader-side-panel" aria-label="原文操作"><div className="source-reader-toolbar">
        <button type="button" disabled={pageNumber <= 1 || loading} onClick={() => void turn(pageNumber - 1)}>上一页</button>
        <strong>PDF {pageNumber}{page ? ` / ${page.page_count}` : ""}</strong>
        <button type="button" disabled={!page || pageNumber >= page.page_count} onClick={() => void turn(pageNumber + 1)}>下一页</button>
      </div></aside>
      {loading && <Card className="source-page-frame" aria-busy="true"><div className="source-page-skeleton" aria-hidden="true"><span className="source-page-skeleton-heading"/><span className="source-page-skeleton-line is-wide"/><span className="source-page-skeleton-line"/></div><p role="status">正在读取这一页的原文…</p></Card>}
      {error && <Card className="source-page-frame source-reader-empty" role="alert"><FileText size={34}/><strong>这一页暂时无法读取</strong><p>{error}</p>
        <Button onClick={() => setRevision(value => value + 1)}>重新读取</Button></Card>}
      {page && <>
        {page.image_url && !imageFailed ? <div className="source-page-frame demo-port-source-frame">
          <SourcePageInk key={`${bookId}:${pageNumber}`} bookId={bookId} pageNumber={pageNumber} title={title} text={page.text}
            imageUrl={retry ? `${page.image_url}${page.image_url.includes("?") ? "&" : "?"}retry=${retry}` : page.image_url}
            onImageError={() => setImageFailed(true)} registerSave={registerSave}/>
          <figcaption><BookOpen size={14}/> 教材原页 · PDF 第 {pageNumber} 页</figcaption>
        </div> : <Card className="source-page-frame source-reader-empty"><FileText size={34}/><h3>原页图片暂时无法显示</h3>
          <p>{page.text ? "可阅读下方提取文字，或重试加载原页。" : "这一页的图像与文字均不可用。"}</p>
          {page.image_url && <Button onClick={() => { setImageFailed(false); setRetry(value => value + 1); }}>重试加载页图</Button>}</Card>}
        {page.text && <article className="source-page-text-document" aria-label="可检索文字"><h3>可检索文字</h3>
          {page.text.split(/\n{2,}/u).map((paragraph, index) => <p key={index}>{paragraph.trim()}</p>)}</article>}
        <div className="source-reader-actions"><Button variant="secondary" disabled={pageNumber <= 1} onClick={() => void turn(pageNumber - 1)}>上一页</Button>
          <Button variant="secondary" disabled={pageNumber >= page.page_count} onClick={() => void turn(pageNumber + 1)}>下一页</Button>
          <Button onClick={onBack}>返回上一页</Button></div>
      </>}
    </div>
  </div>;
}
