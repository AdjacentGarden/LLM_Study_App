import type { NoteEvidence } from "./types";

export const demoRecognizedInkText = "DNA 在复制时边解旋边复制；减数分裂使配子中的染色体数目减半。";

export const demoVoiceTranscript = "我想记住，减数第一次分裂时同源染色体分离，减数第二次分裂时姐妹染色单体分离。这样形成的配子染色体数目会减半。";

export const demoNoteEvidence: NoteEvidence[] = [
  {
    label: "教材第 16 页",
    page: 16,
    excerpt: "减数分裂过程中，染色体只复制一次，而细胞连续分裂两次。"
  },
  {
    label: "教材第 18 页",
    page: 18,
    excerpt: "减数第一次分裂后期同源染色体分离，减数第二次分裂后期姐妹染色单体分开。"
  }
];

export const demoOrganizedInkText = `## 减数分裂与 DNA 复制

- DNA 在减数分裂开始前完成一次复制，复制过程表现为边解旋边复制。
- 随后的细胞连续分裂两次，因此最终形成的配子中染色体数目减半。
- 这条笔记记录的是过程关键词，仍需要结合教材图区分同源染色体与姐妹染色单体。`;

export const demoOrganizedVoiceText = `## 两次分裂的核心区别

1. **减数第一次分裂**：同源染色体彼此分离，染色体数目减半。
2. **减数第二次分裂**：着丝粒分裂，姐妹染色单体彼此分开。
3. 复习时应先判断题目询问的是“同源染色体”还是“姐妹染色单体”，再确定发生在哪一次分裂。`;

export function delay(ms: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, ms));
}
