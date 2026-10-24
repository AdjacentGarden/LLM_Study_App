import assert from "node:assert/strict";
import test from "node:test";
import { parsePendingUpload, prepareAndClaimBook } from "../src/components/bookUploadFlow.ts";
import type { BookPreparation, BookStatus } from "../src/types/api.ts";

type Options = Parameters<typeof prepareAndClaimBook>[1];
const status = (state: string): BookStatus => ({
  book_id: "book", status: state, progress: 1, current_step: "", quality_score: 1,
  needs_human_review: false, attempts: 1, max_attempts: 3, page_count: 12, retryable: state === "failed",
});
const preparation = (state: BookPreparation["status"], stage: BookPreparation["stage"] = "diagnostics"): BookPreparation => ({
  book_id: "book", status: state, stage, error: state === "failed" ? "练习准备没有完成，请重试。" : "",
  updated: 1, next_step: 2,
});

function scenario(bookStates: BookStatus[], preparationStates: BookPreparation[]) {
  const calls: string[] = [];
  const api: Options["api"] = {
    bookStatus: async () => { calls.push("status"); return bookStates.shift()!; },
    processBook: async () => { calls.push("process"); return status("ocr_ready"); },
    retryBook: async () => { calls.push("retry-ocr"); return status("ocr_ready"); },
    prepareBook: async (_id, retry) => { calls.push(retry ? "retry-prepare" : "prepare"); return preparationStates.shift()!; },
    preparationStatus: async () => { calls.push("poll-prepare"); return preparationStates.shift()!; },
    claimBook: async () => { calls.push("claim"); return { book_id: "book", title: "教材", status: "structured", page_count: 12, chapter_count: 2, summary: "概览" }; },
  };
  const options: Options = {
    api, signal: new AbortController().signal,
    onReading: () => {}, onPreparing: () => calls.push("persist-preparing"),
    onPreparation: job => calls.push(`observed:${job.status}`), wait: async () => {},
  };
  return { options, calls };
}

test("OCR ready enqueues preparation and claims only after indexing, chapters and exercises succeed", async () => {
  const flow = scenario([status("ocr_ready")], [
    preparation("queued", "queued"), preparation("running", "indexing"),
    preparation("running", "structuring"), preparation("running", "diagnostics"), preparation("succeeded", "ready"),
  ]);
  await prepareAndClaimBook({ bookId: "book", stage: "reading" }, flow.options);
  assert.equal(flow.calls[0], "status");
  assert.equal(flow.calls.filter(value => value === "prepare").length, 1);
  assert.ok(flow.calls.indexOf("claim") > flow.calls.indexOf("observed:succeeded"));
  assert.equal(flow.calls.at(-1), "claim");
});

test("a structured book still waits for exercises and cannot become ready on intermediate failure", async () => {
  const flow = scenario([status("structured")], [preparation("running"), preparation("failed")]);
  await assert.rejects(prepareAndClaimBook({ bookId: "book", stage: "reading" }, flow.options), /练习准备没有完成/);
  assert.ok(!flow.calls.includes("claim"));
  assert.ok(!flow.calls.includes("observed:succeeded"));
  assert.ok(flow.calls.includes("prepare"));
});

test("retrying preparation never starts or retries OCR", async () => {
  const flow = scenario([], [preparation("queued"), preparation("succeeded", "ready")]);
  await prepareAndClaimBook({ bookId: "book", stage: "preparing" }, { ...flow.options, retryPreparation: true });
  assert.ok(flow.calls.includes("retry-prepare"));
  assert.ok(flow.calls.includes("claim"));
  assert.ok(!flow.calls.some(value => ["status", "process", "retry-ocr"].includes(value)));
});

test("refresh resumes the saved preparation job without enqueueing or repeating OCR", async () => {
  const flow = scenario([], [preparation("running"), preparation("succeeded", "ready")]);
  await prepareAndClaimBook({ bookId: "book", stage: "preparing" }, flow.options);
  assert.equal(flow.calls[1], "poll-prepare");
  assert.ok(!flow.calls.some(value => ["prepare", "retry-prepare", "process", "retry-ocr", "status"].includes(value)));
});

test("refresh between saving a task and enqueue recovers a missing job", async () => {
  const flow = scenario([], [preparation("succeeded", "ready")]);
  flow.options.api.preparationStatus = async () => { throw { status: 404 }; };
  await prepareAndClaimBook({ bookId: "book", stage: "preparing" }, flow.options);
  assert.ok(flow.calls.includes("prepare"));
  assert.ok(flow.calls.includes("claim"));
});

test("refresh before the first OCR request can start a bound uploaded book", async () => {
  const flow = scenario([status("uploaded")], [preparation("succeeded", "ready")]);
  await prepareAndClaimBook({ bookId: "book", stage: "reading" }, flow.options);
  assert.ok(flow.calls.includes("process"));
  assert.ok(!flow.calls.includes("retry-ocr"));
});

test("late preparation responses after unmount/account switch cannot claim or publish readiness", async () => {
  const flow = scenario([], []);
  const controller = new AbortController();
  let finish!: (value: BookPreparation) => void;
  flow.options.api.preparationStatus = () => new Promise(resolve => { finish = resolve; });
  const pending = prepareAndClaimBook({ bookId: "book", stage: "preparing" }, { ...flow.options, signal: controller.signal });
  controller.abort();
  finish(preparation("succeeded", "ready"));
  await assert.rejects(pending, error => error instanceof DOMException && error.name === "AbortError");
  assert.ok(!flow.calls.includes("claim"));
  assert.ok(!flow.calls.includes("observed:succeeded"));
});

test("late OCR responses cannot enqueue work after a cancelled run", async () => {
  const flow = scenario([], []);
  const controller = new AbortController();
  let finish!: (value: BookStatus) => void;
  flow.options.api.bookStatus = () => new Promise(resolve => { finish = resolve; });
  const pending = prepareAndClaimBook({ bookId: "book", stage: "reading" }, { ...flow.options, signal: controller.signal });
  controller.abort();finish(status("ocr_ready"));
  await assert.rejects(pending);
  assert.deepEqual(flow.calls, []);
});

test("saved upload tasks are validated and retain their recovery stage", () => {
  assert.deepEqual(parsePendingUpload('{"bookId":"book","stage":"preparing"}'), { bookId: "book", stage: "preparing" });
  assert.deepEqual(parsePendingUpload('{"bookId":"old-format"}'), { bookId: "old-format", stage: "reading" });
  for (const value of [null, "bad", "{}", '{"bookId":123}', '{"bookId":" "}']) assert.equal(parsePendingUpload(value), null);
});
