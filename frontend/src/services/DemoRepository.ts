import demoStateJson from "../data/generated/demo-state.json";
import type {
  ApiAsset,
  ApiChapter,
  ApiChunk,
  AssignmentSubmitRequest,
  AssignmentSubmitResponse,
  CourseSummary,
  DiagnosisResponse,
  Flashcard,
  ImageGenerationJobResponse,
  ImageGenerationRequest,
  JobStatusResponse,
  LearningState,
  Lesson,
  LessonBuildJobResponse,
  LessonBuildRequest,
  MistakeRecord,
  PageMapEntry,
  ParseJobResponse,
  QuizQuestion,
  RagQuery,
  RagResponse,
  ScanResult,
  StudyPlan,
  StudyPlanRequest,
  StudyTask,
  StudyTaskUpdate,
  TocAnalysis,
  UploadInitRequest,
  UploadInitResponse,
  FileSaveResponse
} from "../types/api";

type DemoState = {
  provenance: Record<string, unknown>;
  book: {
    id: string;
    title: string;
    fileName: string;
    pages: number;
    fileSize: string;
    chapterCount: number;
    sectionCount: number;
    knowledgePointCount: number;
    progress: number;
    mastery: number;
    planDays: number;
    dailyMinutes: number;
    cover: string;
  };
  scan: ScanResult;
  chapters: ApiChapter[];
  chunks: ApiChunk[];
  assets: ApiAsset[];
  lessons: Lesson[];
  flashcards: Flashcard[];
  quizzes: QuizQuestion[];
  studyPlan: StudyPlan;
  assignment: {
    assignment_id: string;
    title: string;
    source: string;
    question: string;
    initialAnswer: string;
    correctIdea: string;
  };
  diagnosis: {
    result: string;
    stuck_point: string;
    knowledge_points: string[];
    review_page: string;
    hint: string;
  };
  aiReplies: Record<string, string>;
};

type DemoJob = {
  jobId: string;
  bookId: string;
  pollCount: number;
};

const seed = demoStateJson as unknown as DemoState;
const demoDelay = Math.max(0, Number(import.meta.env.VITE_DEMO_DELAY_MS ?? 80));

