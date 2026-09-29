import type {
  Bm25Index,
  TextbookRagChunk,
  TextbookSearchHit
} from "./types";

const STOP_WORDS = new Set(["的", "了", "和", "是", "在", "与", "及", "为", "中", "第"]);
const BM25_SATURATION = 8;

export function normalizeRagText(value: string) {
  return value
    .normalize("NFKC")
    .replace(/[ \t\u3000]+/gu, " ")
    .replace(/\r\n?/gu, "\n")
    .replace(/\n{3,}/gu, "\n\n")
    .trim();
}

/**
 * This intentionally mirrors the offline build tokenizer. It emits Chinese
 * single characters and bigrams plus Latin/numeric terms, which keeps the
 * BM25 fallback deterministic without shipping a second segmentation model.
 */
export function tokenizeTextbookBm25(value: string) {
  const normalized = normalizeRagText(value).toLocaleLowerCase("zh-CN");
  const tokens: string[] = [];
  const latin = normalized.match(/[a-z0-9]+/gu) ?? [];
  tokens.push(...latin.filter((term) => term.length >= 2));
  const chineseRuns = normalized.match(/[\u3400-\u9fff]+/gu) ?? [];
  chineseRuns.forEach((run) => {
    for (let index = 0; index < run.length; index += 1) {
      tokens.push(run[index]);
      if (index + 1 < run.length) tokens.push(run.slice(index, index + 2));
    }
  });
  return tokens.filter((token) => !STOP_WORDS.has(token));
}

export function scoreBm25(query: string, index: Bm25Index) {
  const scores = new Float32Array(index.document_count);
  const queryFrequencies = new Map<string, number>();
  tokenizeTextbookBm25(query).forEach((token) => {
    queryFrequencies.set(token, (queryFrequencies.get(token) ?? 0) + 1);
  });
  queryFrequencies.forEach((queryFrequency, token) => {
    const posting = index.postings[token];
    if (!posting) return;
    posting.postings.forEach(([documentIndex, termFrequency]) => {
      const documentLength = index.document_lengths[documentIndex];
      const denominator = termFrequency
        + index.k1 * (1 - index.b + index.b * documentLength / index.average_document_length);
      scores[documentIndex] += posting.idf
        * ((termFrequency * (index.k1 + 1)) / denominator)
        * Math.min(queryFrequency, 3);
    });
  });
  return scores;
}

export function normalizedBm25Score(score: number) {
  return score > 0 ? score / (score + BM25_SATURATION) : 0;
}

export function cosineDotProduct(queryVector: ArrayLike<number>, vectors: Float32Array, position: number, dimension: number) {
  let sum = 0;
  const offset = position * dimension;
  for (let index = 0; index < dimension; index += 1) {
    sum += queryVector[index] * vectors[offset + index];
  }
  return sum;
}

export function rankTextbookChunks({
  query,
  chunks,
  bm25,
  vectors,
  queryVector,
  dimension,
  chapterId,
  chapterScopeIds,
  weights,
  limit = 5,
  reliableThreshold,
  semanticEnabled
}: {
  query: string;
  chunks: TextbookRagChunk[];
  bm25: Bm25Index;
  vectors?: Float32Array;
  queryVector?: ArrayLike<number>;
  dimension: number;
  chapterId?: string | null;
  chapterScopeIds?: readonly string[];
  weights: { semantic: number; bm25: number; chapterPrior: number };
  limit?: number;
  reliableThreshold: number;
  semanticEnabled: boolean;
}): TextbookSearchHit[] {
  const lexicalScores = scoreBm25(query, bm25);
  if (semanticEnabled && (!vectors || !queryVector)) {
    throw new Error("Semantic ranking requires a query vector.");
  }
  const chapterScope = new Set(chapterScopeIds ?? []);
  const hits = chunks.map((chunk, index) => {
    const bm25Score = lexicalScores[index] ?? 0;
    const semanticScore = queryVector
      ? cosineDotProduct(queryVector, vectors!, chunk.vector_position, dimension)
      : null;
    const chapterPrior = chapterId && (
      chapterId === chunk.chapter_id
      || chapterId === chunk.section_id
      || chapterScope.has(chunk.chapter_id)
      || chapterScope.has(chunk.section_id)
    ) ? 1 : 0;
    const score = semanticScore === null
      ? bm25Score
      : weights.semantic * semanticScore
        + weights.bm25 * normalizedBm25Score(bm25Score)
        + weights.chapterPrior * chapterPrior;
    return {
      chunk,
      score,
      semantic_score: semanticScore,
      bm25_score: bm25Score,
      chapter_prior: chapterPrior,
      reliable: semanticScore === null
        ? bm25Score >= reliableThreshold
        : score >= reliableThreshold
    };
  });
  return hits
    .sort((left, right) => right.score - left.score || left.chunk.chunk_id.localeCompare(right.chunk.chunk_id))
    .slice(0, Math.max(0, limit));
}
