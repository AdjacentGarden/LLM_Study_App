export type StudyToolId = "source" | "assignment" | "flashcards" | "mistakes";

export type StudyToolDefinition = Readonly<{
  id: StudyToolId;
  title: string;
  description: string;
}>;

/**
 * The study panel is intentionally data-driven: adding a tool here is enough
 * to reserve its position in the section flow without changing the accordion.
 */
export const studyToolDefinitions: readonly StudyToolDefinition[] = [
  {
    id: "source",
    title: "进入学习",
    description: "打开本章结构化课程，从目标与原文证据开始学习"
  },
  {
    id: "assignment",
    title: "作业诊断",
    description: "提交解题过程，定位理解卡点"
  },
  {
    id: "flashcards",
    title: "闪卡复习",
    description: "用短时回忆巩固本节概念"
  },
  {
    id: "mistakes",
    title: "错题集",
    description: "重做卡点题，追踪是否真正掌握"
  }
] as const;
