import assert from "node:assert/strict";
import test from "node:test";
import { chapterEstimate, prioritizeChapters, masteryLabel, chaptersToConsolidate } from "../src/components/learningPlan.ts";

const chapters = ["strong", "unknown", "weak"].map((id, order) => ({ chapter_id: id, order, title: id }));
const profile = { chapter_mastery: {
  strong: { tracked_mastery: .9, evidence_count: 3, alpha: 9, beta: 1 },
  unknown: { tracked_mastery: .1, evidence_count: 0, alpha: 1, beta: 1 },
  weak: { tracked_mastery: .3, evidence_count: 2, alpha: 2, beta: 3 },
} };

test("prioritizes evidenced weak chapters without treating unknown as weak", () => {
  assert.deepEqual(prioritizeChapters(chapters, profile).map(x => x.chapter.chapter_id), ["weak", "strong", "unknown"]);
  assert.equal(chapterEstimate(profile, "unknown"), null);
  assert.equal(masteryLabel(null), "尚待诊断");
});
test("uses sequential mastery before posterior ratio", () => {
  assert.equal(chapterEstimate(profile, "weak"), .3);
});
test("does not mutate chapters and handles empty or absent profiles", () => {
  assert.equal(prioritizeChapters(chapters, undefined)[0].chapter.chapter_id, "strong");
  assert.equal(chapters[0].chapter_id, "strong");
  assert.deepEqual(prioritizeChapters([], profile), []);
  assert.equal(chapterEstimate(undefined, "weak"), null);
});
test("labels progress as an estimate, with distinct learning priorities", () => {
  assert.equal(masteryLabel(.3), "建议先补基础");
  assert.equal(masteryLabel(.6), "重点巩固");
  assert.equal(masteryLabel(.9), "可以进阶");
});

test("chapter filter excludes unknown and strong chapters without changing original order", () => {
  assert.deepEqual(chaptersToConsolidate(chapters, profile).map(x=>x.chapter.chapter_id), ["weak"]);
  assert.deepEqual(chapters.map(x=>x.chapter_id), ["strong", "unknown", "weak"]);
  assert.deepEqual(chaptersToConsolidate(chapters), []);
});

test("consolidation cutoff is strictly below 75 percent, with empty results supported", () => {
  const boundary = {chapter_mastery:{weak:{tracked_mastery:.75,evidence_count:1,alpha:3,beta:1}}};
  assert.deepEqual(chaptersToConsolidate(chapters, boundary), []);
  boundary.chapter_mastery.weak.tracked_mastery = .749;
  assert.equal(chaptersToConsolidate(chapters, boundary).length, 1);
  assert.deepEqual(chaptersToConsolidate([], boundary), []);
});
