import { Capacitor, CapacitorHttp } from "@capacitor/core";
import {
  deepSeekConfig,
  deepSeekKeySetupMessage,
  hasDirectDeepSeekKey
} from "../config/deepseek";
import type {
  ApiAsset,
  ApiChapter,
  ApiChunk,
  Citation,
  RagQuery,
  RagRetrievalSummary,
  RagResponse
} from "../types/api";
import type { TextbookRetriever } from "./TextbookRetriever";

type DeepSeekSystemMessage = {
  role: "system";
  content: string;
};

type DeepSeekHistoryMessage = {
  role: "user" | "assistant";
  content: string;
};

type DeepSeekToolCall = {
  id: string;
  type: "function";
  function: {
    name: string;
    arguments: string;
  };
};

type DeepSeekAssistantToolMessage = {
  role: "assistant";
  content: string | null;
  tool_calls: DeepSeekToolCall[];
};

type DeepSeekToolMessage = {
  role: "tool";
  tool_call_id: string;
  content: string;
};

type DeepSeekMessage =
  | DeepSeekSystemMessage
  | DeepSeekHistoryMessage
  | DeepSeekAssistantToolMessage
  | DeepSeekToolMessage;

type DeepSeekToolDefinition = {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: {
      type: "object";
      properties: Record<string, {
        type: string;
        description: string;
      }>;
      required: string[];
      additionalProperties: boolean;
    };
  };
};

type DeepSeekRequestOptions = {
  tools?: DeepSeekToolDefinition[];
  toolChoice?: "auto" | "none" | {
    type: "function";
    function: {
      name: string;
    };
  };
  maxTokens?: number;
};

type DeepSeekAssistantResponse = {
  content: string | null;
  toolCalls: DeepSeekToolCall[];
};

type SearchToolInvocation = {
  call: DeepSeekToolCall;
  query: string;
  rankedChunks: RankedChunk[];
  method: string | null;
  errorCode: string | null;
};

export type DeepSeekRagCorpus = {
  assets: ApiAsset[];
  chapters: ApiChapter[];
  chunks: ApiChunk[];
  /**
   * Optional complete static textbook retriever. The legacy fixture chunks
   * remain a deterministic fallback for tests and the small demo corpus.
   */
  textbookRetriever?: TextbookRetriever;
};

export type RankedChunk = {
  chunk: ApiChunk;
  score: number;
  retrievalMethod?: string;
  reliabilityThreshold?: number;
};

/**
 * A local chunk must cross this score before its text is sent to the provider
 * or surfaced as a citation. A matching declared key concept alone scores 3,
 * while incidental shared characters remain well below this threshold.
 */
export const LOCAL_TEXTBOOK_RELIABILITY_THRESHOLD = 2.4;

/**
 * The direct-call mode never sends more than this many textbook chunks in one
 * tool result. The optional TextbookRetriever supplies the complete,
 * versioned frontend index; fixture scoring remains only as a test fallback.
 */
export const LOCAL_TEXTBOOK_CONTEXT_LIMIT = 3;

const CURRENT_CHAPTER_BOOST = 0.2;
const TOOL_QUERY_MAX_LENGTH = 500;
const TOOL_CHUNK_MAX_LENGTH = 1_800;
const RETRIEVAL_PLANNER_MAX_TOKENS = 180;

export const searchTextbookTool: DeepSeekToolDefinition = {
  type: "function",
  function: {
    name: "search_textbook",
    description: "Required first-stage lookup for every user message. Create one concise query for the locally bundled current-course textbook; the local retriever will decide whether reliable evidence exists.",
    parameters: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "A concise Chinese search query that captures the textbook concept or claim to verify."
        },
        scope: {
          type: "string",
          description: "Use whole_book unless the student explicitly limits the question to the current chapter. The local retriever still keeps the full book eligible."
        }
      },
      required: ["query"],
      additionalProperties: false
    }
  }
};

export class DeepSeekDirectError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DeepSeekDirectError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeForSearch(value: string) {
  return value
    .toLocaleLowerCase("zh-CN")
    .replace(/[^\p{L}\p{N}]/gu, "");
}

