export type AssignmentExerciseType = "judgment" | "choice" | "short-answer";

export type AssignmentChoiceOption = Readonly<{
  key: "A" | "B" | "C" | "D";
  text: string;
}>;

export type AssignmentExercise = Readonly<{
  id: AssignmentExerciseType;
  label: string;
  prompt: string;
  instruction: string;
  options?: readonly AssignmentChoiceOption[];
}>;

export const assignmentExercises: readonly AssignmentExercise[] = [
  {
    id: "judgment",
    label: "判断题",
    prompt: "同源染色体在减数第一次分裂后期彼此分离。",
    instruction: "判断这句话是否正确"
  },
  {
    id: "choice",
    label: "选择题",
    prompt: "减数第一次分裂后期，细胞中发生的主要变化是？",
    instruction: "选择一个最准确的答案",
    options: [
      { key: "A", text: "姐妹染色单体分离" },
      { key: "B", text: "同源染色体分离" },
      { key: "C", text: "DNA 再次复制" },
      { key: "D", text: "染色体数目加倍" }
    ]
  },
  {
    id: "short-answer",
    label: "简答题",
    prompt: "请用自己的话说明减数分裂为什么能使生殖细胞中的染色体数目减半。",
    instruction: "先写出染色体复制次数，再说明两次分裂。"
  }
];

export function getNextAssignmentExerciseIndex(currentIndex: number) {
  return Math.min(currentIndex + 1, assignmentExercises.length - 1);
}

