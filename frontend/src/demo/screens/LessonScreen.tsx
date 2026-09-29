import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import {
  BookOpen,
  CheckCircle2,
  CirclePlay,
  Upload
} from "lucide-react";
import {
  Button,
  Card,
  ProgressBar
} from "../../demoPort/DemoPrimitives";
import { useAppContext } from "../context/AppContext";
import {
  globalMotionFallbackMs,
  useLocalMotionItem,
  useMotionPresence,
  useReducedMotion
} from "../motion";
import type { ApiAsset, LessonCitation } from "../types/api";
import {
  backendAssetUrl,
  liveBookTitle,
  sourcePageImageUrl,
  sourcePageLabel
} from "./shared";
import {
  detailedCitationPageLabel,
  learnerCitationPageLabel
} from "./lessonEvidence";
import {
  buildLessonReadingSections
} from "./lessonReading";
import { LessonAiChatEntry } from "./LessonAiChatEntry";
import {
  LessonAnimationDialog,
  lessonAnimationDialogMotionNames
} from "./LessonAnimationDialog";
import {
  lessonAnimationForAsset,
  type LessonAnimation
} from "./lessonAnimations";

const legacyLessonIntroductionAssetId = "asset_ai_meiosis_fertilization_cycle_v1";

function lessonAnimationKey(animation: LessonAnimation) {
  return animation.animationId;
}

function assetPrintedPage(asset: ApiAsset) {
  if (asset.source_type !== "extracted") return null;
  const printedPage = Number(asset.metadata?.printed_page);
  return Number.isFinite(printedPage) && printedPage > 0 ? printedPage : null;
}

function LessonFigure({
  asset,
  citation,
  onOpenSource,
  animation = null,
  onOpenAnimation
}: {
  asset: ApiAsset;
  citation: LessonCitation | null;
  onOpenSource: () => void;
  animation?: LessonAnimation | null;
  onOpenAnimation?: (animation: LessonAnimation, origin: HTMLButtonElement) => void;
}) {
  const [imageFailed, setImageFailed] = useState(false);
  const image = backendAssetUrl(asset.image_url);
  if (!image || imageFailed) return null;

  const printedPage = assetPrintedPage(asset);
  const sourceLabel = citation
    ? learnerCitationPageLabel(citation)
    : asset.source_type === "extracted"
      ? `教材${sourcePageLabel(printedPage ?? asset.page)}`
      : null;
  const media = (
    <img
      src={image}
      alt={asset.caption}
      loading="lazy"
      decoding="async"
      onError={() => setImageFailed(true)}
    />
  );

  return (
    <figure className="lesson-inline-figure">
      {asset.source_type === "extracted" ? (
        <button
          className="lesson-figure-open"
          type="button"
          aria-label={`查看教材原图：${asset.caption}`}
          onClick={onOpenSource}
        >
          {media}
        </button>
      ) : media}
      <figcaption>
        <span className="lesson-figure-caption-copy">
          <span>{asset.source_type === "extracted" ? "教材原图" : "AI 辅助示意"}</span>
          {sourceLabel ? <span aria-hidden="true"> · </span> : null}
          {sourceLabel ? <span>{sourceLabel}</span> : null}
        </span>
        {animation && onOpenAnimation ? (
          <button
            className="lesson-figure-animation-button"
            type="button"
            aria-haspopup="dialog"
            aria-controls="lesson-animation-dialog"
            onClick={(event) => onOpenAnimation(animation, event.currentTarget)}
          >
            <CirclePlay size={15} aria-hidden="true" />
            <span>{animation.triggerLabel}</span>
          </button>
        ) : null}
      </figcaption>
    </figure>
  );
}

function LessonSourceEntry({
  citation,
  onOpen
}: {
  citation: LessonCitation;
  onOpen: () => void;
}) {
  const pageLabel = learnerCitationPageLabel(citation);

  return (
    <button
      className="lesson-source-link"
      type="button"
      aria-label={`查看原文，${pageLabel}`}
      onClick={onOpen}
    >
      <BookOpen size={17} aria-hidden="true" />
      <span>查看原文</span>
    </button>
  );
}