function characterOverlap(left: string, right: string) {
  const leftCharacters = new Set(left);
  const rightCharacters = new Set(right);
  if (leftCharacters.size === 0 || rightCharacters.size === 0) return 0;
  let shared = 0;
  leftCharacters.forEach((character) => {
    if (rightCharacters.has(character)) shared += 1;
  });
  return shared / leftCharacters.size;
}

function chunkSearchText(chunk: ApiChunk) {
  return chunk.key_concepts.join(" ") + " " + chunk.text;
}

function longestUsefulSharedPhrase(question: string, searchable: string) {
  const ignored = new Set([
    "什么", "怎么", "为何", "为什么", "请问", "解释", "一下", "一个",
    "原文", "教材", "结合", "给我", "可以", "如何", "能否", "问题",
    "这个", "那个", "学习", "知识"
  ]);
  const maximumLength = Math.min(8, question.length);
  for (let length = maximumLength; length >= 3; length -= 1) {
    for (let start = 0; start <= question.length - length; start += 1) {
      const phrase = question.slice(start, start + length);
      if (ignored.has(phrase)) continue;
      if (searchable.includes(phrase)) return length;
    }
  }
  return 0;
}

/**
 * Lightweight on-device keyword retrieval for the currently bundled fixture.
 *
 * Every chunk stays eligible. The active chapter earns only CURRENT_CHAPTER_BOOST,
 * so a clearly stronger result from another chapter must still win. The
 * reliability threshold is intentionally applied by the tool executor rather
 * than here so callers can inspect ranked candidates for diagnostics and tests.
 */
export function rankLocalChunks(
  question: string,
  chunks: ApiChunk[],
  chapterId?: string | null,
  limit = LOCAL_TEXTBOOK_CONTEXT_LIMIT
) {
  const normalizedQuestion = normalizeForSearch(question);

  return chunks
    .map<RankedChunk>((chunk) => {
      const searchable = normalizeForSearch(chunkSearchText(chunk));
      const conceptHits = chunk.key_concepts.reduce(
        (count, concept) => count + (normalizedQuestion.includes(normalizeForSearch(concept)) ? 1 : 0),
        0
      );
      const sharedPhraseLength = longestUsefulSharedPhrase(normalizedQuestion, searchable);
      const phraseEvidence = sharedPhraseLength > 0
        ? Math.min(2.2, (sharedPhraseLength - 2) * 0.55)
        : 0;
      const fullQuestionMatch = normalizedQuestion.length > 3 && searchable.includes(normalizedQuestion) ? 2 : 0;
      const chapterBoost = chapterId === chunk.chapter_id ? CURRENT_CHAPTER_BOOST : 0;
      return {
        chunk,
        score: (
          (characterOverlap(normalizedQuestion, searchable) * 0.45)
          + (conceptHits * 3)
          + phraseEvidence
          + fullQuestionMatch
          + chapterBoost
        )
      };
    })
    .sort((left, right) => right.score - left.score || left.chunk.page_start - right.chunk.page_start)
    .slice(0, Math.max(0, limit));
}

export function selectReliableLocalChunks(
  question: string,
  chunks: ApiChunk[],
  chapterId?: string | null
) {
  return rankLocalChunks(question, chunks, chapterId)
    .filter((item) => item.score >= LOCAL_TEXTBOOK_RELIABILITY_THRESHOLD);
}

function asPageNumberList(value: unknown) {
  return Array.isArray(value)
    ? value.filter((item): item is number => typeof item === "number" && Number.isFinite(item))
    : [];
}

const MAX_CITATION_EXCERPT_LENGTH = 180;
const MIN_SUBSTANTIVE_CITATION_CHARACTERS = 12;

function citationSentenceCandidates(text: string) {
  return (text.match(/[^\n。！？!?]+[。！？!?]?/gu) ?? [])
    .map((sentence) => sentence
      .trim()
      // OCR chunk boundaries sometimes begin in the middle of a sentence
      // immediately after punctuation from the previous chunk. Removing only
      // boundary punctuation still leaves an exact contiguous source substring.
      .replace(/^[,，、;；:：]+/u, "")
      .trim())
    .filter(Boolean);
}

function meaningfulCitationCharacters(text: string) {
  return text.replace(/[\s\p{P}\p{S}]/gu, "").length;
}

