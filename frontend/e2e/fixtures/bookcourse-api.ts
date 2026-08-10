import { env } from "node:process";
import type { Page, Route } from "playwright/test";
import type { ApiAsset, ApiChapter, CourseSummary, Flashcard, QuizQuestion, StudyTask } from "../../src/types/api";

type ApiRequest = {
  body: unknown;
  method: string;
  path: string;
};

type FixtureResponse = {
  status: number;
  body: unknown;
  contentType?: string;
  rawBody?: boolean;
};

export type StageFiveImageMode = "success" | "failure" | "mixed";
export type StageSixMistakeMode = "success" | "error" | "loading";
export type StageSixMistakeSet = "default" | "detail_pair";
export type StageSixPlanTaskMode = "sparse" | "out_of_range";
export type StageSixFlowOptions = {
  mistakeMode?: StageSixMistakeMode;
  mistakeSet?: StageSixMistakeSet;
  studyPlanDays?: unknown;
  taskMode?: StageSixPlanTaskMode;
};

export type ProcessingMotionFlowOptions = {
  jobIds?: string[];
  progressSequence?: number[];
};

export type BookCourseApiFixture = {
  requests: ApiRequest[];
  unhandledRequests: ApiRequest[];
  externalRequests: string[];
  consoleErrors: string[];
  pageErrors: string[];
  lastStageFourConfirmationResponse: ApiChapter[] | null;
  appendPreparedCourse: () => CourseSummary;
  setPreparedImageMode: (mode: StageFiveImageMode) => void;
  usePreparedCourse: () => void;
  useStageFiveFlow: (options?: { imageMode?: StageFiveImageMode }) => void;
  useStageSixFlow: (options?: StageSixFlowOptions) => void;
  releaseStageSixMistakes: () => void;
  useStageFourFlow: (options?: { mode?: "success" | "failed"; progress?: number }) => void;
  useProcessingMotionFlow: (options?: ProcessingMotionFlowOptions) => void;
};

const applicationOrigin = `http://127.0.0.1:${env.E2E_PORT ?? "4173"}`;
const stageThreeBookId = "book_stage3";
const stageThreeJobId = "job_stage3";
const stageFourBookId = "book_stage4";
const stageFourJobId = "job_stage4";
const processingMotionBookId = "book_processing_motion";
const defaultProcessingMotionJobIds = ["job_processing_motion_a", "job_processing_motion_b"];
const defaultProcessingMotionProgressSequence = [0, 1, 50, 50, 99, 100];
const stageFourLessonJobId = "lesson_job_stage4";
const stageFourLongFilename = `${"biology-reference-".repeat(10)}chapter-confirmation.pdf`;
const stageFourLongChapterTitle = `${"Long chapter title for responsive wrapping ".repeat(5)}alpha`;
const stageFourLongError = `${"The parser could not read this unusually long diagnostic message. ".repeat(5)}Retry after checking the source file.`;
const stageFiveLongCourseTitle = `${"Long Stage Five biology course title for stable two-line cards ".repeat(3)}with a deliberate ending`;

const stageThreeCourse = {
  book_id: stageThreeBookId,
  title: "阶段 3 测试教材",
  filename: "stage3-biology.pdf",
  status: "ready",
  page_count: 4,
  chapter_count: 1,
  chunk_count: 1,
  asset_count: 1,
  average_confidence: 96,
  next_title: "细胞分裂",
  rag_index_status: "ready",
  rag_index_provider: "fixture",
  rag_index_generation: 1,
  rag_fallback_reason: null,
  parse_job_id: null,
  parse_job_status: null,
  parse_job_stage: null,
  parse_job_progress: null,
  parse_job_message: null,
  parse_job_error: null,
  updated_at: 1
};

const stageFiveLibraryCourses: CourseSummary[] = [
  stageThreeCourse,
  {
    ...stageThreeCourse,
    book_id: "book_stage5_long_title",
    title: stageFiveLongCourseTitle,
    filename: "stage-five-long-title-textbook.pdf",
    chapter_count: 12,
    chunk_count: 42,
    asset_count: 6,
    next_title: "Long next lesson title remains readable in a stable card",
    updated_at: 2
  },
  {
    ...stageThreeCourse,
    book_id: "book_stage5_cover",
    title: "Stage Five cover and library fixture",
    filename: "stage-five-cover.pdf",
    chapter_count: 7,
    chunk_count: 28,
    asset_count: 3,
    next_title: "Open the course overview",
    updated_at: 3
  }
];

const stageThreeScanResult = {
  book_id: stageThreeBookId,
  filename: "stage3-biology.pdf",
  file_type: "pdf",
  page_count: 4,
  has_text_layer: true,
  needs_ocr: false,
  source_unit: "page",
  source_locations: [],
  quality_warnings: []
};

const stageThreeChapters = [{
  chapter_id: "chapter_stage3",
  level: 1,
  source_title: "细胞分裂",
  ai_title: "细胞分裂基础",
  page_start: 1,
  page_end: 4,
  confidence: 96,
  status: "匹配良好",
  source: "fixture",
  parent_id: null
}];

const stageThreeChunks = [{
  chunk_id: "chunk_stage3",
  book_id: stageThreeBookId,
  chapter_id: "chapter_stage3",
  page_start: 1,
  page_end: 2,
  content_type: "paragraph",
  text: "细胞分裂是生物体生长与繁殖的基础。",
  asset_ids: ["asset_stage3"],
  key_concepts: ["同源染色体"]
}];

