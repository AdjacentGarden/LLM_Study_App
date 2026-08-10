import { useEffect, useRef, useState } from "react";
import { Download } from "lucide-react";
import { Button } from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useLocalMotionItem } from "../motion";
import { CommunityCover } from "./CommunityCover";
import { resolveCommunityBook } from "./communityCatalog";

export function CommunityBookScreen() {
  const { go, selectedCommunityBookId } = useAppContext();
  const book = resolveCommunityBook(selectedCommunityBookId);
  const detailMotion = useLocalMotionItem(`community-book:${book.id}:detail`);
  const [activeDetailTab, setActiveDetailTab] = useState<"overview" | "comments">("overview");
  const [isCollapsed, setIsCollapsed] = useState(false);
  const screenRef = useRef<HTMLDivElement>(null);
  const collapseSentinelRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const screen = screenRef.current;
    const sentinel = collapseSentinelRef.current;
    const scroller = screen?.closest<HTMLElement>(".screen-content");
    if (!screen || !sentinel || !scroller || typeof IntersectionObserver === "undefined") return;

    let observer: IntersectionObserver | null = null;

    const observeCollapseBoundary = () => {
      observer?.disconnect();
      const headerInset = Number.parseFloat(getComputedStyle(scroller).paddingTop) || 0;
      observer = new IntersectionObserver(
        ([entry]) => {
          const boundaryTop = entry.rootBounds?.top ?? scroller.getBoundingClientRect().top + headerInset;
          setIsCollapsed(!entry.isIntersecting && entry.boundingClientRect.top <= boundaryTop);
        },
        {
          root: scroller,
          rootMargin: `${0 - headerInset}px 0px 0px`,
          threshold: 0,
        },
      );
      observer.observe(sentinel);
    };

    observeCollapseBoundary();
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(observeCollapseBoundary);
    resizeObserver?.observe(scroller);

    return () => {
      observer?.disconnect();
      resizeObserver?.disconnect();
    };
  }, [book.id]);

  return (
    <div ref={screenRef} className="screen-stack community-detail-screen" data-collapsed={isCollapsed ? "true" : "false"}>
      <div className="community-detail-workspace">
        <article {...detailMotion.attributes} className="community-detail-overview">
          <span ref={collapseSentinelRef} className="community-detail-collapse-sentinel" aria-hidden="true" />
          <div className="community-detail-visual" aria-hidden={isCollapsed}>
            <CommunityCover source={book.cover} title={book.title} variant="detail" />
          </div>

          <div className="community-detail-summary">
            <p className="community-detail-owner">{book.owner}</p>
            <h2>{book.title}</h2>
            <p className="community-detail-edition">
              {[book.subject, book.grade, book.version, book.volume].filter(Boolean).join(" · ")}
            </p>

            <dl className="community-detail-stats" aria-label="课程概览">
              <div data-stat="learners">
                <dd>{book.learners} 人</dd>
                <dt>正在学习</dt>
              </div>
              <div data-stat="chapters">
                <dd>{book.chapters.length} 章</dd>
                <dt>课程章节</dt>
              </div>
              <div data-stat="flashcards">
                <dd>{book.flashcardCount} 张</dd>
                <dt>概念闪卡</dt>
              </div>
              <div data-stat="progress">
                <dd>{book.progress}%</dd>
                <dt>学习进度</dt>
              </div>
            </dl>

            <div className="community-detail-tabs" role="tablist" aria-label="课程详情内容">
              <button
                id="community-detail-overview-tab"
                type="button"
                role="tab"
                aria-controls="community-detail-overview-panel"
                aria-selected={activeDetailTab === "overview"}
                onClick={() => setActiveDetailTab("overview")}
              >
                课程简介
              </button>
              <button
                id="community-detail-comments-tab"
                type="button"
                role="tab"
                aria-controls="community-detail-comments-panel"
                aria-selected={activeDetailTab === "comments"}
                onClick={() => setActiveDetailTab("comments")}
              >
                评论
              </button>
            </div>

            {activeDetailTab === "overview" ? (
              <section
                className="community-detail-tab-panel community-detail-description"
                id="community-detail-overview-panel"
                role="tabpanel"
                aria-labelledby="community-detail-overview-tab"
              >
                <p>{book.description}</p>
              </section>
            ) : (
              <section
                className="community-detail-tab-panel community-detail-comments"
                id="community-detail-comments-panel"
                role="tabpanel"
                aria-labelledby="community-detail-comments-tab"
              >
                <article>
                  <span aria-hidden="true">周</span>
                  <div>
                    <strong>周同学</strong>
                    <p>重点整理得很清楚，适合复习{book.catalogTitle}。</p>
                  </div>
                </article>
                <article>
                  <span aria-hidden="true">陈</span>
                  <div>
                    <strong>陈同学</strong>
                    <p>课程结构简洁，导入后继续学习很方便。</p>
                  </div>
                </article>
              </section>
            )}
          </div>
        </article>

      </div>

      <div className="community-detail-actions">
        <Button icon={<Download size={18} aria-hidden="true" />} onClick={() => go("communityImport")}>
          导入到我的课程
        </Button>
      </div>
    </div>
  );
}
