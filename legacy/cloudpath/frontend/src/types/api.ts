export type ApiErrorPayload = {
  code: string;
  message: string;
  details?: Record<string, unknown>;
};

export type UploadInitRequest = {
  filename: string;
  content_type?: string;
  size_bytes?: number;
};

export type UploadInitResponse = {
  book_id: string;
  upload_url: string;
  max_upload_bytes: number;
};

export type FileSaveResponse = {
  book_id: string;
  filename: string;
  size_bytes: number;
  status: string;
};

export type ParseJobResponse = {
  book_id: string;
  job_id: string;
  status: string;
};

export type JobStatusResponse = {
  job_id: string;
  book_id: string;
  status: "pending" | "processing" | "done" | "failed";
  stage: string;
  progress: number;
  message?: string | null;
  error?: string | null;
};

export type RuntimeCapabilities = {
  parser_provider: string;
  ocr_provider: string;
  ocr_model: string;
  ocr_device: string;
  ocr_quality_profile: "fast" | "balanced" | "quality" | string;
  ocr_cache_enabled: boolean;
  ocr_adaptive_retry: boolean;
  ocr_text_fallback_enabled: boolean;
  mineru_backend: string;
  mineru_effort: string;
  rag_index_provider: string;
  rag_answer_cache_enabled?: boolean;
  rag_answer_cache_ttl_seconds?: number;
  rag_answer_cache_max_items?: number;
  rag_answer_cache?: Record<string, unknown>;
  embedding_provider: string;
  reranker_provider: string;
  reranker_fail_open: boolean;
  lesson_provider: string;
  rag_answer_provider: string;
  llm_model: string;
  lesson_model: string;
  rag_answer_model: string;
  ai_max_concurrent: number;
  ai_max_retries: number;
  ai_circuit_failure_threshold: number;
  ai_runtime: Array<Record<string, unknown>>;
  image_provider: string;
  worker_enabled: boolean;
};

export type ScanResult = {
  book_id: string;
  filename: string;
  file_type: string;
  page_count: number;
  has_text_layer: boolean;
  needs_ocr: boolean;
  source_unit: "page" | "image" | "document" | "slide" | "sheet" | string;
  source_locations: Array<Record<string, unknown>>;
  quality_warnings: Array<{
    page?: number | null;
    code: string;
    message: string;
  }>;
};

export type ApiChapter = {
  chapter_id: string;
  level: number;
  source_title: string;
  ai_title: string;
  page_start: number;
  page_end: number;
  printed_page_start?: number | null;
  printed_page_end?: number | null;
  confidence: number;
  status: string;
  source: string;
  parent_id?: string | null;
};

export type ChapterUpdate = Partial<Pick<ApiChapter, "source_title" | "ai_title" | "level" | "parent_id" | "page_start" | "page_end" | "status">>;

export type TocPageCandidate = {
  page: number;
  score: number;
  line_count: number;
  reasons: string[];
  sample_lines: string[];
};

export type PageMapEntry = {
  pdf_page: number;
  printed_page?: number | null;
  confidence: number;
  source: string;
  evidence?: string | null;
};

export type ChapterEvidence = {
  chapter_id: string;
  source_title: string;
  level: number;
  printed_page_start?: number | null;
  pdf_page_start: number;
  pdf_page_end: number;
  toc_line_confidence: number;
  page_map_confidence: number;
  title_match_page?: number | null;
  title_match_score: number;
  confidence: number;
  status: string;
  reasons: string[];
};

export type TocAnalysis = {
  book_id: string;
  status: string;
  toc_pages: TocPageCandidate[];
  page_map: PageMapEntry[];
  chapter_evidence: ChapterEvidence[];
  warnings: string[];
};

export type CourseSummary = {
  book_id: string;
  title: string;
  author?: string | null;
  source_catalog_id?: string | null;
  cover_url?: string | null;
  filename?: string | null;
  status: string;
  page_count: number;
  chapter_count: number;
  chunk_count: number;
  asset_count: number;
  average_confidence: number;
  next_title?: string | null;
  rag_index_status?: string | null;
  rag_index_provider?: string | null;
  rag_index_generation?: number | null;
  rag_fallback_reason?: string | null;
  parse_job_id?: string | null;
  parse_job_status?: JobStatusResponse["status"] | null;
  parse_job_stage?: string | null;
  parse_job_progress?: number | null;
  parse_job_message?: string | null;
  parse_job_error?: string | null;
  updated_at: number;
};