const stageThreeAssets: ApiAsset[] = [{
  asset_id: "asset_stage3",
  book_id: stageThreeBookId,
  chapter_id: "chapter_stage3",
  source_type: "extracted",
  page: 3,
  type: "diagram",
  caption: "细胞分裂示意图",
  bbox: [0, 0, 100, 100],
  image_url: `/api/books/${stageThreeBookId}/pages/3/image`,
  thumbnail_url: `/api/books/${stageThreeBookId}/pages/3/image`,
  source_page_image_url: `/api/books/${stageThreeBookId}/pages/3/image`,
  source_chunk_ids: ["chunk_stage3"],
  concepts: ["同源染色体"],
  review_status: "approved"
}];

const stageThreeLessons = [{
  book_id: stageThreeBookId,
  lesson_id: "lesson_stage3",
  chapter_id: "chapter_stage3",
  title: "细胞分裂基础",
  source_title: "细胞分裂",
  page_start: 1,
  page_end: 4,
  lesson_kind: "lesson",
  status: "ready",
  confidence: 96,
  objectives: ["理解细胞分裂的基本过程"],
  key_concepts: ["同源染色体"],
  summary: "阶段 3 的本地课程 fixture。",
  blocks: [{
    block_id: "block_stage3",
    block_type: "explanation",
    title: "核心过程",
    content: "同源染色体在减数第一次分裂中分离。",
    citations: [{
      chunk_id: "chunk_stage3",
      page_start: 1,
      page_end: 2,
      quote: "同源染色体在减数第一次分裂中分离。"
    }],
    source_chunk_ids: ["chunk_stage3"],
    asset_ids: ["asset_stage3"],
    ai_generated: false
  }],
  source_chunk_ids: ["chunk_stage3"],
  asset_ids: ["asset_stage3"],
  warnings: []
}];

const stageThreeStudyPlan = {
  user_id: "responsive_fixture_user",
  book_id: stageThreeBookId,
  days: 1,
  daily_minutes: 20,
  tasks: []
};

const stageThreeFlashcards: Flashcard[] = [{
  card_id: "card_stage3",
  book_id: stageThreeBookId,
  lesson_id: "lesson_stage3",
  chapter_id: "chapter_stage3",
  front: "什么是同源染色体？",
  back: "一对来源不同但形态和基因位点相对应的染色体。",
  concept: "同源染色体",
  source_chunk_ids: ["chunk_stage3"],
  page_start: 1,
  page_end: 2,
  due: "today",
  mastery: 48,
  reason: "来自课程核心概念"
}];

const stageSixFlashcards: Flashcard[] = [
  ...stageThreeFlashcards.map((card) => ({
    ...card,
    source_chunk_ids: [...card.source_chunk_ids]
  })),
  {
    ...stageThreeFlashcards[0],
    card_id: "card_stage6_2",
    front: "减数第一次分裂中同源染色体发生什么变化？",
    back: "同源染色体彼此分离，并分别进入不同的子细胞。",
    concept: "减数第一次分裂",
    due: "soon",
    mastery: 62,
    reason: "来自课程中的分离过程证据",
    source_chunk_ids: ["chunk_stage3"]
  }
];

const stageThreeQuizzes: QuizQuestion[] = [{
  question_id: "quiz_stage3",
  book_id: stageThreeBookId,
  lesson_id: "lesson_stage3",
  chapter_id: "chapter_stage3",
  prompt: "同源染色体何时分离？",
  choices: ["减数第一次分裂", "有丝分裂末期", "DNA 复制前", "受精后"],
  answer: "减数第一次分裂",
  explanation: "同源染色体会在减数第一次分裂中彼此分离。",
  concept: "同源染色体",
  source_chunk_ids: ["chunk_stage3"],
  page_start: 1,
  page_end: 2
}];

type StageSixFixtureStudyPlan = {
  user_id: string;
  book_id: string;
  days: unknown;
  daily_minutes: number;
  tasks: StudyTask[];
};

const stageSixSparseStudyPlan: StageSixFixtureStudyPlan = {
  user_id: "responsive_fixture_user",
  book_id: stageThreeBookId,
  days: 7,
  daily_minutes: 30,
  tasks: [1, 2].map((day) => {
    return {
      task_id: `task_stage6_${day}`,
      user_id: "responsive_fixture_user",
      day,
      title: `Stage 6 review task for day ${day}`,
      task_type: "review",
      minutes: 20 + day,
      lesson_id: "lesson_stage3",
      review_target: "chapter_stage3",
      status: day === 2 ? "done" : "pending",
      score: day === 2 ? 88 : null,
      weak_points: [],
      adjustment_reason: null
    };
  })
};

const stageSixOutOfRangeTask: StudyTask = {
  task_id: "task_stage6_out_of_range",
  user_id: "responsive_fixture_user",
  day: 4_294_967_297,
  title: "Stage 6 out-of-range review task",
  task_type: "review",
  minutes: 20,
  lesson_id: "lesson_stage3",
  review_target: "chapter_stage3",
  status: "pending",
  score: null,
  weak_points: [],
  adjustment_reason: null
};

const stageSixMistakes = [{
  mistake_id: "mistake_stage6_1",
  user_id: "responsive_fixture_user",
  book_id: stageThreeBookId,
  assignment_id: "assignment_chapter_stage3",
  question: "Explain the Stage 6 fixture concept using the cited source.",
  answer: "The fixture answer needs a more specific explanation.",
  stuck_point: "Distinguishing source evidence from the final conclusion.",
  knowledge_points: ["homologous chromosomes", "meiosis"],
  citation_ids: ["chunk_stage3"]
}];

