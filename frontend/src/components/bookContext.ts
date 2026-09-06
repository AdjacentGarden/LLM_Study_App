import type { BookCatalogItem, BookStructure, InterviewResponse } from "../types/api";

export function selectBook(catalog: BookCatalogItem[], saved: InterviewResponse | null, preferred: string | null) {
  return catalog.find(item => item.book_id === preferred)
    ?? catalog.find(item => item.book_id === saved?.profile.book_id) ?? catalog[0] ?? null;
}

export function suggestedQuestions(structure: BookStructure | null): string[] {
  const chapter = structure?.chapters[0];
  const topic = chapter?.knowledge_points.find(point => point.trim().length > 0 && point.trim().length <= 24);
  if (topic && chapter) return [
    `请结合原文解释：${topic.replace(/[。！？]+$/, "")}`,
    `“${chapter.title}”的核心结论是什么？`,
    `请说明“${chapter.title}”中一个重要结论的成立条件。`,
  ];
  if (chapter) return [`“${chapter.title}”主要讲了什么？`, `请解释“${chapter.title}”的核心概念。`];
  return ["这本书主要讨论什么问题？", "请结合原文解释一个核心概念。"];
}

export function safeGet(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}
export function safeSet(key: string, value: string | null) {
  try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch { /* Private mode can disable persistence; in-memory use still works. */ }
}

export function hasAdditionalExplanation(title: string, explanation: string): boolean {
  const normalize = (text: string) => text.trim().replace(/。$/, "");
  return normalize(title) !== normalize(explanation);
}
