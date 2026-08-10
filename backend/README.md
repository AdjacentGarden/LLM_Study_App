# BookCourse AI Backend

FastAPI backend for the BookCourse AI Phase 3 demo. It covers upload, cloud parsing, OCR/layout adapters, source figure extraction, AI-assisted figure generation, RAG, assignment diagnosis, and study planning.

## Run Locally

```powershell
cd backend
python -m pip install -e ".[test,ocr]"
$env:BOOKCOURSE_AUTH_MODE="optional"
$env:BOOKCOURSE_PARSER_PROVIDER="pymupdf"
$env:BOOKCOURSE_RAG_INDEX_PROVIDER="artifact"
$env:BOOKCOURSE_USE_WORKER="false"
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

This local profile needs no external parser, database, or API key and supports PDFs with a text layer. Use `BOOKCOURSE_AUTH_MODE=strict` plus `BOOKCOURSE_API_KEY` outside a single-user development machine.

The default parse route expects the local MinerU HTTP service at
`http://127.0.0.1:8001`. The `ocr` extra installs the real PaddleOCR fallback;
installing only `.[test]` is sufficient for unit tests that do not exercise
real OCR, but not for the default production-style fallback chain.

Run tests:

```powershell
python -m pytest
```

## Supported uploads and safety validation

The public upload contract accepts PDF; PNG, JPG/JPEG, JP2, WEBP, single-frame
GIF, BMP, single-page TIF/TIFF; and OOXML DOCX, PPTX, and XLSX. Legacy binary
Office formats (`.doc`, `.ppt`, `.xls`) and unknown formats are rejected. The
frontend `accept` contract and backend whitelist cover the same extensions.

Every saved upload is checked against its declared MIME type, extension, and
actual signature/decoder format before parsing. PDF validation rejects
encryption and excessive page counts. Image validation rejects format
spoofing, excessive pixels, animated GIF, and multi-page TIFF. JP2 and GIF are
normalized to a single-frame PNG only when PaddleOCR needs a compatibility
input; the original upload remains unchanged.

OOXML validation runs before MinerU and rejects malformed packages, encrypted
containers, path traversal, symbolic links, duplicate parts, unsafe or
external relationships, DTD/entity declarations, type spoofing, macros/active
content, nested archives, ZIP bombs, and documents beyond configured resource
limits. Relevant settings are:

- `BOOKCOURSE_MAX_UPLOAD_BYTES`, `BOOKCOURSE_MAX_PDF_PAGES`, and
  `BOOKCOURSE_MAX_IMAGE_PIXELS`.
- `BOOKCOURSE_MAX_ZIP_ENTRIES`,
  `BOOKCOURSE_MAX_ZIP_ENTRY_UNCOMPRESSED_BYTES`,
  `BOOKCOURSE_MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES`, and
  `BOOKCOURSE_MAX_ZIP_COMPRESSION_RATIO`.
- `BOOKCOURSE_MAX_OFFICE_XML_BYTES`,
  `BOOKCOURSE_MAX_OOXML_RELATIONSHIPS`, `BOOKCOURSE_MAX_PPTX_SLIDES`,
  `BOOKCOURSE_MAX_XLSX_SHEETS`, and `BOOKCOURSE_MAX_DOCX_RESOURCES`.
- `BOOKCOURSE_OFFICE_VALIDATION_TIMEOUT_SECONDS` for the bounded package
  inspection; `BOOKCOURSE_MINERU_TIMEOUT_SECONDS` remains the total upstream
  parse wait deadline.

Office files have no non-MinerU semantic fallback. A failed DOCX/PPTX/XLSX
MinerU task fails explicitly instead of sending an Office package into the
PDF or OCR parser. Source locations are represented as DOCX heading/block
positions, PPTX slide numbers/titles, and XLSX sheet names/ranges. The source
preview endpoint returns a safely escaped text SVG if no rendered Office page
image is available, and never invokes the PDF renderer for Office inputs.