// The default Stage 6 response intentionally remains one record for the
// existing acceptance flow. Stage 4C opts into this second real record only
// when it needs to verify that a master list keeps its identity while the
// selected detail pane changes.
const stageSixMistakeDetailPair = [
  ...stageSixMistakes,
  {
    ...stageSixMistakes[0],
    mistake_id: "mistake_stage6_2",
    question: "Explain the inherited-trait rule using the cited source.",
    answer: "The rule was named but not connected to the observed trait ratio.",
    stuck_point: "Connecting the cited evidence to the genetics rule.",
    knowledge_points: ["遗传规律"],
    citation_ids: ["chunk_stage3"]
  }
];

const stageSixDiagnosis = {
  assignment_id: "assignment_chapter_stage3",
  submission_id: "submission_stage6",
  result: "The answer identifies the topic but needs to connect the conclusion to the cited source evidence.",
  stuck_point: "Explain why homologous chromosomes separate during the first meiotic division.",
  knowledge_points: ["homologous chromosomes", "meiosis"],
  review_citations: [{
    chapter_id: "chapter_stage3",
    chapter_title: "Stage 3 fixture chapter",
    page: 1,
    chunk_id: "chunk_stage3",
    quote: "Fixture source evidence for the Stage 6 diagnosis.",
    score: 0.98,
    retrieval_method: "fixture",
    source_type: "page",
    location_type: "page",
    location_label: "Page 1",
    source_metadata: {}
  }],
  related_assets: [stageThreeAssets[0]],
  hint: "Use the cited page to explain the sequence before stating the conclusion.",
  needs_followup: true,
  followup_question: "Which event in the source supports your conclusion?",
  mistake_recorded: true
};

const stageSixLearningState = {
  user_id: "responsive_fixture_user",
  completed_tasks: 2,
  pending_tasks: 5,
  average_score: 88,
  weak_points: ["homologous chromosomes"],
  mistake_count: 1
};

const stageFourCourse = {
  ...stageThreeCourse,
  book_id: stageFourBookId,
  title: "Stage 4 responsive flow",
  filename: stageFourLongFilename,
  status: "processing",
  page_count: 12,
  chapter_count: 8,
  chunk_count: 1,
  average_confidence: 95,
  next_title: stageFourLongChapterTitle,
  parse_job_id: stageFourJobId,
  parse_job_status: "processing",
  parse_job_stage: "indexing",
  parse_job_progress: 0,
  parse_job_message: "Stage 4 parser is running in the background.",
  parse_job_error: null
};

const stageFourScanResult = {
  ...stageThreeScanResult,
  book_id: stageFourBookId,
  filename: stageFourLongFilename,
  page_count: 12
};

const stageFourChapters: ApiChapter[] = Array.from({ length: 8 }, (_, index) => ({
  chapter_id: index === 0 ? "chapter_stage4_primary" : `chapter_stage4_${index + 1}`,
  level: index === 2 ? 2 : 1,
  source_title: index === 0 ? stageFourLongChapterTitle : `Stage 4 chapter ${index + 1}`,
  ai_title: index === 0 ? "Responsive source review" : `Generated lesson ${index + 1}`,
  page_start: index + 1,
  page_end: index === 1 ? 3 : index + 1,
  confidence: 96 - index,
  status: "匹配良好",
  source: "stage-four-fixture",
  parent_id: index === 2 ? "chapter_stage4_2" : null
}));

const stageFourChunks = [{
  ...stageThreeChunks[0],
  chunk_id: "chunk_stage4",
  book_id: stageFourBookId,
  chapter_id: "chapter_stage4_primary",
  page_start: 1,
  page_end: 1,
  text: "Stage 4 fixture content for the responsive course generation flow."
}];

const stageFourLessons = [{
  ...stageThreeLessons[0],
  book_id: stageFourBookId,
  lesson_id: "lesson_stage4",
  chapter_id: "chapter_stage4_primary",
  title: "Responsive source review",
  source_title: stageFourLongChapterTitle,
  page_start: 1,
  page_end: 1,
  source_chunk_ids: ["chunk_stage4"],
  blocks: [{
    ...stageThreeLessons[0].blocks[0],
    block_id: "block_stage4",
    source_chunk_ids: ["chunk_stage4"],
    citations: [{
      ...stageThreeLessons[0].blocks[0].citations[0],
      chunk_id: "chunk_stage4",
      page_start: 1,
      page_end: 1
    }]
  }]
}];

const stageFourStudyPlan = {
  ...stageThreeStudyPlan,
  book_id: stageFourBookId
};

