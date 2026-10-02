import { env, pipeline } from "@huggingface/transformers";
import { rankTextbookChunks } from "./retrievalMath";
import {
  BIOLOGY_RAG_CORPUS_VERSION,
  BIOLOGY_RAG_MODEL_ID,
  BIOLOGY_RAG_MODEL_REVISION,
  BIOLOGY_RAG_QUERY_PREFIX,
  BIOLOGY_RAG_VECTOR_DIMENSION,
  type Bm25Index,
  type RagManifest,
  type TextbookRagChunk,
  type TextbookSearchRequest,
  type TextbookSearchResponse,
  type TextbookWorkerRequest,
  type TextbookWorkerResponse
} from "./types";

type LoadedCorpus = {
  manifest: RagManifest;
  chunks: TextbookRagChunk[];
  bm25: Bm25Index;
  vectors: Float32Array;
};

type FeatureExtractor = (
  input: string,
  options: { pooling: "cls"; normalize: true; truncation: true; max_length: number }
) => Promise<{ data: Float32Array }>;

let corpusPromise: Promise<LoadedCorpus> | null = null;
let extractorPromise: Promise<FeatureExtractor> | null = null;
let modelUnavailable = false;
let localWasmModuleUrl: string | null = null;

function publicPath(path: string) {
  return "/" + path.replace(/^\/+/u, "");
}

async function fetchRequired(path: string, init?: RequestInit) {
  const response = await fetch(path, init);
  if (!response.ok) throw new Error("asset_fetch_failed");
  return response;
}

async function fetchJson<T>(path: string, reload = false) {
  const response = await fetchRequired(path, reload ? { cache: "reload" } : undefined);
  return response.json() as Promise<T>;
}

function validateManifest(manifest: RagManifest) {
  if (
    manifest.corpus_version !== BIOLOGY_RAG_CORPUS_VERSION
    || manifest.embeddings.model_id !== BIOLOGY_RAG_MODEL_ID
    || manifest.embeddings.revision !== BIOLOGY_RAG_MODEL_REVISION
    || manifest.embeddings.dimension !== BIOLOGY_RAG_VECTOR_DIMENSION
    || manifest.embeddings.wasm_threads !== 1
    || manifest.coverage.covered_pdf_pages !== 125
    || manifest.coverage.no_text_documented_pages.length !== 0
  ) {
    throw new Error("manifest_incompatible");
  }
}

async function loadCorpus(reload = false): Promise<LoadedCorpus> {
  const manifest = await fetchJson<RagManifest>(
    publicPath("rag/" + BIOLOGY_RAG_CORPUS_VERSION + "/manifest.json"),
    reload
  );
  validateManifest(manifest);
  const [chunksDocument, bm25, vectorsResponse] = await Promise.all([
    fetchJson<{ chunks: TextbookRagChunk[] }>(publicPath(manifest.artifacts.chunks.path), reload),
    fetchJson<Bm25Index>(publicPath(manifest.artifacts.bm25.path), reload),
    fetchRequired(publicPath(manifest.artifacts.vectors.path), reload ? { cache: "reload" } : undefined)
  ]);
  const vectorBuffer = await vectorsResponse.arrayBuffer();
  const chunks = chunksDocument.chunks;
  if (
    !Array.isArray(chunks)
    || chunks.length === 0
    || bm25.document_count !== chunks.length
    || vectorBuffer.byteLength !== chunks.length * BIOLOGY_RAG_VECTOR_DIMENSION * Float32Array.BYTES_PER_ELEMENT
  ) {
    throw new Error("corpus_corrupt");
  }
  return {
    manifest,
    chunks,
    bm25,
    vectors: new Float32Array(vectorBuffer)
  };
}

async function getCorpus(reload = false) {
  if (reload || !corpusPromise) corpusPromise = loadCorpus(reload);
  return corpusPromise;
}

async function localWasmPaths() {
  // Vite treats a dynamic import of a public .mjs file as a source import in
  // development. Loading the pinned local module text into a Worker-scoped
  // Blob URL keeps the ONNX Runtime import local in both Vite and Capacitor,
  // while the paired binary still has a stable static URL.
  if (!localWasmModuleUrl) {
    const moduleResponse = await fetchRequired("/rag/runtime/wasm/ort-wasm-simd-threaded.jsep.mjs");
    const source = await moduleResponse.text();
    localWasmModuleUrl = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
  }
  return {
    mjs: localWasmModuleUrl,
    wasm: new URL("/rag/runtime/wasm/ort-wasm-simd-threaded.jsep.wasm", self.location.href).href
  };
}

async function getExtractor(): Promise<FeatureExtractor> {
  if (modelUnavailable) throw new Error("model_unavailable");
  if (!extractorPromise) {
    env.allowRemoteModels = false;
    env.allowLocalModels = true;
    env.useBrowserCache = true;
    env.localModelPath = "/rag/models/";
    const wasm = env.backends.onnx.wasm;
    if (!wasm) throw new Error("wasm_runtime_unavailable");
    wasm.wasmPaths = await localWasmPaths();
    wasm.numThreads = 1;
    const createFeatureExtractor = pipeline as unknown as (
      task: "feature-extraction",
      model: string,
      options: Record<string, unknown>
    ) => Promise<FeatureExtractor>;
    extractorPromise = createFeatureExtractor("feature-extraction", BIOLOGY_RAG_MODEL_ID, {
      revision: BIOLOGY_RAG_MODEL_REVISION,
      dtype: "int8",
      local_files_only: true
    });
  }
  try {
    return await extractorPromise;
  } catch (error) {
    modelUnavailable = true;
    extractorPromise = null;
    throw error;
  }
}

