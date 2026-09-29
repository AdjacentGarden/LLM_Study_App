import { rankTextbookChunks } from "../rag/retrievalMath";
import {
  BIOLOGY_RAG_CORPUS_VERSION,
  BIOLOGY_RAG_VECTOR_DIMENSION,
  type Bm25Index,
  type RagManifest,
  type TextbookRagChunk,
  type TextbookRetrievalStatus,
  type TextbookSearchRequest,
  type TextbookSearchResponse,
  type TextbookWorkerRequest,
  type TextbookWorkerResponse
} from "../rag/types";

type WorkerLike = Pick<Worker, "postMessage" | "terminate"> & {
  onmessage: ((event: MessageEvent<TextbookWorkerResponse>) => void) | null;
  onerror: ((event: ErrorEvent) => void) | null;
  onmessageerror: ((event: MessageEvent<unknown>) => void) | null;
};

type TextbookRetrieverOptions = {
  workerFactory?: () => WorkerLike;
  fetcher?: typeof fetch;
  /**
   * A semantic model is an optional enhancement. A slow or wedged Worker must
   * not keep the chat composer loading forever, so it is bounded independently
   * from the bundled lexical-index load.
   */
  workerRequestTimeoutMs?: number;
  workerHardTimeoutMs?: number;
  assetLoadTimeoutMs?: number;
};

type LexicalCorpus = {
  manifest: RagManifest;
  chunks: TextbookRagChunk[];
  bm25: Bm25Index;
};

const corpusManifestUrl = "/rag/" + BIOLOGY_RAG_CORPUS_VERSION + "/manifest.json";
const defaultWorkerRequestTimeoutMs = 1_800;
const defaultWorkerHardTimeoutMs = 15_000;
const defaultAssetLoadTimeoutMs = 4_000;

type WorkerFailure = "worker_unavailable" | "worker_timeout";

function publicPath(path: string) {
  return "/" + path.replace(/^\/+/u, "");
}

function unavailableResponse(errorCode: TextbookSearchResponse["error_code"]): TextbookSearchResponse {
  return {
    status: "unavailable",
    method: "unavailable",
    corpus_version: null,
    high_confidence_threshold: null,
    minimum_evidence_threshold: null,
    hits: [],
    error_code: errorCode
  };
}

/**
 * Owns one browser Worker for the static Biology textbook corpus. The worker
 * is deliberately lazy: ordinary chat neither downloads the index nor starts
 * the embedding model. If Workers/ONNX fail, this class has a small BM25-only
 * local fallback; it never asks a remote retrieval service for textbook text.
 */
export class TextbookRetriever {
  private readonly workerFactory?: () => WorkerLike;
  private readonly fetcher: typeof fetch;
  private readonly workerRequestTimeoutMs: number;
  private readonly workerHardTimeoutMs: number;
  private readonly assetLoadTimeoutMs: number;
  private worker: WorkerLike | null = null;
  private sequence = 0;
  private readonly pending = new Map<string, {
    resolve: (response: TextbookSearchResponse) => void;
    reject: (reason?: Error) => void;
    timeout: ReturnType<typeof setTimeout>;
  }>();
  private workerHardTimeout: {
    worker: WorkerLike;
    timeout: ReturnType<typeof setTimeout>;
  } | null = null;
  private lexicalCorpusPromise: Promise<LexicalCorpus> | null = null;
  private status: TextbookRetrievalStatus = "idle";

  constructor(options: TextbookRetrieverOptions = {}) {
    this.workerFactory = options.workerFactory;
    // Keep the browser Fetch receiver intact. Storing the global function
    // directly can throw an Illegal invocation in Chromium when the lexical
    // fallback calls it as an object property.
    this.fetcher = options.fetcher ?? ((input, init) => fetch(input, init));
    this.workerRequestTimeoutMs = options.workerRequestTimeoutMs ?? defaultWorkerRequestTimeoutMs;
    this.workerHardTimeoutMs = options.workerHardTimeoutMs ?? defaultWorkerHardTimeoutMs;
    this.assetLoadTimeoutMs = options.assetLoadTimeoutMs ?? defaultAssetLoadTimeoutMs;
  }

  getStatus() {
    return this.status;
  }

  async prewarm() {
    this.status = "loading";
    try {
      const response = await this.invoke("prewarm");
      this.status = response.status;
      return response;
    } catch (error) {
      const response = await this.searchLexically({ query: "", limit: 1 }, workerFailureFrom(error));
      this.status = response.status;
      return response;
    }
  }