const stageFourTocAnalysis = {
  book_id: stageFourBookId,
  status: "ready",
  toc_pages: [{ page: 1, score: 98, line_count: stageFourChapters.length, reasons: ["fixture"], sample_lines: [stageFourLongChapterTitle] }],
  page_map: stageFourChapters.map((chapter) => ({
    pdf_page: chapter.page_start,
    printed_page: chapter.page_start,
    confidence: 96,
    source: "fixture"
  })),
  chapter_evidence: stageFourChapters.map((chapter) => ({
    chapter_id: chapter.chapter_id,
    source_title: chapter.source_title,
    level: chapter.level,
    printed_page_start: chapter.page_start,
    pdf_page_start: chapter.page_start,
    pdf_page_end: chapter.page_end,
    toc_line_confidence: 98,
    page_map_confidence: 96,
    title_match_page: chapter.page_start,
    title_match_score: 97,
    confidence: chapter.confidence,
    status: chapter.status,
    reasons: ["Stage 4 fixture evidence"]
  })),
  warnings: []
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStageFourChapter(value: unknown): value is ApiChapter {
  if (!isRecord(value)) return false;
  return typeof value.chapter_id === "string"
    && value.chapter_id.length > 0
    && typeof value.level === "number"
    && Number.isInteger(value.level)
    && value.level >= 1
    && typeof value.source_title === "string"
    && value.source_title.trim().length > 0
    && typeof value.ai_title === "string"
    && value.ai_title.trim().length > 0
    && typeof value.page_start === "number"
    && Number.isInteger(value.page_start)
    && value.page_start >= 1
    && typeof value.page_end === "number"
    && Number.isInteger(value.page_end)
    && value.page_end >= value.page_start
    && typeof value.confidence === "number"
    && Number.isFinite(value.confidence)
    && typeof value.status === "string"
    && typeof value.source === "string"
    && (value.parent_id === undefined || value.parent_id === null || typeof value.parent_id === "string");
}

type StageFourConfirmationError = {
  code: string;
  details?: Record<string, string>;
  message: string;
};

type StageFourConfirmationValidation = {
  chapters: ApiChapter[] | null;
  error: StageFourConfirmationError | null;
};

function parseStageFourConfirmationChapters(body: unknown): StageFourConfirmationValidation {
  if (!isRecord(body) || !Array.isArray(body.chapters) || body.chapters.length === 0 || !body.chapters.every(isStageFourChapter)) {
    return {
      chapters: null,
      error: {
        code: "invalid_chapters_payload",
        message: "Stage 4 confirmation requires a non-empty { chapters } payload with valid chapter records."
      }
    };
  }

  const chapters = body.chapters.map((chapter) => ({ ...chapter }));
  const byId = new Map<string, ApiChapter>();
  for (const chapter of chapters) {
    if (byId.has(chapter.chapter_id)) {
      return {
        chapters: null,
        error: {
          code: "invalid_chapters_payload",
          message: "Stage 4 confirmation chapter IDs must be unique."
        }
      };
    }
    byId.set(chapter.chapter_id, chapter);
  }

  for (const chapter of chapters) {
    if (chapter.parent_id === null || chapter.parent_id === undefined || chapter.parent_id === "") continue;
    const parent = byId.get(chapter.parent_id);
    if (!parent) {
      return {
        chapters: null,
        error: {
          code: "parent_chapter_missing",
          details: { chapter_id: chapter.chapter_id, parent_id: chapter.parent_id },
          message: "Parent chapter is missing."
        }
      };
    }
    if (chapter.page_start < parent.page_start || chapter.page_end > parent.page_end) {
      return {
        chapters: null,
        error: {
          code: "child_chapter_out_of_parent",
          details: { chapter_id: chapter.chapter_id, parent_id: chapter.parent_id },
          message: "Child chapter pages must stay within the parent chapter range."
        }
      };
    }
  }

  for (let index = 0; index < chapters.length; index += 1) {
    const chapter = chapters[index];
    for (const other of chapters.slice(index + 1)) {
      if (chapter.level !== other.level || chapter.parent_id !== other.parent_id) continue;
      if (chapter.page_start <= other.page_end && other.page_start <= chapter.page_end) {
        return {
          chapters: null,
          error: {
            code: "chapter_range_overlap",
            details: { chapter_id: chapter.chapter_id, overlap_with: other.chapter_id },
            message: "Sibling chapter page ranges cannot overlap."
          }
        };
      }
    }
  }

  return { chapters, error: null };
}

function stageFourCurrentChapters(state: StageFourFlowState) {
  return state.confirmedChapters ?? stageFourChapters;
}

type PreparedCourseState = {
  courses: CourseSummary[];
  imageMode: StageFiveImageMode;
  nextCourseIndex: number;
};

function resetPreparedCourseState(state: PreparedCourseState, imageMode: PreparedCourseState["imageMode"] = "success") {
  state.courses = stageFiveLibraryCourses.map((course) => ({ ...course }));
  state.imageMode = imageMode;
  state.nextCourseIndex = 0;
}

function appendPreparedCourse(state: PreparedCourseState) {
  state.nextCourseIndex += 1;
  const index = state.nextCourseIndex;
  const course: CourseSummary = {
    ...stageThreeCourse,
    book_id: `book_stage4a_arriving_${index}`,
    title: `Stage 4A arriving course ${index}`,
    filename: `stage4a-arriving-${index}.pdf`,
    next_title: `New course ${index} is ready to open`,
    updated_at: 100 + index
  };
  state.courses = [course, ...state.courses];
  return course;
}

function fixturePageSvg(page: number) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1200" viewBox="0 0 900 1200"><rect width="900" height="1200" fill="#f8fbff"/><rect x="72" y="72" width="756" height="1056" rx="28" fill="#ffffff" stroke="#dce8f8" stroke-width="8"/><rect x="132" y="164" width="414" height="38" rx="19" fill="#b8d9ff"/><rect x="132" y="240" width="614" height="24" rx="12" fill="#e6eef9"/><rect x="132" y="286" width="560" height="24" rx="12" fill="#e6eef9"/><circle cx="450" cy="600" r="166" fill="#e7f4ff"/><path d="M330 600h240M450 480v240" stroke="#7f74ff" stroke-width="26" stroke-linecap="round"/><text x="450" y="970" fill="#4f5e75" font-family="sans-serif" font-size="38" text-anchor="middle">Fixture source page ${page}</text></svg>`;
}

