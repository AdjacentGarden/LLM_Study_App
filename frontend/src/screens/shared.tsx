import { useLayoutEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { ChevronDown, ChevronRight, FileText } from "lucide-react";
import { textbookAssets } from "../data/mockBook";
import { runtimeConfig } from "../config/runtime";
import { CollapsibleRegion, MotionIconSwap, useStageThreeImageMotion } from "../motion";
import type { ApiChapter, ApiChunk, ChapterEvidence, ScanResult } from "../types/api";
import type { Chapter, UploadedCourseFile } from "../types/app";
import { Button, ProgressBar } from "../components/ui";


export const coursePreparationSteps = ["阅读文件内容和版面", "整理章节与知识点", "生成可学习的课程内容"];

export const ragPipelineSteps = ["安全检查学习资料", "提取文字、图片和文档结构", "识别并核对章节目录", "建立可引用的教材检索"];


export function backendAssetUrl(url?: string | null, fallback = textbookAssets.illustration) {
  if (!url) return fallback;
  if (/^https?:\/\//i.test(url) || url.startsWith("data:") || url.startsWith("/assets/")) return url;
  return `${runtimeConfig.apiBaseUrl}${url}`;
}


export function sourcePageImageUrl(bookId: string, page: number) {
  const safePage = Math.max(1, Math.trunc(page));
  return `${runtimeConfig.apiBaseUrl}/api/books/${encodeURIComponent(bookId)}/pages/${safePage}/image`;
}


export function sourcePageLabel(pageStart: number, pageEnd?: number) {
  return pageEnd && pageEnd !== pageStart ? `第 ${pageStart}-${pageEnd} 页` : `第 ${pageStart} 页`;
}

export function sourceUnitName(sourceUnit?: string | null) {
  if (sourceUnit === "slide") return "幻灯片";
  if (sourceUnit === "sheet") return "工作表";
  if (sourceUnit === "document") return "文档位置";
  if (sourceUnit === "image") return "图片";
  return "页";
}

export function sourceUnitCountLabel(scan?: ScanResult | null) {
  if (!scan) return "- 页";
  return `${scan.page_count} 个${sourceUnitName(scan.source_unit)}`;
}


export function chapterConcepts(chapter: ApiChapter | null, chunk?: ApiChunk | null) {
  const concepts = chunk?.key_concepts?.filter(Boolean) ?? [];
  if (concepts.length > 0) return concepts.slice(0, 6);
  if (!chapter) return ["待识别概念"];
  const derived = chapter.source_title
    .replace(/^第\s*[一二三四五六七八九十百千万\d]+\s*[章节]\s*/, "")
    .split(/[、，,：:\s]+/)
    .filter((item) => item.length >= 2)
    .slice(0, 6);
  return derived.length > 0 ? derived : [chapter.source_title];
}


const BIOLOGY_BOOK_DISPLAY_NAME = "人教版高中生物必修二遗传与进化";

function isBiologyGeneticsBookTitle(value: string) {
  const normalized = value.replace(/\s+/g, "");
  return normalized.includes("人教版高中生物必修") && normalized.includes("遗传与进化");
}

export function liveBookTitle(uploadedFile?: UploadedCourseFile | null, scan?: ScanResult | null) {
  const title = uploadedFile?.name ?? scan?.filename ?? "未选择教材";
  return isBiologyGeneticsBookTitle(title) ? BIOLOGY_BOOK_DISPLAY_NAME : title;
}

export function fileTitleBeforeParenthesis(fileName: string) {
  const [titleBeforeParenthesis] = fileName.split(/[（(]/u, 1);
  return titleBeforeParenthesis.trim() || fileName.trim();
}


export function QuickAction({ icon, title, helper, onClick }: { icon: ReactNode; title: string; helper: string; onClick: () => void }) {
  return (
    <button className="quick-action" type="button" onClick={onClick}>
      <span className="quick-icon">{icon}</span>
      <strong>{title}</strong>
      <small>{helper}</small>
    </button>
  );
}


export function BookMini({
  title,
  cover,
  progress,
  onClick,
  motionAttributes
}: {
  title: string;
  cover: string;
  progress: number;
  onClick: () => void;
  motionAttributes?: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "onClick" | "type">;
}) {
  const { className: motionClassName, ...buttonMotionAttributes } = motionAttributes ?? {};
  return (
    <button {...buttonMotionAttributes} className={`book-mini ${motionClassName ?? ""}`} type="button" onClick={onClick}>
      {cover ? (
        <img src={cover} alt={title} />
      ) : (
        <span className="book-summary-icon">
          <FileText size={24} aria-hidden="true" />
        </span>
      )}
      <div>
        <strong>{title}</strong>
        <ProgressBar value={progress} label={`${progress}%`} />
      </div>
      <ChevronRight size={18} aria-hidden="true" />
    </button>
  );
}


export const supportedCourseExtensions = [
  ".pdf", ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".docx", ".pptx", ".xlsx"
] as const;

export const supportedCourseMimeTypes = [
  "application/pdf",
  "image/png",
  "image/jpeg",
  "image/jp2",
  "image/jpeg2000",
  "image/webp",
  "image/gif",
  "image/bmp",
  "image/x-ms-bmp",
  "image/tiff",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
] as const;

export const acceptedCourseFileTypes = [
  ...supportedCourseExtensions,
  ...supportedCourseMimeTypes
].join(",");

export function validateCourseFile(file: Pick<File, "name" | "size" | "type">) {
  const normalizedName = file.name.trim().toLowerCase();
  const normalizedType = file.type.trim().toLowerCase();
  const hasSupportedExtension = supportedCourseExtensions.some((extension) => normalizedName.endsWith(extension));
  const hasSupportedMimeType = supportedCourseMimeTypes.some((type) => normalizedType === type);

  if (!file.name.trim()) return "请选择一个有文件名的学习资料";
  if (file.size <= 0) return "这个文件为空，请重新选择";
  if (!hasSupportedExtension && !hasSupportedMimeType) {
    return "请选择 PDF、图片、Word、PowerPoint 或 Excel 文件";
  }
  return null;
}


export function formatFileSize(sizeBytes: number) {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`;
  return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`;
}


export function getFileKind(name: string, contentType: string) {
  const normalizedName = name.toLowerCase();
  if (contentType.includes("pdf") || normalizedName.endsWith(".pdf")) return "PDF 教材";
  if (contentType.startsWith("image/")) return "图片资料";
  if (normalizedName.endsWith(".docx")) return "Word 文档";
  if (normalizedName.endsWith(".pptx")) return "PowerPoint 演示文稿";
  if (normalizedName.endsWith(".xlsx")) return "Excel 工作簿";
  return "学习资料";
}


export function apiChapterToChapter(chapter: ApiChapter): Chapter {
  const status = chapter.status === "匹配良好" || chapter.status === "已确认" ? "匹配良好" : "需检查";
  return {
    id: chapter.chapter_id,
    sourceTitle: chapter.source_title,
    aiTitle: chapter.ai_title,
    pages: `第 ${chapter.page_start}-${chapter.page_end} 页`,
    confidence: Math.round(chapter.confidence),
    status,
    progress: 0,
    duration: chapter.level === 1 ? "按章学习" : "30 分钟",
    concepts: []
  };
}


export function averageConfidence(items: Chapter[]) {
  if (items.length === 0) return 0;
  return Math.round(items.reduce((sum, item) => sum + item.confidence, 0) / items.length);
}


export function ChapterEvidenceSummary({ evidence }: { evidence: ChapterEvidence }) {
  return (
    <div className="chapter-evidence">
      <span>目录 {evidence.toc_line_confidence}%</span>
      <span>页码 {evidence.page_map_confidence}%</span>
      <span>标题 {evidence.title_match_score}%</span>
      <small>
        印刷第 {evidence.printed_page_start ?? "-"} 页 → PDF 第 {evidence.pdf_page_start}-{evidence.pdf_page_end} 页
        {evidence.title_match_page ? `，正文命中第 ${evidence.title_match_page} 页` : ""}
      </small>
    </div>
  );
}


export function CourseCover({ course }: { course: { bookId?: string; title: string; cover?: string } }) {
  const cover = course.bookId ? sourcePageImageUrl(course.bookId, 1) : course.cover;
  const imageMotion = useStageThreeImageMotion(cover);
  if (cover && imageMotion.state !== "failed") {
    return (
      <img
        className="course-cover-image"
        src={cover}
        alt={`${course.title} 封面`}
        ref={imageMotion.imageRef}
        data-motion-image-source={cover}
        data-motion-image-state={imageMotion.state}
        onLoad={imageMotion.onLoad}
        onError={imageMotion.onError}
        onAnimationEnd={(event) => {
          if (event.animationName === "motion-stage3-image-in") imageMotion.settleAnimation();
        }}
      />
    );
  }
  return (
    <span
      className="book-summary-icon course-cover-fallback"
      data-motion-image-source={cover ?? undefined}
      data-motion-image-state={imageMotion.state}
      role="img"
      aria-label={`${course.title} 封面不可用`}
    >
      <FileText size={30} aria-hidden="true" />
    </span>
  );
}

export function ChapterEvidenceReasons({ evidenceId, reasons }: { evidenceId: string; reasons: string[] }) {
  const [expanded, setExpanded] = useState(false);
  const toggleRef = useRef<HTMLButtonElement | null>(null);
  const regionId = `${evidenceId}-reasons`;
  const toggleId = `${regionId}-toggle`;

  useLayoutEffect(() => {
    if (!reasons.length) setExpanded(false);
  }, [reasons.length]);

  if (reasons.length === 0) return null;

  return (
    <div className="chapter-evidence-disclosure">
      <button
        ref={toggleRef}
        className="chapter-evidence-toggle"
        type="button"
        id={toggleId}
        aria-expanded={expanded}
        aria-controls={regionId}
        onClick={() => setExpanded((current) => !current)}
      >
        <MotionIconSwap
          state={expanded ? "expanded" : "collapsed"}
          firstState="collapsed"
          secondState="expanded"
          firstIcon={<ChevronRight size={14} />}
          secondIcon={<ChevronDown size={14} />}
        />
        <span>识别理由（{reasons.length}）</span>
      </button>
      <CollapsibleRegion
        expanded={expanded}
        id={regionId}
        labelledBy={toggleId}
        focusFallbackRef={toggleRef}
      >
        <ul className="chapter-evidence-reasons">
          {reasons.map((reason) => <li key={reason}>{reason}</li>)}
        </ul>
      </CollapsibleRegion>
    </div>
  );
}

export function CourseSummariesSkeleton({ variant }: { variant: "compact" | "grid" }) {
  const count = variant === "compact" ? 2 : 3;
  return (
    <div className={`course-summary-skeleton course-summary-skeleton-${variant}`} aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <div className="course-summary-skeleton-block" key={index}>
          <span className="course-summary-skeleton-cover" />
          <span className="course-summary-skeleton-line is-long" />
          <span className="course-summary-skeleton-line" />
          <span className="course-summary-skeleton-line is-short" />
        </div>
      ))}
    </div>
  );
}

export function CourseSummaryLoadError({
  message,
  onRetry,
  refreshing
}: {
  message: string;
  onRetry: () => void;
  refreshing: boolean;
}) {
  return (
    <div className="course-summary-load-error" role="alert">
      <strong>课程列表暂时不可用</strong>
      <p>{message}</p>
      <Button variant="secondary" disabled={refreshing} onClick={onRetry}>
        {refreshing ? "重试中…" : "重试"}
      </Button>
    </div>
  );
}


export function SettingsRow({ icon, title, helper, onClick }: { icon: ReactNode; title: string; helper: string; onClick: () => void }) {
  return (
    <button className="settings-row" type="button" onClick={onClick}>
      <span className="settings-icon">{icon}</span>
      <div>
        <strong>{title}</strong>
        <small>{helper}</small>
      </div>
      <ChevronRight size={18} aria-hidden="true" />
    </button>
  );
}


