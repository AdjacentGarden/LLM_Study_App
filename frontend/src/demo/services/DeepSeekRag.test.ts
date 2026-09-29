import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { deepSeekConfig, deepSeekKeySetupMessage } from "../config/deepseek";
import { demoMathBookId } from "../data/demoMathCourse";
import type { ApiAsset, ApiChapter, ApiChunk, RagQuery } from "../types/api";
import {
  createOfflineTextbookRagResponse,
  DemoRepository,
  selectOfflineReliableChunksForBook
} from "./DemoRepository";
import type { TextbookRetriever } from "./TextbookRetriever";
import {
  askDeepSeekWithLocalRag,
  collectChapterScopeIds,
  createCitationExcerpt,
  createLocalCitation,
  DeepSeekDirectError,
  LOCAL_TEXTBOOK_RELIABILITY_THRESHOLD,
  rankLocalChunks
} from "./DeepSeekRag";

const originalDeepSeekConfig = { ...deepSeekConfig };

function readPublishedJson<T>(relativePath: string): T {
  return JSON.parse(readFileSync(new URL(relativePath, import.meta.url), "utf8")) as T;
}

afterEach(() => {
  Object.assign(deepSeekConfig, originalDeepSeekConfig);
  vi.restoreAllMocks();
});

const chapters: ApiChapter[] = [
  {
    chapter_id: "chapter-genetics",
    level: 1,
    source_title: "遗传的分子基础",
    ai_title: "遗传的分子基础",
    page_start: 12,
    page_end: 28,
    confidence: 1,
    status: "ready",
    source: "fixture"
  },
  {
    chapter_id: "chapter-evolution",
    level: 1,
    source_title: "生物的进化",
    ai_title: "生物的进化",
    page_start: 74,
    page_end: 90,
    confidence: 1,
    status: "ready",
    source: "fixture"
  }
];

const chunks: ApiChunk[] = [
  {
    chunk_id: "dna-structure",
    book_id: "book-1",
    chapter_id: "chapter-genetics",
    page_start: 15,
    page_end: 15,
    printed_page_start: 13,
    content_type: "paragraph",
    text: "DNA 分子通常由两条反向平行的脱氧核苷酸链组成，碱基按照互补配对原则连接。",
    asset_ids: ["asset-dna"],
    key_concepts: ["DNA", "碱基互补配对", "脱氧核苷酸"],
    source_metadata: { pdf_pages: [15], printed_pages: [13] }
  },
  {
    chunk_id: "natural-selection",
    book_id: "book-1",
    chapter_id: "chapter-evolution",
    page_start: 80,
    page_end: 80,
    content_type: "paragraph",
    text: "自然选择使适应环境的变异逐渐积累，种群的基因频率可能随之改变。",
    asset_ids: [],
    key_concepts: ["自然选择", "种群", "基因频率"]
  }
];

const assets: ApiAsset[] = [
  {
    asset_id: "asset-dna",
    book_id: "book-1",
    chapter_id: "chapter-genetics",
    source_type: "extracted",
    page: 15,
    type: "figure",
    caption: "DNA 结构示意图",
    bbox: [0, 0, 100, 100],
    image_url: "/fixture/dna.webp",
    thumbnail_url: "/fixture/dna-thumb.webp",
    source_page_image_url: "/fixture/page-15.webp",
    source_chunk_ids: ["dna-structure"],
    concepts: ["DNA"]
  }
];

const ragQuery: RagQuery = {
  book_id: "book-1",
  chapter_id: "chapter-genetics",
  history: [{ role: "user", content: "上一题我没理解。" }],
  question: "DNA 的碱基互补配对是什么？",
  context: {
    book_title: "生物教材",
    chapter_title: "遗传的分子基础",
    section_title: "DNA 的结构",
    page_label: "教材第 13 页",
    key_concepts: ["DNA", "碱基互补配对"]
  }
};