function lexicalResponse(corpus: LoadedCorpus, request: TextbookSearchRequest): TextbookSearchResponse {
  const limit = Math.min(5, Math.max(1, request.limit ?? 5));
  const hits = rankTextbookChunks({
    query: request.query,
    chunks: corpus.chunks,
    bm25: corpus.bm25,
    dimension: BIOLOGY_RAG_VECTOR_DIMENSION,
    chapterId: request.chapterId,
    chapterScopeIds: request.chapterScopeIds,
    weights: corpus.manifest.retrieval.weights,
    limit,
    reliableThreshold: corpus.manifest.retrieval.lexical_fallback_threshold,
    semanticEnabled: false
  });
  return {
    status: "lexical_fallback",
    method: "on-device-bm25-fallback",
    corpus_version: corpus.manifest.corpus_version,
    high_confidence_threshold: corpus.manifest.retrieval.high_confidence_threshold,
    minimum_evidence_threshold: corpus.manifest.retrieval.minimum_evidence_threshold,
    hits: request.reliableOnly ? hits.filter((hit) => hit.reliable) : hits,
    error_code: "model_unavailable"
  };
}

async function search(request: TextbookSearchRequest): Promise<TextbookSearchResponse> {
  const query = request.query.trim();
  if (!query) {
    return {
      status: "ready",
      method: "on-device-hybrid-rag",
      corpus_version: BIOLOGY_RAG_CORPUS_VERSION,
      high_confidence_threshold: null,
      minimum_evidence_threshold: null,
      hits: []
    };
  }
  let corpus: LoadedCorpus;
  try {
    corpus = await getCorpus();
  } catch {
    // A cached incomplete JSON/binary asset is retried once with a reload
    // request. No remote model or remote textbook fallback is allowed.
    try {
      corpus = await getCorpus(true);
    } catch {
      return {
        status: "unavailable",
        method: "unavailable",
        corpus_version: null,
        high_confidence_threshold: null,
        minimum_evidence_threshold: null,
        hits: [],
        error_code: "corrupt_index"
      };
    }
  }

  try {
    const extractor = await getExtractor();
    const embedded = await extractor(BIOLOGY_RAG_QUERY_PREFIX + query, {
      pooling: "cls",
      normalize: true,
      truncation: true,
      max_length: 512
    });
    if (embedded.data.length !== BIOLOGY_RAG_VECTOR_DIMENSION) throw new Error("invalid_embedding");
    const hits = rankTextbookChunks({
      query,
      chunks: corpus.chunks,
      bm25: corpus.bm25,
      vectors: corpus.vectors,
      queryVector: embedded.data,
      dimension: BIOLOGY_RAG_VECTOR_DIMENSION,
      chapterId: request.chapterId,
      chapterScopeIds: request.chapterScopeIds,
      weights: corpus.manifest.retrieval.weights,
      limit: Math.min(5, Math.max(1, request.limit ?? 5)),
      reliableThreshold: corpus.manifest.retrieval.minimum_evidence_threshold,
      semanticEnabled: true
    });
    return {
      status: "ready",
      method: "on-device-hybrid-rag",
      corpus_version: corpus.manifest.corpus_version,
      high_confidence_threshold: corpus.manifest.retrieval.high_confidence_threshold,
      minimum_evidence_threshold: corpus.manifest.retrieval.minimum_evidence_threshold,
      hits: request.reliableOnly ? hits.filter((hit) => hit.reliable) : hits
    };
  } catch {
    return lexicalResponse(corpus, request);
  }
}

async function prewarm(): Promise<TextbookSearchResponse> {
  try {
    const corpus = await getCorpus();
    try {
      await getExtractor();
      return {
        status: "ready",
        method: "on-device-hybrid-rag",
        corpus_version: corpus.manifest.corpus_version,
        high_confidence_threshold: corpus.manifest.retrieval.high_confidence_threshold,
        minimum_evidence_threshold: corpus.manifest.retrieval.minimum_evidence_threshold,
        hits: []
      };
    } catch {
      return lexicalResponse(corpus, { query: "", limit: 1 });
    }
  } catch {
    return {
      status: "unavailable",
      method: "unavailable",
      corpus_version: null,
      high_confidence_threshold: null,
      minimum_evidence_threshold: null,
      hits: [],
      error_code: "index_unavailable"
    };
  }
}

function respond(message: TextbookWorkerResponse) {
  self.postMessage(message);
}

self.addEventListener("message", (event: MessageEvent<TextbookWorkerRequest>) => {
  const message = event.data;
  void (async () => {
    if (message.type === "reset") {
      corpusPromise = null;
      extractorPromise = null;
      modelUnavailable = false;
      respond({
        request_id: message.request_id,
        ok: true,
        response: await prewarm()
      });
      return;
    }
    const response = message.type === "prewarm"
      ? await prewarm()
      : await search(message.request ?? { query: "" });
    respond({ request_id: message.request_id, ok: true, response });
  })().catch(() => {
    respond({
      request_id: message.request_id,
      ok: false,
      error: "textbook_rag_worker_failed"
    });
  });
});
