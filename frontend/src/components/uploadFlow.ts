import type { BookCatalogItem, BookStatus } from "../types/api";

export interface PreparationStatus {
  book_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  stage: string;
  error: string;
  updated: number;
}

export interface UploadFlowAPI {
  bindUpload: (bookId: string) => Promise<unknown>;
  bookStatus: (bookId: string) => Promise<BookStatus>;
  processBook: (bookId: string) => Promise<BookStatus>;
  retryBook: (bookId: string) => Promise<BookStatus>;
  prepareBook: (bookId: string, retry?: boolean) => Promise<PreparationStatus>;
  preparationStatus: (bookId: string) => Promise<PreparationStatus>;
  claimBook: (bookId: string) => Promise<BookCatalogItem>;
}

export class UploadStepError extends Error {
  readonly stage: string;
  readonly retryOCR: boolean;
  constructor(message: string, stage: string, retryOCR = false) {
    super(message);
    this.name = "UploadStepError";
    this.stage = stage;
    this.retryOCR = retryOCR;
  }
}

export class UploadFlowCancelled extends Error {}

/** Continue a durable server task. An unknown network outcome never re-uploads or redoes OCR. */
export async function resumeUpload(bookId: string, options: {
  api: UploadFlowAPI;
  active: () => boolean;
  wait: (milliseconds: number) => Promise<void>;
  onStatus: (status: BookStatus) => void;
  onStage: (stage: string) => void;
  retryFailedOCR?: boolean;
  retryPreparation?: boolean;
}): Promise<BookCatalogItem> {
  const { api, active, wait, onStatus, onStage } = options;
  const check = () => { if (!active()) throw new UploadFlowCancelled(); };
  check();
  // Also completes a bind whose successful response was lost before a reload.
  await api.bindUpload(bookId);
  check();
  onStage("processing");
  let status = await api.bookStatus(bookId);
  check();
  if (status.status === "uploaded") {
    status = await api.processBook(bookId);
  } else if (status.retryable && options.retryFailedOCR) {
    status = await api.retryBook(bookId);
  }
  while (true) {
    check();
    onStatus(status);
    if (["ocr_ready", "structured"].includes(status.status)) break;
    if (["failed", "ocr_review_required"].includes(status.status)) {
      throw new UploadStepError("教材读取未完成，请核对后选择重新读取。", "processing", status.retryable);
    }
    if (!["uploaded", "queued", "running", "processing", "retry_wait"].includes(status.status)) {
      throw new UploadStepError(`教材读取状态异常（${status.status}），请稍后继续查看。`, "processing");
    }
    await wait(1500);
    check();
    status = await api.bookStatus(bookId);
  }

  async function prepare(id: string) {
    check();
    onStage("queued");
    let task = await api.prepareBook(id, options.retryPreparation ?? false);
    while (true) {
      check();
      onStage(task.stage);
      if (task.status === "failed") {
        throw new UploadStepError(task.error || "教材整理失败。处理进度已保存，请点击继续整理重试。", task.stage);
      }
      if (task.status === "succeeded") return;
      if (!["queued", "running"].includes(task.status)) {
        throw new UploadStepError("教材整理状态异常，请稍后继续查看。", task.stage);
      }
      await wait(1500);
      check();
      task = await api.preparationStatus(id);
    }
  }

  // Structured only means chapter reconstruction is done, not that diagnostics exist.
  await prepare(bookId);
  check();
  onStage("claiming");
  let claimed = await api.claimBook(bookId);
  check();
  if (!claimed.diagnostics_ready && claimed.book_id !== bookId) {
    // Exact-content dedup may select an existing owned canonical book. The API
    // authorizes this task using membership; no upload ownership is reassigned.
    await prepare(claimed.book_id);
    check();
    onStage("claiming");
    claimed = await api.claimBook(bookId);
    check();
  }
  if (!claimed.diagnostics_ready) {
    throw new UploadStepError("整理任务已结束，但当前版本的诊断题仍未就绪。请点击继续整理重试。", "diagnostics");
  }
  return claimed;
}
