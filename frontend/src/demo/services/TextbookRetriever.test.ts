import { describe, expect, it, vi } from "vitest";
import type {
  Bm25Index,
  RagManifest,
  TextbookRagChunk,
  TextbookSearchResponse,
  TextbookWorkerRequest,
  TextbookWorkerResponse
} from "../rag/types";
import { TextbookRetriever } from "./TextbookRetriever";

const chunk: TextbookRagChunk = {
  chunk_id: "rag_meiosis",
  book_id: "book_biology_2",
  chapter_id: "c2",
  section_id: "c2s1",
  page_start: 11,
  page_end: 11,
  printed_page_start: 16,
  content_type: "body",
  text: "减数分裂会使配子中的染色体数目减半。",
  asset_ids: [],
  key_concepts: ["减数分裂"],
  title_path: ["第2章", "第1节"],
  vector_position: 0,
  source_metadata: { pdf_pages: [11], printed_pages: [16] }
};

const manifest: RagManifest = {
  schema_version: 1,
  corpus_version: "biology-required-2-rag-v1",
  book_id: "book_biology_2",
  source: {
    sha256: "a".repeat(64),
    pdf_page_count: 125,
    missing_chapter_one_body: true,
    content_scope: "test",
    frontmatter: {
      chapter_id: "frontmatter",
      title: "教材封面、前言与目录",
      pdf_page_start: 1,
      pdf_page_end: 9
    }
  },
  embeddings: {
    model_id: "Xenova/bge-small-zh-v1.5",
    revision: "75c43b069aac4d136ba6bc1122f995fedcfd2781",
    model_int8_sha256: "b".repeat(64),
    dimension: 512,
    pooling: "cls",
    normalize: true,
    query_prefix: "为这个句子生成表示以用于检索相关文章：",
    provider: "wasm",
    wasm_threads: 1
  },
  retrieval: {
    algorithm_version: "test",
    weights: { semantic: 0.75, bm25: 0.20, chapterPrior: 0.05 },
    high_confidence_threshold: 0.6,
    minimum_evidence_threshold: 0.6,
    // The greeting shares common Chinese characters with the corpus, so this
    // calibrated fixture threshold must distinguish it from a specific
    // paraphrased textbook query rather than relying on a zero lexical score.
    lexical_fallback_threshold: 8
  },
  coverage: {
    required_pdf_pages: 125,
    covered_pdf_pages: 125,
    recovered_pdf_pages: [6],
    no_text_documented_pages: []
  },
  artifacts: {
    chunks: { path: "rag/biology-required-2-rag-v1/chunks.json", bytes: 1, sha256: "c".repeat(64) },
    bm25: { path: "rag/biology-required-2-rag-v1/bm25.json", bytes: 1, sha256: "d".repeat(64) }
  }
};

const bm25: Bm25Index = {
  schema_version: 1,
  tokenizer_version: "test",
  document_count: 1,
  average_document_length: 1,
  document_lengths: [1],
  k1: 1.2,
  b: 0.75,
  postings: {
    减数: { idf: 4, postings: [[0, 2]] },
    分裂: { idf: 4, postings: [[0, 2]] },
    配子: { idf: 4, postings: [[0, 2]] },
    染色: { idf: 3, postings: [[0, 2]] },
    色体: { idf: 3, postings: [[0, 2]] },
    你: { idf: 2, postings: [[0, 1]] },
    好: { idf: 2, postings: [[0, 1]] }
  }
};

function fetcherForCorpus() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("manifest.json")) return new Response(JSON.stringify(manifest));
    if (path.endsWith("chunks.json")) return new Response(JSON.stringify({ chunks: [chunk] }));
    if (path.endsWith("bm25.json")) return new Response(JSON.stringify(bm25));
    return new Response(null, { status: 404 });
  }) as unknown as typeof fetch;
}

function workerWith(response: TextbookSearchResponse) {
  const worker: {
    onmessage: ((event: MessageEvent<TextbookWorkerResponse>) => void) | null;
    onerror: ((event: ErrorEvent) => void) | null;
    onmessageerror: ((event: MessageEvent<unknown>) => void) | null;
    postMessage: (message: TextbookWorkerRequest) => void;
    terminate: () => void;
  } = {
    onmessage: null,
    onerror: null,
    onmessageerror: null,
    postMessage(message) {
      queueMicrotask(() => worker.onmessage?.({
        data: { request_id: message.request_id, ok: true, response }
      } as MessageEvent<TextbookWorkerResponse>));
    },
    terminate: vi.fn()
  };
  return worker;
}

