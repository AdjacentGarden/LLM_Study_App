import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Download, ExternalLink, Loader2 } from "lucide-react";
import { Button } from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import { useLocalMotionItem } from "../motion";
import type { CommunityBookSummary } from "../types/api";
import { CommunityCover } from "./CommunityCover";

function formatBytes(bytes: number) {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function CommunityBookScreen() {
  const {
    clearCourseSession,
    clearLoadedCourse,
    go,
    refreshCourses,
    selectedCommunityBookId,
    setSelectedUpload,
    setUploadedFile,
    showToast
  } = useAppContext();
  const repository = useBookCourseRepository();
  const detailMotion = useLocalMotionItem(`community-book:${selectedCommunityBookId}:detail`);
  const [book, setBook] = useState<CommunityBookSummary | null>(null);
  const [activeDetailTab, setActiveDetailTab] = useState<"overview" | "source">("overview");
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [state, setState] = useState<"loading" | "ready" | "error" | "importing">("loading");
  const [error, setError] = useState<string | null>(null);
  const screenRef = useRef<HTMLDivElement>(null);
  const collapseSentinelRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!selectedCommunityBookId) {
      setState("error");
      setError("尚未选择社区教材");
      return;
    }
    let active = true;
    setState("loading");
    repository.getCommunityBook(selectedCommunityBookId).then((nextBook) => {
      if (!active) return;
      setBook(nextBook);
      setState("ready");
      setError(null);
    }).catch((cause: unknown) => {
      if (!active) return;
      setState("error");
      setError(cause instanceof Error ? cause.message : "社区教材详情加载失败");
    });
    return () => { active = false; };
  }, [repository, selectedCommunityBookId]);

  useEffect(() => {
    const screen = screenRef.current;
    const sentinel = collapseSentinelRef.current;
    const scroller = screen?.closest<HTMLElement>(".screen-content");
    if (!screen || !sentinel || !scroller || typeof IntersectionObserver === "undefined") return;
    let observer: IntersectionObserver | null = null;
    const observe = () => {
      observer?.disconnect();
      const inset = Number.parseFloat(getComputedStyle(scroller).paddingTop) || 0;
      observer = new IntersectionObserver(([entry]) => {
        const top = entry.rootBounds?.top ?? scroller.getBoundingClientRect().top + inset;
        setIsCollapsed(!entry.isIntersecting && entry.boundingClientRect.top <= top);
      }, { root: scroller, rootMargin: `${-inset}px 0px 0px`, threshold: 0 });
      observer.observe(sentinel);
    };
    observe();
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(observe);
    resizeObserver?.observe(scroller);
    return () => { observer?.disconnect(); resizeObserver?.disconnect(); };
  }, [book?.id]);

  async function importBook() {
    if (!book || state === "importing") return;
    setState("importing");
    setError(null);
    try {
      const imported = await repository.importCommunityBook(book.id);
      clearCourseSession();
      clearLoadedCourse();
      setUploadedFile({
        bookId: imported.book_id,
        name: imported.filename,
        sizeBytes: imported.size_bytes,
        contentType: "application/pdf",
        uploadedAt: Date.now(),
        origin: "community-import"
      });
      setSelectedUpload(true);
      await refreshCourses();
      showToast(imported.already_imported ? "这本真实教材已经在你的课程中" : "真实 PDF 已加入，接下来开始整理", "success");
      go("parseReady");
    } catch (cause) {
      setState("ready");
      const message = cause instanceof Error ? cause.message : "真实 PDF 导入失败";
      setError(message);
      showToast(message, "warning");
    }
  }

  function handleDetailTabKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = ["overview", "source"] as const;
    const currentIndex = tabs.indexOf(activeDetailTab);
    const nextIndex = event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    const nextTab = tabs[nextIndex];
    setActiveDetailTab(nextTab);
    requestAnimationFrame(() => document.getElementById(`community-detail-${nextTab}-tab`)?.focus());
  }

  if (state === "loading") {
    return <div className="community-empty-state" role="status"><Loader2 className="spin" aria-hidden="true" /><div><h3>正在读取教材详情</h3><p>信息来自后端的真实书目目录。</p></div></div>;
  }
  if (!book) {
    return <div className="community-empty-state" role="alert"><div><h3>无法打开这本教材</h3><p>{error}</p></div></div>;
  }

  return (
    <div ref={screenRef} className="screen-stack community-detail-screen" data-collapsed={isCollapsed ? "true" : "false"}>
      <div className="community-detail-workspace">
        <article {...detailMotion.attributes} className="community-detail-overview">
          <span ref={collapseSentinelRef} className="community-detail-collapse-sentinel" aria-hidden="true" />
          <div className="community-detail-visual" aria-hidden={isCollapsed}>
            <CommunityCover source={book.cover} title={book.title} variant="detail" />
          </div>
          <div className="community-detail-summary">
            <p className="community-detail-owner">{book.author}</p>
            <h2>{book.title}</h2>
            <p className="community-detail-edition">{[book.subject, book.level, book.language, book.edition].filter(Boolean).join(" · ")}</p>
            <dl className="community-detail-stats" aria-label="课程概览">
              <div data-stat="learners"><dd>{book.page_count} 页</dd><dt>真实页数</dt></div>
              <div data-stat="chapters"><dd>{book.chapters.length} 项</dd><dt>目录预览</dt></div>
              <div data-stat="flashcards"><dd>{formatBytes(book.file_size_bytes)}</dd><dt>PDF 大小</dt></div>
              <div data-stat="progress"><dd>{book.imported_book_id ? "已加入" : book.server_cached ? "服务器已备好" : "待缓存"}</dd><dt>课程状态</dt></div>
            </dl>
            <div className="community-detail-tabs" role="tablist" aria-label="课程详情内容">
              <button id="community-detail-overview-tab" type="button" role="tab" aria-controls="community-detail-overview-panel" aria-selected={activeDetailTab === "overview"} tabIndex={activeDetailTab === "overview" ? 0 : -1} onKeyDown={handleDetailTabKeyDown} onClick={() => setActiveDetailTab("overview")}>课程简介</button>
              <button id="community-detail-source-tab" type="button" role="tab" aria-controls="community-detail-source-panel" aria-selected={activeDetailTab === "source"} tabIndex={activeDetailTab === "source" ? 0 : -1} onKeyDown={handleDetailTabKeyDown} onClick={() => setActiveDetailTab("source")}>来源与版权</button>
            </div>
            {activeDetailTab === "overview" ? (
              <section className="community-detail-tab-panel community-detail-description" id="community-detail-overview-panel" role="tabpanel" aria-labelledby="community-detail-overview-tab">
                <p>{book.description}</p>
              </section>
            ) : (
              <section className="community-detail-tab-panel community-detail-description" id="community-detail-source-panel" role="tabpanel" aria-labelledby="community-detail-source-tab">
                <p>{book.rights_notice}</p>
                <p>{book.license_name}</p>
                <p><a href={book.source_page_url} target="_blank" rel="noreferrer">查看 Project Gutenberg 原始书目 <ExternalLink size={14} aria-hidden="true" /></a></p>
                <p><a href={book.license_url} target="_blank" rel="noreferrer">查看许可说明 <ExternalLink size={14} aria-hidden="true" /></a></p>
              </section>
            )}
          </div>
        </article>
      </div>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <div className="community-detail-actions">
        <Button icon={state === "importing" ? <Loader2 className="spin" size={18} aria-hidden="true" /> : <Download size={18} aria-hidden="true" />} onClick={() => void importBook()} disabled={state === "importing"}>
          {state === "importing" ? "正在从服务器导入" : book.imported_book_id ? "已加入，继续整理" : book.server_cached ? "从服务器加入课程" : "下载到服务器并加入"}
        </Button>
      </div>
    </div>
  );
}
