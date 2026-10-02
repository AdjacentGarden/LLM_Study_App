import type { HomeBookStudyPreview } from "../screens/homeBookPreview";

export const demoShelfStudyPreviews: readonly HomeBookStudyPreview[] = [
  {
    bookId: "catalog_physics_required_3",
    chapterId: "demo-physics-9-1",
    chapterTitle: "第 1 节 电荷",
    toolPreview: {
      assignmentPrompt: "电荷守恒定律适用于哪些过程？",
      flashcardTitle: "电荷与库仑定律",
      mistakeItems: [
        { label: "感应起电", status: "错 2 次" },
        { label: "元电荷", status: "待复习" }
      ]
    }
  },
  {
    bookId: "catalog_chemistry_required_2",
    chapterId: "demo-chemistry-5-1",
    chapterTitle: "第 1 节 硫及其化合物",
    toolPreview: {
      assignmentPrompt: "二氧化硫为什么能够使品红溶液褪色？",
      flashcardTitle: "硫及其化合物",
      mistakeItems: [
        { label: "SO₂ 的性质", status: "错 2 次" },
        { label: "硫酸根检验", status: "待复习" }
      ]
    }
  },
  {
    bookId: "catalog_english_required_3",
    chapterId: "demo-english-unit-1",
    chapterTitle: "Unit 1 Festivals and Celebrations",
    toolPreview: {
      assignmentKicker: "Reading Check",
      assignmentPrompt: "What do festivals around the world have in common?",
      assignmentOptionLabel: "Choose your answer",
      flashcardTitle: "Festival vocabulary",
      mistakeKicker: "Today’s review",
      mistakeCount: "3 cards",
      mistakeItems: [
        { label: "动词-ing 形式", status: "再练一次" },
        { label: "重点词组", status: "待复习" }
      ]
    }
  },
  {
    bookId: "catalog_advanced_mathematics_1",
    chapterId: "demo-calculus-1-1",
    chapterTitle: "第一节 映射与函数",
    toolPreview: {
      assignmentPrompt: "确定函数定义域时需要检查哪些限制？",
      flashcardTitle: "映射与函数",
      mistakeItems: [
        { label: "复合函数定义域", status: "错 2 次" },
        { label: "反函数", status: "待复习" }
      ]
    }
  },
  {
    bookId: "catalog_theoretical_mechanics_1",
    chapterId: "demo-mechanics-1-1",
    chapterTitle: "第 1 章 静力学公理和物体的受力分析",
    toolPreview: {
      assignmentPrompt: "画受力图时应当先确定哪些研究对象？",
      flashcardTitle: "静力学公理",
      mistakeItems: [
        { label: "二力平衡", status: "错 1 次" },
        { label: "约束反力", status: "待复习" }
      ]
    }
  },
  {
    bookId: "catalog_micro_psychology_set",
    chapterId: "demo-psychology-1-1",
    chapterTitle: "第 1 章 表情不会说谎",
    toolPreview: {
      assignmentPrompt: "哪些短暂面部变化可能是真实情绪线索？",
      flashcardTitle: "微表情识别",
      mistakeItems: [
        { label: "基线行为", status: "易混淆" },
        { label: "情绪泄漏", status: "待复习" }
      ]
    }
  }
] as const;

const previewByBookId = new Map(demoShelfStudyPreviews.map((preview) => [preview.bookId, preview]));

export function demoShelfStudyPreviewFor(bookId: string | null | undefined) {
  return bookId ? previewByBookId.get(bookId) ?? null : null;
}