  async search(request: TextbookSearchRequest): Promise<TextbookSearchResponse> {
    if (!request.query.trim()) return unavailableResponse(null);
    this.status = "loading";
    try {
      const response = await this.invoke("search", request);
      this.status = response.status;
      if (response.status !== "unavailable") {
        // A conservative semantic threshold can reject a direct textbook term
        // whose BM25 evidence is nevertheless unambiguous. Recheck only this
        // no-evidence case with the separately calibrated lexical threshold;
        // it is a proof-based rescue, not a relaxed hybrid threshold.
        if (
          request.reliableOnly
          && response.method === "on-device-hybrid-rag"
          && response.hits.length === 0
        ) {
          const lexical = await this.searchLexically(request, null);
          if (lexical.hits.length > 0) {
            this.status = lexical.status;
            return lexical;
          }
        }
        return response;
      }
    } catch (error) {
      // The fallback below intentionally contains no model/network branch.
      const fallback = await this.searchLexically(request, workerFailureFrom(error));
      this.status = fallback.status;
      return fallback;
    }
    const fallback = await this.searchLexically(request, "worker_unavailable");
    this.status = fallback.status;
    return fallback;
  }

  reset() {
    this.lexicalCorpusPromise = null;
    this.status = "idle";
    this.disposeWorker(new Error("worker_reset"));
  }

  destroy() {
    this.disposeWorker(new Error("worker_destroyed"));
    this.status = "idle";
  }

  private createWorker(): WorkerLike | null {
    if (this.worker) return this.worker;
    try {
      const worker = this.workerFactory
        ? this.workerFactory()
        : typeof Worker === "undefined"
          ? null
          : new Worker(new URL("../rag/textbookRag.worker.ts", import.meta.url), { type: "module" });
      if (!worker) return null;
      worker.onmessage = (event) => {
        const message = event.data;
        const pending = this.pending.get(message.request_id);
        // A soft request timeout may already have returned the BM25 answer.
        // A late response proves the Worker recovered, so preserve the warmed
        // local model for the next question instead of killing it.
        if (!pending) {
          this.clearWorkerHardTimeout(worker);
          return;
        }
        this.pending.delete(message.request_id);
        clearTimeout(pending.timeout);
        this.clearWorkerHardTimeout(worker);
        if (message.ok && message.response) pending.resolve(message.response);
        else pending.reject(new Error("worker_response_invalid"));
      };
      worker.onerror = () => this.disposeWorker(new Error("worker_error"), worker);
      worker.onmessageerror = () => this.disposeWorker(new Error("worker_message_error"), worker);
      this.worker = worker;
      return worker;
    } catch {
      return null;
    }
  }

  private invoke(type: TextbookWorkerRequest["type"], request?: TextbookSearchRequest) {
    const worker = this.createWorker();
    if (!worker) return Promise.reject(new Error("worker_unavailable"));
    this.sequence += 1;
    const requestId = "rag_" + this.sequence;
    const message: TextbookWorkerRequest = {
      request_id: requestId,
      type,
      request
    };
    return new Promise<TextbookSearchResponse>((resolve, reject) => {
      const timeout = setTimeout(() => {
        if (!this.pending.delete(requestId)) return;
        reject(new Error("worker_timeout"));
        // Return promptly to BM25, but give a slow one-time local model load a
        // bounded grace period. If it never answers, the hard timeout below
        // tears it down and clears all remaining pending requests.
        this.scheduleWorkerHardTimeout(worker);
      }, this.workerRequestTimeoutMs);
      this.pending.set(requestId, { resolve, reject, timeout });
      try {
        worker.postMessage(message);
      } catch {
        clearTimeout(timeout);
        this.pending.delete(requestId);
        reject(new Error("worker_post_failed"));
        this.disposeWorker(new Error("worker_post_failed"), worker);
      }
    });
  }

  private disposeWorker(reason: Error, target = this.worker) {
    this.clearWorkerHardTimeout(target);
    if (target) {
      try {
        target.terminate();
      } catch {
        // A browser can throw while tearing down an already-crashed Worker.
      }
    }
    if (this.worker === target) this.worker = null;
    this.pending.forEach((pending) => {
      clearTimeout(pending.timeout);
      pending.reject(reason);
    });
    this.pending.clear();
  }

  private scheduleWorkerHardTimeout(worker: WorkerLike) {
    if (this.workerHardTimeout?.worker === worker) return;
    this.clearWorkerHardTimeout();
    const timeout = setTimeout(() => {
      if (this.workerHardTimeout?.worker !== worker) return;
      this.workerHardTimeout = null;
      this.disposeWorker(new Error("worker_hard_timeout"), worker);
    }, this.workerHardTimeoutMs);
    this.workerHardTimeout = { worker, timeout };
  }

