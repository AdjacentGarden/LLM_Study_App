import type { ApiChunk } from "../types/api";

export const BIOLOGY_RAG_CORPUS_VERSION = "biology-required-2-rag-v1";
export const BIOLOGY_RAG_MODEL_ID = "Xenova/bge-small-zh-v1.5";
export const BIOLOGY_RAG_MODEL_REVISION = "75c43b069aac4d136ba6bc1122f995fedcfd2781";
export const BIOLOGY_RAG_VECTOR_DIMENSION = 512;
export const BIOLOGY_RAG_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章：";
export const BIOLOGY_RAG_RETRIEVAL_WEIGHTS = Object.freeze({
  semantic: 0.75,
  bm25: 0.20,
  chapterPrior: 0.05
});

export type TextbookRagChunk = ApiChunk & {
  section_id: string;
  title_path: string[];
  text_characters?: number;
  chunking_exception?: string | null;
  vector_position: number;
};

// Public names used by the RAG routing contract. The Textbook-prefixed forms
// above make call sites self-explanatory; these aliases keep the static corpus
// schema aligned with the documented RagChunk/RagSearchRequest/RagSearchResult
// terminology.
export type RagChunk = TextbookRagChunk;

export type Bm25Posting = {
  idf: number;
  postings: Array<[documentIndex: number, termFrequency: number]>;
};

export type Bm25Index = {
  schema_version: number;
  tokenizer_version: string;
  document_count: number;
  average_document_length: number;
  document_lengths: number[];
  k1: number;
  b: number;
  postings: Record<string, Bm25Posting>;
};

export type RagArtifact = {
  path: string;
  bytes: number;
  sha256: string;
};

export type RagManifest = {
  schema_version: number;
  corpus_version: string;
  book_id: string;
  source: {
    sha256: string;
    pdf_page_count: number;
    missing_chapter_one_body: boolean;
    content_scope: string;
    frontmatter: {
      chapter_id: "frontmatter";
      title: "教材封面、前言与目录";
      pdf_page_start: 1;
      pdf_page_end: 9;
    };
  };
  mineru?: {
    content_list_sha256: string;
    middle_sha256: string;
    content_list_entries: number;
    page_count: number;
  };
  embeddings: {
    model_id: string;
    revision: string;
    model_int8_sha256: string;
    dimension: number;
    pooling: "cls";
    normalize: boolean;
    query_prefix: string;
    provider: "wasm";
    wasm_threads: number;
  };
  retrieval: {
    algorithm_version: string;
    weights: {
      semantic: number;
      bm25: number;
      chapterPrior: number;
    };
    high_confidence_threshold: number;
    minimum_evidence_threshold: number;
    lexical_fallback_threshold: number;
    calibration?: {
      report_path: string;
      report_sha256: string;
      selected_thresholds: {
        high_confidence: number;
        minimum_evidence: number;
        lexical_fallback: number;
      };
      metrics?: Record<string, number>;
      lexical_fallback?: Record<string, unknown>;
      evaluated_query_count?: number;
    } | null;
  };
  coverage: {
    required_pdf_pages: number;
    covered_pdf_pages: number;
    recovered_pdf_pages: number[];
    no_text_documented_pages: number[];
  };
  artifacts: Record<string, RagArtifact>;
};

export type TextbookRetrievalMethod =
  | "on-device-hybrid-rag"
  | "on-device-bm25-fallback"
  | "unavailable";

export type TextbookRetrievalStatus =
  | "idle"
  | "loading"
  | "ready"
  | "lexical_fallback"
  | "unavailable";

export type TextbookSearchRequest = {
  query: string;
  chapterId?: string | null;
  chapterScopeIds?: string[];
  limit?: number;
  reliableOnly?: boolean;
};

export type RagSearchRequest = TextbookSearchRequest;

export type TextbookSearchHit = {
  chunk: TextbookRagChunk;
  score: number;
  semantic_score: number | null;
  bm25_score: number;
  chapter_prior: number;
  reliable: boolean;
};

export type TextbookSearchResponse = {
  status: TextbookRetrievalStatus;
  method: TextbookRetrievalMethod;
  corpus_version: string | null;
  high_confidence_threshold: number | null;
  minimum_evidence_threshold: number | null;
  hits: TextbookSearchHit[];
  error_code?: "worker_unavailable" | "worker_timeout" | "index_unavailable" | "model_unavailable" | "corrupt_index" | null;
};

export type RagSearchResult = TextbookSearchResponse;

export type RagCitation = {
  chunk_id: string;
  chapter_id: string;
  pdf_page: number;
  textbook_page: number | null;
  quote: string;
};

export type TextbookWorkerRequest = {
  request_id: string;
  type: "prewarm" | "search" | "reset";
  request?: TextbookSearchRequest;
};

export type TextbookWorkerResponse = {
  request_id: string;
  ok: boolean;
  response?: TextbookSearchResponse;
  error?: string;
};