function silentWorker() {
  const worker: {
    onmessage: ((event: MessageEvent<TextbookWorkerResponse>) => void) | null;
    onerror: ((event: ErrorEvent) => void) | null;
    onmessageerror: ((event: MessageEvent<unknown>) => void) | null;
    postMessage: (message: TextbookWorkerRequest) => void;
    terminate: () => void;
  } = {
    onmessage: null,
    onerror: null,
    onmessageerror: null,
    postMessage: () => undefined,
    terminate: vi.fn()
  };
  return worker;
}

function errorWorker(kind: "error" | "messageerror") {
  const worker = silentWorker();
  worker.postMessage = () => {
    queueMicrotask(() => {
      if (kind === "error") {
        worker.onerror?.({ type: "error" } as ErrorEvent);
      } else {
        worker.onmessageerror?.({ type: "messageerror" } as MessageEvent<unknown>);
      }
    });
  };
  return worker;
}

describe("TextbookRetriever", () => {
  it("uses one worker result without falling back to any network endpoint", async () => {
    const fetcher = fetcherForCorpus();
    const response: TextbookSearchResponse = {
      status: "ready",
      method: "on-device-hybrid-rag",
      corpus_version: manifest.corpus_version,
      high_confidence_threshold: 0.6,
      minimum_evidence_threshold: 0.6,
      hits: [{
        chunk,
        score: 0.8,
        semantic_score: 0.9,
        bm25_score: 4,
        chapter_prior: 0,
        reliable: true
      }]
    };
    const factory = vi.fn(() => workerWith(response));
    const retriever = new TextbookRetriever({ workerFactory: factory, fetcher });

    await expect(retriever.search({ query: "减数分裂", reliableOnly: true }))
      .resolves.toMatchObject({ method: "on-device-hybrid-rag", hits: [{ chunk: { chunk_id: "rag_meiosis" } }] });
    expect(factory).toHaveBeenCalledOnce();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("uses the independent BM25 threshold only to rescue a hybrid no-evidence result", async () => {
    const hybridNoEvidence: TextbookSearchResponse = {
      status: "ready",
      method: "on-device-hybrid-rag",
      corpus_version: manifest.corpus_version,
      high_confidence_threshold: 0.6,
      minimum_evidence_threshold: 0.6,
      hits: []
    };
    const fetcher = fetcherForCorpus();
    const retriever = new TextbookRetriever({
      workerFactory: () => workerWith(hybridNoEvidence),
      fetcher
    });

    await expect(retriever.search({
      query: "配子中的染色体数量为什么会减半？",
      chapterId: "c2",
      reliableOnly: true
    })).resolves.toMatchObject({
      status: "lexical_fallback",
      method: "on-device-bm25-fallback",
      error_code: null,
      hits: [{ chunk: { chunk_id: "rag_meiosis" }, reliable: true }]
    });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("does not cite a greeting when a hybrid no-hit tries lexical rescue", async () => {
    const hybridNoEvidence: TextbookSearchResponse = {
      status: "ready",
      method: "on-device-hybrid-rag",
      corpus_version: manifest.corpus_version,
      high_confidence_threshold: 0.6,
      minimum_evidence_threshold: 0.6,
      hits: []
    };
    const fetcher = fetcherForCorpus();
    const retriever = new TextbookRetriever({
      workerFactory: () => workerWith(hybridNoEvidence),
      fetcher
    });

    await expect(retriever.search({ query: "你好", reliableOnly: true })).resolves.toMatchObject({
      hits: []
    });
    // Three local asset reads prove the lexical rescue was attempted; its
    // non-zero common-character score still could not create a citation.
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("falls back to bundled BM25 when a worker cannot be created", async () => {
    const fetcher = fetcherForCorpus();
    const retriever = new TextbookRetriever({
      workerFactory: () => {
        throw new Error("worker blocked");
      },
      fetcher
    });

    const response = await retriever.search({
      query: "减数分裂",
      chapterId: "c2",
      reliableOnly: true
    });

    expect(response).toMatchObject({
      status: "lexical_fallback",
      method: "on-device-bm25-fallback",
      error_code: "worker_unavailable",
      hits: [{ chunk: { chunk_id: "rag_meiosis" }, semantic_score: null, reliable: true }]
    });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("does not cite a greeting when Worker creation fails despite common-character BM25 overlap", async () => {
    const retriever = new TextbookRetriever({
      workerFactory: () => {
        throw new Error("worker blocked");
      },
      fetcher: fetcherForCorpus()
    });

    await expect(retriever.search({ query: "你好", reliableOnly: true })).resolves.toMatchObject({
      status: "lexical_fallback",
      method: "on-device-bm25-fallback",
      error_code: "worker_unavailable",
      hits: []
    });
  });

  it("wraps the default browser fetch instead of invoking it with the retriever as receiver", async () => {
    let retriever: TextbookRetriever | null = null;
    const browserFetch = vi.fn(function (
      this: unknown,
      input: RequestInfo | URL
    ) {
      if (this === retriever) throw new TypeError("Illegal invocation");
      return fetcherForCorpus()(input);
    });
    vi.stubGlobal("fetch", browserFetch);
    try {
      retriever = new TextbookRetriever({
        workerFactory: () => {
          throw new Error("worker blocked");
        }
      });
      await expect(retriever.search({ query: "减数分裂", reliableOnly: true })).resolves.toMatchObject({
        status: "lexical_fallback",
        hits: [{ chunk: { chunk_id: "rag_meiosis" } }]
      });
      expect(browserFetch).toHaveBeenCalledTimes(3);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("returns no fabricated hit when both the worker and bundled index are unavailable", async () => {
    const retriever = new TextbookRetriever({
      workerFactory: () => {
        throw new Error("worker blocked");
      },
      fetcher: vi.fn(async () => new Response(null, { status: 503 })) as unknown as typeof fetch
    });

    await expect(retriever.search({ query: "教材以外的问题" })).resolves.toMatchObject({
      status: "unavailable",
      method: "unavailable",
      hits: []
    });
  });

  it("times out a Worker that never responds, terminates it, and uses the bundled BM25 index", async () => {
    const worker = silentWorker();
    const retriever = new TextbookRetriever({
      workerFactory: () => worker,
      fetcher: fetcherForCorpus(),
      workerRequestTimeoutMs: 10,
      workerHardTimeoutMs: 10
    });

    await expect(retriever.search({
      query: "减数分裂",
      chapterId: "c2",
      reliableOnly: true
    })).resolves.toMatchObject({
      status: "lexical_fallback",
      method: "on-device-bm25-fallback",
      error_code: "worker_timeout",
      hits: [{ chunk: { chunk_id: "rag_meiosis" }, reliable: true }]
    });
    await new Promise((resolve) => setTimeout(resolve, 15));
    expect(worker.terminate).toHaveBeenCalledOnce();
  });

  it("cleans up worker error and messageerror events before lexical fallback", async () => {
    for (const kind of ["error", "messageerror"] as const) {
      const worker = errorWorker(kind);
      const retriever = new TextbookRetriever({
        workerFactory: () => worker,
        fetcher: fetcherForCorpus(),
        workerRequestTimeoutMs: 100
      });

      await expect(retriever.search({ query: "减数分裂", reliableOnly: true })).resolves.toMatchObject({
        status: "lexical_fallback",
        error_code: "worker_unavailable",
        hits: [{ chunk: { chunk_id: "rag_meiosis" } }]
      });
      expect(worker.terminate).toHaveBeenCalledOnce();
    }
  });

  it("does not manufacture a textbook hit after a timeout when BM25 has no reliable evidence", async () => {
    const retriever = new TextbookRetriever({
      workerFactory: silentWorker,
      fetcher: fetcherForCorpus(),
      workerRequestTimeoutMs: 10
    });

    await expect(retriever.search({
      // The fixture deliberately gives this greeting a non-zero score (4),
      // below the calibrated threshold (8), so timeout fallback cannot turn
      // common Chinese characters into a textbook citation.
      query: "你好",
      reliableOnly: true
    })).resolves.toMatchObject({
      status: "lexical_fallback",
      error_code: "worker_timeout",
      hits: []
    });
  });
});