function isCitationPrompt(sentence: string) {
  if (/[？?]/u.test(sentence)) return true;
  if (/^(?:请|想一想|思考(?:一下)?|讨论|回答|分析|判断|解释|观察|阅读|比较|指出|结合)/u.test(sentence)) {
    return true;
  }
  return /(?:什么|为何|为什么|如何|怎么|吗|呢|是否|可行)\s*[。！？!?]?\s*$/u.test(sentence);
}

function isConclusionLikeCitationSentence(sentence: string) {
  return /(?:实验表明|研究表明|结果表明|证明|因此|由此|可见|意味着|说明|结论)/u.test(sentence);
}

function truncateCitationExcerpt(text: string) {
  // Do not append an ellipsis: citations must remain a continuous substring
  // of the locally bundled chunk, never generated presentation text.
  return text.length > MAX_CITATION_EXCERPT_LENGTH
    ? text.slice(0, MAX_CITATION_EXCERPT_LENGTH).trimEnd()
    : text;
}

/**
 * Select a readable, evidence-bearing sentence from a retrieved local chunk.
 * The result is always copied from the chunk verbatim (apart from surrounding
 * whitespace / a cut at the maximum length), so the no-key answer and source
 * reader can never claim model-generated wording as textbook evidence.
 */
export function createCitationExcerpt(text: string) {
  const controlledSource = text.trim();
  if (!controlledSource) return "";

  const substantiveCandidates = citationSentenceCandidates(controlledSource)
    .filter((sentence) => (
      meaningfulCitationCharacters(sentence) >= MIN_SUBSTANTIVE_CITATION_CHARACTERS
      && !isCitationPrompt(sentence)
    ));
  const preferred = substantiveCandidates.find(isConclusionLikeCitationSentence)
    ?? substantiveCandidates[0]
    // When OCR only yielded labels, very short fragments, or prompts, retain
    // the controlled chunk instead of manufacturing a paraphrase or citation.
    ?? controlledSource;

  return truncateCitationExcerpt(preferred);
}

export function createLocalCitation(
  ranked: RankedChunk,
  chapters: ApiChapter[]
): Citation {
  const { chunk, score } = ranked;
  const metadata = chunk.source_metadata ?? {};
  const pdfPages = asPageNumberList(metadata.pdf_pages);
  const printedPages = asPageNumberList(metadata.printed_pages);
  const page = pdfPages[0] ?? chunk.page_start;
  const printedPage = printedPages[0] ?? chunk.printed_page_start ?? null;
  const chapter = chapters.find((item) => item.chapter_id === chunk.chapter_id);
  const quote = createCitationExcerpt(chunk.text);

  return {
    chapter_id: chunk.chapter_id,
    chapter_title: chapter?.source_title ?? "教材原文",
    page,
    chunk_id: chunk.chunk_id,
    quote,
    score: Number(score.toFixed(3)),
    retrieval_method: ranked.retrievalMethod ?? "on-device-keyword-rag",
    source_type: "textbook",
    location_type: "page",
    location_label: printedPage
      ? "教材第 " + printedPage + " 页（PDF 第 " + page + " 页）"
      : "PDF 第 " + page + " 页",
    source_metadata: {
      ...metadata,
      retrieval_quote: quote,
      // This is locally bundled corpus text, not model output. It gives the
      // source reader a usable offline page-text view when no released page
      // bitmap is available.
      retrieved_chunk_text: chunk.text
    }
  };
}

function trimForPrompt(value: string, maximumLength: number) {
  const compact = value.trim();
  return compact.length > maximumLength ? compact.slice(0, maximumLength - 1) + "…" : compact;
}

function normalizeHistory(history: RagQuery["history"]): DeepSeekHistoryMessage[] {
  if (!history) return [];
  const messages: DeepSeekHistoryMessage[] = [];
  history.forEach((item) => {
    const role = item.role;
    const content = item.content;
    if ((role !== "user" && role !== "assistant") || typeof content !== "string") return;
    const text = content.trim();
    if (text) messages.push({ role, content: trimForPrompt(text, 1_200) });
  });
  return messages.slice(-8);
}

function normalizedContextLine(label: string, value: string | null | undefined) {
  const text = value?.trim();
  return `${label}：${text || "未指定"}`;
}