function enableDirectCallForTest() {
  deepSeekConfig.mode = "auto";
  deepSeekConfig.apiKey = "test-key";
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

function getRequestBody(fetchMock: { mock: { calls: unknown[][] } }, index: number) {
  const call = fetchMock.mock.calls[index] as [unknown, RequestInit | undefined] | undefined;
  return JSON.parse(String(call?.[1]?.body)) as {
    messages: Array<{
      role: string;
      content?: string | null;
      tool_calls?: Array<{
        id: string;
        function: { name: string; arguments: string };
      }>;
      tool_call_id?: string;
    }>;
    tools?: Array<{ function: { name: string } }>;
    tool_choice?: string | {
      type: "function";
      function: { name: string };
    };
    max_tokens?: number;
  };
}

function textbookToolCall(argumentsText: string, id = "call_textbook") {
  return {
    id,
    type: "function",
    function: {
      name: "search_textbook",
      arguments: argumentsText
    }
  };
}

describe("two-stage local textbook tool routing", () => {
  it("collects the active chapter and all of its descendant section ids", () => {
    const hierarchy: ApiChapter[] = [
      { ...chapters[0]!, chapter_id: "c2", parent_id: null },
      { ...chapters[0]!, chapter_id: "c2s1", parent_id: "c2" },
      { ...chapters[0]!, chapter_id: "c2s1a", parent_id: "c2s1" },
      { ...chapters[0]!, chapter_id: "c2s1b", parent_id: "c2s1" },
      { ...chapters[0]!, chapter_id: "c2s2", parent_id: "c2" }
    ];

    expect(collectChapterScopeIds(hierarchy, "c2s1")).toEqual(["c2s1", "c2s1a", "c2s1b"]);
  });

  it("forces a textbook search and a second request even for a greeting", async () => {
    enableDirectCallForTest();
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({
        choices: [{ message: { content: "你好！今天想聊什么？" } }]
      }))
      .mockResolvedValueOnce(jsonResponse({
        choices: [{ message: { content: "你好！今天想聊什么？" } }]
      }));

    const response = await askDeepSeekWithLocalRag({
      ...ragQuery,
      question: "你好"
    }, { assets, chapters, chunks });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const firstBody = getRequestBody(fetchMock, 0);
    expect(firstBody.tool_choice).toEqual({
      type: "function",
      function: { name: "search_textbook" }
    });
    expect(firstBody.max_tokens).toBeLessThan(300);
    expect(firstBody.tools?.[0]?.function.name).toBe("search_textbook");
    expect(firstBody.messages.at(-1)).toMatchObject({ role: "user", content: "你好" });
    expect(firstBody.messages[0]?.content).toContain("当前教材：生物教材");
    expect(firstBody.messages[0]?.content).toContain("当前章节：遗传的分子基础");
    expect(JSON.stringify(firstBody.messages)).not.toContain("DNA 分子通常由两条");
    const secondBody = getRequestBody(fetchMock, 1);
    expect(secondBody.tool_choice).toBe("none");
    expect(secondBody.messages.find((message) => message.role === "tool")?.content)
      .toContain("no_reliable_textbook_match");
    expect(response).toMatchObject({
      answer: "你好！今天想聊什么？",
      citations: [],
      related_assets: [],
      retrieval: { attempted: true, status: "no_match", hit_count: 0 }
    });
  });

  it("fails on a missing first response but locally forces search when content has no tool call", async () => {
    enableDirectCallForTest();
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ choices: [] }))
      .mockResolvedValueOnce(jsonResponse({
        choices: [{ message: { content: null } }]
      }))
      .mockResolvedValueOnce(jsonResponse({
        choices: [{ message: { content: "已根据教材片段回答。" } }]
      }));

    await expect(askDeepSeekWithLocalRag(ragQuery, { assets, chapters, chunks }))
      .rejects.toBeInstanceOf(DeepSeekDirectError);
    await expect(askDeepSeekWithLocalRag(ragQuery, { assets, chapters, chunks }))
      .resolves.toMatchObject({
        answer: "已根据教材片段回答。",
        citations: [{ chunk_id: "dna-structure" }]
      });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    const fallbackBody = getRequestBody(fetchMock, 2);
    expect(fallbackBody.messages).toContainEqual(expect.objectContaining({
      role: "assistant",
      tool_calls: [expect.objectContaining({ id: "local_search_fallback" })]
    }));
  });

  it("executes a reliable textbook tool call and returns only injected local citations", async () => {
    enableDirectCallForTest();
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({
        choices: [{
          message: {
            content: null,
            tool_calls: [textbookToolCall(JSON.stringify({ query: "DNA 碱基互补配对" }))]
          }
        }]
      }))
      .mockResolvedValueOnce(jsonResponse({
        choices: [{ message: { content: "DNA 的碱基会按互补配对原则连接。" } }]
      }));

    const response = await askDeepSeekWithLocalRag(ragQuery, { assets, chapters, chunks });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const firstBody = getRequestBody(fetchMock, 0);
    const secondBody = getRequestBody(fetchMock, 1);
    expect(JSON.stringify(firstBody.messages)).not.toContain("DNA 分子通常由两条");
    expect(secondBody.tool_choice).toBe("none");
    expect(secondBody.messages).toContainEqual(expect.objectContaining({
      role: "assistant",
      content: null,
      tool_calls: [expect.objectContaining({ id: "call_textbook" })]
    }));
    const toolResult = secondBody.messages.find((message) => message.role === "tool");
    expect(toolResult).toMatchObject({ role: "tool", tool_call_id: "call_textbook" });
    expect(toolResult?.content).toContain("reliable_textbook_match");
    expect(toolResult?.content).toContain("DNA 分子通常由两条");
    expect(response).toMatchObject({
      answer: "DNA 的碱基会按互补配对原则连接。",
      citations: [{
        chunk_id: "dna-structure",
        page: 15,
        location_label: "教材第 13 页（PDF 第 15 页）"
      }],
      related_assets: [{ asset_id: "asset-dna" }]
    });
    expect(response.citations.map((citation) => citation.chunk_id)).toEqual(["dna-structure"]);
  });

  it("returns a tool result for every tool_call_id and deduplicates the limited injected source union", async () => {
    enableDirectCallForTest();
    const multiChunks: ApiChunk[] = [
      chunks[0]!,
      {
        chunk_id: "dna-replication",
        book_id: "book-1",
        chapter_id: "chapter-genetics",
        page_start: 16,
        page_end: 16,
        printed_page_start: 14,
        content_type: "paragraph",
        text: "DNA 复制会让遗传信息在细胞分裂前得到准确传递。",
        asset_ids: ["asset-dna-replication"],
        key_concepts: ["DNA"],
        source_metadata: { pdf_pages: [16], printed_pages: [14] }
      },
      {
        chunk_id: "dna-expression",
        book_id: "book-1",
        chapter_id: "chapter-genetics",
        page_start: 17,
        page_end: 17,
        printed_page_start: 15,
        content_type: "paragraph",
        text: "DNA 上的遗传信息可以在细胞中表达并影响性状。",
        asset_ids: ["asset-dna-expression"],
        key_concepts: ["DNA"],
        source_metadata: { pdf_pages: [17], printed_pages: [15] }
      },
      {
        chunk_id: "dna-mutation",
        book_id: "book-1",
        chapter_id: "chapter-genetics",
        page_start: 18,
        page_end: 18,
        printed_page_start: 16,
        content_type: "paragraph",
        text: "DNA 的碱基序列发生改变时可能形成基因突变。",
        asset_ids: ["asset-dna-mutation"],
        key_concepts: ["DNA"],
        source_metadata: { pdf_pages: [18], printed_pages: [16] }
      }
    ];
    const multiAssets: ApiAsset[] = multiChunks.map((chunk) => ({
      asset_id: "asset-" + chunk.chunk_id,
      book_id: "book-1",
      chapter_id: "chapter-genetics",
      source_type: "extracted",
      page: chunk.page_start,
      type: "figure",
      caption: chunk.chunk_id + " 示意图",
      bbox: [0, 0, 100, 100],
      image_url: "/fixture/" + chunk.chunk_id + ".webp",
      thumbnail_url: "/fixture/" + chunk.chunk_id + "-thumb.webp",
      source_page_image_url: "/fixture/page-" + chunk.page_start + ".webp",
      source_chunk_ids: [chunk.chunk_id],
      concepts: ["DNA"]
    }));
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({
        choices: [{
          message: {
            content: null,
            tool_calls: [
              textbookToolCall(JSON.stringify({ query: "DNA" }), "call_dna_one"),
              textbookToolCall(JSON.stringify({ query: "DNA" }), "call_dna_two")
            ]
          }
        }]
      }))
      .mockResolvedValueOnce(jsonResponse({
        choices: [{ message: { content: "DNA 相关内容已根据教材片段整理。" } }]
      }));

    const response = await askDeepSeekWithLocalRag(ragQuery, {
      assets: multiAssets,
      chapters,
      chunks: multiChunks
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const secondBody = getRequestBody(fetchMock, 1);
    const toolMessages = secondBody.messages.filter((message) => message.role === "tool");
    expect(toolMessages.map((message) => message.tool_call_id).sort())
      .toEqual(["call_dna_one", "call_dna_two"]);
    const injectedSourceIds = new Set(toolMessages.flatMap((message) => {
      const result = JSON.parse(message.content ?? "{}") as {
        sources?: Array<{ chunk_id: string }>;
      };
      return result.sources?.map((source) => source.chunk_id) ?? [];
    }));
    expect(injectedSourceIds.size).toBe(3);
    expect(response.citations).toHaveLength(3);
    expect(new Set(response.citations.map((citation) => citation.chunk_id))).toEqual(injectedSourceIds);
    expect(new Set(response.related_assets.flatMap((asset) => asset.source_chunk_ids))).toEqual(injectedSourceIds);
  });

  it("fails with a controlled error when the second stage has no displayable content", async () => {
    enableDirectCallForTest();
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({
        choices: [{
          message: {
            content: null,
            tool_calls: [textbookToolCall(JSON.stringify({ query: "DNA 碱基互补配对" }))]
          }
        }]
      }))
      .mockResolvedValueOnce(jsonResponse({
        choices: [{ message: { content: null } }]
      }));

    await expect(askDeepSeekWithLocalRag(ragQuery, { assets, chapters, chunks }))
      .rejects.toThrow("DeepSeek 没有返回可显示的回答");

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const secondBody = getRequestBody(fetchMock, 1);
    expect(secondBody.messages.find((message) => message.role === "tool")?.content)
      .toContain("reliable_textbook_match");
  });

  it("does not inject a low-relevance tool result or create a citation", async () => {
    enableDirectCallForTest();
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({
        choices: [{
          message: {
            content: null,
            tool_calls: [textbookToolCall(JSON.stringify({ query: "今天天气怎么样" }))]
          }
        }]
      }))
      .mockResolvedValueOnce(jsonResponse({
        choices: [{ message: { content: "我没有可靠的教材内容可用于这个问题。" } }]
      }));

    const response = await askDeepSeekWithLocalRag(ragQuery, { assets, chapters, chunks });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const secondBody = getRequestBody(fetchMock, 1);
    const toolResult = secondBody.messages.find((message) => message.role === "tool");
    expect(toolResult?.content).toContain("no_reliable_textbook_match");
    expect(toolResult?.content).not.toContain("DNA 分子通常由两条");
    expect(secondBody.messages.filter((message) => message.role === "tool")).toHaveLength(1);
    expect(response.citations).toEqual([]);
    expect(response.related_assets).toEqual([]);
    expect(response.retrieval).toMatchObject({
      attempted: true,
      status: "no_match",
      hit_count: 0
    });
    expect(response.confidence).toBe("low");
  });

  it("does not create a citation when the complete local retriever rejects a greeting after lexical fallback", async () => {
    enableDirectCallForTest();
    const textbookRetriever = {
      search: vi.fn().mockResolvedValue({
        status: "lexical_fallback",
        method: "on-device-bm25-fallback",
        corpus_version: "biology-required-2-rag-v1",
        high_confidence_threshold: 0.6037,
        minimum_evidence_threshold: 0.6037,
        hits: [],
        error_code: "worker_unavailable"
      })
    } as unknown as TextbookRetriever;
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({
        choices: [{
          message: {
            content: null,
            tool_calls: [textbookToolCall(JSON.stringify({ query: "你好" }))]
          }
        }]
      }))
      .mockResolvedValueOnce(jsonResponse({
        choices: [{ message: { content: "你好！有什么可以帮你的吗？" } }]
      }));

    const response = await askDeepSeekWithLocalRag({ ...ragQuery, question: "你好" }, {
      assets,
      chapters,
      chunks,
      textbookRetriever
    });

    expect(textbookRetriever.search).toHaveBeenCalledWith(expect.objectContaining({
      query: "你好",
      chapterScopeIds: ["chapter-genetics"],
      reliableOnly: true
    }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const toolResult = getRequestBody(fetchMock, 1).messages.find((message) => message.role === "tool");
    expect(toolResult?.content).toContain("no_reliable_textbook_match");
    expect(response.citations).toEqual([]);
    expect(response.related_assets).toEqual([]);
    expect(response.retrieval).toMatchObject({
      attempted: true,
      status: "no_match",
      method: "on-device-bm25-fallback",
      hit_count: 0
    });
  });

  it("treats the current chapter as a small boost rather than a hard retrieval filter", () => {
    const ranked = rankLocalChunks(
      "自然选择如何改变种群的基因频率？",
      chunks,
      "chapter-genetics"
    );

    expect(ranked[0]?.chunk.chunk_id).toBe("natural-selection");
    expect(ranked.some(({ chunk }) => chunk.chunk_id === "dna-structure")).toBe(true);
    expect(ranked[0]?.score).toBeGreaterThan(LOCAL_TEXTBOOK_RELIABILITY_THRESHOLD);
  });

  it("returns a controlled no-match result for an unknown tool", async () => {
    enableDirectCallForTest();
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({
        choices: [{
          message: {
            content: null,
            tool_calls: [{
              id: "call_unknown",
              type: "function",
              function: {
                name: "unknown_tool",
                arguments: "{}"
              }
            }]
          }
        }]
      }))
      .mockResolvedValueOnce(jsonResponse({
        choices: [{ message: { content: "我无法依据教材确认这个请求。" } }]
      }));

    const response = await askDeepSeekWithLocalRag(ragQuery, { assets, chapters, chunks });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const secondBody = getRequestBody(fetchMock, 1);
    expect(secondBody.messages.filter((message) => message.role === "tool")).toHaveLength(2);
    expect(secondBody.messages.find((message) => message.tool_call_id === "call_unknown")?.content)
      .toContain("工具不可用");
    expect(secondBody.messages.find((message) => message.tool_call_id === "local_search_fallback")?.content)
      .toContain("reliable_textbook_match");
    expect(response.citations).toEqual([expect.objectContaining({ chunk_id: "dna-structure" })]);
    expect(response.retrieval).toMatchObject({ status: "hit", hit_count: 1 });
  });

  it("returns a no-match tool result for malformed tool arguments without throwing", async () => {
    enableDirectCallForTest();
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({
        choices: [{
          message: {
            content: null,
            tool_calls: [textbookToolCall("{not valid json")]
          }
        }]
      }))
      .mockResolvedValueOnce(jsonResponse({
        choices: [{ message: { content: "我需要更具体的问题才能依据教材说明。" } }]
      }));

    const response = await askDeepSeekWithLocalRag(ragQuery, { assets, chapters, chunks });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const secondBody = getRequestBody(fetchMock, 1);
    expect(secondBody.messages.filter((message) => message.role === "tool")).toHaveLength(2);
    expect(secondBody.messages.find((message) => message.tool_call_id === "call_textbook")?.content)
      .toContain("检索参数无效");
    expect(secondBody.messages.find((message) => message.tool_call_id === "local_search_fallback")?.content)
      .toContain("reliable_textbook_match");
    expect(response.citations).toEqual([expect.objectContaining({ chunk_id: "dna-structure" })]);
    expect(response.retrieval).toMatchObject({ status: "hit", hit_count: 1 });
  });

  it("fails before network access when no personal key is configured", async () => {
    deepSeekConfig.mode = "auto";
    deepSeekConfig.apiKey = "";
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(askDeepSeekWithLocalRag(ragQuery, { assets, chapters, chunks }))
      .rejects.toThrow(deepSeekKeySetupMessage);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("maps provider authentication failures without exposing a key or provider payload", async () => {
    enableDirectCallForTest();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({
      error: {
        message: "provider diagnostic containing test-key and a serialized request body"
      }
    }, 401));

    const error = await askDeepSeekWithLocalRag(ragQuery, { assets, chapters, chunks })
      .catch((failure: unknown) => failure);

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(error).toBeInstanceOf(DeepSeekDirectError);
    expect(String((error as Error).message)).toContain("DeepSeek API Key 无效");
    expect(String((error as Error).message)).not.toContain("test-key");
    expect(String((error as Error).message)).not.toContain("serialized request body");
  });

  it("maps fetch failures to a controlled connection error without exposing request data", async () => {
    enableDirectCallForTest();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockRejectedValue(
      new Error("network failure containing test-key and DNA 的碱基互补配对是什么？")
    );

    const error = await askDeepSeekWithLocalRag(ragQuery, { assets, chapters, chunks })
      .catch((failure: unknown) => failure);

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(error).toBeInstanceOf(DeepSeekDirectError);
    expect(String((error as Error).message)).toContain("无法连接 DeepSeek");
    expect(String((error as Error).message)).not.toContain("test-key");
    expect(String((error as Error).message)).not.toContain(ragQuery.question);
  });

  it("creates a citation from local metadata rather than model-generated page data", () => {
    const [ranked] = rankLocalChunks("DNA 的碱基互补配对", chunks, "chapter-genetics");
    if (!ranked) throw new Error("Expected a local textbook chunk");

    expect(createLocalCitation(ranked, chapters)).toMatchObject({
      chapter_id: "chapter-genetics",
      chapter_title: "遗传的分子基础",
      chunk_id: "dna-structure",
      location_label: "教材第 13 页（PDF 第 15 页）",
      page: 15,
      retrieval_method: "on-device-keyword-rag"
    });
  });

  it("keeps the actual PDF 1–9 corpus and generated directory distinct from the missing Chapter 1 body", () => {
    const actualChapters = readPublishedJson<ApiChapter[]>("../data/generated/chapters.json");
    const actualChunks = readPublishedJson<{ chunks: ApiChunk[] }>(
      "../../public/rag/biology-required-2-rag-v1/chunks.json"
    ).chunks;
    const frontmatterQueryTerms = ["普通高中课程标准实验教科书", "遗传与进化"];
    const compact = (value: string) => value.replace(/\s/gu, "");
    const frontmatterChunk = actualChunks.find((chunk) => (
      chunk.chapter_id === "frontmatter"
      && chunk.page_start === 1
      // The cover inserts the word “必修” on its own line between these two
      // literal query terms, so assert both actual evidence terms instead of
      // pretending the OCR line break did not exist.
      && frontmatterQueryTerms.every((term) => compact(chunk.text).includes(term))
    ));
    const actualChapterChunk = actualChunks.find((chunk) => chunk.chapter_id === "c2" && chunk.page_start === 10);

    expect(frontmatterChunk).toBeDefined();
    expect(actualChapterChunk).toBeDefined();

    const frontmatterCitation = createLocalCitation({
      chunk: frontmatterChunk!,
      score: 0.99,
      retrievalMethod: "on-device-hybrid-rag"
    }, actualChapters);
    const actualChapterCitation = createLocalCitation({
      chunk: actualChapterChunk!,
      score: 0.99,
      retrievalMethod: "on-device-hybrid-rag"
    }, actualChapters);

    // This is the real generated-directory + published-corpus path used by
    // DemoRepository before SourceReader receives its target fields.
    expect(frontmatterCitation).toMatchObject({
      chapter_id: "frontmatter",
      chapter_title: "教材封面、前言与目录",
      page: 1
    });
    expect(frontmatterCitation.chapter_title).not.toMatch(/第\s*1\s*章|遗传因子的发现/u);
    expect(actualChapterCitation.chapter_title).toBe("第 2 章 基因和染色体的关系");
  });

  it("selects a substantive source sentence instead of an OCR question prompt", () => {
    const source = ",这一结果说明了什么？\n进一步观察发现：细菌裂解释放出的噬菌体中，可以检测到 DNA。\n赫尔希和蔡斯的实验表明：噬菌体侵染细菌时，DNA 进入细菌的细胞中，而蛋白质外壳仍留在外面。";
    const quote = createCitationExcerpt(source);

    expect(quote).toBe("赫尔希和蔡斯的实验表明：噬菌体侵染细菌时，DNA 进入细菌的细胞中，而蛋白质外壳仍留在外面。");
    expect(quote).not.toContain("这一结果说明了什么");
    expect(source).toContain(quote);
  });

  it("keeps a normal declarative excerpt and falls back to controlled source text when every fragment is short", () => {
    const declarativeSource = "DNA 分子由两条反向平行的脱氧核苷酸链组成。碱基按照互补配对原则连接。";
    const shortSource = "图。\n45。\n注。";
    const declarativeQuote = createCitationExcerpt(declarativeSource);
    const shortFallbackQuote = createCitationExcerpt(shortSource);

    expect(declarativeQuote).toBe("DNA 分子由两条反向平行的脱氧核苷酸链组成。");
    expect(shortFallbackQuote).toBe(shortSource);
    expect(declarativeSource).toContain(declarativeQuote);
    expect(shortSource).toContain(shortFallbackQuote);

    const promptChunk = {
      ...chunks[0],
      text: ",这一结果说明了什么？\n赫尔希和蔡斯的实验表明：DNA 才是真正的遗传物质。"
    };
    const response = createOfflineTextbookRagResponse([{
      chunk: promptChunk,
      score: 0.91,
      sectionId: "chapter-genetics",
      retrievalMethod: "on-device-hybrid-rag",
      reliabilityThreshold: 0.6
    }], chapters, "book-1");

    expect(response.citations[0]?.quote).toBe("赫尔希和蔡斯的实验表明：DNA 才是真正的遗传物质。");
    expect(response.answer).toBe(`教材原文：${response.citations[0]?.quote}`);
  });
});