export type CommunityBookSummary = {
  id: string;
  title: string;
  catalog_title: string;
  author: string;
  cover: string;
  subject: string;
  level: string;
  language: string;
  edition: string;
  page_count: number;
  file_size_bytes: number;
  source_page_url: string;
  license_name: string;
  license_url: string;
  rights_notice: string;
  description: string;
  chapters: string[];
  tags: string[];
  server_cached: boolean;
  imported_book_id?: string | null;
};

export type CommunityImportResponse = {
  catalog_id: string;
  book_id: string;
  filename: string;
  size_bytes: number;
  status: string;
  already_imported: boolean;
};

export type AssistantHistoryMessage = {
  role: "user" | "assistant";
  content: string;
};

export type AssistantChatRequest = {
  message: string;
  history?: AssistantHistoryMessage[];
  context_title?: string | null;
  context_description?: string | null;
};

export type AssistantChatResponse = {
  answer: string;
  provider: string;
  model: string;
};

export type AssetSourceType = "extracted" | "ai_generated";

export type ExtractedAsset = {
  asset_id: string;
  book_id: string;
  chapter_id?: string | null;
  source_type: "extracted";
  page: number;
  type: string;
  caption: string;
  bbox: number[];
  image_url: string;
  thumbnail_url: string;
  source_page_image_url: string;
  source_chunk_ids: string[];
  concepts: string[];
  review_status?: string | null;
  source_parser?: string | null;
  content_hash?: string | null;
  metadata?: Record<string, unknown>;
};

export type AiGeneratedAsset = {
  asset_id: string;
  book_id: string;
  chapter_id?: string | null;
  source_type: "ai_generated";
  page?: null;
  type: string;
  caption: string;
  bbox?: null;
  image_url: string;
  thumbnail_url: string;
  source_page_image_url?: null;
  source_chunk_ids: string[];
  concepts: string[];
  generation_provider: string;
  review_status: string;
  source_parser?: string | null;
  content_hash?: string | null;
  metadata?: Record<string, unknown>;
};

export type ApiAsset = ExtractedAsset | AiGeneratedAsset;

export type ApiChunk = {
  chunk_id: string;
  book_id: string;
  chapter_id: string;
  page_start: number;
  page_end: number;
  printed_page_start?: number | null;
  printed_page_end?: number | null;
  content_type: string;
  text: string;
  asset_ids: string[];
  key_concepts: string[];
  source_metadata?: Record<string, unknown>;
  source_entries?: Array<Record<string, unknown>>;
};

export type LessonCitation = {
  chunk_id: string;
  page_start: number;
  page_end: number;
  printed_page_start?: number | null;
  printed_page_end?: number | null;
  quote?: string | null;
  source_metadata?: Record<string, unknown>;
};

export type LessonBlock = {
  block_id: string;
  block_type: string;
  title: string;
  content: string;
  citations: LessonCitation[];
  source_chunk_ids: string[];
  asset_ids: string[];
  ai_generated: boolean;
};

export type Lesson = {
  book_id: string;
  lesson_id: string;
  chapter_id: string;
  title: string;
  source_title: string;
  page_start: number;
  page_end: number;
  lesson_kind: "lesson" | "module_intro" | string;
  status: string;
  confidence: number;
  objectives: string[];
  key_concepts: string[];
  summary: string;
  blocks: LessonBlock[];
  source_chunk_ids: string[];
  asset_ids: string[];
  warnings: string[];
};

export type LessonBuildRequest = {
  chapter_ids?: string[] | null;
  force?: boolean;
};

export type LessonBuildChapterResult = {
  chapter_id: string;
  chapter_title: string;
  status: "pending" | "processing" | "done" | "container" | "skipped" | "failed" | string;
  reason?: string | null;
  lesson_kind?: "lesson" | "module_intro" | string | null;
  lesson_id?: string | null;
  message?: string | null;
};

export type LessonBuildJobResponse = {
  job_id: string;
  book_id: string;
  status: "pending" | "processing" | "done" | "done_with_warnings" | "failed";
  stage: string;
  progress: number;
  lessons: Lesson[];
  chapter_results: LessonBuildChapterResult[];
  error?: string | null;
};