function wait() {
  return new Promise<void>((resolve) => globalThis.setTimeout(resolve, demoDelay));
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function asCitation(chapterId: string, chunkId: string, quote: string) {
  const chapter = seed.chapters.find((item) => item.chapter_id === chapterId);
  const chunk = seed.chunks.find((item) => item.chunk_id === chunkId);
  const metadata = (chunk?.source_metadata ?? {}) as {
    pdf_pages?: number[];
    printed_pages?: number[];
  };
  const page = metadata.pdf_pages?.[0] ?? chunk?.page_start ?? 1;
  const printedPage = metadata.printed_pages?.[0] ?? chunk?.printed_page_start ?? null;
  return {
    chapter_id: chapterId,
    chapter_title: chapter?.source_title ?? "教材原文",
    page,
    chunk_id: chunkId,
    quote,
    score: 0.93,
    retrieval_method: "mineru-fixture",
    source_type: "textbook",
    location_type: "page" as const,
    location_label: printedPage ? `教材第 ${printedPage} 页（PDF 第 ${page} 页）` : `PDF 第 ${page} 页`,
    source_metadata: { ...metadata, parser: seed.provenance.parser, parser_version: seed.provenance.parser_version, retrieval_quote: quote }
  };
}

export class DemoRepository {
  private readonly jobs = new Map<string, DemoJob>();
  private readonly lessonJobs = new Map<string, number>();
  private parseJobSequence = 0;
  private state: DemoState = clone(seed);

  reset() {
    this.jobs.clear();
    this.lessonJobs.clear();
    this.parseJobSequence = 0;
    this.state = clone(seed);
  }

  async health() {
    await wait();
    return { status: "ok", service: "study-app-demo" };
  }

  async listCourses(): Promise<CourseSummary[]> {
    await wait();
    const { book } = this.state;
    return [{
      book_id: book.id,
      title: book.title,
      filename: book.fileName,
      status: "ready",
      page_count: book.pages,
      chapter_count: book.chapterCount,
      chunk_count: this.state.chunks.length,
      asset_count: this.state.assets.length,
      average_confidence: 93,
      next_title: "第 2 章 1 节 · 减数分裂和受精作用",
      rag_index_status: "ready",
      rag_index_provider: "local-fixture",
      rag_index_generation: 1,
      parse_job_id: null,
      parse_job_status: "done",
      parse_job_stage: "completed",
      parse_job_progress: 100,
      parse_job_message: "MinerU 结构化课程已就绪",
      parse_job_error: null,
      updated_at: 1785638400
    }];
  }

  async deleteCourse(_bookId: string) {
    await wait();
  }

  async initUpload(_payload: UploadInitRequest): Promise<UploadInitResponse> {
    await wait();
    return {
      book_id: this.state.book.id,
      upload_url: "demo://local-fixture",
      max_upload_bytes: 100 * 1024 * 1024
    };
  }

  async uploadFile(bookId: string, file: File): Promise<FileSaveResponse> {
    await wait();
    return { book_id: bookId, filename: file.name, size_bytes: file.size, status: "uploaded" };
  }

  async startParse(bookId: string): Promise<ParseJobResponse> {
    await wait();
    this.parseJobSequence += 1;
    const jobId = `parse_job_demo_${this.parseJobSequence}`;
    this.jobs.set(jobId, { jobId, bookId, pollCount: 0 });
    return { book_id: bookId, job_id: jobId, status: "pending" };
  }

  async getJob(jobId: string): Promise<JobStatusResponse> {
    await wait();
    const job = this.jobs.get(jobId) ?? { jobId, bookId: this.state.book.id, pollCount: 0 };
    job.pollCount += 1;
    this.jobs.set(jobId, job);
    const progress = [18, 46, 74, 100][Math.min(job.pollCount - 1, 3)];
    const done = progress === 100;
    return {
      job_id: job.jobId,
      book_id: job.bookId,
      status: done ? "done" : "processing",
      stage: done ? "completed" : ["mineru_layout", "mineru_ocr", "chapters", "lessons"][Math.min(job.pollCount - 1, 3)],
      progress,
      message: done ? "MinerU 扫描与固定课程内容已完成" : "正在调用本地 MinerU 解析扫描版教材",
      error: null
    };
  }

  async getScanResult(_bookId: string) {
    await wait();
    return clone(this.state.scan);
  }

  async getChapters(_bookId: string) {
    await wait();
    return clone(this.state.chapters);
  }

  async updateChapter(_bookId: string, chapterId: string, payload: Partial<ApiChapter>) {
    await wait();
    const chapter = this.state.chapters.find((item) => item.chapter_id === chapterId);
    if (!chapter) throw new Error("目录项不存在");
    Object.assign(chapter, payload);
    return clone(chapter);
  }

  async rebuildChapters(_bookId: string) {
    await wait();
    return clone(this.state.chapters);
  }

  async getTocAnalysis(bookId: string): Promise<TocAnalysis> {
    await wait();
    const pageMap: PageMapEntry[] = this.state.scan.source_locations.map((location) => ({
      pdf_page: Number(location.pdf_page ?? location.index),
      printed_page: typeof location.printed_page === "number" ? location.printed_page : null,
      confidence: Number(location.confidence ?? 0),
      source: String(location.source ?? "mineru"),
      evidence: typeof location.evidence === "string" ? location.evidence : "MinerU page-level layout result"
    }));
    const pageMapByPdf = new Map(pageMap.map((entry) => [entry.pdf_page, entry]));
    return {
      book_id: bookId,
      status: "ready",
      toc_pages: [{ page: 3, score: 96, line_count: this.state.chapters.length, reasons: ["MinerU title hierarchy", "page range continuity"], sample_lines: this.state.chapters.slice(0, 4).map((item) => item.source_title) }],
      page_map: pageMap,
      chapter_evidence: this.state.chapters.map((chapter) => ({
        chapter_id: chapter.chapter_id,
        source_title: chapter.source_title,
        level: chapter.level,
        printed_page_start: pageMapByPdf.get(chapter.page_start)?.printed_page ?? null,
        pdf_page_start: chapter.page_start,
        pdf_page_end: chapter.page_end,
        toc_line_confidence: chapter.confidence,
        page_map_confidence: pageMapByPdf.get(chapter.page_start)?.confidence ?? chapter.confidence,
        title_match_page: chapter.page_start,
        title_match_score: chapter.confidence,
        confidence: chapter.confidence,
        status: chapter.status,
        reasons: ["MinerU 目录层级", "content_list 标题页", "PDF 与教材印刷页映射"]
      })),
      warnings: []
    };
  }

  async getPageMap(bookId: string) {
    return (await this.getTocAnalysis(bookId)).page_map;
  }

  async confirmChapters(_bookId: string, chapters?: ApiChapter[]) {
    await wait();
    this.state.chapters = clone(chapters?.length ? chapters : this.state.chapters);
    return clone(this.state.chapters);
  }

  async getChunks(_bookId: string) {
    await wait();
    return clone(this.state.chunks);
  }

  async buildLessons(bookId: string, payload: LessonBuildRequest = {}): Promise<LessonBuildJobResponse> {
    await wait();
    const requested = payload.chapter_ids?.length ? new Set(payload.chapter_ids) : null;
    const lessons = clone(this.state.lessons.filter((lesson) => !requested || requested.has(lesson.chapter_id)));
    const jobId = `lesson_job_${requested?.values().next().value ?? "all"}`;
    this.lessonJobs.set(jobId, 0);
    return {
      job_id: jobId,
      book_id: bookId,
      status: "processing",
      stage: "lesson_generation",
      progress: 35,
      lessons,
      chapter_results: lessons.map((lesson) => ({
        chapter_id: lesson.chapter_id,
        chapter_title: lesson.source_title,
        status: "processing",
        lesson_kind: "lesson",
        lesson_id: lesson.lesson_id
      }))
    };
  }

  async getLessonJob(jobId: string): Promise<LessonBuildJobResponse> {
    await wait();
    const bookId = this.state.book.id;
    const pollCount = (this.lessonJobs.get(jobId) ?? 0) + 1;
    this.lessonJobs.set(jobId, pollCount);
    return {
      job_id: jobId,
      book_id: bookId,
      status: "done",
      stage: "completed",
      progress: 100,
      lessons: clone(this.state.lessons),
      chapter_results: this.state.lessons.map((lesson) => ({
        chapter_id: lesson.chapter_id,
        chapter_title: lesson.source_title,
        status: "done",
        lesson_kind: "lesson",
        lesson_id: lesson.lesson_id
      }))
    };
  }

  async getLessons(_bookId: string) {
    await wait();
    return clone(this.state.lessons);
  }

  async getLesson(_bookId: string, lessonId: string) {
    await wait();
    const lesson = this.state.lessons.find((item) => item.lesson_id === lessonId);
    if (!lesson) throw new Error("课程不存在");
    return clone(lesson);
  }

  async buildFlashcards(_bookId: string, payload: LessonBuildRequest = {}) {
    await wait();
    const requested = payload.chapter_ids?.length ? new Set(payload.chapter_ids) : null;
    return clone(this.state.flashcards.filter((card) => !requested || requested.has(card.chapter_id)));
  }

  async getFlashcards(_bookId: string) {
    await wait();
    return clone(this.state.flashcards);
  }

  async buildQuizzes(_bookId: string, payload: LessonBuildRequest = {}) {
    await wait();
    const requested = payload.chapter_ids?.length ? new Set(payload.chapter_ids) : null;
    return clone(this.state.quizzes.filter((quiz) => !requested || requested.has(quiz.chapter_id)));
  }

  async getQuizzes(_bookId: string) {
    await wait();
    return clone(this.state.quizzes);
  }

  async getAssets(_bookId: string) {
    await wait();
    return clone(this.state.assets);
  }

  async getChapterFigures(chapterId: string) {
    await wait();
    return clone(this.state.assets.filter((asset) => asset.chapter_id === chapterId));
  }

  async generateLessonFigure(_lessonId: string, payload: ImageGenerationRequest): Promise<ImageGenerationJobResponse> {
    return this.generateAsset(payload);
  }

  async generateAsset(payload: ImageGenerationRequest): Promise<ImageGenerationJobResponse> {
    await wait();
    const asset: ApiAsset = {
      asset_id: "asset_demo_generated",
      book_id: payload.book_id,
      chapter_id: payload.chapter_id,
      source_type: "ai_generated",
      page: null,
      type: "diagram",
      caption: "固定演示示意图",
      bbox: null,
      image_url: "/assets/textbook/biology-illustration-cell-division.webp",
      thumbnail_url: "/assets/textbook/biology-illustration-cell-division.webp",
      source_page_image_url: null,
      source_chunk_ids: payload.source_chunk_ids,
      concepts: payload.concepts,
      generation_provider: "local-fixture",
      review_status: "approved"
    };
    this.state.assets.push(asset);
    return { job_id: "image_job_demo", book_id: payload.book_id, status: "done", stage: "completed", progress: 100, asset };
  }

  async getImageGenerationJob(jobId: string): Promise<ImageGenerationJobResponse> {
    await wait();
    return { job_id: jobId, book_id: this.state.book.id, status: "done", stage: "completed", progress: 100, asset: null };
  }

  async queryRag(payload: RagQuery): Promise<RagResponse> {
    await wait();
    const key = payload.question.includes("第二次") ? "quiz" : payload.question.includes("例") ? "example" : "default";
    const chunkId = key === "quiz" ? "chunk_c2s1_13" : key === "example" ? "chunk_c2s1_19" : "chunk_c2s1_11";
    const quote = key === "quiz"
      ? "两条姐妹染色单体也随之分开"
      : key === "example"
        ? "受精作用是卵细胞和精子相互识别、融合成为受精卵的过程"
        : "染色体只复制一次，而细胞分裂两次";
    return {
      answer: this.state.aiReplies[key],
      citations: [asCitation("c2s1", chunkId, quote)],
      related_assets: clone(this.state.assets.filter((asset) => asset.chapter_id === "c2s1")),
      confidence: "high"
    };
  }

  async submitAssignment(assignmentId: string, _payload: AssignmentSubmitRequest): Promise<AssignmentSubmitResponse> {
    await wait();
    return { assignment_id: assignmentId, submission_id: "submission_demo_01", status: "submitted" };
  }

  async diagnoseAssignment(assignmentId: string, submissionId: string): Promise<DiagnosisResponse> {
    await wait();
    return {
      assignment_id: assignmentId,
      submission_id: submissionId,
      result: this.state.diagnosis.result,
      stuck_point: this.state.diagnosis.stuck_point,
      knowledge_points: clone(this.state.diagnosis.knowledge_points),
       review_citations: [asCitation("c2s1", "chunk_c2s1_13", "配对的两条同源染色体彼此分离")],
      related_assets: clone(this.state.assets.filter((asset) => asset.chapter_id === "c2s1")),
      hint: this.state.diagnosis.hint,
      needs_followup: false,
      followup_question: null,
      mistake_recorded: true
    };
  }

  async getMistakes(userId: string, bookId = this.state.book.id): Promise<MistakeRecord[]> {
    await wait();
    return [{
      mistake_id: "mistake_demo_01",
      user_id: userId,
      book_id: bookId,
      assignment_id: this.state.assignment.assignment_id,
      question: this.state.assignment.question,
      answer: this.state.assignment.initialAnswer,
      stuck_point: this.state.diagnosis.stuck_point,
      knowledge_points: clone(this.state.diagnosis.knowledge_points),
      citation_ids: ["chunk_c2s1_13"]
    }];
  }

  async createStudyPlan(bookId: string, payload: StudyPlanRequest): Promise<StudyPlan> {
    await wait();
    this.state.studyPlan = { ...clone(this.state.studyPlan), book_id: bookId, user_id: payload.user_id ?? this.state.studyPlan.user_id };
    return clone(this.state.studyPlan);
  }

  async getStudyPlan(_bookId: string, userId = "local_user") {
    await wait();
    return { ...clone(this.state.studyPlan), user_id: userId };
  }

  async patchStudyTask(taskId: string, payload: StudyTaskUpdate): Promise<StudyTask> {
    await wait();
    const task = this.state.studyPlan.tasks.find((item) => item.task_id === taskId);
    if (!task) throw new Error("学习任务不存在");
    Object.assign(task, payload);
    return clone(task);
  }

  async getLearningState(userId: string): Promise<LearningState> {
    await wait();
    const completed = this.state.studyPlan.tasks.filter((task) => task.status === "done").length;
    return {
      user_id: userId,
      completed_tasks: completed,
      pending_tasks: this.state.studyPlan.tasks.length - completed,
      average_score: 82,
      weak_points: clone(this.state.diagnosis.knowledge_points),
      mistake_count: 1
    };
  }
}

export const demoRepository = new DemoRepository();
