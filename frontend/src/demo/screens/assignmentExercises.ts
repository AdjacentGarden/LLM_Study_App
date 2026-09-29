import type { QuizQuestion } from "../types/api";

export type AssignmentExerciseType = "judgment" | "choice" | "short-answer";

export type AssignmentChoiceOption = Readonly<{
  key: "A" | "B" | "C" | "D";
  text: string;
}>;

export type AssignmentExercise = Readonly<{
  id: AssignmentExerciseType;
  questionId: string;
  label: string;
  prompt: string;
  instruction: string;
  options?: readonly AssignmentChoiceOption[];
  answer: string;
  explanation: string;
  concept: string;
  sourceChunkIds: readonly string[];
  pageStart: number;
  pageEnd: number;
  printedPageStart?: number | null;
  printedPageEnd?: number | null;
  sourceKind: "textbook" | "ai_supplement";
  sourceQuote?: string | null;
}>;

const labels: Record<AssignmentExerciseType, string> = {
  judgment: "判断题",
  choice: "选择题",
  "short-answer": "简答题"
};

const defaultInstructions: Record<AssignmentExerciseType, string> = {
  judgment: "判断这句话是否正确",
  choice: "选择一个最准确的答案",
  "short-answer": "请结合本节核心概念，用完整的因果关系作答。"
};

export function buildAssignmentExercises(quizzes: readonly QuizQuestion[]): AssignmentExercise[] {
  return quizzes.map((quiz) => {
    const type = quiz.question_type
      ?? (quiz.choices.length === 0 ? "short-answer" : quiz.choices.length === 2 && quiz.choices.includes("正确") ? "judgment" : "choice");
    return {
      id: type,
      questionId: quiz.question_id,
      label: labels[type],
      prompt: quiz.prompt,
      instruction: quiz.instruction ?? defaultInstructions[type],
      options: type === "choice"
        ? quiz.choices.slice(0, 4).map((text, index) => ({
            key: (["A", "B", "C", "D"] as const)[index],
            text
          }))
        : undefined,
      answer: quiz.answer,
      explanation: quiz.explanation,
      concept: quiz.concept,
      sourceChunkIds: quiz.source_chunk_ids,
      pageStart: quiz.page_start,
      pageEnd: quiz.page_end,
      printedPageStart: quiz.printed_page_start,
      printedPageEnd: quiz.printed_page_end,
      sourceKind: quiz.source_kind ?? (quiz.page_start > 0 ? "textbook" : "ai_supplement"),
      sourceQuote: quiz.source_quote
    };
  });
}

export function getNextAssignmentExerciseIndex(currentIndex: number, exerciseCount = 3) {
  return Math.min(currentIndex + 1, Math.max(0, exerciseCount - 1));
}