  private clearWorkerHardTimeout(worker?: WorkerLike | null) {
    if (!this.workerHardTimeout) return;
    if (worker && this.workerHardTimeout.worker !== worker) return;
    clearTimeout(this.workerHardTimeout.timeout);
    this.workerHardTimeout = null;
  }

  private async fetchAsset(path: string) {
    const controller = typeof AbortController === "undefined" ? null : new AbortController();
    let timeout: ReturnType<typeof setTimeout> | null = null;
    try {
      return await new Promise<Response>((resolve, reject) => {
        timeout = setTimeout(() => {
          controller?.abort();
          reject(new Error("asset_load_timeout"));
        }, this.assetLoadTimeoutMs);
        void this.fetcher(path, controller ? { signal: controller.signal } : undefined)
          .then(resolve, reject)
          .finally(() => {
            if (timeout) clearTimeout(timeout);
          });
      });
    } finally {
      if (timeout) clearTimeout(timeout);
    }
  }

  private async loadLexicalCorpus(): Promise<LexicalCorpus> {
    if (!this.lexicalCorpusPromise) {
      this.lexicalCorpusPromise = (async () => {
        const manifestResponse = await this.fetchAsset(corpusManifestUrl);
        if (!manifestResponse.ok) throw new Error("manifest_unavailable");
        const manifest = await manifestResponse.json() as RagManifest;
        if (
          manifest.corpus_version !== BIOLOGY_RAG_CORPUS_VERSION
          || manifest.embeddings.dimension !== BIOLOGY_RAG_VECTOR_DIMENSION
          || manifest.coverage.covered_pdf_pages !== 125
        ) {
          throw new Error("manifest_incompatible");
        }
        const [chunksResponse, bm25Response] = await Promise.all([
          this.fetchAsset(publicPath(manifest.artifacts.chunks.path)),
          this.fetchAsset(publicPath(manifest.artifacts.bm25.path))
        ]);
        if (!chunksResponse.ok || !bm25Response.ok) throw new Error("index_unavailable");
        const chunksDocument = await chunksResponse.json() as { chunks: TextbookRagChunk[] };
        const bm25 = await bm25Response.json() as Bm25Index;
        if (!Array.isArray(chunksDocument.chunks) || bm25.document_count !== chunksDocument.chunks.length) {
          throw new Error("index_corrupt");
        }
        return { manifest, chunks: chunksDocument.chunks, bm25 };
      })().catch((error) => {
        this.lexicalCorpusPromise = null;
        throw error;
      });
    }
    return this.lexicalCorpusPromise;
  }

  private async searchLexically(
    request: TextbookSearchRequest,
    errorCode: WorkerFailure | null = "worker_unavailable"
  ): Promise<TextbookSearchResponse> {
    try {
      const corpus = await this.loadLexicalCorpus();
      const hits = request.query.trim()
        ? rankTextbookChunks({
            query: request.query,
            chunks: corpus.chunks,
            bm25: corpus.bm25,
            dimension: BIOLOGY_RAG_VECTOR_DIMENSION,
            chapterId: request.chapterId,
            chapterScopeIds: request.chapterScopeIds,
            weights: corpus.manifest.retrieval.weights,
            limit: Math.min(5, Math.max(1, request.limit ?? 5)),
            reliableThreshold: corpus.manifest.retrieval.lexical_fallback_threshold,
            semanticEnabled: false
          })
        : [];
      return {
        status: "lexical_fallback",
        method: "on-device-bm25-fallback",
        corpus_version: corpus.manifest.corpus_version,
        high_confidence_threshold: corpus.manifest.retrieval.high_confidence_threshold,
        minimum_evidence_threshold: corpus.manifest.retrieval.minimum_evidence_threshold,
        hits: request.reliableOnly ? hits.filter((hit) => hit.reliable) : hits,
        error_code: errorCode
      };
    } catch {
      return unavailableResponse("index_unavailable");
    }
  }
}

function workerFailureFrom(error: unknown): WorkerFailure {
  return error instanceof Error && error.message === "worker_timeout"
    ? "worker_timeout"
    : "worker_unavailable";
}

let sharedRetriever: TextbookRetriever | null = null;

export function getTextbookRetriever() {
  sharedRetriever ??= new TextbookRetriever();
  return sharedRetriever;
}
