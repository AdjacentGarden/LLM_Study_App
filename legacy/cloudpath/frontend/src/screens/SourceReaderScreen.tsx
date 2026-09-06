import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import {
  BookOpen,
  FileText
} from "lucide-react";
import {
  Button,
  Card,
  Pill
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { SkeletonReveal, useImageMotion, useLocalMotionItem, type LoadState } from "../motion";
import {
  sourcePageImageUrl,
  sourcePageLabel,
  sourceUnitName
} from "./shared";

export function SourceReaderScreen() {
  const { back, go, parsedChapters, parsedScanResult, sourcePageTarget, showToast, uploadedFile } = useAppContext();
  const bookId = sourcePageTarget?.bookId ?? uploadedFile?.bookId ?? "";
  const pageCount = parsedScanResult?.page_count ?? null;
  const sourceUnit = parsedScanResult?.source_unit ?? "page";
  const unitName = sourceUnitName(sourceUnit);
  const targetStart = Math.max(1, sourcePageTarget?.pageStart ?? 1);
  const targetEnd = Math.max(targetStart, sourcePageTarget?.pageEnd ?? targetStart);
  const [currentPage, setCurrentPage] = useState(targetStart);
  const [failedImageKey, setFailedImageKey] = useState<string | null>(null);
  const pageDragStart = useRef<{ pointerId: number; x: number; y: number } | null>(null);
  const imageUrl = bookId ? sourcePageImageUrl(bookId, currentPage) : "";
  const imageKey = `${bookId}:${currentPage}:${imageUrl}`;
  const imageFailed = Boolean(imageUrl) && failedImageKey === imageKey;
  const pageMotion = useLocalMotionItem(`source-page:${imageKey}`, "source-page-content");
  const imageMotion = useImageMotion(imageUrl);
  const sourceLoadState: LoadState = imageFailed || imageMotion.state === "failed"
    ? "error"
    : imageMotion.state === "loading"
      ? "loading"
      : "ready";

  useEffect(() => {
    setCurrentPage(targetStart);
    setFailedImageKey(null);
  }, [bookId, targetStart]);

  useEffect(() => {
    setFailedImageKey(null);
  }, [currentPage, imageUrl]);

  const maxPage = Math.max(pageCount ?? targetEnd, targetEnd, 1);
  const previousPage = () => setCurrentPage((page) => Math.max(1, page - 1));
  const nextPage = () => setCurrentPage((page) => Math.min(maxPage, page + 1));

  function handlePagePointerDown(event: PointerEvent<HTMLElement>) {
    if (!event.isPrimary) return;
    pageDragStart.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch {
      // Synthetic accessibility tests do not own a native pointer; real touch pointers do.
    }
  }

  function commitPageSwipe(event: PointerEvent<HTMLElement>) {
    const start = pageDragStart.current;
    if (!start || start.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - start.x;
    const deltaY = event.clientY - start.y;
    const threshold = Math.max(48, event.currentTarget.clientWidth * 0.08);
    if (Math.abs(deltaX) < threshold || Math.abs(deltaX) <= Math.abs(deltaY) * 1.2) return;
    pageDragStart.current = null;
    if (deltaX < 0) nextPage();
    else previousPage();
  }

  function handlePagePointerEnd(event: PointerEvent<HTMLElement>) {
    commitPageSwipe(event);
    pageDragStart.current = null;
  }

  function handlePageKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      previousPage();
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      nextPage();
    }
  }
  const currentChapter = parsedChapters
    ?.filter((chapter) => chapter.page_start <= currentPage && currentPage <= chapter.page_end)
    .sort((left, right) => right.level - left.level || (left.page_end - left.page_start) - (right.page_end - right.page_start))[0];
  const displayTitle = currentChapter?.source_title ?? sourcePageTarget?.title ?? uploadedFile?.name ?? "教材原文";
  const exactLocation = parsedScanResult?.source_locations?.find((item) => Number(item.index) === targetStart);
  const sourceRange = targetStart === targetEnd && typeof exactLocation?.label === "string"
    ? exactLocation.label
    : sourceUnit === "page"
      ? sourcePageLabel(targetStart, targetEnd)
      : `${unitName} ${targetStart}${targetEnd !== targetStart ? `-${targetEnd}` : ""}`;
  const printedStart = sourcePageTarget?.printedPageStart;
  const printedEnd = sourcePageTarget?.printedPageEnd ?? printedStart;
  const displayRange = typeof printedStart === "number"
    ? `教材${sourcePageLabel(printedStart, typeof printedEnd === "number" ? printedEnd : printedStart)}（PDF ${sourcePageLabel(targetStart, targetEnd)}）`
    : sourceRange;
  const isOnTargetRange = currentPage >= targetStart && currentPage <= targetEnd;
  const currentLocation = parsedScanResult?.source_locations?.find((item) => Number(item.index) === currentPage);
  const currentLocationLabel = typeof currentLocation?.label === "string"
      ? currentLocation.label
      : sourceUnit === "page"
        ? `PDF ${sourcePageLabel(currentPage)}`
        : `${unitName} ${currentPage}`;

  if (!bookId) {
    return (
      <div className="screen-stack source-reader-screen">
        <Card className="source-reader-empty">
          <FileText size={34} aria-hidden="true" />
          <h2>还没有可查看的原文</h2>
          <p>请先上传并解析教材，再从闪卡、课程证据或做题诊断中打开原文页。</p>
          <Button onClick={() => go("upload")}>去上传教材</Button>
        </Card>
      </div>
    );
  }

  return (
    <div className="screen-stack source-reader-screen">
      <section className="source-reader-summary">
        <Pill tone={isOnTargetRange ? "mint" : "sky"}>{isOnTargetRange ? "已定位引用页" : "正在浏览原文"}</Pill>
        <h2>{displayTitle}</h2>
        <p>
          当前位置：{currentLocationLabel}{pageCount ? ` · 共 ${pageCount} 个${unitName}` : ""}
          {!isOnTargetRange ? ` · 原引用：${displayRange}` : ""}
        </p>
      </section>

      <div className="source-reader-workspace">
      <aside className="source-reader-side-panel" aria-label="原文操作">
      <div className="source-reader-toolbar">
        <button
          type="button"
          disabled={currentPage <= 1}
          onClick={previousPage}
        >
          上一页
        </button>
        <strong>{unitName} {currentPage}</strong>
        <button
          type="button"
          disabled={currentPage >= maxPage}
          onClick={nextPage}
        >
          下一页
        </button>
      </div>
      </aside>

      <figure
        {...pageMotion.attributes}
        className="source-page-frame"
        key={pageMotion.motionKey}
        tabIndex={0}
        aria-label={`原文翻页区域，当前${unitName} ${currentPage}，左右滑动或使用方向键翻页`}
        onKeyDown={handlePageKeyDown}
        onPointerDown={handlePagePointerDown}
        onPointerMove={commitPageSwipe}
        onPointerUp={handlePagePointerEnd}
        onPointerCancel={() => { pageDragStart.current = null; }}
      >
        <div className="source-page-media">
          <SkeletonReveal
            className="source-page-skeleton-reveal"
            state={sourceLoadState}
            readyKind="content"
            skeleton={(
              <div className="source-page-skeleton" aria-hidden="true">
                <span className="source-page-skeleton-heading" />
                <span className="source-page-skeleton-line is-wide" />
                <span className="source-page-skeleton-line" />
                <span className="source-page-skeleton-line is-short" />
              </div>
            )}
            error={(
              <div
                className="source-page-fallback"
                data-motion-image-source={imageUrl}
                data-motion-image-state="failed"
                role="status"
              >
                <FileText size={34} aria-hidden="true" />
                <strong>原文页暂不可用</strong>
                <span>{unitName} {currentPage}</span>
              </div>
            )}
          >
            <img
              key={imageKey}
              className="source-page-image"
              data-motion-image-state={imageMotion.state}
              ref={imageMotion.imageRef}
              src={imageUrl}
              alt={`${displayTitle} ${unitName} ${currentPage}`}
              onLoad={imageMotion.onLoad}
              onDragStart={(event) => event.preventDefault()}
              onAnimationEnd={(event) => {
                if (event.animationName === "motion-stage3-image-in") imageMotion.settleAnimation();
              }}
              onError={() => {
                imageMotion.onError();
                setFailedImageKey(imageKey);
                showToast("原文页加载失败，请检查页码范围或后端页图接口", "warning");
              }}
            />
          </SkeletonReveal>
        </div>
        <figcaption>
          <BookOpen size={14} aria-hidden="true" />
          原文件安全预览 · {unitName} {currentPage}
        </figcaption>
      </figure>

      <div className="source-reader-actions">
        <Button
          variant="secondary"
          disabled={currentPage === targetStart}
          onClick={() => setCurrentPage(targetStart)}
        >
          回到引用页
        </Button>
        <Button variant="secondary" onClick={back}>返回上一页</Button>
        <Button onClick={() => go("lesson")}>回到课程</Button>
      </div>
      </div>
    </div>
  );
}