describe("offline demo fallback", () => {
  it("does not turn composite, negated, or concept-cameo questions into a fixture citation", async () => {
    deepSeekConfig.mode = "demo";
    deepSeekConfig.apiKey = "";
    const repository = new DemoRepository();
    const fetchMock = vi.spyOn(globalThis, "fetch");

    for (const question of [
      "请说明抗生素使用过程，顺便写上受精作用",
      "不要解释受精作用，只说抗生素如何使用",
      "受精作用这个词出现在题干里，但请讲光合作用"
    ]) {
      const response = await repository.queryRag({
        book_id: "book_biology_2",
        chapter_id: "c2s1",
        question
      });
      expect(response.citations).toEqual([]);
      expect(response.related_assets).toEqual([]);
      expect(response.answer).not.toContain("如果体细胞里有一对 1 号同源染色体");
    }

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("answers simple greetings without citations or a network request when no key exists", async () => {
    deepSeekConfig.mode = "auto";
    deepSeekConfig.apiKey = "";
    const repository = new DemoRepository();
    const fetchMock = vi.spyOn(globalThis, "fetch");

    for (const question of ["你好", "嗨！", "早上好"]) {
      const response = await repository.queryRag({
        book_id: "book_biology_2",
        chapter_id: "c2s1",
        question
      });
      expect(response.answer).toContain("你好");
      expect(response.citations).toEqual([]);
      expect(response.related_assets).toEqual([]);
    }

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("answers a normal textbook question only from a reliable full-corpus chunk and makes the answer entailed by its quote", () => {
    const response = createOfflineTextbookRagResponse([{
      chunk: chunks[0],
      score: 0.91,
      sectionId: "chapter-genetics",
      retrievalMethod: "on-device-hybrid-rag",
      reliabilityThreshold: 0.6
    }], chapters, "book-1");

    expect(response.citations).toHaveLength(1);
    expect(response.related_assets).toEqual([]);
    expect(response.citations[0]).toMatchObject({
      chunk_id: "dna-structure",
      page: 15,
      location_label: "教材第 13 页（PDF 第 15 页）"
    });
    // The no-key answer is a controlled rendering of the exact cited quote,
    // rather than a fixture answer selected by a substring in the question.
    expect(response.answer).toBe(`教材原文：${response.citations[0].quote}`);
    expect(response.citations[0].source_metadata.retrieved_chunk_text).toBe(chunks[0].text);
  });

  it("rejects cross-book and unrecognised-section hits before a local citation is created", () => {
    const reliableHit = {
      chunk: chunks[0],
      score: 0.91,
      sectionId: "chapter-genetics",
      retrievalMethod: "on-device-hybrid-rag"
    };
    const wrongBookHit = {
      ...reliableHit,
      chunk: { ...chunks[0], book_id: demoMathBookId }
    };
    const wrongSectionHit = {
      ...reliableHit,
      sectionId: "math-section"
    };

    expect(selectOfflineReliableChunksForBook([reliableHit], chapters, "book-1"))
      .toHaveLength(1);
    expect(selectOfflineReliableChunksForBook([wrongBookHit], chapters, "book-1"))
      .toEqual([]);
    expect(selectOfflineReliableChunksForBook([wrongSectionHit], chapters, "book-1"))
      .toEqual([]);
    expect(createOfflineTextbookRagResponse([wrongBookHit], chapters, "book-1").citations)
      .toEqual([]);
    expect(createOfflineTextbookRagResponse([wrongSectionHit], chapters, "book-1").citations)
      .toEqual([]);
  });

  it("never lets biology fixture wording leak into math, unknown, or no-worker biology requests", async () => {
    deepSeekConfig.mode = "demo";
    deepSeekConfig.apiKey = "";
    const repository = new DemoRepository();
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const requests = [
      { bookId: demoMathBookId, chapterId: "math-chapter" },
      { bookId: "book_unknown", chapterId: "unknown-section" },
      { bookId: "book_biology_2", chapterId: "c2s1" }
    ];

    for (const { bookId, chapterId } of requests) {
      for (const question of [
        "第二次分裂举例说明",
        "请举一个例子",
        "受精作用是什么"
      ]) {
        const response = await repository.queryRag({
          book_id: bookId,
          chapter_id: chapterId,
          question
        });
        expect(response.citations).toEqual([]);
        expect(response.related_assets).toEqual([]);
        expect(response.answer).not.toContain("如果体细胞里有一对 1 号同源染色体");
        expect(response.citations.some((citation) => citation.chunk_id.startsWith("chunk_c2s1_"))).toBe(false);
      }
    }

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not send a direct provider request or biology corpus for an unsupported book even when a Key is configured", async () => {
    enableDirectCallForTest();
    const repository = new DemoRepository();
    const fetchMock = vi.spyOn(globalThis, "fetch");

    for (const bookId of [demoMathBookId, "book_unknown"]) {
      const response = await repository.queryRag({
        book_id: bookId,
        chapter_id: "other-section",
        question: "请举一个第二次受精作用的例子"
      });
      expect(response.citations).toEqual([]);
      expect(response.related_assets).toEqual([]);
    }

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
