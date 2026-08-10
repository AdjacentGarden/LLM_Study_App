import { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import {
  BookOpen,
  CheckCircle2,
  Upload
} from "lucide-react";
import {
  Button,
  Card,
  ProgressBar
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import { useLocalMotionItem } from "../motion";
import type { ApiAsset, LessonCitation } from "../types/api";
import {
  isLessonBuildTerminal,
  lessonBuildSummary,
  successfulLessonChapterIds
} from "../utils/lessonGeneration";
import {
  backendAssetUrl,
  chapterConcepts,
  liveBookTitle,
  sourcePageLabel
} from "./shared";
import {
  detailedCitationPageLabel,
  learnerCitationPageLabel
} from "./lessonEvidence";
import {
  buildLessonConceptDetail,
  buildLessonReadingSections
} from "./lessonReading";

function assetPrintedPage(asset: ApiAsset) {
  if (asset.source_type !== "extracted") return null;
  const printedPage = Number(asset.metadata?.printed_page);
  return Number.isFinite(printedPage) && printedPage > 0 ? printedPage : null;
}

function LessonFigure({
  asset,
  citation,
  onOpenSource
}: {
  asset: ApiAsset;
  citation: LessonCitation | null;
  onOpenSource: () => void;
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
        <span>{asset.source_type === "extracted" ? "教材原图" : "AI 辅助示意"}</span>
        {sourceLabel ? <span aria-hidden="true"> · </span> : null}
        {sourceLabel ? <span>{sourceLabel}</span> : null}
      </figcaption>
    </figure>
  );
}

export function LessonScreen() {
  const bookcourseRepository = useBookCourseRepository();
  const {
    activeChapterId,
    generatedFlashcards,
    generatedLessons,
    generatedQuizzes,
    go,
    lessonBuildJobStatus,
    openSourcePage,
    openSheet,
    parsedAssets,
    parsedChapters,
    parsedChunks,
    setGeneratedFlashcards,
    setGeneratedLessons,
    setGeneratedQuizzes,
    setLessonBuildJobId,
    setLessonBuildJobStatus,
    showToast,
    uploadedFile
  } = useAppContext();
  const [buildingLesson, setBuildingLesson] = useState(false);
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
  const concepts = lesson?.key_concepts.length
    ? lesson.key_concepts
    : chapterConcepts(liveChapter, liveChunks[0]);
  const lessonTitle = lesson?.title ?? liveChapter?.ai_title ?? "等待章节课程";
  const sourceTitle = lesson?.source_title ?? liveChapter?.source_title ?? "等待章节";
  const learnerPageStart = liveChapter?.printed_page_start ?? lesson?.page_start ?? liveChapter?.page_start;
  const learnerPageEnd = liveChapter?.printed_page_end ?? lesson?.page_end ?? liveChapter?.page_end;
  const pages = learnerPageStart
    ? sourcePageLabel(learnerPageStart, learnerPageEnd ?? learnerPageStart)
    : "等待页码";
  const lessonBlocks = lesson?.blocks ?? [];
  const readingSections = useMemo(
    () => buildLessonReadingSections(lessonBlocks, liveAssets),
    [lessonBlocks, liveAssets]
  );
  const lessonMotionKey = `${uploadedFile?.bookId ?? "empty"}:${liveChapter?.chapter_id ?? "empty"}:${lesson?.lesson_id ?? "pending"}`;
  const emptyMotion = useLocalMotionItem(`lesson:${lessonMotionKey}:empty`);
  const titleMotion = useLocalMotionItem(`lesson:${lessonMotionKey}:title`);
  const primaryMotion = useLocalMotionItem(`lesson:${lessonMotionKey}:primary`);
  const conceptsMotion = useLocalMotionItem(`lesson:${lessonMotionKey}:concepts`);

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
    openSourcePage({
      bookId: activeUploadedFile.bookId,
      title: blockTitle,
      pageStart: citation.page_start,
      pageEnd: citation.page_end,
      printedPageStart: citation.printed_page_start,
      printedPageEnd: citation.printed_page_end,
      from: "lesson"
    });
  }

  function openAssetSource(blockTitle: string, asset: ApiAsset, citation: LessonCitation | null) {
    if (citation) {
      openCitationSource(blockTitle, citation);
      return;
    }
    if (asset.source_type !== "extracted") return;
    const printedPage = assetPrintedPage(asset);
    openSourcePage({
      bookId: activeUploadedFile.bookId,
      title: asset.caption,
      pageStart: asset.page,
      pageEnd: asset.page,
      printedPageStart: printedPage ?? undefined,
      printedPageEnd: printedPage ?? undefined,
      from: "lesson"
    });
  }

  function openConceptDetail(concept: string) {
    const detail = buildLessonConceptDetail(concept, lesson?.summary ?? sourceTitle, readingSections);
    const citation = detail.citation;
    const source = citation ? {
      bookId: activeUploadedFile.bookId,
      title: concept,
      pageStart: citation.page_start,
      pageEnd: citation.page_end,
      printedPageStart: citation.printed_page_start,
      printedPageEnd: citation.printed_page_end,
      from: "lesson" as const
    } : undefined;

    openSheet({
      type: "note",
      concept,
      explanation: detail.explanation,
      sourceLabel: citation ? `查看${detailedCitationPageLabel(citation)}` : undefined,
      source,
      image: backendAssetUrl(detail.asset?.image_url),
      imageCaption: detail.asset?.caption
    });
  }

  async function buildCurrentLesson() {
    setBuildingLesson(true);
    try {
      const job = await bookcourseRepository.buildLessons(activeUploadedFile.bookId, {
        chapter_ids: [activeLiveChapter.chapter_id],
        force: true
      });
      setLessonBuildJobId(job.job_id);
      setLessonBuildJobStatus(job);
      let finalJob = job;
      for (let attempt = 0; attempt < 50 && !isLessonBuildTerminal(finalJob.status); attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        finalJob = await bookcourseRepository.getLessonJob(job.job_id);
        setLessonBuildJobStatus(finalJob);
      }
      if (finalJob.status === "failed") throw new Error(finalJob.error ?? "本章课程生成失败");
      const generatedChapterIds = successfulLessonChapterIds(finalJob);
      if (generatedChapterIds.length === 0) throw new Error("本次没有章节成功生成课程");
      const nextLessons = await bookcourseRepository.getLessons(activeUploadedFile.bookId);
      const [cards, quizzes] = await Promise.all([
        bookcourseRepository.buildFlashcards(activeUploadedFile.bookId, { chapter_ids: generatedChapterIds }),
        bookcourseRepository.buildQuizzes(activeUploadedFile.bookId, { chapter_ids: generatedChapterIds })
      ]);
      const affectedChapterIds = new Set(finalJob.chapter_results.map((item) => item.chapter_id));
      setGeneratedLessons(nextLessons);
      setGeneratedFlashcards([
        ...(generatedFlashcards ?? []).filter((card) => !affectedChapterIds.has(card.chapter_id)),
        ...cards
      ]);
      setGeneratedQuizzes([
        ...(generatedQuizzes ?? []).filter((quiz) => !affectedChapterIds.has(quiz.chapter_id)),
        ...quizzes
      ]);
      const summary = lessonBuildSummary(finalJob);
      showToast(summary.message, summary.warningCount > 0 ? "warning" : "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "本章课程生成失败", "warning");
    } finally {
      setBuildingLesson(false);
    }
  }

  const appShell = typeof document === "undefined" ? null : document.querySelector(".app-shell");

  return (
    <>
      <div className="lesson-screen">
        <div className="lesson-layout">
          <article className="lesson-reading-column" aria-labelledby="lesson-article-title">
          <header {...titleMotion.attributes} className="lesson-article-header">
            <h2 id="lesson-article-title">{lessonTitle}</h2>
            <p className="lesson-article-meta">{liveBookTitle(activeUploadedFile)} · 原书{pages}</p>
            {!lesson ? (
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
            ) : null}
          </header>

          {lesson ? (
            <>
              <section {...primaryMotion.attributes} className="lesson-introduction" aria-labelledby="lesson-introduction-title">
                <h3 id="lesson-introduction-title">本节导读</h3>
                <p>{lesson.summary}</p>
                {lesson.objectives.length > 0 ? (
                  <ul aria-label="本节学习目标">
                    {lesson.objectives.map((objective) => <li key={objective}>{objective}</li>)}
                  </ul>
                ) : null}
              </section>

              <div className="lesson-knowledge-flow">
                {readingSections.map(({ block, asset }) => {
                  const citation = block.citations[0] ?? null;
                  return (
                    <section className="lesson-knowledge-section" key={block.block_id} aria-labelledby={`${block.block_id}-title`}>
                      <h3 id={`${block.block_id}-title`}>{block.title}</h3>
                      <p>{block.content}</p>
                      {asset ? (
                        <LessonFigure
                          asset={asset}
                          citation={citation}
                          onOpenSource={() => openAssetSource(block.title, asset, citation)}
                        />
                      ) : null}
                      {citation ? (
                        <button
                          className="lesson-source-link"
                          type="button"
                          onClick={() => openCitationSource(block.title, citation)}
                        >
                          查看{learnerCitationPageLabel(citation)}
                        </button>
                      ) : null}
                    </section>
                  );
                })}
              </div>

              <section {...conceptsMotion.attributes} className="lesson-concepts" aria-labelledby="lesson-concepts-title">
                <div className="lesson-section-heading">
                  <h3 id="lesson-concepts-title">核心概念</h3>
                  <span>点击查看知识详情</span>
                </div>
                <div className="concept-card-grid">
                  {concepts.slice(0, 6).map((concept) => (
                    <button
                      type="button"
                      key={concept}
                      aria-label={`查看核心概念：${concept}`}
                      onClick={() => openConceptDetail(concept)}
                    >
                      <strong>{concept}</strong>
                    </button>
                  ))}
                </div>
              </section>
            </>
          ) : null}
          </article>
        </div>
      </div>

      {lesson && appShell ? createPortal(
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