export function LessonScreen() {
  const {
    activeChapterId,
    generatedLessons,
    go,
    lessonBuildJobStatus,
    openSheet,
    parsedAssets,
    parsedChapters,
    parsedChunks,
    showToast,
    uploadedFile,
    demoPort
  } = useAppContext();
  const [buildingLesson, setBuildingLesson] = useState(false);
  const [activeAnimation, setActiveAnimation] = useState<LessonAnimation | null>(null);
  const animationOriginRef = useRef<HTMLButtonElement | null>(null);
  const reducedMotion = useReducedMotion();
  const animationPresence = useMotionPresence<LessonAnimation>({
    requested: activeAnimation,
    getKey: lessonAnimationKey,
    reducedMotion,
    motionNames: lessonAnimationDialogMotionNames,
    maxMotionMs: globalMotionFallbackMs
  });
  const liveChapter = parsedChapters?.find((chapter) => chapter.chapter_id === activeChapterId)
    ?? parsedChapters?.[0]
    ?? null;
  const lesson = liveChapter
    ? generatedLessons?.find((item) => item.chapter_id === liveChapter.chapter_id) ?? null
    : generatedLessons?.[0] ?? null;
  const liveChunks = liveChapter
    ? parsedChunks?.filter((chunk) => chunk.chapter_id === liveChapter.chapter_id) ?? []
    : [];
  const sourceChunkIds = lesson?.source_chunk_ids.length
    ? lesson.source_chunk_ids
    : liveChunks
      .filter((chunk) => chunk.content_type !== "ocr_pending")
      .map((chunk) => chunk.chunk_id);
  const liveAssets = liveChapter
    ? parsedAssets?.filter((asset) => (
        asset.chapter_id === liveChapter.chapter_id
        || (
          asset.source_type === "extracted"
          && liveChapter.page_start <= asset.page
          && asset.page <= liveChapter.page_end
        )
      )) ?? []
    : [];
  const lessonIntroductionAsset = lesson
    ? lesson.asset_ids
        .map((assetId) => liveAssets.find((asset) => asset.asset_id === assetId) ?? null)
        .find((asset) => (
          asset?.source_type === "ai_generated"
          && asset.metadata?.role === "lesson_overview"
        ))
      ?? lesson.asset_ids
        .map((assetId) => liveAssets.find((asset) => asset.asset_id === assetId) ?? null)
        .find((asset) => asset?.source_type === "ai_generated")
      ?? liveAssets.find((asset) => (
        lesson.lesson_id === "lesson_meiosis"
        && asset.asset_id === legacyLessonIntroductionAssetId
      ))
      ?? null
    : null;
  const lessonTitle = lesson?.title ?? liveChapter?.ai_title ?? "等待章节课程";
  const learnerPageStart = liveChapter?.printed_page_start ?? lesson?.page_start ?? liveChapter?.page_start;
  const learnerPageEnd = liveChapter?.printed_page_end ?? lesson?.page_end ?? liveChapter?.page_end;
  const pages = learnerPageStart && learnerPageStart > 0
    ? sourcePageLabel(learnerPageStart, learnerPageEnd ?? learnerPageStart)
    : null;
  const lessonBlocks = lesson?.blocks ?? [];
  const readingSections = useMemo(
    () => buildLessonReadingSections(lessonBlocks, liveAssets),
    [lessonBlocks, liveAssets]
  );
  const [activeLessonPage, setActiveLessonPage] = useState(0);
  const [lessonPageDirection, setLessonPageDirection] = useState<"back" | "forward">("forward");
  const lessonSwipeStart = useRef<{ x: number; y: number; pointerId: number } | null>(null);
  const lessonMotionKey = `${uploadedFile?.bookId ?? "empty"}:${liveChapter?.chapter_id ?? "empty"}:${lesson?.lesson_id ?? "pending"}`;
  const emptyMotion = useLocalMotionItem(`lesson:${lessonMotionKey}:empty`);
  const titleMotion = useLocalMotionItem(`lesson:${lessonMotionKey}:title`);
  const primaryMotion = useLocalMotionItem(`lesson:${lessonMotionKey}:primary`);

  useEffect(() => {
    setActiveLessonPage(0);
    setLessonPageDirection("forward");
  }, [lessonMotionKey]);

  useEffect(() => {
    setActiveLessonPage((currentPage) => Math.min(currentPage, readingSections.length));
  }, [readingSections.length]);

  if (!uploadedFile || !liveChapter) {
    return (
      <div className="screen-stack lesson-screen">
        <Card {...emptyMotion.attributes} className="parse-empty-card">
          <BookOpen size={34} aria-hidden="true" />
          <h2>没有可学习的章节</h2>
          <p>请先上传教材并完成目录解析，章节学习页只展示真实后端生成的课程内容。</p>
          <Button icon={<Upload size={18} aria-hidden="true" />} onClick={() => go("upload")}>上传教材</Button>
          <Button variant="secondary" onClick={() => go("library")}>查看课程库</Button>
        </Card>
      </div>
    );
  }

  const activeUploadedFile = uploadedFile;
  const activeLiveChapter = liveChapter;

  function openCitationSource(blockTitle: string, citation: LessonCitation) {
    const sourceChunk = liveChunks.find((chunk) => chunk.chunk_id === citation.chunk_id);
    const sourceText = sourceChunk?.text.trim() || citation.quote?.trim() || "";
    const source = {
      bookId: activeUploadedFile.bookId,
      title: blockTitle,
      pageStart: citation.page_start,
      pageEnd: citation.page_end,
      printedPageStart: citation.printed_page_start,
      printedPageEnd: citation.printed_page_end,
      sourceText: sourceText || undefined,
      from: "lesson"
    } as const;
    openSheet({
      type: "source",
      title: blockTitle,
      page: detailedCitationPageLabel(citation),
      image: sourcePageImageUrl(activeUploadedFile.bookId, citation.page_start),
      text: sourceText || undefined,
      source
    });
  }

  function openAssetSource(blockTitle: string, asset: ApiAsset, citation: LessonCitation | null) {
    if (citation) {
      openCitationSource(blockTitle, citation);
      return;
    }
    if (asset.source_type !== "extracted") return;
    const printedPage = assetPrintedPage(asset);
    const sourceText = liveChunks
      .filter((chunk) => chunk.page_start <= asset.page && asset.page <= chunk.page_end)
      .map((chunk) => chunk.text.trim())
      .filter(Boolean)
      .join("\n\n");
    const source = {
      bookId: activeUploadedFile.bookId,
      title: asset.caption,
      pageStart: asset.page,
      pageEnd: asset.page,
      printedPageStart: printedPage ?? undefined,
      printedPageEnd: printedPage ?? undefined,
      sourceText: sourceText || undefined,
      from: "lesson"
    } as const;
    const page = typeof printedPage === "number"
      ? `教材${sourcePageLabel(printedPage)}（PDF ${sourcePageLabel(asset.page)}）`
      : `教材${sourcePageLabel(asset.page)}`;
    openSheet({
      type: "source",
      title: asset.caption,
      page,
      image: sourcePageImageUrl(activeUploadedFile.bookId, asset.page),
      text: sourceText || undefined,
      source
    });
  }

  function moveLessonPage(nextPage: number) {
    const boundedPage = Math.max(0, Math.min(readingSections.length, nextPage));
    if (boundedPage === activeLessonPage) return;
    setLessonPageDirection(boundedPage > activeLessonPage ? "forward" : "back");
    setActiveLessonPage(boundedPage);
  }

  function openLessonAnimation(animation: LessonAnimation, origin: HTMLButtonElement) {
    animationOriginRef.current = origin;
    setActiveAnimation(animation);
  }

  const closeLessonAnimation = useCallback(() => {
    setActiveAnimation(null);
  }, []);

  function handleLessonKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      moveLessonPage(activeLessonPage - 1);
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      moveLessonPage(activeLessonPage + 1);
    }
  }

  function handleLessonPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    lessonSwipeStart.current = {
      x: event.clientX,
      y: event.clientY,
      pointerId: event.pointerId
    };
  }

  function handleLessonPointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    const swipeStart = lessonSwipeStart.current;
    lessonSwipeStart.current = null;
    if (!swipeStart || swipeStart.pointerId !== event.pointerId) return;
    const horizontalDistance = event.clientX - swipeStart.x;
    const verticalDistance = event.clientY - swipeStart.y;
    if (Math.abs(horizontalDistance) < 48 || Math.abs(horizontalDistance) <= Math.abs(verticalDistance) * 1.2) return;
    moveLessonPage(activeLessonPage + (horizontalDistance < 0 ? 1 : -1));
  }

  async function buildCurrentLesson() {
    setBuildingLesson(true);
    try {
      if (!demoPort?.updateCourse) throw new Error("本章课程暂时无法更新");
      if (!await demoPort.updateCourse()) throw new Error("本章课程更新失败，请重试");
      showToast("本章课程已更新", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "本章课程生成失败", "warning");
    } finally {
      setBuildingLesson(false);
    }
  }

  const appShell = typeof document === "undefined" ? null : document.querySelector<HTMLElement>(".app-shell");
  const lessonPageCount = readingSections.length + 1;
  const isLastLessonPage = activeLessonPage === lessonPageCount - 1;
  const fallbackChunk = liveChunks.find((chunk) => chunk.content_type !== "ocr_pending") ?? liveChunks[0] ?? null;
  const lessonFallbackCitation: LessonCitation | null = readingSections
    .map((section) => section.block.citations[0] ?? null)
    .find((citation): citation is LessonCitation => citation !== null)
    ?? (fallbackChunk ? {
      chunk_id: fallbackChunk.chunk_id,
      page_start: fallbackChunk.page_start,
      page_end: fallbackChunk.page_end,
      printed_page_start: fallbackChunk.printed_page_start,
      printed_page_end: fallbackChunk.printed_page_end,
      quote: fallbackChunk.text.trim() || null
    } : null);

  return (
    <>
      <div
        className="lesson-screen"
        onPointerDown={handleLessonPointerDown}
        onPointerUp={handleLessonPointerUp}
        onPointerCancel={() => { lessonSwipeStart.current = null; }}
      >
        <div className="lesson-layout">
          <article className="lesson-reading-column" aria-label={lessonTitle}>
          {!lesson ? (
            <header {...titleMotion.attributes} className="lesson-article-header">
              <h2 id="lesson-article-title">{lessonTitle}</h2>
              <p className="lesson-article-meta">{liveBookTitle(activeUploadedFile)}{pages ? ` · 原书${pages}` : ""}</p>
              <div className="lesson-generation-panel">
                <p>生成后会把本节原文整理成可追溯的图文知识讲解。</p>
                <ProgressBar
                  value={lessonBuildJobStatus && lessonBuildJobStatus.status !== "done"
                    ? lessonBuildJobStatus.progress
                    : activeLiveChapter.confidence}
                  label={buildingLesson
                    ? `正在整理课程 ${lessonBuildJobStatus?.progress ?? 0}%`
                    : `已准备 ${sourceChunkIds.length} 个原文片段`}
                />
                <Button loading={buildingLesson} disabled={buildingLesson} onClick={buildCurrentLesson}>
                  基于全文生成本章课程
                </Button>
              </div>
            </header>
          ) : null}

          {lesson ? (
            <>
              {(() => {
                const activeSection = activeLessonPage === 0
                  ? null
                  : readingSections[activeLessonPage - 1] ?? null;
                const citation = activeSection?.block.citations[0] ?? lessonFallbackCitation;
                const pageTitle = activeSection?.block.title ?? "本节导读";
                const activeAssets = activeSection?.assets ?? [];
                const contentParagraphs = activeSection
                  ? activeSection.block.content
                      .split(/\n\s*\n/)
                      .map((paragraph) => paragraph.trim())
                      .filter(Boolean)
                  : [];
                return (
                  <div
                    className="lesson-knowledge-pager"
                    role="group"
                    aria-roledescription="章节学习分页"
                    aria-label={`章节学习第 ${activeLessonPage + 1} 页，共 ${lessonPageCount} 页`}
                    tabIndex={0}
                    onKeyDown={handleLessonKeyDown}
                  >
                    <div className="lesson-page-overview">
                      <div className="lesson-page-status" aria-live="polite" aria-atomic="true">
                        <span>第 {activeLessonPage + 1} / {lessonPageCount} 页</span>
                        <strong>{pageTitle}</strong>
                        <small aria-hidden="true">左右滑动</small>
                      </div>
                      <div
                        className="lesson-page-progress"
                        role="progressbar"
                        aria-label="章节学习进度"
                        aria-valuemin={1}
                        aria-valuemax={lessonPageCount}
                        aria-valuenow={activeLessonPage + 1}
                      >
                        <span style={{ width: `${((activeLessonPage + 1) / lessonPageCount) * 100}%` }} />
                      </div>
                    </div>

                    <div className="lesson-knowledge-page-window">
                      {activeSection ? (
                        <section
                          className="lesson-knowledge-section lesson-learning-page"
                          key={activeSection.block.block_id}
                          data-page-direction={lessonPageDirection}
                          aria-labelledby={`${activeSection.block.block_id}-title`}
                        >
                          <h3 id={`${activeSection.block.block_id}-title`}>{activeSection.block.title}</h3>
                          {contentParagraphs.map((paragraph, paragraphIndex) => {
                            const inlineAsset = activeAssets[paragraphIndex] ?? null;
                            return (
                              <Fragment key={`${activeSection.block.block_id}-paragraph-${paragraphIndex}`}>
                                <p>{paragraph}</p>
                                {inlineAsset ? (
                                  <LessonFigure
                                    asset={inlineAsset}
                                    citation={citation}
                                    onOpenSource={() => openAssetSource(activeSection.block.title, inlineAsset, citation)}
                                    animation={lessonAnimationForAsset(inlineAsset.asset_id)}
                                    onOpenAnimation={openLessonAnimation}
                                  />
                                ) : null}
                              </Fragment>
                            );
                          })}
                          {activeAssets.slice(contentParagraphs.length).map((asset) => (
                            <LessonFigure
                              key={asset.asset_id}
                              asset={asset}
                              citation={citation}
                              onOpenSource={() => openAssetSource(activeSection.block.title, asset, citation)}
                              animation={lessonAnimationForAsset(asset.asset_id)}
                              onOpenAnimation={openLessonAnimation}
                            />
                          ))}
                          {citation ? (
                            <LessonSourceEntry
                              citation={citation}
                              onOpen={() => openCitationSource(activeSection.block.title, citation)}
                            />
                          ) : null}
                        </section>
                      ) : (
                        <section
                          {...primaryMotion.attributes}
                          className="lesson-introduction lesson-learning-page"
                          key="lesson-introduction"
                          data-page-direction={lessonPageDirection}
                          aria-labelledby="lesson-introduction-title"
                        >
                          <div {...titleMotion.attributes} className="lesson-introduction-heading">
                            <h2 id="lesson-article-title">{lessonTitle}</h2>
                            <p className="lesson-article-meta">{liveBookTitle(activeUploadedFile)}{pages ? ` · 原书${pages}` : ""}</p>
                          </div>
                          <h3 id="lesson-introduction-title">本节导读</h3>
                          <p>{lesson.summary}</p>
                          {lesson.warnings.length > 0 ? (
                            <aside className="lesson-generation-panel" aria-label="内容生成与来源说明">
                              <p>{lesson.warnings[0]}</p>
                            </aside>
                          ) : null}
                          {lessonIntroductionAsset ? (
                            <LessonFigure
                              asset={lessonIntroductionAsset}
                              citation={null}
                              onOpenSource={() => {}}
                              animation={lessonAnimationForAsset(lessonIntroductionAsset.asset_id)}
                              onOpenAnimation={openLessonAnimation}
                            />
                          ) : null}
                          {lesson.objectives.length > 0 ? (
                            <ul aria-label="本节学习目标">
                              {lesson.objectives.map((objective) => <li key={objective}>{objective}</li>)}
                            </ul>
                          ) : null}
                          {citation ? (
                            <LessonSourceEntry
                              citation={citation}
                              onOpen={() => openCitationSource("本节导读", citation)}
                            />
                          ) : null}
                        </section>
                      )}
                    </div>
                  </div>
                );
              })()}
            </>
          ) : null}
          </article>
        </div>
      </div>

      {animationPresence.rendered && appShell ? createPortal(
        <LessonAnimationDialog
          animation={animationPresence.rendered}
          originRef={animationOriginRef}
          presenceId={animationPresence.presenceId}
          state={animationPresence.state}
          onClose={closeLessonAnimation}
          onAnimationEnd={animationPresence.onAnimationEnd}
          onAnimationCancel={animationPresence.onAnimationCancel}
        />,
        appShell
      ) : null}

      {appShell ? createPortal(
        <LessonAiChatEntry
          avoidCompletionAction={Boolean(lesson && isLastLessonPage)}
          containerElement={appShell}
          onOpen={() => demoPort?.openAsk?.()}
        />,
        appShell
      ) : null}

      {lesson && isLastLessonPage && appShell ? createPortal(
        <div className="lesson-floating-complete" aria-label="章节完成操作">
          <Button icon={<CheckCircle2 size={18} aria-hidden="true" />} onClick={() => go("study")}>
            完成本节
          </Button>
        </div>,
        appShell
      ) : null}
    </>
  );
}