## OCR Fallback

The default OCR provider is the complete `PaddleOCR-VL 1.6` layout + VLM
pipeline. It is used only for pages that
remain unusable after MinerU and, for PDFs, PyMuPDF. It is fail-closed: a
missing model/runtime or recognition error is reported as an unavailable OCR
attempt and is not silently replaced with fabricated text.

Install the supported OCR dependencies and enable Chinese recognition with:

```powershell
python -m pip install -e ".[test,ocr]"
$env:BOOKCOURSE_OCR_PROVIDER="paddleocr-vl"
$env:BOOKCOURSE_OCR_MODEL="PaddleOCR-VL-1.6"
$env:BOOKCOURSE_OCR_LANGUAGE="ch"
```

OCR settings:

- `BOOKCOURSE_OCR_PROVIDER`: `paddleocr-vl` (default and highest quality),
  `paddleocr` (lighter text-only fallback), or `mock` (tests only).
- `BOOKCOURSE_OCR_MODEL`: defaults to `PaddleOCR-VL-1.6`.
- `BOOKCOURSE_OCR_VL_PIPELINE_VERSION`: defaults to `v1.6`. The complete
  layout + recognition pipeline is required; a standalone VLM endpoint is not
  treated as equivalent.
- `BOOKCOURSE_OCR_VL_BACKEND`, `BOOKCOURSE_OCR_VL_SERVER_URL`, and
  `BOOKCOURSE_OCR_VL_API_KEY`: optional compatible VLM inference service.
- `BOOKCOURSE_OCR_VL_MAX_CONCURRENCY`: VLM sub-image request concurrency,
  default `4`.
- `BOOKCOURSE_OCR_LANGUAGE`: PaddleOCR language. Defaults to `ch`.
- `BOOKCOURSE_OCR_DEVICE`: Paddle device, default `cpu`. Keep PaddleOCR on CPU
  while the local MinerU service owns the CUDA GPU, unless a compatible Paddle
  GPU runtime has been installed and tested separately.
- `BOOKCOURSE_OCR_ENABLE_MKLDNN`: oneDNN acceleration switch. Defaults to
  `false`, which is the verified Windows-safe setting.
- `BOOKCOURSE_OCR_PROCESS_ISOLATION`: defaults to `true`. One persistent child
  process owns Paddle's models so native inference cannot block parse progress
  heartbeats or generation checks in the API process.
- `BOOKCOURSE_OCR_WORKER_STARTUP_TIMEOUT_SECONDS`: defaults to `120`.
- `BOOKCOURSE_OCR_RECOGNITION_TIMEOUT_SECONDS`: per-image hard deadline,
  default `300` for the structured VLM pipeline.
- `BOOKCOURSE_OCR_QUALITY_PROFILE`: `fast`, `balanced` (default), or `quality`.
  The profile selects a default PDF render scale of `1.5`, `2.0`, or `2.5`.
- `BOOKCOURSE_OCR_RENDER_ZOOM`: optional explicit override for the profile's
  render scale.
- `BOOKCOURSE_OCR_ADAPTIVE_RETRY`: retries the preprocessed image when source
  recognition has too little text or low confidence. Defaults to `true`.
- `BOOKCOURSE_OCR_TEXT_FALLBACK_ENABLED`: lets the PP-OCRv6 text specialist
  recover tiny or sparse text when the structured VL pass remains weak.
- `BOOKCOURSE_OCR_CACHE_ENABLED`, `BOOKCOURSE_OCR_CACHE_TTL_SECONDS`, and
  `BOOKCOURSE_OCR_CACHE_MAX_ITEMS`: content-addressed inference cache controls.
  Every cache key includes the image bytes, model, pipeline, device, language,
  page, and quality profile so stale model results cannot be mixed silently.
