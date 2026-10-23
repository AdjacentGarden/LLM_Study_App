import assert from "node:assert/strict";
import test from "node:test";
import { resumeUpload, UploadFlowCancelled, UploadStepError } from "../src/components/uploadFlow.ts";
import type { PreparationStatus, UploadFlowAPI } from "../src/components/uploadFlow.ts";
import type { BookStatus, BookCatalogItem } from "../src/types/api.ts";

const status = (value: string): BookStatus => ({ book_id: "new", status: value, progress: .8,
  current_step: "读取", quality_score: null, needs_human_review: false, attempts: 1,
  max_attempts: 2, page_count: 125, retryable: value === "failed" });
const task = (state: PreparationStatus["status"] = "succeeded", stage = "ready", error = ""): PreparationStatus =>
  ({ book_id: "new", status: state, stage, error, updated: 0 });
const book = (id = "new", ready = true): BookCatalogItem => ({ book_id: id, title: "教材", status: "structured",
  chapter_count: 6, page_count: 125, summary: "摘要", diagnostics_ready: ready });

function fixture() {
  const calls: string[] = [];
  const api: UploadFlowAPI = {
    bindUpload: async id => { calls.push(`bind:${id}`); },
    bookStatus: async id => { calls.push(`status:${id}`); return status("structured"); },
    processBook: async id => { calls.push(`process:${id}`); return status("ocr_ready"); },
    retryBook: async id => { calls.push(`retry-ocr:${id}`); return status("ocr_ready"); },
    prepareBook: async (id, retry) => { calls.push(`prepare:${id}:${retry}`); return task(); },
    preparationStatus: async id => { calls.push(`poll:${id}`); return task(); },
    claimBook: async id => { calls.push(`claim:${id}`); return book(); },
  };
  const stages: string[] = [];
  const options = { api, active: () => true, wait: async () => {}, onStatus: () => {},
    onStage: (value: string) => stages.push(value) };
  return { api, calls, options, stages };
}

test("structured upload still completes diagnostics task before claiming ready", async () => {
  const f = fixture();
  f.api.prepareBook = async () => { f.calls.push("prepare"); return task("running", "diagnostics"); };
  assert.equal((await resumeUpload("new", f.options)).diagnostics_ready, true);
  assert.deepEqual(f.calls, ["bind:new", "status:new", "prepare", "poll:new", "claim:new"]);
  assert.ok(f.stages.includes("diagnostics"));
});

test("transient status failure is resumed using the same upload, without another OCR", async () => {
  const f = fixture(); let attempt = 0;
  f.api.bookStatus = async () => { if (++attempt === 1) throw new Error("connection lost"); return status("ocr_ready"); };
  await assert.rejects(resumeUpload("new", f.options), /connection lost/);
  await resumeUpload("new", f.options);
  assert.equal(attempt, 2);
  assert.ok(!f.calls.some(value => value.startsWith("process:") || value.startsWith("retry-ocr:")));
});

test("failed preparation exposes its actual error and requires explicit retry", async () => {
  const f = fixture();
  f.api.prepareBook = async (_id, retry) => retry ? task() : task("failed", "diagnostics", "题库未通过原文审核");
  await assert.rejects(resumeUpload("new", f.options), value => value instanceof UploadStepError &&
    value.stage === "diagnostics" && !value.retryOCR && value.message === "题库未通过原文审核");
  assert.ok(!f.calls.includes("claim:new"));
  await resumeUpload("new", { ...f.options, retryPreparation: true });
  assert.ok(f.calls.includes("claim:new"));
  assert.ok(!f.calls.some(value => value.startsWith("retry-ocr:")));
});

test("only explicit OCR recovery restarts a confirmed failed OCR job", async () => {
  const f = fixture(); f.api.bookStatus = async () => status("failed");
  await assert.rejects(resumeUpload("new", f.options), value => value instanceof UploadStepError && value.retryOCR);
  assert.ok(!f.calls.includes("retry-ocr:new"));
  await resumeUpload("new", { ...f.options, retryFailedOCR: true });
  assert.equal(f.calls.filter(value => value === "retry-ocr:new").length, 1);
});

test("lost process response reconciles saved status rather than processing twice", async () => {
  const f = fixture(); let serverStatus = "uploaded";
  f.api.bookStatus = async () => status(serverStatus);
  f.api.processBook = async () => { f.calls.push("process:new"); serverStatus = "ocr_ready"; throw new Error("response lost"); };
  await assert.rejects(resumeUpload("new", f.options), /response lost/);
  await resumeUpload("new", f.options);
  assert.equal(f.calls.filter(value => value === "process:new").length, 1);
});

test("unready owned canonical book is prepared without another OCR or ownership reassignment", async () => {
  const f = fixture(); let claims = 0;
  f.api.claimBook = async () => { f.calls.push("claim:new"); return book("canonical", ++claims > 1); };
  assert.equal((await resumeUpload("new", f.options)).book_id, "canonical");
  assert.ok(f.calls.includes("prepare:canonical:false"));
  assert.ok(!f.calls.includes("bind:canonical") && !f.calls.some(value => value.startsWith("process:")));
});

test("finished task with a stale bank never reports upload complete", async () => {
  const f = fixture(); f.api.claimBook = async () => book("new", false);
  await assert.rejects(resumeUpload("new", f.options), value => value instanceof UploadStepError && value.stage === "diagnostics");
});

test("navigating away stops further mutations after an in-flight status response", async () => {
  const f = fixture(); let active = true;
  f.api.bookStatus = async () => { active = false; return status("ocr_ready"); };
  await assert.rejects(resumeUpload("new", { ...f.options, active: () => active }), UploadFlowCancelled);
  assert.ok(!f.calls.some(value => value.startsWith("prepare:") || value.startsWith("claim:")));
});

test("a different account cannot continue another user's pending upload", async () => {
  const f = fixture(); f.api.bindUpload = async () => { throw new Error("403 ownership mismatch"); };
  await assert.rejects(resumeUpload("new", f.options), /403/);
  assert.deepEqual(f.calls, []);
});
