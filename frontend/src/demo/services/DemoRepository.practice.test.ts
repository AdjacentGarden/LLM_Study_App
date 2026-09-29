import { describe, expect, it } from "vitest";
import { DemoRepository } from "./DemoRepository";

describe("DemoRepository section assignment diagnosis", () => {
  it("diagnoses the submitted section and cites that section instead of fixed meiosis content", async () => {
    const repository = new DemoRepository();
    const assignmentId = "assignment_c5s3";
    const submission = await repository.submitAssignment(assignmentId, {
      book_id: "book_biology_2",
      lesson_id: "lesson_human_genetic_disease",
      chapter_id: "c5s3",
      question: "本节练习",
      answer: "判断题：错误\n选择题：C\n简答题：兼顾科学价值、隐私和知情同意。"
    });
    const diagnosis = await repository.diagnoseAssignment(assignmentId, submission.submission_id);

    expect(diagnosis.knowledge_points).toEqual(["21 三体综合征", "遗传服务伦理", "基因组与伦理"]);
    expect(diagnosis.review_citations[0]?.chapter_id).toBe("c5s3");
    expect(diagnosis.related_assets.every((asset) => asset.chapter_id === "c5s3")).toBe(true);
    expect(diagnosis.result).toContain("已答对 2 题");
    expect(diagnosis.mistake_recorded).toBe(false);
  });

  it("keeps Chapter 1 diagnosis citation-free because the source PDF has no Chapter 1 body", async () => {
    const repository = new DemoRepository();
    const assignmentId = "assignment_c1s1";
    const submission = await repository.submitAssignment(assignmentId, {
      book_id: "book_biology_2",
      lesson_id: "lesson_mendel_pea_one",
      chapter_id: "c1s1",
      question: "本节练习",
      answer: "判断题：错误\n选择题：B\n简答题：F1 形成两类配子，受精后得到 1∶2∶1。"
    });
    const diagnosis = await repository.diagnoseAssignment(assignmentId, submission.submission_id);

    expect(diagnosis.review_citations).toEqual([]);
    expect(diagnosis.knowledge_points).toContain("分离定律");
  });
});