- `BOOKCOURSE_OCR_LOW_CONFIDENCE_THRESHOLD`: block-level low confidence threshold. Defaults to `0.55`.
- `BOOKCOURSE_OCR_TEXT_CONFIDENCE_THRESHOLD`: minimum confidence used for page text. Defaults to `0.5`.

`mock` emits only typed `ocr_pending` placeholders for deterministic tests.
Those pages fail the document quality gate, and `ocr_pending` blocks are
excluded from chunk generation, so Mock OCR content can never enter RAG.

Image preprocessing settings:

- `BOOKCOURSE_IMAGE_DARK_THRESHOLD`
- `BOOKCOURSE_IMAGE_BRIGHT_THRESHOLD`
- `BOOKCOURSE_IMAGE_BLUR_THRESHOLD`
- `BOOKCOURSE_IMAGE_DARK_BORDER_RATIO`
- `BOOKCOURSE_PREPROCESS_MEDIAN_KERNEL`
- `BOOKCOURSE_PREPROCESS_ADAPTIVE_BLOCK_SIZE`
- `BOOKCOURSE_PREPROCESS_ADAPTIVE_C`

Layout detection is exposed through `app.document.layout.LayoutService.detect_regions`. The MVP uses an OpenCV fallback adapter and writes `layout_regions.jsonl`; a Surya adapter can replace it without changing downstream artifacts.

## Image Generation API

AI-assisted illustrations are generated only through the backend adapter.

- `BOOKCOURSE_IMAGE_PROVIDER`: `mock` or `openai_compatible`. Defaults to `mock`.
- `BOOKCOURSE_IMAGE_API_URL`: compatible image generation endpoint.
- `BOOKCOURSE_IMAGE_API_KEY`: provider key, kept server-side only.

Source figures remain the primary strategy. AI images are stored as `source_type=ai_generated`, keep `source_chunk_ids`, and must be reviewed before display as final course illustrations.

## RAG Configuration

Chunker V2 counts the exact text sent to embeddings (heading path, overlap,
and body) with `BAAI/bge-m3` revision
`5617a9f61b028005a4858fdac845db406aefb181`. This protocol is intentionally
not configurable: `transformers==4.57.6` and `tokenizers==0.22.2` are exact
runtime dependencies, and tokenizer/version/load failures stop chunking rather
than silently changing persisted token counts.

The configurable Chunker V2 policy is:

- `BOOKCOURSE_CHUNK_TARGET_TOKENS`: target size, default `450`.
- `BOOKCOURSE_CHUNK_MAX_TOKENS`: ordinary hard maximum, default `700`.
- `BOOKCOURSE_CHUNK_MIN_TOKENS`: minimum size before short-tail handling,
  default `120`.
- `BOOKCOURSE_CHUNK_OVERLAP_TOKENS`: adjacent overlap, default `80`.
- `BOOKCOURSE_CHUNK_ATOMIC_CONTENT_HARD_MAX_TOKENS`: table/formula hard
  maximum, default `900`.
- `BOOKCOURSE_CHUNK_QUALITY_THRESHOLD`: default retrieval quality gate,
  default `0.45`.
- `BOOKCOURSE_CHUNK_VERSION`: persisted chunk protocol version, default `v2`.

Legacy `chunks.jsonl` records remain readable. Missing V2 fields are not
backfilled with invented provenance or quality values; legacy chunks remain
eligible unless explicitly quarantined, while V2 chunks require a recorded
quality score at or above the configured threshold.

