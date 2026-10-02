import type { TextNotePosition, TextStudyNote } from "./types";

export function textNotePosition(clientX: number, clientY: number, bounds: Pick<DOMRect, "left" | "top" | "width" | "height">): TextNotePosition {
  return {
    x: Math.min(1, Math.max(0, (clientX - bounds.left) / Math.max(1, bounds.width))),
    y: Math.min(1, Math.max(0, (clientY - bounds.top) / Math.max(1, bounds.height)))
  };
}

export function textNoteQuestion(note: TextStudyNote, question: string) {
  const page = note.anchor?.printedPageStart ?? note.anchor?.pageStart;
  return [
    `我在${note.anchor?.chapterTitle ?? note.anchor?.bookTitle ?? "教材原文"}${page ? `第 ${page} 页` : ""}写了一条文字批注。`,
    `我的批注：\n${note.body}`,
    note.anchor?.quote ? `关联原文：\n${note.anchor.quote}` : "",
    `请结合这条批注和教材回答我的问题：${question}`
  ].filter(Boolean).join("\n\n");
}