function textbookContextLines(query: RagQuery) {
  const context = query.context;
  const concepts = context?.key_concepts
    ?.map((concept) => concept.trim())
    .filter(Boolean)
    .slice(0, 8)
    .join("、");
  return [
    normalizedContextLine("当前教材", context?.book_title),
    normalizedContextLine("当前章节", context?.chapter_title),
    normalizedContextLine("当前小节", context?.section_title),
    normalizedContextLine("当前页码范围", context?.page_label),
    normalizedContextLine("当前知识点", concepts)
  ];
}

function buildFallbackSearchQuery(query: RagQuery) {
  const question = query.question.trim();
  const needsResolvedContext = /(?:本章|本节|这一章|这一节|这章|这节|这里|这个概念|概括|主要内容|讲什么)/u.test(question);
  if (!needsResolvedContext) return trimForPrompt(question, TOOL_QUERY_MAX_LENGTH);
  const contextTerms = [
    query.context?.chapter_title,
    query.context?.section_title,
    ...(query.context?.key_concepts ?? []).slice(0, 6)
  ]
    .map((value) => value?.trim())
    .filter((value): value is string => Boolean(value));
  return trimForPrompt([...contextTerms, question].join(" "), TOOL_QUERY_MAX_LENGTH);
}

export function collectChapterScopeIds(chapters: readonly ApiChapter[], chapterId?: string | null) {
  if (!chapterId) return [];
  const scope = new Set<string>([chapterId]);
  let changed = true;
  while (changed) {
    changed = false;
    chapters.forEach((chapter) => {
      if (!chapter.parent_id || !scope.has(chapter.parent_id) || scope.has(chapter.chapter_id)) return;
      scope.add(chapter.chapter_id);
      changed = true;
    });
  }
  return [...scope];
}

function getProviderErrorMessage(_payload: unknown, status: number) {
  if (status === 401 || status === 403) return "DeepSeek API Key 无效、已过期或无权限。";
  if (status === 429) return "DeepSeek 请求过于频繁或当前额度不足，请稍后再试。";
  return "DeepSeek 请求失败（HTTP " + status + "）。";
}

function parseProviderPayload(value: unknown) {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return value;
  }
}

async function requestDeepSeek(messages: DeepSeekMessage[], options: DeepSeekRequestOptions = {}) {
  const url = deepSeekConfig.baseUrl.replace(/\/+$/, "") + "/chat/completions";
  const body = {
    model: deepSeekConfig.model,
    messages,
    max_tokens: options.maxTokens ?? deepSeekConfig.maxTokens,
    stream: false,
    thinking: { type: "disabled" },
    ...(options.tools
      ? {
          tools: options.tools,
          tool_choice: options.toolChoice ?? "auto"
        }
      : {})
  };
  const headers = {
    Authorization: "Bearer " + deepSeekConfig.apiKey.trim(),
    "Content-Type": "application/json"
  };

  if (Capacitor.isNativePlatform()) {
    const response = await CapacitorHttp.post({
      url,
      headers,
      data: body,
      connectTimeout: 30_000,
      readTimeout: 60_000
    });
    const payload = parseProviderPayload(response.data);
    if (response.status < 200 || response.status >= 300) {
      throw new DeepSeekDirectError(getProviderErrorMessage(payload, response.status));
    }
    return payload;
  }

  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body)
  });
  const payload = parseProviderPayload(await response.text());
  if (!response.ok) {
    throw new DeepSeekDirectError(getProviderErrorMessage(payload, response.status));
  }
  return payload;
}

function readToolCalls(value: unknown) {
  if (!Array.isArray(value)) return [] as DeepSeekToolCall[];
  return value.flatMap((item) => {
    if (!isRecord(item) || !isRecord(item.function)) return [];
    const id = item.id;
    const type = item.type;
    const name = item.function.name;
    const argumentsText = item.function.arguments;
    if (
      typeof id !== "string"
      || !id.trim()
      || type !== "function"
      || typeof name !== "string"
      || !name.trim()
      || typeof argumentsText !== "string"
    ) {
      return [];
    }
    return [{
      id,
      type: "function" as const,
      function: {
        name,
        arguments: argumentsText
      }
    }];
  });
}

function readAssistantResponse(payload: unknown): DeepSeekAssistantResponse | null {
  if (!isRecord(payload) || !Array.isArray(payload.choices)) return null;
  const firstChoice = payload.choices[0];
  if (!isRecord(firstChoice) || !isRecord(firstChoice.message)) return null;
  const content = firstChoice.message.content;
  return {
    content: typeof content === "string" && content.trim() ? content.trim() : null,
    toolCalls: readToolCalls(firstChoice.message.tool_calls)
  };
}

