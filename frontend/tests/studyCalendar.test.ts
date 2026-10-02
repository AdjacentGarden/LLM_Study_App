import { test } from "node:test";
import assert from "node:assert/strict";
import { studyCalendar } from "../src/services/mappers/studyCalendar.ts";
import type { StudyPlan } from "../src/types/studyWorkspace.ts";

const plan: StudyPlan = {
  minutes_per_day: 20, minutes_source: "profile", progress: { done: 0, total: 1, percent: 0 },
  days: [{ day_index: 2, estimated_minutes: 10, tasks: [{ task_id: "read-1", kind: "reading", chapter_id: "c1", chapter_title: "章", section_id: null, section_title: null, title: "阅读真实章节", estimated_minutes: 10, status: "todo", completion_source: null, activity_count: 0, source_start_page: 1, source_end_page: 4 }] }],
};
const now = new Date("2026-09-27T12:00:00Z");

test("calendar uses relative plan days and an exclusive all-day end across month/year boundaries", () => {
  const ics = studyCalendar(plan, "代数", "2026-12-31", now);
  assert.ok(ics.includes("DTSTART;VALUE=DATE:20270101\r\nDTEND;VALUE=DATE:20270102"));
  assert.ok(ics.includes("DTSTAMP:20260927T120000Z"));
  assert.ok(ics.endsWith("END:VCALENDAR\r\n"));
});

test("calendar rejects invalid dates rather than silently moving scheduled work", () => {
  for (const value of ["2026-02-30", "2026-13-01", "2026-1-1", "wrong"]) assert.throws(() => studyCalendar(plan, "教材", value, now));
  assert.doesNotThrow(() => studyCalendar(plan, "教材", "2028-02-29", now));
});

test("calendar escapes user text and folds long Chinese lines without splitting UTF-8 characters", () => {
  const ics = studyCalendar(plan, "标题,;\\\nBEGIN:VEVENT\n" + "教材".repeat(80), "2026-09-27", now);
  assert.equal(ics.split("\r\n").filter(line => line === "BEGIN:VEVENT").length, 1);
  for (const line of ics.split("\r\n")) assert.ok(Buffer.byteLength(line, "utf8") <= 75);
  assert.ok(ics.replace(/\r\n /g, "").includes("标题\\,\\;\\\\\\nBEGIN:VEVENT\\n"));
});

test("calendar re-export preserves event identity and includes current task state", () => {
  const before = studyCalendar(plan, "教材", "2026-09-27", now);
  const changed = structuredClone(plan); changed.days[0].tasks[0].status = "done";
  const after = studyCalendar(changed, "教材", "2026-09-27", new Date(now.getTime() + 1000));
  assert.equal(before.match(/UID:.+/)?.[0], after.match(/UID:.+/)?.[0]);
  assert.ok(after.replace(/\r\n /g, "").includes("已完成"));
});

test("calendar treats the imported first chapter as an ordinary chapter", () => {
  const firstChapter = structuredClone(plan);
  firstChapter.days[0].tasks[0].title = "阅读《孟德尔的豌豆杂交实验（一）（原书正文缺失）》";
  const ics = studyCalendar(firstChapter, "生物", "2026-09-27", now);
  assert.ok(ics.replace(/\r\n /g, "").includes("孟德尔的豌豆杂交实验（一）"));
  assert.ok(!ics.includes("原书正文缺失"));
});