- `BOOKCOURSE_EMBEDDING_PROVIDER`: `hashing` or `bge_m3`. Defaults to `hashing`.
- `BOOKCOURSE_BGE_M3_MODEL`: defaults to `BAAI/bge-m3`; its revision is frozen by `BOOKCOURSE_BGE_M3_REVISION`.
- `BOOKCOURSE_EMBEDDING_DEVICE`: use `cuda:0` for the locally verified BGE-M3 GPU path, or `auto` for portable deployments.
- `BOOKCOURSE_EMBEDDING_DIMENSIONS`: pgvector V2 requires `1024`. Hashing test/fallback vectors keep their own auditable provider identity and never claim to be BGE.
- `BOOKCOURSE_RAG_INDEX_PROVIDER`: `pgvector`, `faiss`, `chroma`, or `milvus`. Defaults to `pgvector`. If no database DSN is configured, the active provider is explicitly recorded as `artifact_fallback` with a reason; it is never reported as pgvector.
- `BOOKCOURSE_DATABASE_URL`: PostgreSQL connection string used by `PgVectorIndex`.
- `BOOKCOURSE_VECTOR_TOP_K`: dense retrieval candidate count. Defaults to `80`.
- `BOOKCOURSE_BM25_TOP_K`: lexical retrieval candidate count. Defaults to `80`.
- `BOOKCOURSE_RERANK_INPUT_K`: fused candidates sent to reranker. Defaults to `30`.
- `BOOKCOURSE_FINAL_CONTEXT_K`: final contexts sent to answer generation. Defaults to `5`.
- `BOOKCOURSE_RERANKER_PROVIDER`: `heuristic` or `bge`. Defaults to `heuristic`.
- `BOOKCOURSE_RERANKER_MODEL`: defaults to `BAAI/bge-reranker-v2-m3`.
- `BOOKCOURSE_RERANKER_FAIL_OPEN`: when `true`, logs and uses the heuristic
  fallback if BGE is unavailable; when `false`, the request fails visibly.
- `BOOKCOURSE_RAG_LLM_PROVIDER`: `template`, `openai_compatible`, or `deepseek`. Defaults to `template`.

DeepSeek can be used for both generated lessons and RAG answers. The API key must stay in server-side environment variables, not in source files:

```powershell
$env:BOOKCOURSE_LLM_PROVIDER="deepseek"
$env:BOOKCOURSE_RAG_LLM_PROVIDER="deepseek"
$env:BOOKCOURSE_DEEPSEEK_API_KEY="<your DeepSeek API key>"
$env:BOOKCOURSE_DEEPSEEK_LESSON_MODEL="deepseek-v4-pro"
$env:BOOKCOURSE_DEEPSEEK_RAG_MODEL="deepseek-v4-flash"
$env:BOOKCOURSE_DEEPSEEK_THINKING="disabled"
```

DeepSeek defaults:

- `BOOKCOURSE_DEEPSEEK_API_URL`: `https://api.deepseek.com/chat/completions`.
- `BOOKCOURSE_DEEPSEEK_MODEL`: `deepseek-v4-flash`.
- `BOOKCOURSE_DEEPSEEK_LESSON_MODEL` and `BOOKCOURSE_DEEPSEEK_RAG_MODEL`:
  optional workload-specific overrides. Use Pro for quality-sensitive
  asynchronous authoring and Flash for latency-sensitive interactive answers.
- `BOOKCOURSE_DEEPSEEK_THINKING`: `disabled` by default for low-latency course/RAG calls; set to `enabled` when you want DeepSeek thinking mode.

The generic OpenAI-compatible lesson adapter still supports:

- `BOOKCOURSE_LLM_PROVIDER=openai_compatible`
- `BOOKCOURSE_LLM_API_URL`
- `BOOKCOURSE_LLM_API_KEY`
- `BOOKCOURSE_LLM_MODEL`

All remote lesson and RAG model calls share a bounded runtime. Configure it
with `BOOKCOURSE_AI_MAX_CONCURRENT`, `BOOKCOURSE_AI_QUEUE_TIMEOUT_SECONDS`,
`BOOKCOURSE_AI_MAX_RETRIES`, retry backoff bounds, circuit-breaker threshold
and reset time, and `BOOKCOURSE_AI_MAX_RESPONSE_BYTES`. Explicit transient HTTP
responses are retried with jitter. Ambiguous POST transport failures are not
replayed unless `BOOKCOURSE_AI_RETRY_TRANSPORT_ERRORS=true`, which avoids
accidental duplicate billable generations. RAG answers use a short-lived exact
request cache; generated lesson JSON is still validated against the source
chunk IDs and page range before it can be accepted.
`BOOKCOURSE_LLM_MAX_INPUT_CHARS` and `BOOKCOURSE_LLM_MAX_OUTPUT_TOKENS`
provide hard request budgets so exceptionally long chapters cannot create
unbounded latency or model spend.