function parseSearchToolQuery(argumentsText: string) {
  try {
    const parsed = JSON.parse(argumentsText) as unknown;
    if (!isRecord(parsed) || typeof parsed.query !== "string") return null;
    const query = parsed.query.trim();
    return query ? trimForPrompt(query, TOOL_QUERY_MAX_LENGTH) : null;
  } catch {
    return null;
  }
}

function toolNoReliableMatch(message: string, query?: string | null) {
  return JSON.stringify({
    status: "no_reliable_textbook_match",
    query: query ?? null,
    message,
    sources: []
  });
}

function toolReliableMatches(
  query: string,
  rankedChunks: RankedChunk[],
  chapters: ApiChapter[]
) {
  const citations = rankedChunks.map((item) => createLocalCitation(item, chapters));
  return JSON.stringify({
    status: "reliable_textbook_match",
    query,
    sources: rankedChunks.map((ranked, index) => ({
      retrieval_method: ranked.retrievalMethod ?? "on-device-keyword-rag",
      score: Number(ranked.score.toFixed(4)),
      chunk_id: ranked.chunk.chunk_id,
      chapter_id: ranked.chunk.chapter_id,
      section_id: "section_id" in ranked.chunk && typeof ranked.chunk.section_id === "string"
        ? ranked.chunk.section_id
        : null,
      chapter_title: citations[index]?.chapter_title ?? "教材原文",
      pdf_page: citations[index]?.page ?? ranked.chunk.page_start,
      textbook_page: asPageNumberList(ranked.chunk.source_metadata?.printed_pages)[0]
        ?? ranked.chunk.printed_page_start
        ?? null,
      text: trimForPrompt(ranked.chunk.text, TOOL_CHUNK_MAX_LENGTH)
    }))
  });
}

async function rankToolChunks(
  question: string,
  corpus: DeepSeekRagCorpus,
  chapterId?: string | null
) {
  if (!corpus.textbookRetriever) {
    return {
      rankedChunks: selectReliableLocalChunks(question, corpus.chunks, chapterId),
      method: "on-device-keyword-rag",
      errorCode: null
    };
  }
  const response = await corpus.textbookRetriever.search({
    query: question,
    chapterId,
    chapterScopeIds: collectChapterScopeIds(corpus.chapters, chapterId),
    limit: 5,
    reliableOnly: true
  });
  return {
    rankedChunks: response.hits.slice(0, LOCAL_TEXTBOOK_CONTEXT_LIMIT).map((hit) => ({
      chunk: hit.chunk,
      score: hit.score,
      retrievalMethod: response.method,
      reliabilityThreshold: response.minimum_evidence_threshold ?? undefined
    })),
    method: response.method,
    errorCode: response.error_code ?? null
  };
}

