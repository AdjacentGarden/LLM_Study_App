import type { BookCatalogItem, BookPreparation, BookStatus } from "../types/api";

export type PendingBookUpload = { bookId: string; stage: "reading" | "preparing" };

export function parsePendingUpload(value: string | null): PendingBookUpload | null {
  if (!value) return null;
  try {
    const task: unknown = JSON.parse(value);
    if (!task || typeof task !== "object" || !("bookId" in task)
      || typeof task.bookId !== "string" || !task.bookId.trim()) return null;
    return { bookId: task.bookId, stage: "stage" in task && task.stage === "preparing" ? "preparing" : "reading" };
  } catch { return null; }
}

type PreparationApi = {
  bookStatus: (bookId: string, signal?: AbortSignal) => Promise<BookStatus>;
  processBook: (bookId: string, signal?: AbortSignal) => Promise<BookStatus>;
  retryBook: (bookId: string, signal?: AbortSignal) => Promise<BookStatus>;
  prepareBook: (bookId: string, retry?: boolean, signal?: AbortSignal) => Promise<BookPreparation>;
  preparationStatus: (bookId: string, signal?: AbortSignal) => Promise<BookPreparation>;
  claimBook: (bookId: string, signal?: AbortSignal) => Promise<BookCatalogItem>;
};

function checkActive(signal: AbortSignal) { signal.throwIfAborted(); }

function pollDelay(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    checkActive(signal);
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 2000);
    signal.addEventListener("abort", abort, { once: true });
  });
}

/** Claim only after every preparation stage succeeds; a retry keeps the existing OCR output. */
export async function prepareAndClaimBook(task: PendingBookUpload, options: {
  api: PreparationApi;
  signal: AbortSignal;
  retryPreparation?: boolean;
  retryReading?: boolean;
  onReading: (status: BookStatus) => void;
  onPreparing: () => void;
  onPreparation: (job: BookPreparation) => void;
  wait?: (signal: AbortSignal) => Promise<void>;
}): Promise<BookCatalogItem> {
  const { api, signal } = options;
  const wait = options.wait ?? pollDelay;
  checkActive(signal);
  if (task.stage === "reading") {
    let status = options.retryReading ? await api.retryBook(task.bookId, signal) : await api.bookStatus(task.bookId, signal);
    checkActive(signal);
    if (status.status === "uploaded") {
      status = await api.processBook(task.bookId, signal);
      checkActive(signal);
    }
    while (true) {
      options.onReading(status);
      checkActive(signal);
      if (status.status === "ocr_ready" || status.status === "structured") break;
      if (status.status === "failed" || status.status === "ocr_review_required") {
        throw new Error("教材读取没有完成，请重试。");
      }
      await wait(signal);
      checkActive(signal);
      status = await api.bookStatus(task.bookId, signal);
      checkActive(signal);
    }
  }
  options.onPreparing();
  checkActive(signal);
  let job: BookPreparation;
  if (task.stage === "preparing" && !options.retryPreparation) {
    try { job = await api.preparationStatus(task.bookId, signal); }
    catch (error) {
      checkActive(signal);
      // Reload may happen after saving the task but before enqueue reaches the server.
      if (!error || typeof error !== "object" || !("status" in error) || error.status !== 404) throw error;
      job = await api.prepareBook(task.bookId, false, signal);
    }
  } else {
    job = await api.prepareBook(task.bookId, options.retryPreparation ?? false, signal);
  }
  checkActive(signal);
  while (true) {
    options.onPreparation(job);
    checkActive(signal);
    if (job.status === "failed") throw new Error(job.error || "教材整理没有完成，请重试。");
    if (job.status === "succeeded") break;
    await wait(signal);
    checkActive(signal);
    job = await api.preparationStatus(task.bookId, signal);
    checkActive(signal);
  }
  const claimed = await api.claimBook(task.bookId, signal);
  checkActive(signal);
  return claimed;
}
