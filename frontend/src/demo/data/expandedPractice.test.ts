import { describe, expect, it } from "vitest";
import flashcards from "./generated/flashcards.json";
import quizzes from "./generated/quiz.json";
import ragPayload from "./testFixtures/biology-rag-chunks.json";
import demoState from "./generated/demo-state.json";

const sectionIds = [
  "c1s1", "c1s2",
  "c2s1", "c2s2", "c2s3",
  "c3s1", "c3s2", "c3s3", "c3s4",
  "c4s1", "c4s2", "c4s3",
  "c5s1", "c5s2", "c5s3"
];

describe("expanded Chapter 1–5 practice", () => {
  it("provides six flashcards and three ordered exercises for every formal section", () => {
    expect(flashcards).toHaveLength(90);
    expect(quizzes).toHaveLength(45);
    for (const chapterId of sectionIds) {
      expect(flashcards.filter((card) => card.chapter_id === chapterId)).toHaveLength(6);
      expect(quizzes.filter((quiz) => quiz.chapter_id === chapterId).map((quiz) => quiz.question_type)).toEqual([
        "judgment",
        "choice",
        "short-answer"
      ]);
    }
  });

  it("does not fabricate textbook citations for the source-missing Chapter 1", () => {
    const chapterOneItems = [...flashcards, ...quizzes].filter((item) => item.chapter_id.startsWith("c1"));
    expect(chapterOneItems).toHaveLength(18);
    for (const item of chapterOneItems) {
      expect(item.source_kind).toBe("ai_supplement");
      expect(item.source_chunk_ids).toEqual([]);
      expect(item.page_start).toBe(0);
      expect(item.page_end).toBe(0);
      expect(item.source_quote).toBeNull();
    }
  });

  it("keeps every Chapter 2–5 practice source inside its own published RAG section", () => {
    const ragById = new Map([...ragPayload.chunks, ...demoState.chunks].map((chunk) => [chunk.chunk_id, chunk]));
    const groundedItems = [...flashcards, ...quizzes].filter((item) => !item.chapter_id.startsWith("c1"));
    for (const item of groundedItems) {
      const sourceId = item.source_chunk_ids[0];
      const source = ragById.get(sourceId) as {
        section_id?: string;
        chapter_id: string;
        text: string;
        page_start: number;
        printed_page_start: number | null;
      } | undefined;
      expect(item.source_kind).toBe("textbook");
      expect(item.source_chunk_ids).toHaveLength(1);
      expect(source?.section_id ?? source?.chapter_id).toBe(item.chapter_id);
      expect(source?.text).toContain(item.source_quote);
      expect(item.page_start).toBe(source?.page_start);
      expect(item.printed_page_start).toBe(source?.printed_page_start);
    }
  });
});