async function prepareToolFollowup(
  toolCalls: DeepSeekToolCall[],
  corpus: DeepSeekRagCorpus,
  chapterId?: string | null
) {
  const searchInvocations: SearchToolInvocation[] = [];
  const queryByCallId = new Map<string, string | null>();

  for (const call of toolCalls) {
    if (call.function.name !== searchTextbookTool.function.name) continue;
    const query = parseSearchToolQuery(call.function.arguments);
    queryByCallId.set(call.id, query);
    if (!query) continue;
    const ranked = await rankToolChunks(query, corpus, chapterId);
    searchInvocations.push({
      call,
      query,
      rankedChunks: ranked.rankedChunks,
      method: ranked.method,
      errorCode: ranked.errorCode
    });
  }

  const strongestByChunkId = new Map<string, RankedChunk>();
  searchInvocations.forEach((invocation) => {
    invocation.rankedChunks.forEach((ranked) => {
      const existing = strongestByChunkId.get(ranked.chunk.chunk_id);
      if (!existing || ranked.score > existing.score) {
        strongestByChunkId.set(ranked.chunk.chunk_id, ranked);
      }
    });
  });
  const injectedChunks = [...strongestByChunkId.values()]
    .sort((left, right) => right.score - left.score || left.chunk.page_start - right.chunk.page_start)
    .slice(0, LOCAL_TEXTBOOK_CONTEXT_LIMIT);
  const injectedChunkIds = new Set(injectedChunks.map(({ chunk }) => chunk.chunk_id));
  const invocationByCallId = new Map(searchInvocations.map((item) => [item.call.id, item]));

  const toolMessages = toolCalls.map<DeepSeekToolMessage>((call) => {
    if (call.function.name !== searchTextbookTool.function.name) {
      return {
        role: "tool",
        tool_call_id: call.id,
        content: toolNoReliableMatch("该工具不可用。请不要编造教材页码或教材出处。")
      };
    }

    const query = queryByCallId.get(call.id);
    if (!query) {
      return {
        role: "tool",
        tool_call_id: call.id,
        content: toolNoReliableMatch("检索参数无效，未查询教材。请不要编造教材页码或教材出处。")
      };
    }

    const invocation = invocationByCallId.get(call.id);
    const callChunks = invocation?.rankedChunks.filter(
      ({ chunk }) => injectedChunkIds.has(chunk.chunk_id)
    ) ?? [];
    return {
      role: "tool",
      tool_call_id: call.id,
      content: callChunks.length > 0
        ? toolReliableMatches(query, callChunks, corpus.chapters)
        : toolNoReliableMatch("没有可靠教材命中。请不要引用教材页码；可直接说明当前教材片段不足，或回答无需教材证据的部分。", query)
    };
  });

  const firstSuccessfulInvocation = searchInvocations.find((item) => item.rankedChunks.length > 0);
  const firstInvocation = firstSuccessfulInvocation ?? searchInvocations[0];
  const unavailable = searchInvocations.length > 0
    && searchInvocations.every((item) => item.method === "unavailable");
  const retrieval: RagRetrievalSummary = {
    attempted: true,
    status: injectedChunks.length > 0 ? "hit" : unavailable ? "unavailable" : "no_match",
    method: firstInvocation?.method ?? null,
    hit_count: injectedChunks.length,
    error_code: firstInvocation?.errorCode ?? null
  };

  return { injectedChunks, toolMessages, retrieval };
}

function selectRelatedAssets(assets: ApiAsset[], rankedChunks: RankedChunk[]) {
  const chunkIds = new Set(rankedChunks.map(({ chunk }) => chunk.chunk_id));
  return assets
    .filter((asset) => asset.source_chunk_ids.some((chunkId) => chunkIds.has(chunkId)))
    .slice(0, 3);
}

function responseConfidence(rankedChunks: RankedChunk[]) {
  const highestScore = rankedChunks[0]?.score ?? 0;
  const threshold = rankedChunks[0]?.reliabilityThreshold ?? LOCAL_TEXTBOOK_RELIABILITY_THRESHOLD;
  if (highestScore < threshold) return "low";
  return highestScore >= threshold + (threshold < 1 ? 0.08 : 2) ? "high" : "medium";
}

function asDirectError(error: unknown) {
  if (error instanceof DeepSeekDirectError) return error;
  return new DeepSeekDirectError("无法连接 DeepSeek。请检查网络、个人 Key 和 API 服务状态。");
}

function retrievalPlannerSystemMessage(query: RagQuery): DeepSeekSystemMessage {
  return {
    role: "system",
    content: [
      "你是教材检索查询规划器。对于每一条用户消息都必须调用 search_textbook，不能直接生成最终回答。",
      ...textbookContextLines(query),
      "调用工具时，query 必须是适合检索当前教材的简洁中文检索词。",
      "如果用户使用‘本章’‘本节’‘这里’‘这个概念’等指代，必须结合当前章节、小节和知识点改写成具体概念。",
      "如果用户是问候、闲聊或教材外问题，也必须调用 search_textbook；本地检索器会决定是否有可靠教材证据。",
      "不得在第一阶段回答问题，不得编造教材内容或页码。"
    ].join("\n")
  };
}