function getPreparedCourseResponse(request: ApiRequest, state: PreparedCourseState): FixtureResponse | undefined {
  if (request.method === "GET" && request.path === "/api/books") {
    return { status: 200, body: state.courses };
  }
  if (request.method === "DELETE" && request.path.startsWith("/api/books/")) {
    const bookId = decodeURIComponent(request.path.slice("/api/books/".length));
    if (!state.courses.some((course) => course.book_id === bookId)) {
      return { status: 404, body: { code: "book_not_found", message: "Fixture course does not exist." } };
    }
    state.courses = state.courses.filter((course) => course.book_id !== bookId);
    return { status: 204, body: null };
  }
  const sourcePageMatch = request.path.match(/^\/api\/books\/[^/]+\/pages\/(\d+)\/image$/);
  if (request.method === "GET" && sourcePageMatch) {
    const page = Number(sourcePageMatch[1]);
    const imageShouldFail = state.imageMode === "failure" || (state.imageMode === "mixed" && page === 1);
    if (imageShouldFail) {
      return { status: 404, body: { code: "source_page_unavailable", message: "Fixture image failure." } };
    }
    return {
      status: 200,
      contentType: "image/svg+xml",
      rawBody: true,
      body: fixturePageSvg(page)
    };
  }
  if (request.method === "GET" && request.path === `/api/jobs/${stageThreeJobId}`) {
    return {
      status: 200,
      body: {
        job_id: stageThreeJobId,
        book_id: stageThreeBookId,
        status: "done",
        stage: "complete",
        progress: 100,
        message: "解析完成",
        error: null
      }
    };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageThreeBookId}/scan-result`) {
    return { status: 200, body: stageThreeScanResult };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageThreeBookId}/chapters`) {
    return { status: 200, body: stageThreeChapters };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageThreeBookId}/chunks`) {
    return { status: 200, body: stageThreeChunks };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageThreeBookId}/assets`) {
    return { status: 200, body: stageThreeAssets };
  }
  if (request.method === "GET" && request.path.startsWith(`/api/books/${stageThreeBookId}/plan`)) {
    return { status: 200, body: stageThreeStudyPlan };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageThreeBookId}/lessons`) {
    return { status: 200, body: stageThreeLessons };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageThreeBookId}/flashcards`) {
    return { status: 200, body: stageThreeFlashcards };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageThreeBookId}/quizzes`) {
    return { status: 200, body: stageThreeQuizzes };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageThreeBookId}/toc-candidates`) {
    return {
      status: 200,
      body: {
        book_id: stageThreeBookId,
        status: "ready",
        toc_pages: [],
        page_map: [],
        chapter_evidence: [],
        warnings: []
      }
    };
  }
  if (request.method === "POST" && request.path === "/api/rag/query") {
    return {
      status: 200,
      body: {
        answer: "同源染色体会在减数第一次分裂中分离。",
        citations: [{
          chapter_id: "chapter_stage3",
          chapter_title: "细胞分裂",
          page: 1,
          chunk_id: "chunk_stage3",
          quote: "同源染色体在减数第一次分裂中分离。",
          score: 0.98,
          retrieval_method: "fixture",
          source_type: "page",
          location_type: "page",
          location_label: "第 1 页",
          source_metadata: {}
        }],
        related_assets: [],
        confidence: "high"
      }
    };
  }
  return undefined;
}

type ProcessingMotionFlowState = {
  enabled: boolean;
  jobIds: string[];
  jobReads: Map<string, number>;
  nextJobIndex: number;
  progressSequence: number[];
  uploaded: boolean;
};

function resetProcessingMotionFlow(state: ProcessingMotionFlowState, options: ProcessingMotionFlowOptions = {}) {
  state.enabled = false;
  state.jobIds = options.jobIds?.length ? [...options.jobIds] : [...defaultProcessingMotionJobIds];
  state.jobReads = new Map();
  state.nextJobIndex = 0;
  state.progressSequence = options.progressSequence?.length
    ? [...options.progressSequence]
    : [...defaultProcessingMotionProgressSequence];
  state.uploaded = false;
}

function getProcessingMotionFlowResponse(request: ApiRequest, state: ProcessingMotionFlowState): FixtureResponse | undefined {
  if (!state.enabled) return undefined;

  if (request.method === "GET" && request.path === "/api/books") {
    return { status: 200, body: [] };
  }

  if (request.method === "POST" && request.path === "/api/uploads/init") {
    return {
      status: 200,
      body: {
        book_id: processingMotionBookId,
        upload_url: `/api/books/${processingMotionBookId}/files`,
        max_upload_bytes: 20_000_000
      }
    };
  }

  if (request.method === "POST" && request.path === `/api/books/${processingMotionBookId}/files`) {
    state.uploaded = true;
    return {
      status: 200,
      body: {
        book_id: processingMotionBookId,
        filename: "processing-motion.pdf",
        size_bytes: 1024,
        status: "uploaded"
      }
    };
  }

  if (request.method === "POST" && request.path === `/api/books/${processingMotionBookId}/parse`) {
    state.uploaded = true;
    const jobId = state.jobIds[Math.min(state.nextJobIndex, state.jobIds.length - 1)];
    state.nextJobIndex += 1;
    return {
      status: 200,
      body: {
        book_id: processingMotionBookId,
        job_id: jobId,
        status: "pending"
      }
    };
  }

  const jobId = request.path.startsWith("/api/jobs/")
    ? decodeURIComponent(request.path.slice("/api/jobs/".length))
    : null;
  if (request.method === "GET" && jobId && state.jobIds.includes(jobId)) {
    const readCount = state.jobReads.get(jobId) ?? 0;
    const progress = state.progressSequence[Math.min(readCount, state.progressSequence.length - 1)];
    state.jobReads.set(jobId, readCount + 1);
    return {
      status: 200,
      body: {
        job_id: jobId,
        book_id: processingMotionBookId,
        status: "processing",
        stage: "motion-fixture",
        progress,
        message: `Processing motion fixture at ${progress}%.`,
        error: null
      }
    };
  }

  return undefined;
}