Retrieval is organized as BM25 + vector retrieval + RRF fusion + reranker. Both BM25 and embedding caches are keyed by the active index generation. A successful parse, chapter rebuild, or generated-asset backlink now replaces the book's pgvector rows before the task reports success. Database, embedding, count, or cache-barrier failures remain visible as `failed`, `committed`, or `cache_failed` and never masquerade as a ready index.

Install optional RAG dependencies:

```powershell
python -m pip install -e ".[test,rag]"
```

Enable PostgreSQL/pgvector:

```powershell
$env:BOOKCOURSE_DATABASE_URL="postgresql://user:password@localhost:5432/bookcourse"
$env:BOOKCOURSE_RAG_INDEX_PROVIDER="pgvector"
$env:BOOKCOURSE_EMBEDDING_PROVIDER="bge_m3"
$env:BOOKCOURSE_EMBEDDING_DEVICE="cuda:0"
$env:BOOKCOURSE_RERANKER_PROVIDER="bge"
```

The pgvector schema entry point is `app.rag.index_pgvector.PGVECTOR_SCHEMA_SQL`, backed by `migrations/001_pgvector_v2.sql`. Normal application requests **do not run migrations automatically**. Apply that migration only through an approved database-change procedure with a declared target, backup, rollback, and impact scope. If the schema is absent, indexing fails explicitly instead of mutating the database.

Operational state is available from `GET /api/books/{book_id}/rag-index-status`. A generation left in `committed` or `cache_failed` after a cache barrier failure can be finalized with `POST /api/books/{book_id}/rag-index/retry`; the retry reuses the same generation and does not re-embed the book.

## Parser Configuration

The default route is MinerU-first and operates at page granularity:

1. The complete supported document is submitted to the local MinerU HTTP API.
2. Accepted MinerU pages, structured blocks, tables, formulas, and source
   images are preserved.
3. Only missing or low-quality PDF pages are retried with PyMuPDF's text-layer
   extraction.
4. Any PDF pages still unusable are rendered and retried with PaddleOCR. For
   image uploads, the fallback goes directly from MinerU to PaddleOCR.
5. Page results are merged once and written through the normalized artifact
   writer; a fallback cannot overwrite a good MinerU page.

The `DocumentParser` interface exposes these providers:

- `pymupdf`: local PDF text-layer extraction.
- `ocr`: real PaddleOCR fallback plus the configured layout adapter.
- `marker`: legacy external adapter, available only when selected explicitly.
- `mineru`: local protocol-v2 HTTP client plus the MinerU result mapper.

Settings:

- `BOOKCOURSE_PARSER_PROVIDER`: `mineru` (default), `auto`, `pymupdf`,
  `marker`, or `ocr`. `auto` currently uses the same MinerU-first route;
  explicit non-MinerU values are intended for rollback and diagnosis.
- `BOOKCOURSE_MARKER_COMMAND` / `BOOKCOURSE_MARKER_ENDPOINT`: optional Marker integration entry points.
- `BOOKCOURSE_MINERU_ENDPOINT`: local MinerU origin. It defaults to
  `http://127.0.0.1:8001` and is restricted to an explicit loopback origin.
- `BOOKCOURSE_LAYOUT_PROVIDER`: `opencv` by default, with `surya` reserved for a production layout adapter.

Each parse writes `parser_report.json`, including requested/final parser,
generation, every document/page attempt, duration, typed error, final per-page
decision, quality score, warnings, and missing pages. A document with an
unrecoverable page, page quality below `0.60`, or average quality below `0.75`
is not chunked or indexed.

