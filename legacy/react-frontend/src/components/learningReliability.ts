import type { Course, InterviewResponse } from "../types/api";

export interface ScopedStorage {
  safeGet(key: string): string | null;
  safeSet(key: string, value: string | null): void;
}
export function operationId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `op_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}
export class PendingOperations {
  private memory = new Map<string, Record<string, unknown>>();
  private inflight = new Map<string, Promise<unknown>>();
  private storage: ScopedStorage;
  constructor(storage: ScopedStorage) { this.storage = storage; }
  pending(key: string): Record<string, unknown> | null {
    const cached = this.memory.get(key);
    if (cached) return cached;
    try {
      const value = JSON.parse(this.storage.safeGet(`zhiwo.pending-operation:${key}`) ?? "null");
      if (value && typeof value === "object" && typeof value.event_id === "string") {
        this.memory.set(key, value); return value;
      }
    } catch { /* An invalid draft must not replace a new operation. */ }
    return null;
  }
  async run<T>(key: string, create: () => Record<string, unknown>, send: (payload: Record<string, unknown>) => Promise<T>): Promise<T> {
    const running = this.inflight.get(key);
    if (running) return running as Promise<T>;
    const payload = this.pending(key) ?? JSON.parse(JSON.stringify(create()));
    this.memory.set(key, payload);
    this.storage.safeSet(`zhiwo.pending-operation:${key}`, JSON.stringify(payload));
    const result = (async () => {
      const value = await send(payload);
      this.memory.delete(key);
      this.storage.safeSet(`zhiwo.pending-operation:${key}`, null);
      return value;
    })();
    this.inflight.set(key, result);
    try { return await result; }
    finally { this.inflight.delete(key); }
  }
}

export interface CourseJob {
  job_id: string;
  session_id: string;
  chapter_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  stage: string;
  error: string | null;
  retryable: boolean;
  course: Course | null;
}
export interface CourseTaskAPI {
  getCourse(session: string, chapter: string): Promise<Course>;
  startCourseJob(session: string, chapter: string, requestId: string, retry: boolean): Promise<CourseJob>;
  courseJob(session: string, jobId: string): Promise<CourseJob>;
}
type Receipt = { requestId: string; jobId: string | null };
const courseReceipts = new WeakMap<ScopedStorage, Map<string, Receipt>>();
export class CourseTaskCancelled extends Error {}
export class CourseTaskFailure extends Error {
  job: CourseJob;
  constructor(job: CourseJob) { super(job.error || "课程暂未通过审核，可继续查看任务或明确重试。"); this.job = job; }
}
const errorStatus = (error: unknown) => error && typeof error === "object" && "status" in error ? Number(error.status) : 0;

export async function loadChapterCourse(sessionId: string, chapterId: string, options: {
  api: CourseTaskAPI; storage: ScopedStorage; active: () => boolean;
  wait: (milliseconds: number) => Promise<void>; onJob: (job: CourseJob) => void;
  refresh?: boolean; retryFailed?: boolean;
}): Promise<Course> {
  const { api, storage, active, onJob, wait } = options;
  const key = `zhiwo.course-task:${sessionId}:${chapterId}`;
  const check = () => { if (!active()) throw new CourseTaskCancelled(); };
  let memory = courseReceipts.get(storage);
  if (!memory) { memory = new Map(); courseReceipts.set(storage, memory); }
  const retain = (value: Receipt | null) => {
    if (value) memory.set(key, value); else memory.delete(key);
    storage.safeSet(key, value ? JSON.stringify(value) : null);
  };
  let receipt: Receipt | null = memory.get(key) ?? null;
  try {
    const saved = JSON.parse(storage.safeGet(key) ?? "null");
    if (saved && typeof saved.requestId === "string" && (saved.jobId === null || typeof saved.jobId === "string")) receipt = saved;
  } catch { /* A malformed local receipt cannot be used as a server job ID. */ }
  check();
  if (!options.refresh && !receipt) {
    try { const course = await api.getCourse(sessionId, chapterId); check(); return course; }
    catch (error) { if (![404, 409].includes(errorStatus(error))) throw error; }
  }
  check();
  const refreshingSavedReceipt = !!options.refresh && !!receipt && !options.retryFailed;
  if (!receipt) {
    receipt = { requestId: operationId(), jobId: null };
    retain(receipt);
  }
  let refreshedSubmissionStarted = false;
  try {
  let job: CourseJob;
  if (refreshingSavedReceipt) {
    // Only the existing explicit refresh action may retire a known terminal receipt.
    // An unknown POST response must be reconciled under its original request ID first.
    const previous = { ...receipt };
    if (!previous.jobId) throw new Error("上次准备请求的回执尚未确认，请先恢复同一请求；未提交新版本。");
    const previousJob = await api.courseJob(sessionId, previous.jobId);
    check();
    if (previousJob.job_id !== previous.jobId || previousJob.session_id !== sessionId || previousJob.chapter_id !== chapterId ||
        !["queued", "running", "succeeded", "failed"].includes(previousJob.status)) {
      throw new Error("未能确认原课程任务，已保留原请求编号；未提交新版本。");
    }
    if (previousJob.status === "failed" || previousJob.status === "succeeded") {
      // A concurrent refresh must not replace a newer receipt from this tab.
      let latest = memory!.get(key);
      const stored = JSON.parse(storage.safeGet(key) ?? "null");
      if (stored && typeof stored.requestId === "string") latest = stored;
      if (latest && (latest.requestId !== previous.requestId || latest.jobId !== previous.jobId)) {
        throw new Error("本章已有另一次准备请求，请先查看已保存任务；未重复提交。");
      }
      const next: Receipt = { requestId: operationId(), jobId: null };
      const serialized = JSON.stringify(next);
      // safeSet can silently fail (blocked storage), so prove durability before POST.
      storage.safeSet(key, serialized);
      let durable: Receipt | null = null;
      try { durable = JSON.parse(storage.safeGet(key) ?? "null"); } catch { /* No POST without a readable saved intent. */ }
      if (!durable || durable.requestId !== next.requestId || durable.jobId !== null) {
        throw new Error("新准备请求无法保存，未提交生成；请检查本机存储并保留原任务。");
      }
      memory!.set(key, next); receipt = next;
      check();
      refreshedSubmissionStarted = true;
      job = await api.startCourseJob(sessionId, chapterId, receipt.requestId, false);
      receipt.jobId = job.job_id;
      retain(receipt);
    } else {
      // A pending task is resumed by GET only; completion does not auto-start another.
      job = previousJob;
    }
  } else if (receipt.jobId && !options.retryFailed) {
    job = await api.courseJob(sessionId, receipt.jobId);
  } else {
    // The same request_id reconciles a POST whose response was lost, even if
    // another lesson changed the learner profile before the retry.
    job = await api.startCourseJob(sessionId, chapterId, receipt.requestId, options.retryFailed ?? false);
    receipt.jobId = job.job_id;
    retain(receipt);
  }
  while (true) {
    check(); onJob(job);
    if (job.status === "failed") throw new CourseTaskFailure(job);
    if (job.status === "succeeded") {
      // GET rechecks the current publication version and makes no model request.
      const course = await api.getCourse(sessionId, chapterId);
      check(); retain(null); return course;
    }
    if (!["queued", "running"].includes(job.status)) throw new Error("课程任务状态异常，请保留任务并稍后重试。");
    await wait(1500); check();
    job = await api.courseJob(sessionId, receipt.jobId!);
  }
  } catch (error) {
    // An explicit conflict proves this historical receipt cannot open the
    // current publication. Network errors must retain its idempotency key.
    // An unconfirmed refresh preflight (including 409) cannot retire the old receipt.
    if (errorStatus(error) === 409 && (!refreshingSavedReceipt || refreshedSubmissionStarted)) retain(null);
    throw error;
  }
}

export async function reconcileInterviewFailure(
  resume: () => Promise<InterviewResponse>, originalTurnId: string,
): Promise<{ interview: InterviewResponse; advanced: boolean }> {
  const interview = await resume();
  return { interview, advanced: interview.turn.turn_id !== originalTurnId };
}