type StageSixFlowState = {
  enabled: boolean;
  mistakeMode: StageSixMistakeMode;
  mistakeSet: StageSixMistakeSet;
  releaseMistakes: (() => void) | null;
  submissionCount: number;
  studyPlan: StageSixFixtureStudyPlan;
};

function cloneStageSixStudyPlan(options: StageSixFlowOptions = {}): StageSixFixtureStudyPlan {
  const sourceTasks = options.taskMode === "out_of_range" ? [stageSixOutOfRangeTask] : stageSixSparseStudyPlan.tasks;
  return {
    ...stageSixSparseStudyPlan,
    days: options.studyPlanDays === undefined ? stageSixSparseStudyPlan.days : options.studyPlanDays,
    tasks: sourceTasks.map((task) => ({
      ...task,
      weak_points: [...task.weak_points]
    }))
  };
}

function resetStageSixFlow(state: StageSixFlowState, options: StageSixFlowOptions = {}) {
  state.enabled = false;
  state.mistakeMode = options.mistakeMode ?? "success";
  state.mistakeSet = options.mistakeSet ?? "default";
  state.releaseMistakes = null;
  state.submissionCount = 0;
  state.studyPlan = cloneStageSixStudyPlan(options);
}

type StageFourFlowState = {
  confirmedChapters: ApiChapter[] | null;
  confirmationResponse: ApiChapter[] | null;
  enabled: boolean;
  jobReads: number;
  mode: "success" | "failed";
  progress: number;
  uploaded: boolean;
};