function answerSystemMessage(query: RagQuery): DeepSeekSystemMessage {
  return {
    role: "system",
    content: [
      "你是中文教材学习助手。你已经收到 search_textbook 的本地检索结果。",
      ...textbookContextLines(query),
      "当工具返回 reliable_textbook_match 时，教材事实只能依据 sources 中的有限 text；先直接回答，再用适合学生的语言解释。",
      "不得引用 sources 之外的教材页码，也不要在正文中伪造引用编号；真实来源由前端显示。",
      "当工具返回 no_reliable_textbook_match 时，不得声称回答来自当前教材，不得编造教材原文或页码。",
      "对于问候可自然回答；对于教材问题应说明当前教材证据不足；对于一般问题可简短回答，但必须与教材依据区分。",
      "最终回答不提及 Tool Call、向量、BM25、阈值、模型选择或内部检索流程。"
    ].join("\n")
  };
}

function ensureSearchToolCall(firstResponse: DeepSeekAssistantResponse, query: RagQuery) {
  const toolCalls = [...firstResponse.toolCalls];
  const hasUsableSearchCall = toolCalls.some((call) => (
    call.function.name === searchTextbookTool.function.name
    && parseSearchToolQuery(call.function.arguments)
  ));
  if (hasUsableSearchCall) return toolCalls;
  toolCalls.push({
    id: "local_search_fallback",
    type: "function",
    function: {
      name: searchTextbookTool.function.name,
      arguments: JSON.stringify({
        query: buildFallbackSearchQuery(query),
        scope: "whole_book"
      })
    }
  });
  return toolCalls;
}

/**
 * Direct BYOK chat with optional local textbook evidence.
 *
 * Request one intentionally contains no textbook text and is forced to plan a
 * search_textbook call. Request two always runs, receives either reliable
 * local chunks or an explicit no-match result, and produces the student-facing
 * answer. Every returned citation is recreated from an actually injected
 * local chunk rather than from model output.
 */
export async function askDeepSeekWithLocalRag(
  query: RagQuery,
  corpus: DeepSeekRagCorpus
): Promise<RagResponse> {
  if (!hasDirectDeepSeekKey()) throw new DeepSeekDirectError(deepSeekKeySetupMessage);

  const question = query.question.trim();
  if (!question) throw new DeepSeekDirectError("请输入要提问的问题。");
  if (question.length > 2_000) throw new DeepSeekDirectError("问题过长，请控制在 2,000 个字符以内。");

  const initialMessages: DeepSeekMessage[] = [
    retrievalPlannerSystemMessage(query),
    ...normalizeHistory(query.history),
    { role: "user", content: question }
  ];

  let firstPayload: unknown;
  try {
    firstPayload = await requestDeepSeek(initialMessages, {
      tools: [searchTextbookTool],
      toolChoice: {
        type: "function",
        function: { name: searchTextbookTool.function.name }
      },
      maxTokens: RETRIEVAL_PLANNER_MAX_TOKENS
    });
  } catch (error) {
    throw asDirectError(error);
  }

  const firstResponse = readAssistantResponse(firstPayload);
  if (!firstResponse) {
    throw new DeepSeekDirectError("DeepSeek 没有返回可显示的回答，请重试。");
  }

  const effectiveToolCalls = ensureSearchToolCall(firstResponse, query);
  const { injectedChunks, toolMessages, retrieval } = await prepareToolFollowup(
    effectiveToolCalls,
    corpus,
    query.chapter_id
  );
  const followupMessages: DeepSeekMessage[] = [
    answerSystemMessage(query),
    ...normalizeHistory(query.history),
    { role: "user", content: question },
    {
      role: "assistant",
      // The first request is only a retrieval planner. Discard any prose the
      // provider returned so ungrounded preliminary text cannot influence the
      // student-facing answer in stage two.
      content: null,
      tool_calls: effectiveToolCalls
    },
    ...toolMessages
  ];

  let secondPayload: unknown;
  try {
    secondPayload = await requestDeepSeek(followupMessages, {
      tools: [searchTextbookTool],
      toolChoice: "none"
    });
  } catch (error) {
    throw asDirectError(error);
  }

  const secondResponse = readAssistantResponse(secondPayload);
  if (!secondResponse?.content) {
    throw new DeepSeekDirectError("DeepSeek 没有返回可显示的回答，请重试。");
  }

  const citations = injectedChunks.map((item) => createLocalCitation(item, corpus.chapters));
  return {
    answer: secondResponse.content,
    citations,
    related_assets: selectRelatedAssets(corpus.assets, injectedChunks),
    confidence: responseConfidence(injectedChunks),
    retrieval
  };
}