### MinerU HTTP lifecycle and generation safety

The synchronous MinerU protocol-v2 client is connected to the default parser
router. It supports `/health`, task submission, persisted status polling,
result retrieval, bounded safe-method retries, total deadlines, response-size
limits, and restart recovery.

The local CUDA service can be exercised independently (replace `sample.pdf`
with a real path):

```powershell
$env:BOOKCOURSE_MINERU_ENDPOINT="http://127.0.0.1:8001"
python -c "from pathlib import Path; from app.document.mineru import MinerUClient, MinerUClientConfig; c=MinerUClient(MinerUClientConfig.from_settings()); print(c.execute(book_id='standalone_probe', file_path=Path('sample.pdf')).task.status); c.close()"
```

Relevant settings are listed in `.env.example`. Important lifecycle constraints:

- The parse API reserves one monotonically increasing generation before it
  queues work, binds that generation to one CloudPath job, and resumes the
  persisted MinerU task ID instead of uploading the source again.
- A stale MinerU generation is propagated before the router can enter a
  fallback path; mapping and every artifact publication boundary also verify
  the active generation. A late obsolete result cannot replace newer
  artifacts.
- Normalized parse artifacts are published under a local compare-and-publish
  lock. This protection is process-local; multiple backend worker processes
  require a shared database lock before production enablement.
- MinerU 3.4.4 has no cancel endpoint and no upstream idempotency lookup. A CloudPath timeout stops waiting only; the GPU task can continue.
- An ambiguous `POST /tasks` response is persisted as `submission_uncertain` and is never automatically resent.
- Once a task ID exists, later calls resume that ID rather than upload the document again.
- MinerU result responses are capped by `BOOKCOURSE_MINERU_MAX_RESULT_BYTES`; returned URLs are ignored, and all status/result requests are rebuilt from the configured loopback endpoint.

## Main Artifacts

The parse pipeline stores each book under
`BOOKCOURSE_STORAGE_ROOT/books/{book_id}`. Its core layout is:

- `original/{sanitized_filename}`
- `artifacts/scan_result.json`
- `artifacts/parser_report.json`
- `artifacts/pages.json`
- `artifacts/text_blocks.jsonl`
- `artifacts/layout_regions.jsonl`
- `artifacts/mineru_middle.json` (when MinerU returns `middle_json`)
- `artifacts/mineru_content_list.json` (when MinerU returns a content list)
- `artifacts/chapters.json`
- `artifacts/chapters_original.json`
- `artifacts/chunks.jsonl`
- `artifacts/assets.json`
- `artifacts/preprocessed/*` (only for pages sent through OCR)
- `assets/*` (validated MinerU/source images and thumbnails)

`pages.json`, `text_blocks.jsonl`, and `layout_regions.jsonl` are the merged,
normalized evidence consumed downstream. The raw MinerU JSON files are kept
for traceability; RAG reads normalized `chunks.jsonl`, not the raw response.
`artifacts/page_images/*` is generated lazily by the page-image API, and
`artifacts/rag_audit.jsonl` is appended later when RAG queries run; neither is
a mandatory parse output.

## Judge Verification

1. Start the backend and frontend.
2. Upload a text PDF, scanned PDF, or image page.
3. Run parse and inspect `scan_result.json`, `pages.json`, `chunks.jsonl`, and `assets.json`.
4. Verify source figures are `source_type=extracted` with page and bbox.
5. Trigger AI figure generation and verify the generated asset is `source_type=ai_generated`.
6. Call `/api/rag/query` and confirm citations include score and source metadata.
7. Submit and diagnose an assignment; only clear misconceptions should enter `/api/users/{user_id}/mistakes`.
8. Create a study plan, patch a task with `score < 60`, and confirm a review task with `adjustment_reason=quiz_score_below_60`.
