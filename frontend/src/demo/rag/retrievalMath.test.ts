import { describe, expect, it } from "vitest";
import { rankTextbookChunks, tokenizeTextbookBm25 } from "./retrievalMath";
import type { Bm25Index, TextbookRagChunk } from "./types";

const chunks: TextbookRagChunk[] = [
  {
    chunk_id: "current-chapter",
    book_id: "book_biology_2",
    chapter_id: "c2",
    section_id: "c2s1a",
    page_start: 11,
    page_end: 11,
    content_type: "body",
    text: "减数分裂相关内容。",
    asset_ids: [],
    key_concepts: ["减数分裂"],
    title_path: ["第2章"],
    vector_position: 0
  },
  {
    chunk_id: "strong-other-chapter",
    book_id: "book_biology_2",
    chapter_id: "c7",
    section_id: "c7s2",
    page_start: 109,
    page_end: 109,
    content_type: "body",
    text: "自然选择会改变种群的基因频率。",
    asset_ids: [],
    key_concepts: ["自然选择", "基因频率"],
    title_path: ["第7章"],
    vector_position: 1
  }
];

const emptyBm25: Bm25Index = {
  schema_version: 1,
  tokenizer_version: "test",
  document_count: chunks.length,
  average_document_length: 1,
  document_lengths: [1, 1],
  k1: 1.2,
  b: 0.75,
  postings: {}
};

describe("static textbook retrieval math", () => {
  it("uses Chinese unigram/bigram lexical terms deterministically", () => {
    expect(tokenizeTextbookBm25("DNA 的碱基互补配对")).toEqual(
      expect.arrayContaining(["dna", "碱", "碱基", "互补", "配对"])
    );
  });

  it("keeps every chapter eligible and gives the current chapter only a small prior", () => {
    const hits = rankTextbookChunks({
      query: "自然选择如何改变种群基因频率？",
      chunks,
      bm25: emptyBm25,
      vectors: new Float32Array([
        0, 1,
        1, 0
      ]),
      queryVector: new Float32Array([1, 0]),
      dimension: 2,
      chapterId: "c2",
      weights: { semantic: 0.75, bm25: 0.20, chapterPrior: 0.05 },
      reliableThreshold: 0.6,
      semanticEnabled: true
    });

    expect(hits[0]?.chunk.chunk_id).toBe("strong-other-chapter");
    expect(hits[0]?.chapter_prior).toBe(0);
    expect(hits[1]?.chapter_prior).toBe(1);
    expect(hits[0]?.reliable).toBe(true);
  });

  it("applies the chapter prior to descendant section ids without hard-filtering the book", () => {
    const hits = rankTextbookChunks({
      query: "减数分裂",
      chunks,
      bm25: emptyBm25,
      vectors: new Float32Array([
        0.9, 0.1,
        1, 0
      ]),
      queryVector: new Float32Array([1, 0]),
      dimension: 2,
      chapterId: "c2s1",
      chapterScopeIds: ["c2s1", "c2s1a", "c2s1b"],
      weights: { semantic: 0.75, bm25: 0.20, chapterPrior: 0.05 },
      reliableThreshold: 0.6,
      semanticEnabled: true
    });

    expect(hits.find((hit) => hit.chunk.chunk_id === "current-chapter")?.chapter_prior).toBe(1);
    expect(hits.find((hit) => hit.chunk.chunk_id === "strong-other-chapter")?.chapter_prior).toBe(0);
    expect(hits.map((hit) => hit.chunk.chunk_id)).toContain("strong-other-chapter");
  });

  it("can rank a lexical fallback without a vector or accidental semantic citation", () => {
    const bm25: Bm25Index = {
      ...emptyBm25,
      postings: {
        自然: { idf: 4, postings: [[1, 2]] }
      }
    };
    const hits = rankTextbookChunks({
      query: "自然选择",
      chunks,
      bm25,
      dimension: 2,
      weights: { semantic: 0.75, bm25: 0.20, chapterPrior: 0.05 },
      reliableThreshold: 1,
      semanticEnabled: false
    });

    expect(hits[0]).toMatchObject({
      chunk: { chunk_id: "strong-other-chapter" },
      semantic_score: null,
      reliable: true
    });
  });

  it("rejects a non-zero common-character BM25 greeting below its calibrated fallback threshold", () => {
    const bm25: Bm25Index = {
      ...emptyBm25,
      postings: {
        // "你好" overlaps corpus characters, but not with enough distinctive
        // lexical evidence to qualify as a textbook citation.
        你: { idf: 2, postings: [[0, 1]] },
        好: { idf: 2, postings: [[0, 1]] },
        自然: { idf: 4, postings: [[1, 2]] },
        选择: { idf: 4, postings: [[1, 2]] }
      }
    };
    const greetingHits = rankTextbookChunks({
      query: "你好",
      chunks,
      bm25,
      dimension: 2,
      weights: { semantic: 0.75, bm25: 0.20, chapterPrior: 0.05 },
      reliableThreshold: 8,
      semanticEnabled: false
    });
    const textbookHits = rankTextbookChunks({
      query: "自然选择",
      chunks,
      bm25,
      dimension: 2,
      weights: { semantic: 0.75, bm25: 0.20, chapterPrior: 0.05 },
      reliableThreshold: 8,
      semanticEnabled: false
    });

    expect(greetingHits[0]).toMatchObject({
      chunk: { chunk_id: "current-chapter" },
      bm25_score: 4,
      reliable: false
    });
    expect(greetingHits.filter((hit) => hit.reliable)).toEqual([]);
    expect(textbookHits[0]).toMatchObject({
      chunk: { chunk_id: "strong-other-chapter" },
      reliable: true
    });
  });
});
