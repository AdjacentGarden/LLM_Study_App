import test from "node:test";
import assert from "node:assert/strict";
import { readCourseTask } from "../src/components/courseTaskStatus.ts";
import type { CourseJob } from "../src/components/learningReliability.ts";

const failed: CourseJob = { job_id: "known-job", session_id: "session", chapter_id: "chapter",
  status: "failed", stage: "failed", error: "旧服务仍提示稍后重试", retryable: false, course: null };

test("known exhausted status uses exactly one GET and cannot submit or assign an ID", async () => {
  const calls: string[][] = [];
  const original = JSON.stringify(failed);
  const returned = await readCourseTask({ courseJob: async (session, job) => {
    calls.push(["GET", session, job]); return failed;
  } }, "session", "chapter", "known-job");
  assert.equal(returned, failed);
  assert.deepEqual(calls, [["GET", "session", "known-job"]]);
  assert.equal(JSON.stringify(failed), original);
});

test("GET failure preserves unknown rather than restarting", async () => {
  let reads = 0;
  await assert.rejects(readCourseTask({ courseJob: async () => {
    reads++; throw new Error("offline");
  } }, "session", "chapter", "known-job"), /offline/);
  assert.equal(reads, 1);
});

test("unknown server status cannot turn a known failed task into a background task", async () => {
  const unknown = { ...failed, status: "unknown" as CourseJob["status"] };
  await assert.rejects(readCourseTask({ courseJob: async () => unknown },
    "session", "chapter", "known-job"), /任务状态暂时无法确认/);
});

for (const field of ["job_id", "session_id", "chapter_id"] as const) {
  test(`mismatched ${field} cannot be accepted as this chapter status`, async () => {
    await assert.rejects(readCourseTask({ courseJob: async () => ({ ...failed, [field]: "other" }) },
      "session", "chapter", "known-job"), /任务状态与当前章节不一致/);
  });
}