export type Flashcard = {
  card_id: string;
  book_id: string;
  lesson_id: string;
  chapter_id: string;
  front: string;
  back: string;
  concept: string;
  source_chunk_ids: string[];
  page_start: number;
  page_end: number;
  printed_page_start?: number | null;
  printed_page_end?: number | null;
  source_metadata?: Record<string, unknown>;
  due: string;
  mastery: number;
  reason: string;
};

export type QuizQuestion = {
  question_id: string;
  book_id: string;
  lesson_id: string;
  chapter_id: string;
  prompt: string;
  choices: string[];
  answer: string;
  explanation: string;
  concept: string;
  source_chunk_ids: string[];
  page_start: number;
  page_end: number;
  printed_page_start?: number | null;
  printed_page_end?: number | null;
  source_metadata?: Record<string, unknown>;
};

export type ImageGenerationRequest = {
  book_id: string;
  lesson_id?: string | null;
  chapter_id?: string | null;
  concepts: string[];
  style: string;
  purpose: string;
  source_chunk_ids: string[];
};

export type ImageGenerationJobResponse = {
  job_id: string;
  book_id: string;
  status: "pending" | "processing" | "done" | "failed";
  stage: string;
  progress: number;
  asset?: ApiAsset | null;
  error?: string | null;
};

export type Citation = {
  chapter_id: string;
  chapter_title: string;
  page: number;
  chunk_id: string;
  quote: string;
  score: number;
  retrieval_method: string;
  source_type: string;
  location_type?: "page" | "document" | "slide" | "sheet" | string;
  location_label?: string | null;
  source_metadata: Record<string, unknown>;
};

export type RagQuery = {
  book_id: string;
  chapter_id?: string | null;
  question: string;
  history?: Array<Record<string, unknown>>;
};

export type RagResponse = {
  answer: string;
  citations: Citation[];
  related_assets: ApiAsset[];
  confidence: "low" | "medium" | "high" | string;
  performance?: {
    total_ms: number;
    retrieval_ms: number;
    generation_ms: number;
    response_cache_hit: boolean;
    retrieval_cache: string;
    retrieved_chunks: number;
  } | null;
};

export type AssignmentSubmitRequest = {
  user_id?: string;
  book_id: string;
  lesson_id?: string | null;
  chapter_id?: string | null;
  question: string;
  answer: string;
};

export type AssignmentSubmitResponse = {
  assignment_id: string;
  submission_id: string;
  status: string;
};

export type DiagnosisResponse = {
  assignment_id: string;
  submission_id: string;
  result: string;
  stuck_point: string;
  knowledge_points: string[];
  review_citations: Citation[];
  related_assets: ApiAsset[];
  hint: string;
  needs_followup: boolean;
  followup_question?: string | null;
  mistake_recorded: boolean;
};

export type MistakeRecord = {
  mistake_id: string;
  user_id: string;
  book_id: string;
  assignment_id: string;
  question: string;
  answer: string;
  stuck_point: string;
  knowledge_points: string[];
  citation_ids: string[];
  correct_answer?: string | null;
  explanation?: string | null;
  mistake_reason?: "knowledge_gap" | "misread" | "calculation" | "method_unfamiliar" | "unknown" | null;
  error_count?: number | null;
  last_wrong_at?: string | null;
  next_review_at?: string | null;
  mastery?: "due" | "learning" | "repeated" | "mastered" | null;
};

export type StudyTask = {
  task_id: string;
  user_id: string;
  day: number;
  title: string;
  task_type: string;
  minutes: number;
  chapter_id?: string | null;
  lesson_id?: string | null;
  review_target?: string | null;
  status: string;
  score?: number | null;
  weak_points: string[];
  adjustment_reason?: string | null;
};

export type StudyPlan = {
  user_id: string;
  book_id: string;
  days: number;
  daily_minutes: number;
  tasks: StudyTask[];
};

export type StudyPlanRequest = {
  user_id?: string;
  days?: number;
  daily_minutes?: number;
};

export type StudyTaskUpdate = {
  status?: string;
  score?: number | null;
  weak_points?: string[];
};

export type LearningState = {
  user_id: string;
  completed_tasks: number;
  pending_tasks: number;
  average_score?: number | null;
  weak_points: string[];
  mistake_count: number;
};
