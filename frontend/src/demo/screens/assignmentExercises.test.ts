import { describe, expect, it } from "vitest";
import type { QuizQuestion } from "../types/api";
import { buildAssignmentExercises, getNextAssignmentExerciseIndex } from "./assignmentExercises";

describe("assignmentExercises", () => {
  const base = {
    book_id: "book",
    lesson_id: "lesson",
    chapter_id: "c1s1",
    explanation: "解析",
    concept: "概念",
    source_chunk_ids: [],
    page_start: 0,
    page_end: 0,
    source_kind: "ai_supplement" as const
  };
  const quizzes: QuizQuestion[] = [
    { ...base, question_id: "q1", question_type: "judgment", prompt: "判断", choices: ["正确", "错误"], answer: "正确" },
    { ...base, question_id: "q2", question_type: "choice", prompt: "选择", choices: ["甲", "乙", "丙", "丁"], answer: "乙" },
    { ...base, question_id: "q3", question_type: "short-answer", prompt: "简答", choices: [], answer: "参考答案" }
  ];

  it("adapts a section quiz set into the required automatic order", () => {
    const exercises = buildAssignmentExercises(quizzes);
    expect(exercises.map((exercise) => exercise.id)).toEqual([
      "judgment",
      "choice",
      "short-answer"
    ]);
    expect(exercises[1]?.options?.map((option) => option.key)).toEqual(["A", "B", "C", "D"]);
    expect(exercises[0]?.sourceKind).toBe("ai_supplement");
  });

  it("advances through the sequence and stops on the short answer", () => {
    expect(getNextAssignmentExerciseIndex(0)).toBe(1);
    expect(getNextAssignmentExerciseIndex(1)).toBe(2);
    expect(getNextAssignmentExerciseIndex(2)).toBe(2);
  });
});
