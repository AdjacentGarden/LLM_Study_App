import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "../src/App";
import { ErrorBoundary } from "../src/components/ErrorBoundary";
import { BookCourseRepositoryProvider } from "../src/context/BookCourseRepositoryContext";
import { MotionHistoryProvider } from "../src/motion/MotionHistoryContext";
import { DemoRepository } from "../src/services/DemoRepository";
import type { JobStatusResponse, ParseJobResponse } from "../src/types/api";
import "../src/styles/tokens.css";
import "../src/styles/glass.css";
import "../src/styles/base.css";
import "../src/styles/responsive.css";
import "../src/styles/home.css";
import "../src/styles/chapter-tools.css";
import "../src/styles/study.css";
import "../src/styles/motion.css";
import "../src/styles/card-system.css";
import "../src/styles/community.css";
import "../src/styles/upload.css";
import "../src/styles/parse-ready.css";
import "../src/styles/processing.css";
import "../src/styles/chapter-confirm.css";

type HarnessPhase = "zero" | "failed" | "hundred" | "done";

class ControlledParseRepository extends DemoRepository {
  private phase: HarnessPhase = "zero";
  private readonly startedJobIds: string[] = [];
  private readonly jobBooks = new Map<string, string>();
  private jobReadCount = 0;
  private hydrationRelease: (() => void) | null = null;
  private hydrationWait: Promise<void> | null = null;

  setPhase(phase: HarnessPhase) {
    this.phase = phase;
  }

  getStartedJobIds() {
    return [...this.startedJobIds];
  }

  getJobReadCount() {
    return this.jobReadCount;
  }

  holdHydration() {
    if (this.hydrationWait) return;
    this.hydrationWait = new Promise<void>((resolve) => {
      this.hydrationRelease = resolve;
    });
  }

  releaseHydration() {
    this.hydrationRelease?.();
    this.hydrationRelease = null;
    this.hydrationWait = null;
  }

  override async getScanResult(bookId: string) {
    if (this.hydrationWait) await this.hydrationWait;
    return super.getScanResult(bookId);
  }

  override async startParse(bookId: string): Promise<ParseJobResponse> {
    const job = await super.startParse(bookId);
    this.startedJobIds.push(job.job_id);
    this.jobBooks.set(job.job_id, bookId);
    return job;
  }

  override async getJob(jobId: string): Promise<JobStatusResponse> {
    this.jobReadCount += 1;
    const bookId = this.jobBooks.get(jobId) ?? "book-processing-harness";
    if (this.phase === "failed") {
      const failure = "Retry after 30 seconds：解析服务暂时无法读取这份超长文件，请检查文件完整性后返回重新解析；已上传的文件会安全保留。";
      return {
        job_id: jobId,
        book_id: bookId,
        status: "failed",
        stage: "extracting",
        progress: 0,
        message: failure,
        error: failure
      };
    }
    if (this.phase === "done") {
      return {
        job_id: jobId,
        book_id: bookId,
        status: "done",
        stage: "completed",
        progress: 100,
        message: "教材解析完成，目录已准备好",
        error: null
      };
    }
    return {
      job_id: jobId,
      book_id: bookId,
      status: "processing",
      stage: this.phase === "zero" ? "queued" : "indexing",
      progress: this.phase === "zero" ? 0 : 100,
      message: this.phase === "zero"
        ? "解析任务已排队，等待第一个处理阶段"
        : "索引仍在提交，进度达到 100% 但任务尚未完成",
      error: null
    };
  }
}

const repository = new ControlledParseRepository();

declare global {
  interface Window {
    __processingStateHarness?: {
      getJobReadCount: () => number;
      getStartedJobIds: () => string[];
      holdHydration: () => void;
      releaseHydration: () => void;
      setPhase: (phase: HarnessPhase) => void;
    };
  }
}

window.__processingStateHarness = {
  getJobReadCount: () => repository.getJobReadCount(),
  getStartedJobIds: () => repository.getStartedJobIds(),
  holdHydration: () => repository.holdHydration(),
  releaseHydration: () => repository.releaseHydration(),
  setPhase: (phase) => repository.setPhase(phase)
};

const rootElement = document.getElementById("processing-state-root");
if (!rootElement) throw new Error("Processing state harness root is missing.");

createRoot(rootElement).render(
  <StrictMode>
    <ErrorBoundary>
      <BookCourseRepositoryProvider repository={repository}>
        <MotionHistoryProvider>
          <App />
        </MotionHistoryProvider>
      </BookCourseRepositoryProvider>
    </ErrorBoundary>
  </StrictMode>
);