function getStageFourFlowResponse(request: ApiRequest, state: StageFourFlowState): FixtureResponse | undefined {
  if (!state.enabled) return undefined;

  if (request.method === "GET" && request.path === "/api/books") {
    if (!state.uploaded) return { status: 200, body: [] };
    const failed = state.mode === "failed";
    const complete = state.jobReads >= 2 && !failed;
    const chapters = stageFourCurrentChapters(state);
    return {
      status: 200,
      body: [{
        ...stageFourCourse,
        chapter_count: chapters.length,
        next_title: chapters[0]?.source_title ?? stageFourCourse.next_title,
        status: failed ? "error" : complete ? "ready" : "processing",
        parse_job_status: failed ? "failed" : complete ? "done" : "processing",
        parse_job_progress: failed ? 0 : complete ? 100 : state.progress,
        parse_job_error: failed ? stageFourLongError : null
      }]
    };
  }

  if (request.method === "POST" && request.path === "/api/uploads/init") {
    return {
      status: 200,
      body: {
        book_id: stageFourBookId,
        upload_url: `/api/books/${stageFourBookId}/files`,
        max_upload_bytes: 20_000_000
      }
    };
  }
  if (request.method === "POST" && request.path === `/api/books/${stageFourBookId}/files`) {
    state.uploaded = true;
    return {
      status: 200,
      body: {
        book_id: stageFourBookId,
        filename: stageFourLongFilename,
        size_bytes: 1024,
        status: "uploaded"
      }
    };
  }
  if (request.method === "POST" && request.path === `/api/books/${stageFourBookId}/parse`) {
    state.uploaded = true;
    return {
      status: 200,
      body: {
        book_id: stageFourBookId,
        job_id: stageFourJobId,
        status: "pending"
      }
    };
  }
  if (request.method === "GET" && request.path === `/api/jobs/${stageFourJobId}`) {
    state.jobReads += 1;
    if (state.mode === "failed") {
      return {
        status: 200,
        body: {
          job_id: stageFourJobId,
          book_id: stageFourBookId,
          status: "failed",
          stage: "source-validation",
          progress: 0,
          message: stageFourLongError,
          error: stageFourLongError
        }
      };
    }
    if (state.jobReads === 1) {
      return {
        status: 200,
        body: {
          job_id: stageFourJobId,
          book_id: stageFourBookId,
          status: "processing",
          stage: "indexing",
          progress: state.progress,
          message: `Stage 4 processing at ${state.progress}% with a long filename.`,
          error: null
        }
      };
    }
    return {
      status: 200,
      body: {
        job_id: stageFourJobId,
        book_id: stageFourBookId,
        status: "done",
        stage: "complete",
        progress: 100,
        message: "Stage 4 parsing completed.",
        error: null
      }
    };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageFourBookId}/scan-result`) {
    return { status: 200, body: stageFourScanResult };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageFourBookId}/chapters`) {
    return { status: 200, body: stageFourCurrentChapters(state) };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageFourBookId}/chunks`) {
    return { status: 200, body: stageFourChunks };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageFourBookId}/assets`) {
    return { status: 200, body: [] };
  }
  if (request.method === "GET" && request.path.startsWith(`/api/books/${stageFourBookId}/plan`)) {
    return { status: 200, body: stageFourStudyPlan };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageFourBookId}/lessons`) {
    return { status: 200, body: [] };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageFourBookId}/flashcards`) {
    return { status: 200, body: [] };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageFourBookId}/quizzes`) {
    return { status: 200, body: [] };
  }
  if (request.method === "GET" && request.path === `/api/books/${stageFourBookId}/toc-candidates`) {
    return { status: 200, body: stageFourTocAnalysis };
  }
  if (request.method === "POST" && request.path === `/api/books/${stageFourBookId}/chapters/confirm`) {
    const validation = parseStageFourConfirmationChapters(request.body);
    if (!validation.chapters || validation.error) {
      return {
        status: 400,
        body: validation.error ?? {
          code: "invalid_chapters_payload",
          message: "Stage 4 confirmation payload is invalid."
        }
      };
    }
    state.confirmedChapters = validation.chapters.map((chapter) => ({ ...chapter, status: "已确认" }));
    state.confirmationResponse = state.confirmedChapters.map((chapter) => ({ ...chapter }));
    return { status: 200, body: state.confirmationResponse };
  }
  if (request.method === "POST" && request.path === `/api/books/${stageFourBookId}/lessons/build`) {
    return {
      status: 200,
      body: {
        job_id: stageFourLessonJobId,
        book_id: stageFourBookId,
        status: "done",
        stage: "complete",
        progress: 100,
        lessons: stageFourLessons,
        chapter_results: [{
          chapter_id: "chapter_stage4_primary",
          chapter_title: stageFourLongChapterTitle,
          status: "done",
          lesson_kind: "lesson",
          lesson_id: "lesson_stage4"
        }],
        error: null
      }
    };
  }
  if (request.method === "POST" && request.path === `/api/books/${stageFourBookId}/flashcards/build`) {
    return { status: 200, body: [] };
  }
  if (request.method === "POST" && request.path === `/api/books/${stageFourBookId}/quizzes/build`) {
    return { status: 200, body: [] };
  }
  if (request.method === "POST" && request.path === `/api/books/${stageFourBookId}/plan`) {
    return { status: 200, body: stageFourStudyPlan };
  }
  return undefined;
}

function getStageSixFlowResponse(request: ApiRequest, state: StageSixFlowState): FixtureResponse | undefined {
  if (!state.enabled) return undefined;

  if (request.method === "GET" && request.path.startsWith(`/api/books/${stageThreeBookId}/plan`)) {
    return { status: 200, body: state.studyPlan };
  }

  if (request.method === "GET" && request.path === `/api/books/${stageThreeBookId}/flashcards`) {
    return { status: 200, body: stageSixFlashcards };
  }

  if (request.method === "PATCH" && request.path.startsWith("/api/study-tasks/")) {
    const taskId = decodeURIComponent(request.path.slice("/api/study-tasks/".length));
    const currentTask = state.studyPlan.tasks.find((task) => task.task_id === taskId);
    if (!currentTask) {
      return { status: 404, body: { code: "study_task_not_found", message: "Stage 6 fixture task does not exist." } };
    }
    const payload = typeof request.body === "object" && request.body !== null && !Array.isArray(request.body)
      ? request.body as Record<string, unknown>
      : {};
    const updated = {
      ...currentTask,
      status: typeof payload.status === "string" ? payload.status : currentTask.status,
      score: typeof payload.score === "number" || payload.score === null ? payload.score : currentTask.score,
      weak_points: Array.isArray(payload.weak_points) && payload.weak_points.every((point) => typeof point === "string")
        ? [...payload.weak_points]
        : currentTask.weak_points
    };
    state.studyPlan = {
      ...state.studyPlan,
      tasks: state.studyPlan.tasks.map((task) => (task.task_id === taskId ? updated : task))
    };
    return { status: 200, body: updated };
  }

  if (request.method === "POST" && request.path === "/api/assignments/assignment_chapter_stage3/submit") {
    state.submissionCount += 1;
    const submissionId = state.submissionCount === 1 ? "submission_stage6" : `submission_stage6_${state.submissionCount}`;
    return {
      status: 200,
      body: {
        assignment_id: "assignment_chapter_stage3",
        submission_id: submissionId,
        status: "submitted"
      }
    };
  }

  if (request.method === "POST" && request.path.startsWith("/api/assignments/assignment_chapter_stage3/diagnose")) {
    const submissionId = new URL(request.path, applicationOrigin).searchParams.get("submission_id") ?? stageSixDiagnosis.submission_id;
    return { status: 200, body: { ...stageSixDiagnosis, submission_id: submissionId } };
  }

  if (request.method === "GET" && request.path.startsWith("/api/users/responsive_fixture_user/mistakes")) {
    if (state.mistakeMode === "error") {
      return {
        status: 200,
        contentType: "application/json; charset=utf-8",
        rawBody: true,
        body: "not valid Stage 6 fixture JSON"
      };
    }
    if (state.mistakeMode === "loading") return undefined;
    return { status: 200, body: state.mistakeSet === "detail_pair" ? stageSixMistakeDetailPair : stageSixMistakes };
  }

  if (request.method === "GET" && request.path === "/api/users/responsive_fixture_user/learning-state") {
    return { status: 200, body: stageSixLearningState };
  }

  return undefined;
}

function getFixtureResponse(
  request: ApiRequest,
  preparedCourseEnabled: boolean,
  preparedCourseState: PreparedCourseState,
  processingMotionFlow: ProcessingMotionFlowState,
  stageFourFlow: StageFourFlowState,
  stageSixFlow: StageSixFlowState
): FixtureResponse | undefined {
  const processingMotionResponse = getProcessingMotionFlowResponse(request, processingMotionFlow);
  if (processingMotionResponse) return processingMotionResponse;
  const stageSixResponse = getStageSixFlowResponse(request, stageSixFlow);
  if (stageSixResponse) return stageSixResponse;
  if (preparedCourseEnabled) {
    const preparedResponse = getPreparedCourseResponse(request, preparedCourseState);
    if (preparedResponse) return preparedResponse;
  }
  const stageFourResponse = getStageFourFlowResponse(request, stageFourFlow);
  if (stageFourResponse) return stageFourResponse;
  if (request.method === "GET" && request.path === "/api/books") {
    return { status: 200, body: [] };
  }
  return undefined;
}

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": applicationOrigin,
    "Access-Control-Allow-Headers": "Content-Type, X-BookCourse-User-Id",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
    "Cache-Control": "no-store"
  };
}

async function fulfillJson(route: Route, response: FixtureResponse) {
  await route.fulfill({
    status: response.status,
    contentType: response.contentType ?? "application/json; charset=utf-8",
    headers: corsHeaders(),
    body: response.rawBody ? String(response.body) : JSON.stringify(response.body)
  });
}

export async function installBookCourseApiFixture(page: Page): Promise<BookCourseApiFixture> {
  let preparedCourseEnabled = false;
  const preparedCourseState: PreparedCourseState = {
    courses: [],
    imageMode: "success",
    nextCourseIndex: 0
  };
  resetPreparedCourseState(preparedCourseState);
  const stageFourFlow: StageFourFlowState = {
    confirmedChapters: null,
    confirmationResponse: null,
    enabled: false,
    jobReads: 0,
    mode: "success",
    progress: 0,
    uploaded: false
  };
  const processingMotionFlow: ProcessingMotionFlowState = {
    enabled: false,
    jobIds: [],
    jobReads: new Map(),
    nextJobIndex: 0,
    progressSequence: [],
    uploaded: false
  };
  resetProcessingMotionFlow(processingMotionFlow);
  const stageSixFlow: StageSixFlowState = {
    enabled: false,
    mistakeMode: "success",
    mistakeSet: "default",
    releaseMistakes: null,
    submissionCount: 0,
    studyPlan: cloneStageSixStudyPlan()
  };
  const fixture: BookCourseApiFixture = {
    requests: [],
    unhandledRequests: [],
    externalRequests: [],
    get lastStageFourConfirmationResponse() {
      return stageFourFlow.confirmationResponse;
    },
    consoleErrors: [],
    pageErrors: [],
    appendPreparedCourse: () => appendPreparedCourse(preparedCourseState),
    setPreparedImageMode: (mode) => {
      preparedCourseState.imageMode = mode;
    },
    usePreparedCourse: () => {
      preparedCourseEnabled = true;
      resetPreparedCourseState(preparedCourseState);
      resetStageSixFlow(stageSixFlow);
      processingMotionFlow.enabled = false;
    },
    useStageFiveFlow: (options = {}) => {
      preparedCourseEnabled = true;
      resetPreparedCourseState(preparedCourseState, options.imageMode ?? "success");
      resetStageSixFlow(stageSixFlow);
      processingMotionFlow.enabled = false;
    },
    useStageSixFlow: (options = {}) => {
      preparedCourseEnabled = true;
      resetPreparedCourseState(preparedCourseState);
      resetStageSixFlow(stageSixFlow, options);
      stageSixFlow.enabled = true;
      processingMotionFlow.enabled = false;
    },
    releaseStageSixMistakes: () => {
      const release = stageSixFlow.releaseMistakes;
      stageSixFlow.releaseMistakes = null;
      stageSixFlow.mistakeMode = "success";
      release?.();
    },
    useStageFourFlow: (options = {}) => {
      resetStageSixFlow(stageSixFlow);
      processingMotionFlow.enabled = false;
      stageFourFlow.enabled = true;
      stageFourFlow.jobReads = 0;
      stageFourFlow.mode = options.mode ?? "success";
      stageFourFlow.progress = options.progress ?? 0;
      stageFourFlow.uploaded = false;
      stageFourFlow.confirmedChapters = null;
      stageFourFlow.confirmationResponse = null;
    },
    useProcessingMotionFlow: (options = {}) => {
      preparedCourseEnabled = false;
      resetStageSixFlow(stageSixFlow);
      stageFourFlow.enabled = false;
      resetProcessingMotionFlow(processingMotionFlow, options);
      processingMotionFlow.enabled = true;
    }
  };

  page.on("console", (message) => {
    if (message.type() === "error") {
      fixture.consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => fixture.pageErrors.push(error.message));

  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== applicationOrigin) {
      fixture.externalRequests.push(url.toString());
      await route.abort("blockedbyclient");
      return;
    }

    if (!url.pathname.startsWith("/api/")) {
      await route.fallback();
      return;
    }

    const request = route.request();
    const contentType = request.headers()["content-type"] ?? "";
    const postData = request.postData();
    let body: unknown = null;
    if (postData) {
      if (contentType.includes("application/json")) {
        try {
          body = JSON.parse(postData) as unknown;
        } catch {
          body = postData;
        }
      } else {
        body = postData;
      }
    }
    const apiRequest: ApiRequest = {
      body,
      method: request.method(),
      path: `${url.pathname}${url.search}`
    };
    fixture.requests.push(apiRequest);

    if (apiRequest.method === "OPTIONS") {
      await route.fulfill({ status: 204, headers: corsHeaders() });
      return;
    }

    if (
      stageSixFlow.enabled
      && stageSixFlow.mistakeMode === "loading"
      && apiRequest.method === "GET"
      && apiRequest.path.startsWith("/api/users/responsive_fixture_user/mistakes")
    ) {
      await new Promise<void>((resolve) => {
        stageSixFlow.releaseMistakes = resolve;
      });
      const releasedResponse = getStageSixFlowResponse(apiRequest, stageSixFlow);
      if (!releasedResponse) throw new Error("Stage 6 mistake fixture was released without a response.");
      await fulfillJson(route, releasedResponse);
      return;
    }

    const response = getFixtureResponse(apiRequest, preparedCourseEnabled, preparedCourseState, processingMotionFlow, stageFourFlow, stageSixFlow);
    if (response) {
      await fulfillJson(route, response);
      return;
    }

    fixture.unhandledRequests.push(apiRequest);
    await fulfillJson(route, {
      status: 404,
      body: {
        code: "fixture_not_found",
        message: `No local Playwright fixture exists for ${apiRequest.method} ${apiRequest.path}`
      }
    });
  });

  return fixture;
}
