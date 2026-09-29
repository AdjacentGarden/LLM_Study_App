import { useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  FileText,
  FolderOpen
} from "lucide-react";
import {
  Button,
  Card,
  Metric,
  ProgressBar
} from "../components/ui";
import { useAppContext } from "../context/AppContext";
import { useBookCourseRepository } from "../context/BookCourseRepositoryContext";
import {
  fileTitleBeforeParenthesis,
  formatFileSize,
  getFileKind
} from "./shared";
import { startConfirmedCourseParse } from "./uploadFlow";

const uploadedCoverRules = [
  { keywords: ["生物", "遗传与进化"], source: "/assets/textbook/biology-cover.webp" },
  { keywords: ["化学"], source: "/assets/book-covers/chemistry-required-2.webp" },
  { keywords: ["物理"], source: "/assets/book-covers/physics-required-3.webp" },
  { keywords: ["理论力学"], source: "/assets/book-covers/theoretical-mechanics-1.webp" },
  { keywords: ["高等数学", "高数"], source: "/assets/book-covers/advanced-mathematics-1.webp" },
  { keywords: ["数学"], source: "/assets/book-covers/high-school-math-required-2.webp" },
  { keywords: ["英语"], source: "/assets/book-covers/english-required-3.webp" }
] as const;

function resolveUploadedCover(fileName: string) {
  return uploadedCoverRules.find(({ keywords }) => keywords.some((keyword) => fileName.includes(keyword)))?.source ?? null;
}

export function ParseReadyScreen() {
  const bookcourseRepository = useBookCourseRepository();
  const { clearLoadedCourse, go, parseJobId, parseJobStatus, uploadedFile, setParseJobId, setParseJobStatus } = useAppContext();
  const [parsing, setParsing] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);

  async function startParse() {
    if (!uploadedFile) {
      go("upload");
      return;
    }
    setParsing(true);
    setParseError(null);
    try {
      const job = await startConfirmedCourseParse(uploadedFile, bookcourseRepository);
      setParseJobId(job.job_id);
      setParseJobStatus({
        job_id: job.job_id,
        book_id: uploadedFile.bookId,
        status: "pending",
        stage: "queued",
        progress: 1,
        message: "后台 OCR/解析任务已创建",
        error: null
      });
      clearLoadedCourse();
      go("processing");
    } catch (err) {
      const message = err instanceof Error ? err.message : "解析任务创建失败";
      setParseError(message);
    } finally {
      setParsing(false);
    }
  }

  if (!uploadedFile) {
    return (
      <div className="screen-stack parse-ready-screen parse-flow-screen parse-flow-empty">
        <Card className="parse-empty-card">
          <FileText size={34} aria-hidden="true" />
          <h2>还没有选择学习资料</h2>
          <p>先上传 PDF、图片或文档，再进入解析确认。</p>
          <Button icon={<FolderOpen size={18} aria-hidden="true" />} onClick={() => go("upload")}>
            去上传
          </Button>
        </Card>
      </div>
    );
  }

  const fileKind = getFileKind(uploadedFile.name, uploadedFile.contentType);
  const displayTitle = fileTitleBeforeParenthesis(uploadedFile.name);
  const coverSource = resolveUploadedCover(uploadedFile.name);
  const uploadTime = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit"
  }).format(uploadedFile.uploadedAt);
  const jobProgress = Math.max(0, Math.min(100, parseJobStatus?.progress ?? (parseJobId ? 5 : 0)));
  const retryable = parseError !== null || parseJobStatus?.status === "failed";
  const errorMessage = parseError
    ?? (parseJobStatus?.status === "failed"
      ? parseJobStatus.error ?? parseJobStatus.message ?? "解析失败，请检查文件后重试"
      : null);
  const primaryActionText = parseJobStatus?.status === "done"
    ? "查看目录"
    : retryable
      ? "重新解析"
      : parseJobId
        ? "查看解析进度"
        : "开始解析";

  return (
    <div className="screen-stack community-detail-screen parse-ready-screen">
      <div className="community-detail-workspace parse-ready-workspace">
        <article className="community-detail-overview parse-ready-overview">
          <div className="community-detail-visual parse-ready-visual">
            {coverSource ? (
              <img className="community-detail-cover parse-ready-cover-image" src={coverSource} alt="" />
            ) : (
              <div className="community-detail-cover-fallback parse-ready-cover" aria-hidden="true">
                <span className="parse-ready-cover-icon">
                  <FileText size={56} />
                </span>
                <strong>{fileKind}</strong>
                <span>AI 课程资料</span>
              </div>
            )}
          </div>

          <div className="community-detail-summary parse-ready-summary">
            <p className="community-detail-owner parse-ready-owner">
              <span className="upload-success-mark" aria-hidden="true"><CheckCircle2 size={15} /></span>
              <span>已上传 · 待解析</span>
            </p>
            <h2 title={uploadedFile.name}>{displayTitle}</h2>
            <p className="community-detail-edition">
              {fileKind} · {formatFileSize(uploadedFile.sizeBytes)} · {uploadTime}
            </p>

            <div className="parse-info-grid" aria-label="教材解析信息">
              <Metric label="文件类型" value={fileKind} />
              <Metric label="文件大小" value={formatFileSize(uploadedFile.sizeBytes)} />
              <Metric label="下一步" value="后台解析" />
            </div>

            {errorMessage ? (
              <p className="parse-ready-error" role="alert">
                <CircleAlert size={17} aria-hidden="true" />
                <span>{errorMessage}</span>
              </p>
            ) : null}

            <div
              className="parse-status-feedback motion-visually-hidden"
              role="status"
              aria-live="polite"
              aria-atomic="true"
            >
              <span>{errorMessage ?? "文件已上传，等待开始解析"}</span>
              <ProgressBar value={jobProgress} label={`解析进度 ${jobProgress}%`} />
            </div>
          </div>
        </article>
      </div>

      <div className="community-detail-actions parse-flow-actions parse-ready-actions">
        <Button
          loading={parsing}
          disabled={parsing}
          onClick={() => {
            if (parseJobStatus?.status === "done") {
              go("chapterConfirm");
              return;
            }
            if (retryable) {
              void startParse();
              return;
            }
            if (parseJobId) {
              go("processing");
              return;
            }
            void startParse();
          }}
        >
          {primaryActionText}
        </Button>
      </div>
    </div>
  );
}
